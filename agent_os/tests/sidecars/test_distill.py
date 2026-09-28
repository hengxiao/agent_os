"""DistillSidecar 锚点测试(docs/DESIGN.md §11.2 "蒸馏 sidecar" 写路径范式)。

固定约定:

- 订阅 ``run.finished``/``run.aborted``(Mode.ASYNC,priority 90);触发条件:
  ``run.aborted`` 恒触发(failure reflection),``run.finished`` 需该 run 帧树内
  TOOL 角色消息数 > ``min_tool_calls`` 才触发(strategy summary);
- 终态信号直挂总线(builder 特殊接线):emit 后 ``Kernel.run`` 的 finally 立即
  ``supervisor.close()`` 取消 ASYNC wrapper(emit→close 无让出点,wrapper 从未
  运行就被回收),故 ``on_signal`` 只做触发判定 + ``create_task`` 实例自管;
  同 run_id 幂等(``_seen`` 去重,resume 重发 ``run.finished`` 不重复写);
- 写入约定(同 system.memory.write):``MemoryEntry(source={"kind": "experience"},
  trust="experience", tags=["distill", kind, 根技能名])`` +
  ``Provenance(run_id, task=根技能名, note="distill", detail={model, usage})``;
- 蒸馏 LLM 连败 ``breaker_threshold`` 次熔断,此后 on_signal 不再派发;
  providers/memory/stack 任一未 bind(如未配 [memory])→ 实例休眠;
- ``wait_pending()`` 收口在跑蒸馏任务;``close()`` 取消并回收(supervisor.close
  只管 wrapper,不调 sidecar.close,由宿主/测试显式调用)。

双路 mock 大脑按 SYSTEM 指令区分:蒸馏请求的 SYSTEM 含"经验蒸馏器";
verify 档(默认开)再加一路审查请求,SYSTEM 含"入库审查员"(三路 mock)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    RUN_FINISHED,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    Signal,
    ToolCall,
    ToolPolicy,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.memory.local_file import LocalFileMemoryService, _parse
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.sidecars import DistillSidecar
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry

LOOPER_YAML = """
skills:
  - name: test.looper
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [system.python.exec], skills: [] }
    model: { prefer: ["mock/loop"] }
    limits: { max_steps: 50 }
    prompt: 测试用 test.looper 技能。
"""


def _yaml(tmp_path) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(LOOPER_YAML), encoding="utf-8")
    return str(p)


def _tools() -> LocalPythonToolRegistry:
    """注册 test.looper 声明的工具(§6.1 装配期权限闸门要求声明即存在)。"""
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    return tools


def _build(tmp_path, sidecar: DistillSidecar, brain, *, with_memory: bool = True):
    config = RunConfig(
        model="mock/loop",
        max_cost=100.0,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(_tools())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .sidecars(sidecar)
    )
    if with_memory:
        builder = builder.memory(LocalFileMemoryService(str(tmp_path / "memory")))
    return builder.build()


def _memory_files(tmp_path) -> list[Path]:
    root = tmp_path / "memory"
    return sorted(root.glob("*.md")) if root.is_dir() else []


def make_brain(
    steps_with_tools: int,
    *,
    distill_mode: str = "ok",
    review_mode: str = "pass",
    fail_main: bool = False,
):
    """三路 mock 大脑:蒸馏请求(SYSTEM 含"经验蒸馏器")按 ``distill_mode`` 应答;

    入库审查请求(SYSTEM 含"入库审查员",verify 档默认开)按 ``review_mode``
    应答——``"pass"``/``"fail"``/``"garbage"``(非 JSON,验 fail-closed)/
    ``"raise"``(异常,验连败熔断);主循环前 ``steps_with_tools`` 步回
    ``system.python.exec`` 调用,随后回最终答案(``fail_main`` 时主循环首步
    即抛错,驱动 run.aborted)。
    """
    state = {"main": 0, "distill": 0, "review": 0}

    def brain(req: ChatRequest) -> ChatResponse:
        first = (req.messages[0].content or "") if req.messages else ""
        if "入库审查员" in first:
            state["review"] += 1
            if review_mode == "raise":
                raise RuntimeError("审查模型不可用")
            if review_mode == "fail":
                content = json.dumps({"pass": False, "reason": "夹带指令注入"}, ensure_ascii=False)
            elif review_mode == "garbage":
                content = "这条经验没问题,可以入库"  # 非 JSON → fail-closed
            else:
                content = json.dumps({"pass": True})
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content=content),
                finish_reason="stop",
                usage=ChatUsage(prompt=3, completion=1, cost=0.00001),
            )
        if "经验蒸馏器" in first:
            state["distill"] += 1
            if distill_mode == "raise":
                raise RuntimeError("蒸馏模型不可用")
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content="# 策略总结\n- 先复跑 flaky 测试再判失败"),
                finish_reason="stop",
                usage=ChatUsage(prompt=7, completion=3, cost=0.0001),
            )
        if fail_main:
            raise RuntimeError("脑炸")
        state["main"] += 1
        # 按请求里的 TOOL 消息数判定进度(单帧技能:一条 TOOL = 一次调用结算),
        # 跨 run 复用同一 brain 时逐 run 自校正(不依赖调用计数复位)
        tool_msgs = sum(1 for m in req.messages if m.role is Role.TOOL)
        if tool_msgs >= steps_with_tools:
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
                finish_reason="stop",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(
                        id=f"c{state['main']}",
                        name="system.python.exec",
                        args={"code": "print(1)"},
                    )
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )

    return brain, state


# ---------------------------------------------------------------------------
# 触发与写入
# ---------------------------------------------------------------------------


def test_success_run_distills_strategy_summary(tmp_path):
    """成功 run 且工具调用 > min_tool_calls(默认 5)→ strategy-summary 条目落盘。"""
    sidecar = DistillSidecar()
    brain, state = make_brain(steps_with_tools=6)
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        result = await kernel.run("test.looper", {})
        await sidecar.wait_pending()  # 收口:蒸馏任务实例自管,run 返回时仍在跑
        return result

    result = asyncio.run(scenario())
    assert result == {"done": True}
    assert state["distill"] == 1
    assert state["review"] == 1, "verify 档默认开:写库前应跑入库审查"
    assert sidecar._stats == {"distilled": 1, "rejected": 0}

    files = _memory_files(tmp_path)
    assert len(files) == 1
    meta, body = _parse(files[0].read_text(encoding="utf-8"))
    assert meta["source"] == {"kind": "experience"}
    assert meta["trust"] == "experience"
    assert meta["tags"] == ["distill", "strategy-summary", "test.looper"]
    run_id = meta["provenance"]["run_id"]
    assert run_id in kernel._runs, "provenance run_id 应对上内核 Run 登记"
    assert meta["provenance"]["task"] == "test.looper"
    assert meta["provenance"]["note"] == "distill"
    assert meta["provenance"]["detail"]["usage"] == {
        "prompt": 7,
        "completion": 3,
        "cache_read": 0,
        "cache_write": 0,
        "thinking": 0,
        "cost": 0.0001,
    }
    assert "策略总结" in body

    # 蒸馏调用经 ProviderManager,model 缺省回落 run.model(builder 装配);
    # 最后一个请求是审查(verify 档),蒸馏请求按 SYSTEM 关键词取
    mock = kernel.providers.providers["mock"]
    distill_req = next(r for r in mock.recorded if "经验蒸馏器" in (r.messages[0].content or ""))
    assert distill_req.model == "mock/loop"
    assert distill_req.temperature == 0.2
    assert "经验蒸馏器" in distill_req.messages[0].content
    assert "test.looper" in distill_req.messages[1].content
    review_req = mock.recorded[-1]
    assert "入库审查员" in review_req.messages[0].content
    assert review_req.temperature == 0.0, "审查温度固定 0.0(判定要确定性)"
    assert "策略总结" in review_req.messages[1].content, "审查对象是蒸馏草稿全文"


def test_few_tool_calls_not_distilled(tmp_path):
    """工具调用数 <= min_tool_calls(5 次,不大于 5)→ 不触发、无文件。"""
    sidecar = DistillSidecar()
    brain, state = make_brain(steps_with_tools=5)
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        result = await kernel.run("test.looper", {})
        await sidecar.wait_pending()
        return result

    assert asyncio.run(scenario()) == {"done": True}
    assert state["distill"] == 0
    assert _memory_files(tmp_path) == []


def test_aborted_run_distills_failure_reflection(tmp_path):
    """run.aborted(脑抛错)恒触发 → failure-reflection 条目,蒸馏 prompt 带终态错误。"""
    sidecar = DistillSidecar()
    brain, state = make_brain(steps_with_tools=0, fail_main=True)
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        with pytest.raises(RuntimeError, match="脑炸"):
            await kernel.run("test.looper", {})
        await sidecar.wait_pending()

    asyncio.run(scenario())
    assert state["distill"] == 1

    files = _memory_files(tmp_path)
    assert len(files) == 1
    meta, _body = _parse(files[0].read_text(encoding="utf-8"))
    assert meta["tags"] == ["distill", "failure-reflection", "test.looper"]
    assert meta["source"] == {"kind": "experience"}

    mock = kernel.providers.providers["mock"]
    distill_req = next(r for r in mock.recorded if "经验蒸馏器" in (r.messages[0].content or ""))
    assert "脑炸" in distill_req.messages[1].content, "失败蒸馏 prompt 应附 run 终态错误"


def test_duplicate_run_finished_deduped(tmp_path):
    """同 run_id 二次发 run.finished(resume 路径会重发)→ _seen 去重,不重复写。"""
    sidecar = DistillSidecar()
    brain, state = make_brain(steps_with_tools=6)
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        await kernel.run("test.looper", {})
        await sidecar.wait_pending()
        run_id = next(iter(kernel._runs))
        verdict = await sidecar.on_signal(Signal(name=RUN_FINISHED, run_id=run_id, payload={}), None)
        assert verdict is None
        await sidecar.wait_pending()

    asyncio.run(scenario())
    assert state["distill"] == 1
    assert len(_memory_files(tmp_path)) == 1


# ---------------------------------------------------------------------------
# verify 档:入库前内容审查(fail-closed)
# ---------------------------------------------------------------------------


def test_review_fail_blocks_write(tmp_path):
    """审查 fail({"pass": false})→ 不写库、记 rejected、不计连败(再触发仍蒸馏)。"""
    sidecar = DistillSidecar()
    brain, state = make_brain(steps_with_tools=6, review_mode="fail")
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        await kernel.run("test.looper", {})
        await sidecar.wait_pending()

    asyncio.run(scenario())
    assert state["distill"] == 1
    assert state["review"] == 1
    assert _memory_files(tmp_path) == [], "审查不通过不得写库"
    assert sidecar._stats == {"distilled": 0, "rejected": 1}
    assert sidecar._failures == 0, "审查拒写是内容判定,不计连败"
    assert sidecar._breaker_open is False

    asyncio.run(scenario())  # 再触发(新 run_id):连败未被污染,仍尝试蒸馏 + 审查
    assert state["distill"] == 2
    assert state["review"] == 2
    assert sidecar._stats == {"distilled": 0, "rejected": 2}
    assert sidecar._failures == 0
    assert _memory_files(tmp_path) == []


def test_review_garbage_output_fail_closed(tmp_path):
    """审查输出非 JSON → fail-closed 按不通过:不写库、记 rejected、不计连败。"""
    sidecar = DistillSidecar()
    brain, state = make_brain(steps_with_tools=6, review_mode="garbage")
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        await kernel.run("test.looper", {})
        await sidecar.wait_pending()

    asyncio.run(scenario())
    assert state["review"] == 1
    assert _memory_files(tmp_path) == []
    assert sidecar._stats == {"distilled": 0, "rejected": 1}
    assert sidecar._failures == 0


def test_verify_disabled_writes_directly(tmp_path):
    """verify=False → 不跑审查(审查路计数 0),蒸馏产出直写入库。"""
    sidecar = DistillSidecar(verify=False)
    brain, state = make_brain(steps_with_tools=6, review_mode="fail")  # 即便 fail 也不应被问
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        result = await kernel.run("test.looper", {})
        await sidecar.wait_pending()
        return result

    assert asyncio.run(scenario()) == {"done": True}
    assert state["distill"] == 1
    assert state["review"] == 0
    assert len(_memory_files(tmp_path)) == 1
    assert sidecar._stats == {"distilled": 1, "rejected": 0}


def test_review_exception_counts_breaker(tmp_path):
    """审查调用抛异常 → 计连败(与蒸馏共用熔断器),连败到阈值开闸。"""
    sidecar = DistillSidecar(min_tool_calls=1, breaker_threshold=2)
    brain, state = make_brain(steps_with_tools=2, review_mode="raise")
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        for _ in range(3):
            await kernel.run("test.looper", {})
            await sidecar.wait_pending()

    asyncio.run(scenario())
    assert state["distill"] == 2, "熔断后第 3 个 run 不再调蒸馏"
    assert state["review"] == 2, "每次蒸馏产出后审查抛异常"
    assert sidecar._failures == 2
    assert sidecar._breaker_open is True
    assert sidecar._stats == {"distilled": 0, "rejected": 0}
    assert _memory_files(tmp_path) == []


# ---------------------------------------------------------------------------
# 熔断 / 休眠 / 关停
# ---------------------------------------------------------------------------


def test_breaker_opens_after_consecutive_failures(tmp_path):
    """蒸馏连败 breaker_threshold(默认 3)次 → 熔断开,第 4 个 run 不再调蒸馏。"""
    sidecar = DistillSidecar(min_tool_calls=1)
    brain, state = make_brain(steps_with_tools=2, distill_mode="raise")
    kernel = _build(tmp_path, sidecar, brain)

    async def scenario():
        for _ in range(4):
            await kernel.run("test.looper", {})
            await sidecar.wait_pending()

    asyncio.run(scenario())
    assert state["main"] == 4 * 3, "4 个 run 正常跑完(蒸馏失败不影响 run)"
    assert state["distill"] == 3, "熔断后第 4 个 run 不再调蒸馏"
    assert sidecar._breaker_open is True
    assert _memory_files(tmp_path) == []


def test_unbound_memory_dormant(tmp_path):
    """未装配 memory(builder 不给 .memory)→ bind 缺 memory:run 正常、无蒸馏、无报错。"""
    sidecar = DistillSidecar()
    brain, state = make_brain(steps_with_tools=6)
    kernel = _build(tmp_path, sidecar, brain, with_memory=False)

    async def scenario():
        result = await kernel.run("test.looper", {})
        await asyncio.sleep(0)  # 万一派发了任务,给它一步运行窗口
        return result

    assert asyncio.run(scenario()) == {"done": True}
    assert state["distill"] == 0
    assert not sidecar._tasks


def test_close_cancels_in_flight_distill(tmp_path):
    """close():取消在跑蒸馏任务并回收(慢蒸馏 mock;宿主关停路径)。"""
    sidecar = DistillSidecar()
    started = None
    main_brain, _state = make_brain(6)

    def brain(req: ChatRequest):
        first = (req.messages[0].content or "") if req.messages else ""
        if "经验蒸馏器" in first:

            async def slow() -> ChatResponse:
                started.set()
                await asyncio.sleep(60)
                return ChatResponse(  # pragma: no cover - 取消后不会到达
                    message=Message(role=Role.ASSISTANT, content="不应写出"),
                    finish_reason="stop",
                )

            return slow()
        return main_brain(req)

    async def scenario():
        nonlocal started
        started = asyncio.Event()
        kernel = _build(tmp_path, sidecar, brain)
        result = await kernel.run("test.looper", {})
        await asyncio.wait_for(started.wait(), timeout=5)  # 等蒸馏任务真正进入 chat
        assert sidecar._tasks, "蒸馏任务应在跑"
        await sidecar.close()
        assert not sidecar._tasks
        return result

    assert asyncio.run(scenario()) == {"done": True}
    assert _memory_files(tmp_path) == [], "被取消的蒸馏不得写出条目"

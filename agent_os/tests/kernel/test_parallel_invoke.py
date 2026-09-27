"""WS3 锚点测试:parallel_invoke fork/join(docs/DESIGN.md §3.4 第三原语)。

固定约定:

- ``Kernel.parallel_invoke(parent, branches, *, mode, max_concurrency, settle_timeout)``
  按分支序返回 ``[{"ok", "value", "error", "frame_id"}]``;code 技能内经
  ``ctx.parallel(branches, **kw)`` 委托调用(§9.3);
- **起批前串行预检**:批形态错/白名单外 → SkillLoadError;深度超限 → MaxDepthExceeded
  (硬失败);分支级失败(参数不合 schema/运行期帧失败)折叠为该分支 ``ok=False`` 条目;
- **all_settled(默认)**:结构化 gather,普通分支异常折叠永不上抛;``depends_on``
  前置失败 → 依赖分支 ``{"ok": False, "error": {"kind": "cancelled"}}`` 且不启动;
- **first_success**:done-flag 只赢一次;胜方锁定后其余分支 cancel_subtree,
  ``asyncio.wait(timeout=settle_timeout)`` 等 ack;幂等结算一次(恰一个 ``ok=True``);
  全败 → 返回全部错误条目,不挂死;
- **硬失败边界(§3.2)**:RunAborted/BudgetExceeded/MaxDepthExceeded 不折叠,炸 run;
- **concurrency_safe 闸**:code 分支含未声明 concurrency_safe/concurrent_safe 的工具
  → 该分支串行降级(fail-safe 不拒绝,日志可观察);prompt 分支豁免;
- **checkpoint/resume**:批不配对,在跑批断电不恢复;code 父帧 resume 重跑重发整批。
"""

from __future__ import annotations

import asyncio
import json
import logging
import textwrap
from typing import ClassVar

import pytest

from agent_os.api.v1 import (
    Allow,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    FrameStatus,
    Message,
    Mode,
    Permission,
    Role,
    RunConfig,
    Signal,
    ToolCall,
    ToolPolicy,
)
from agent_os.blackboard import LocalBlackboard
from agent_os.kernel.errors import (
    BudgetExceeded,
    MaxDepthExceeded,
    RunAborted,
    SkillLoadError,
    ToolDispatchError,
)
from agent_os.providers.mock import MockProvider
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import assemble, auto_approve, sandbox_tools

FIB_PART = """
  - name: demo.fib
    version: 1.0.0
    kind: prompt
    description: 生成前 n 个菲波拉契数。
    inputs:
      type: object
      properties: { n: { type: integer, minimum: 1 } }
      required: [n]
    outputs:
      type: object
      properties:
        seq: { type: array, items: { type: integer } }
      required: [seq]
    permissions:
      tools: [system.python.exec]
      skills: [demo.fib]
    model: { prefer: ["mock/fib"] }
    limits: { max_steps: 8, timeout: 60 }
    prompt: |
      你是菲波拉契数列生成器。
"""

SLOW_PART = """
  - name: test.slow_echo
    version: 1.0.0
    kind: prompt
    description: 调一个慢工具再给最终答案(first_success 慢分支/run stop 时间窗)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [test.slow_tool], skills: [] }
    model: { prefer: ["mock/fib"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: 先调 test.slow_tool,再回答。
"""

PROBE_PART = """
  - name: test.probe_echo
    version: 1.0.0
    kind: prompt
    description: 调 test.probe_tool 再给最终答案(并发计数的观察窗)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [test.probe_tool], skills: [] }
    model: { prefer: ["mock/fib"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: 先调 test.probe_tool,再回答。
  - name: test.code_probe
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:code_probe
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { probed: { type: boolean } }
      required: [probed]
    permissions: { tools: [test.probe_tool], skills: [] }
"""

BAD_ECHO_PART = """
  - name: test.bad_echo
    version: 1.0.0
    kind: prompt
    description: 永远给不合 outputs schema 的答案(运行期帧内失败的分支)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/fib"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: 直接回答。
"""

COSTLY_PART = """
  - name: test.costly_echo
    version: 1.0.0
    kind: prompt
    description: 一步最终答案,但 usage 带成本(预算硬失败的分支)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/fib"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: 直接回答。
"""

PARALLEL_YAML = """
  - name: test.parallel_pair
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:parallel_pair
    inputs:
      type: object
      properties:
        a: { type: integer }
        b: { type: integer }
        cut_after_batch: { type: boolean }
      required: [a, b]
    outputs:
      type: object
      properties: { results: { type: array } }
      required: [results]
    permissions: { tools: [], skills: [demo.fib] }
  - name: test.parallel_fail_branch
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:parallel_fail_branch
    inputs:
      type: object
      properties:
        n0: { type: integer }
        bad: { type: integer }
        runtime_bad: { type: boolean }
        mode: { type: string }
      required: [n0]
    outputs:
      type: object
      properties: { results: { type: array } }
      required: [results]
    permissions: { tools: [], skills: [demo.fib, test.bad_echo] }
  - name: test.parallel_chain
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:parallel_chain
    inputs:
      type: object
      properties: { n0: { type: integer }, n1: { type: integer } }
      required: [n0, n1]
    outputs:
      type: object
      properties: { results: { type: array } }
      required: [results]
    permissions: { tools: [], skills: [demo.fib] }
  - name: test.parallel_race
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:parallel_race
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { results: { type: array } }
      required: [results]
    permissions: { tools: [], skills: [demo.fib, test.slow_echo] }
  - name: test.parallel_probe
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:parallel_probe
    inputs:
      type: object
      properties:
        branch_skill: { type: string }
        n: { type: integer }
        max_concurrency: { type: integer }
      required: [branch_skill, n]
    outputs:
      type: object
      properties: { results: { type: array } }
      required: [results]
    permissions: { tools: [], skills: [test.probe_echo, test.code_probe, test.slow_echo, test.costly_echo] }
  - name: test.parallel_naughty
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:parallel_naughty
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { never: { type: boolean } }
    permissions: { tools: [], skills: [] }
"""

ALL_PARTS = FIB_PART + SLOW_PART + PROBE_PART + BAD_ECHO_PART + COSTLY_PART + PARALLEL_YAML


def _yaml(tmp_path, body: str = ALL_PARTS) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text("skills:\n" + textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _build(
    tmp_path,
    *,
    max_depth: int = 8,
    max_cost: float = 2.0,
    sidecars=(),
    brain=fib_brain,
    tools=None,
):
    config = RunConfig(
        model="mock/fib",
        max_depth=max_depth,
        max_cost=max_cost,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config,
        brain,
        _yaml(tmp_path),
        tools=tools if tools is not None else _slow_tools(),
        blackboard=LocalBlackboard(),
        # parallel 升权闸与 spawn 同形(docs/ESCALATION.md §3):自动批准通道
        # 等价于生产宿主里人每次放行(demo.fib 是 EXEC 档,逐分支过闸)
        supervisor=auto_approve,
        sidecars=sidecars,
    )


# ---------------------------------------------------------------------------
# 工具与脑
# ---------------------------------------------------------------------------


def slow_brain(req: ChatRequest) -> ChatResponse:
    """首步调 test.slow_tool(秒级睡眠),拿到工具结果后给最终答案。"""
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="slow-1", name="test.slow_tool", args={"seconds": 1})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _slow_tools():
    """默认工具表:test.slow_tool + test.probe_tool(§6.1 闸门要求白名单工具已注册)。"""
    return _probe_tools(_ConcurrencyProbe())


class _ConcurrencyProbe:
    """probe_tool 的并发计数器:max_active 记录观察到的最大并发数。"""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0


def _probe_tools(probe: _ConcurrencyProbe):
    """注册了 test.slow_tool 与 test.probe_tool 的工具注册表。

    test.probe_tool **故意不声明** concurrency_safe(默认值 False 正是
    concurrency_safe 闸要消费的 fail-safe 缺省)。
    """
    reg = sandbox_tools()

    @reg.tool(name="test.slow_tool", permission=Permission.READ, timeout=30)
    async def slow_tool(seconds: int) -> str:
        """睡眠 seconds 秒后返回(first_success 取消/run stop 的时间窗)。"""
        await asyncio.sleep(seconds)
        return "slept"

    @reg.tool(name="test.probe_tool", permission=Permission.READ, timeout=30)
    async def probe_tool() -> str:
        """睡一小段时间并记录并发数(max_concurrency/串行降级的观察窗)。"""
        probe.active += 1
        probe.max_active = max(probe.max_active, probe.active)
        await asyncio.sleep(0.15)
        probe.active -= 1
        return "probed"

    return reg


def mixed_brain(req: ChatRequest) -> ChatResponse:
    """fib 帧(输入含 n)走 fib_brain,其余(test.slow_echo)走 slow_brain。"""
    for m in req.messages:
        if m.role is Role.USER:
            try:
                if "n" in json.loads(m.content):
                    return fib_brain(req)
            except (json.JSONDecodeError, AttributeError):
                continue
    return slow_brain(req)


def bad_brain(req: ChatRequest) -> ChatResponse:
    """永远返回不合 outputs schema 的答案(运行期帧内失败)。"""
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"wrong": 1})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def mixed_bad_brain(req: ChatRequest) -> ChatResponse:
    """fib 帧走 fib_brain,其余(test.bad_echo)走 bad_brain。"""
    for m in req.messages:
        if m.role is Role.USER:
            try:
                if "n" in json.loads(m.content):
                    return fib_brain(req)
            except (json.JSONDecodeError, AttributeError):
                continue
    return bad_brain(req)


def probe_brain(req: ChatRequest) -> ChatResponse:
    """首步调 test.probe_tool,拿到工具结果后给最终答案。"""
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="probe-1", name="test.probe_tool", args={})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def costly_brain(req: ChatRequest) -> ChatResponse:
    """一步最终答案,usage 带 0.5 成本(低 max_cost 下触发 BudgetExceeded)。"""
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1, cost=0.5),
    )


class _StopProbe:
    """post:llm.response 上对首个 depth==2 的帧 stop 整个 run(run stop 撞批)。"""

    name: ClassVar[str] = "parallel-stop-probe"
    subscriptions: ClassVar[tuple[str, ...]] = ("post:llm.response",)
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self) -> None:
        self.done = False

    async def on_signal(self, sig: Signal, ctl) -> Allow:
        if not self.done and sig.payload.get("depth") == 2:
            self.done = True
            await ctl.stop(sig.run_id, "run stop 撞批测试")
        return Allow()


# ---------------------------------------------------------------------------
# all_settled:全成功与批内故障隔离
# ---------------------------------------------------------------------------


def test_all_settled_returns_results_in_branch_order(tmp_path):
    """all_settled 全成功:两分支并发,结果按分支序、值正确(§3.4 fork/join)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(kernel.run("test.parallel_pair", {"a": 4, "b": 3}))
    results = result["results"]
    assert [r["ok"] for r in results] == [True, True]
    assert results[0]["value"] == {"seq": [0, 1, 1, 2]}  # 分支序 ≠ 完成序
    assert results[1]["value"] == {"seq": [0, 1, 1]}
    assert results[0]["error"] is None and results[0]["frame_id"]
    # 每分支独立帧(§3.4:帧树是唯一数据结构),挂在父帧下
    for r in results:
        frame = kernel.stack.get(r["frame_id"])
        assert frame is not None and frame.depth == 2 and frame.status is FrameStatus.DONE
    assert kernel._spawned == {}, "run 收尾后后台帧登记应清空"


def test_branch_fault_isolation(tmp_path):
    """批内故障隔离:预检失败(坏 input)与帧内失败(输出校验连败)各折叠为本分支
    ok=False,独立分支与父帧照常成功(§3.4)。"""
    kernel = _build(tmp_path, brain=mixed_bad_brain)
    result = asyncio.run(
        kernel.run(
            "test.parallel_fail_branch", {"n0": 3, "bad": 1, "runtime_bad": True}
        )
    )
    results = result["results"]
    assert len(results) == 3
    # 独立分支照常成功
    assert results[0]["ok"] is True and results[0]["value"] == {"seq": [0, 1, 1]}
    # 坏 input(n=0 不合 schema):预检折叠,分支未启动(无 frame_id)
    assert results[1]["ok"] is False and results[1]["frame_id"] is None
    assert results[1]["error"]["kind"] == "invalid_args"
    # 帧内失败(OutputValidationError):折叠为该分支错误条目,不上抛
    assert results[2]["ok"] is False and results[2]["error"]["kind"] == "internal"
    assert "OutputValidationError" in results[2]["error"]["message"]
    frame = kernel.stack.get(results[2]["frame_id"])
    assert frame is not None and frame.status is FrameStatus.FAILED


def test_depends_on_cascade(tmp_path):
    """depends_on 级联:前置失败 → 依赖分支不启动 + 结构化 cancelled 错误;
    前置成功 → 依赖分支照常运行(§3.4 批内故障隔离)。"""
    kernel = _build(tmp_path)
    # 前置失败(n0=0 不合 schema):依赖分支标 cancelled 且从未压栈
    result = asyncio.run(kernel.run("test.parallel_chain", {"n0": 0, "n1": 3}))
    failed, dependent = result["results"]
    assert failed["ok"] is False
    assert dependent["ok"] is False and dependent["error"]["kind"] == "cancelled"
    assert "前置" in dependent["error"]["message"]
    assert kernel.stack.get(dependent["frame_id"]) is None, "依赖分支不应启动(未压栈)"
    # 前置成功:依赖分支照常启动,结果按分支序
    result = asyncio.run(kernel.run("test.parallel_chain", {"n0": 2, "n1": 3}))
    first, second = result["results"]
    assert first["ok"] is True and first["value"] == {"seq": [0, 1]}
    assert second["ok"] is True and second["value"] == {"seq": [0, 1, 1]}


def test_whitelist_and_depth_checked_per_branch(tmp_path):
    """白名单/深度逐分支生效(与 spawn 同闸):白名单外分支 → SkillLoadError;
    深度超限 → MaxDepthExceeded(硬失败,不折叠)。"""
    kernel = _build(tmp_path)
    with pytest.raises(SkillLoadError, match="白名单"):
        asyncio.run(kernel.run("test.parallel_naughty", {}))
    shallow = _build(tmp_path, max_depth=1)
    with pytest.raises(MaxDepthExceeded):
        asyncio.run(shallow.run("test.parallel_pair", {"a": 2, "b": 2}))


def test_batch_shape_errors_raise_skill_load_error(tmp_path):
    """批形态预检:非法 mode → SkillLoadError(与 spawn 白名单拒绝同形,整批不启动)。"""
    kernel = _build(tmp_path)
    with pytest.raises(SkillLoadError, match="mode 非法"):
        asyncio.run(kernel.run("test.parallel_fail_branch", {"n0": 2, "mode": "bogus"}))


# ---------------------------------------------------------------------------
# first_success:锁定 / 级联取消 / 幂等结算
# ---------------------------------------------------------------------------


def test_first_success_locks_and_cancels_loser(tmp_path):
    """first_success:快分支锁定,慢分支被级联取消(帧 FAILED/CancelledError),
    结果幂等结算一次(返回列表恰一个 ok=True)。"""
    kernel = _build(tmp_path, brain=mixed_brain)
    result = asyncio.run(kernel.run("test.parallel_race", {}))
    loser, winner = result["results"]
    assert sum(1 for r in result["results"] if r["ok"]) == 1, "只赢一次(幂等结算)"
    assert winner["ok"] is True and winner["value"] == {"seq": [0]}
    assert loser["ok"] is False and loser["error"]["kind"] == "cancelled"
    # 慢分支被级联取消:帧 FAILED 于 CancelledError,不再作为孤儿跑到 run 结束
    frame = kernel.stack.get(loser["frame_id"])
    assert frame is not None and frame.status is FrameStatus.FAILED
    assert isinstance(frame.error, asyncio.CancelledError)
    assert kernel._spawned == {}


def test_first_success_all_fail_returns_errors(tmp_path):
    """first_success 全败:返回全部分支错误条目,不挂死(§3.4)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run("test.parallel_fail_branch", {"n0": 0, "bad": 1, "mode": "first_success"})
    )
    results = result["results"]
    assert len(results) == 2 and all(not r["ok"] for r in results)
    assert all(r["error"] for r in results)


# ---------------------------------------------------------------------------
# max_concurrency 与 concurrency_safe 闸
# ---------------------------------------------------------------------------


def test_max_concurrency_caps_parallelism(tmp_path):
    """max_concurrency 上帽:3 个分支帽 2,probe 观察到的最大并发数恰为 2。"""
    probe = _ConcurrencyProbe()
    kernel = _build(tmp_path, brain=probe_brain, tools=_probe_tools(probe))
    result = asyncio.run(
        kernel.run(
            "test.parallel_probe",
            {"branch_skill": "test.probe_echo", "n": 3, "max_concurrency": 2},
        )
    )
    assert [r["ok"] for r in result["results"]] == [True, True, True]
    assert probe.max_active == 2, "并发数不得超过 max_concurrency(且确实并发了)"


def test_concurrency_safe_gate_serial_degradation(tmp_path, caplog):
    """concurrency_safe 闸:code 分支含未声明并发安全的工具 → 串行降级
    (fail-safe 不拒绝;两个分支的 probe 执行窗互不重叠,日志可观察)。"""
    probe = _ConcurrencyProbe()
    kernel = _build(tmp_path, brain=probe_brain, tools=_probe_tools(probe))
    with caplog.at_level(logging.WARNING, logger="agent_os.kernel"):
        result = asyncio.run(
            kernel.run("test.parallel_probe", {"branch_skill": "test.code_probe", "n": 2})
        )
    # fail-safe 不拒绝:两分支都成功,但执行窗串行(未降级则 max_active 会为 2)
    assert [r["ok"] for r in result["results"]] == [True, True]
    assert probe.max_active == 1, "未声明 concurrency_safe 的 code 分支应串行降级"
    assert "串行降级" in caplog.text and "concurrency_safe" in caplog.text


# ---------------------------------------------------------------------------
# 硬失败边界(§3.2):不折叠,炸 run
# ---------------------------------------------------------------------------


def test_run_stop_aborts_inflight_batch(tmp_path):
    """run stop 撞批:stop 置位后分支在 safe point 抛 RunAborted,不折叠为分支
    错误条目——取消其余分支、等 ack、沿栈上抛炸 run(§3.2)。"""
    kernel = _build(tmp_path, brain=slow_brain, sidecars=(_StopProbe(),))
    with pytest.raises(RunAborted):
        asyncio.run(
            kernel.run("test.parallel_probe", {"branch_skill": "test.slow_echo", "n": 2})
        )
    assert kernel._spawned == {}, "硬失败收尾后后台帧登记应清空"


def test_budget_exceeded_not_folded(tmp_path):
    """硬失败不折叠:分支记账触发 BudgetExceeded(低 max_cost)→ 炸 run 而非
    分支 error 条目(§3.2 硬失败纪律)。"""
    kernel = _build(tmp_path, brain=costly_brain, max_cost=0.4)
    with pytest.raises(BudgetExceeded):
        asyncio.run(
            kernel.run("test.parallel_probe", {"branch_skill": "test.costly_echo", "n": 2})
        )
    run = next(iter(kernel._runs.values()))
    assert run.state.status.value == "aborted"


# ---------------------------------------------------------------------------
# checkpoint/resume:批不配对,code 父帧 resume 重跑重发
# ---------------------------------------------------------------------------


def test_checkpoint_resume_refires_batch(tmp_path):
    """批结算后"断电"→ code 父帧 FAILED;新内核从 checkpoint 恢复:旧 DONE 分支
    不重跑,父帧整体重跑、批整体重发(剩余 LLM 调用数精确可数)。"""
    kernel1 = _build(tmp_path)
    with pytest.raises(ToolDispatchError, match="模拟断电"):
        asyncio.run(kernel1.run("test.parallel_pair", {"a": 2, "b": 2, "cut_after_batch": True}))
    run_id = next(iter(kernel1._runs))
    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))
    assert ckpt.exists()

    mock2 = MockProvider(fib_brain)
    kernel2 = _build(tmp_path, brain=mock2)
    result = asyncio.run(kernel2.resume(str(ckpt)))
    results = result["results"]
    assert [r["ok"] for r in results] == [True, True]
    assert results[0]["value"] == {"seq": [0, 1]} and results[1]["value"] == {"seq": [0, 1]}
    # 恢复重跑重发:整批重发(2 个 fib(2) 分支各 1 次调用),旧 DONE 分支不重跑
    assert len(mock2.recorded) == 2

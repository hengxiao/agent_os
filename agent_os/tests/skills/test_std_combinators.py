"""W5-WS2+3 锚点测试:std/combinators(STDLIB §4.5;STDLIB-CATALOG 第 5 波)。

固定约定:

- ``common.task.race_first``:首个成功分支胜出——分支并发/取消/ack/幂等结算走内核
  ``parallel_invoke``(first_success 模式,经私有批帧 ``common.task.race_batch``);
  ``budget`` 软闸:watchdog 轮询批帧子树 usage,超限取消批帧并返回部分结果
  (``partial=True``);输出 ``{winner, cancelled, partial, usage}``;
- ``common.task.subagent_cancel`` / ``common.task.subagent_status``:``ctx.cancel`` /
  ``ctx.frame_status`` 的技能面(WRITE / READ);
- 分支白名单静态:std 出厂空白名单,测试装配做"调用方复制专化"(STDLIB §4.5)——
  装配后把测试分支技能填进 ``race_batch`` 的 ``permissions.skills``。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import textwrap
from pathlib import Path

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    FrameStatus,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolCall,
    ToolPolicy,
    Usage,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.helpers.kernels import record_all

STD_DIR = Path(__file__).resolve().parents[2] / "std"

#: Usage 九字段(§14.1 冻结清单;subagent_status 的 usage 子树汇总形态锚)
USAGE_FIELDS = {
    "steps",
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "cost",
    "ttft_ms",
    "total_ms",
}

OVERLAY_YAML = """
skills:
  - name: test.fast_echo
    version: 1.0.0
    kind: prompt
    description: 快分支,一步最终答案(race_first 快赢测试用)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties:
        done: { type: boolean }
        tag: { type: string }
      required: [done, tag]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: 你是快分支,直接回答。
  - name: test.slow_echo
    version: 1.0.0
    kind: prompt
    description: 慢分支,先调 test.slow_tool 秒级睡眠再回答(取消/budget 时间窗)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties:
        done: { type: boolean }
        tag: { type: string }
      required: [done, tag]
    permissions: { tools: [test.slow_tool], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: 你是慢分支,先调 test.slow_tool 再回答。
  - name: test.bad_echo
    version: 1.0.0
    kind: prompt
    description: 坏分支,永远给不合 outputs schema 的答案(race_first 全败测试用)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties:
        done: { type: boolean }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 2, timeout: 60 }
    prompt: 你是坏分支,直接回答。
  - name: test.std_probe
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:std_cancel_probe
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions:
      tools: []
      skills:
      - test.fast_echo
      - test.slow_echo
      - common.task.subagent_cancel
      - common.task.subagent_status
"""


def comb_brain(req: ChatRequest) -> ChatResponse:
    """分发脑:慢分支首步调 test.slow_tool(2s 时间窗),坏分支永远答错,其余一步答。"""
    system = req.messages[0].content if req.messages else ""
    has_tool_result = any(m.role is Role.TOOL for m in req.messages)

    def respond(payload):
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    if "慢分支" in system:
        if not has_tool_result:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[ToolCall(id="slow-1", name="test.slow_tool", args={"seconds": 2})],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return respond({"done": True, "tag": "slow"})
    if "坏分支" in system:
        return respond({"wrong": 1})
    return respond({"done": True, "tag": "fast"})


def _tools() -> LocalPythonToolRegistry:
    """工具表:test.slow_tool(async 睡眠;§6.1 闸门要求白名单工具已注册)。"""
    reg = LocalPythonToolRegistry()

    @reg.tool(name="test.slow_tool", permission=Permission.READ, timeout=30)
    async def slow_tool(seconds: int) -> str:
        """睡眠 seconds 秒后返回(first_success 取消/budget 软超限的时间窗)。"""
        await asyncio.sleep(seconds)
        return "slept"

    return reg


def _build(tmp_path, brain=comb_brain):
    """装配:std 目录(合并加载全域)+ 测试 overlay;race_batch 白名单复制专化。"""
    sys.path.insert(0, str(STD_DIR))  # handler dotted path 解析(根 conftest 已钉,双保险)
    overlay = tmp_path / "skills.yaml"
    overlay.write_text(textwrap.dedent(OVERLAY_YAML), encoding="utf-8")
    # 列表形态不展开目录:std 域文件逐个列出(文件名排序,与目录加载同序)
    sources = [str(p) for p in sorted(STD_DIR.glob("*.yaml"))] + [str(overlay)]
    registry = LocalFileSkillRegistry(sources)
    # 调用方复制专化(STDLIB §4.5):std 出厂空白名单,装配侧填入分支技能
    registry.get_by_name("common.task.race_batch").manifest.permissions.skills.extend(
        ["test.fast_echo", "test.slow_echo", "test.bad_echo"]
    )
    config = RunConfig(
        model="mock/x",
        max_depth=8,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    # 全链路推导档 none(分支工具皆 READ)——无升权闸,无需 supervisor 通道
    return (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(_tools())
        .skills(registry)
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


# ---------------------------------------------------------------------------
# common.task.race_first
# ---------------------------------------------------------------------------


def test_race_first_fast_wins_slow_cancelled(tmp_path):
    """快赢慢消:快分支锁定为胜方(值正确),慢分支被级联取消(帧 FAILED 于
    CancelledError),幂等结算一次(cancelled 恰一条,kind=cancelled)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run("common.task.race_first", {
            "branches": [
                {"skill": "test.slow_echo", "input": {}},
                {"skill": "test.fast_echo", "input": {}},
            ],
        })
    )
    winner = result["winner"]
    assert winner is not None and winner["skill"] == "test.fast_echo"
    assert winner["value"] == {"done": True, "tag": "fast"} and winner["frame_id"]
    assert result["partial"] is False
    cancelled = result["cancelled"]
    assert len(cancelled) == 1 and cancelled[0]["skill"] == "test.slow_echo"
    assert cancelled[0]["error"]["kind"] == "cancelled"
    loser = kernel.stack.get(cancelled[0]["frame_id"])
    assert loser is not None and loser.status is FrameStatus.FAILED
    assert isinstance(loser.error, asyncio.CancelledError)
    assert set(result["usage"]) == USAGE_FIELDS and result["usage"]["steps"] >= 1
    assert kernel._spawned == {}, "run 收尾后后台帧登记应清空"


def test_race_first_all_fail(tmp_path):
    """全败:不挂死;winner=None、partial=False(完整结算),全部错误条目落 cancelled。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run("common.task.race_first", {
            "branches": [
                {"skill": "test.bad_echo", "input": {}},
                {"skill": "test.bad_echo", "input": {}},
            ],
        })
    )
    assert result["winner"] is None and result["partial"] is False
    cancelled = result["cancelled"]
    assert len(cancelled) == 2 and all(c["skill"] == "test.bad_echo" for c in cancelled)
    assert all(c["error"] for c in cancelled), "全败条目须带结构化错误"


def test_race_first_budget_soft_trip(tmp_path):
    """budget 软超限:慢分支 + max_steps=0 → watchdog 轮询到子树 steps 超限,
    ctx.cancel 批帧(两慢分支级联取消),返回部分结果(winner=None, partial=True)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run("common.task.race_first", {
            "branches": [
                {"skill": "test.slow_echo", "input": {}},
                {"skill": "test.slow_echo", "input": {}},
            ],
            "budget": {"max_steps": 0},
        })
    )
    assert result["winner"] is None and result["partial"] is True
    cancelled = result["cancelled"]
    assert len(cancelled) == 2, "批帧子树取消 ack 应覆盖两个在跑分支"
    assert all(c["skill"] == "test.slow_echo" for c in cancelled)
    for c in cancelled:
        frame = kernel.stack.get(c["frame_id"])
        assert frame is not None and frame.status is FrameStatus.FAILED
        assert isinstance(frame.error, asyncio.CancelledError)
    # 软语义佐证:超限前已有分支步数入账(不可回滚),取消发生在 2s 慢工具窗口内
    assert result["usage"]["steps"] >= 1


def test_race_first_goes_through_ctx_parallel(tmp_path):
    """dogfood 锚:race_first 内部走 ctx.parallel——信号序列含 spawn/parallel 路径
    (race_first →spawn→ race_batch →parallel_invoke→ 分支,均 background=True)。"""
    kernel = _build(tmp_path)
    seen = record_all(kernel)
    asyncio.run(
        kernel.run("common.task.race_first", {
            "branches": [
                {"skill": "test.slow_echo", "input": {}},
                {"skill": "test.fast_echo", "input": {}},
            ],
        })
    )
    invokes = [
        s for s in seen
        if s.name == "pre:skill.invoke" and s.payload.get("background") is True
    ]
    skills_in_order = [s.payload["skill"] for s in invokes]
    assert "common.task.race_batch" in skills_in_order, "race_first 应经 spawn 起批帧"
    assert "test.fast_echo" in skills_in_order and "test.slow_echo" in skills_in_order
    assert skills_in_order.index("common.task.race_batch") < skills_in_order.index(
        "test.fast_echo"
    ), "批帧 spawn 应先于分支 spawn(并行路径)"


# ---------------------------------------------------------------------------
# common.task.subagent_cancel / common.task.subagent_status
# ---------------------------------------------------------------------------


def test_subagent_cancel_spawned_frame(tmp_path):
    """经 std 技能面取消 spawn 帧:ack 含目标自身(单帧子树恰为单元表),wait 收
    SubtreeCancelled(驱动捕获优雅收尾),子帧 FAILED 终态,run 存活。"""
    kernel = _build(tmp_path)
    result = asyncio.run(kernel.run("test.std_probe", {}))
    fid = result["slow_frame"]
    assert result["cancelled"] is True
    assert result["ack"] == [fid]
    child = kernel.stack.get(fid)
    assert child is not None and child.status is FrameStatus.FAILED
    assert isinstance(child.error, asyncio.CancelledError)
    root = next(f for f in kernel.stack.tree() if f.parent_id is None)
    assert root.status is FrameStatus.DONE, "取消子树不杀 run"


def test_subagent_status_forms(tmp_path):
    """subagent_status 三形态 + done 形态:running(含 usage 九字段)/ failed 终态 /
    未知帧(status=None 同形字典,零值 usage)/ 已完成帧(done,usage 子树汇总)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(kernel.run("test.std_probe", {}))

    running = result["running"]
    assert running, "轮询应在取消前观察到 running 形态(2s 慢工具窗口)"
    assert running["status"] == "running" and running["skill"] == "test.slow_echo"
    assert set(running["usage"]) == USAGE_FIELDS

    terminal = result["terminal"]
    assert terminal["status"] == "failed" and terminal["skill"] == "test.slow_echo"
    assert terminal["usage"] == dataclasses.asdict(kernel.subtree_usage(result["slow_frame"]))

    unknown = result["unknown"]
    assert unknown["status"] is None and unknown["skill"] is None
    assert unknown["usage"] == dataclasses.asdict(Usage())

    done = result["done"]
    assert done["status"] == "done" and done["skill"] == "test.fast_echo"
    assert done["usage"]["steps"] >= 1, "子树汇总应含帧自身步数"

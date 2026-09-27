"""M5b 锚点测试:Blackboard(共享状态)+ spawn 后台帧(docs/DESIGN.md §12、§3.4)。

固定约定:

- ``LocalBlackboard``:``put(ns, key, value, cas_version=None) -> int``(新版本号;
  cas_version 与当前版本不符 → 抛 ``BlackboardConflict``);``get(ns, key) -> (value, version)``;
  ``publish(Envelope)`` 投递给 ``subscribe(frame_id, pattern)`` 的匹配订阅者
  (target 精确匹配或 ``"*"`` 广播);put/publish 发 ``blackboard.write``/``blackboard.publish`` 信号;
- ``ctx.spawn(skill, input) -> frame_id``(code 技能内,TRUSTED):创建子帧并以后台任务运行,
  **父帧不挂起**;白名单(manifest.permissions.skills)与深度检查与 invoke 一致;
- ``ctx.wait(frame_id) -> Any``:join 退化为读终态(§3.4);
- runner 的 StatusBoard 模式(§12.2):帧每步与弹栈时把状态写入 ``status`` 命名空间
  (``{"status": "running"|"done"|"failed", ...}``),供父帧/外部读取;
- ``ctx.board``:按 manifest.permissions.blackboard 白名单做命名空间仲裁(内核中介)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from typing import Any, ClassVar

import pytest

from agent_os.api.v1 import (
    BLACKBOARD_PUBLISH,
    BLACKBOARD_WRITE,
    POST_FRAME_PUSH,
    Allow,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Envelope,
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
from agent_os.blackboard import BlackboardConflict, LocalBlackboard
from agent_os.kernel.errors import MaxDepthExceeded, SkillLoadError
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import assemble, auto_approve, sandbox_tools

SPAWN_YAML = """
  - name: test.spawn_pair
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:spawn_pair
    inputs:
      type: object
      properties: { a: { type: integer }, b: { type: integer } }
      required: [a, b]
    outputs:
      type: object
      properties: { combined: { type: array, items: { type: integer } } }
      required: [combined]
    permissions: { tools: [], skills: [demo.fib], blackboard: [status] }
  - name: test.spawn_naughty
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:spawn_naughty
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { never: { type: boolean } }
    permissions: { tools: [], skills: [] }
  - name: test.spawn_or_report
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:spawn_or_report
    inputs:
      type: object
      properties: { skill: { type: string }, args: { type: object } }
      required: [skill]
    outputs: { type: object }
    permissions: { tools: [], skills: [demo.fib, test.slow_echo] }
"""

SLOW_PART = """
  - name: test.slow_echo
    version: 1.0.0
    kind: prompt
    description: 调一个慢工具再给最终答案(级联取消的时间窗)。
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


def _yaml(tmp_path, body: str) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text("skills:\n" + textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _build(tmp_path, *, max_depth: int = 8, sidecars=(), brain=fib_brain, tools=None):
    config = RunConfig(
        model="mock/fib",
        max_depth=max_depth,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config,
        brain,
        _yaml(tmp_path, FIB_PART + SPAWN_YAML + SLOW_PART),
        # 默认工具表带 test.slow_tool(test.slow_echo 的白名单须注册,§6.1 闸门)
        tools=tools if tools is not None else _slow_tools(),
        blackboard=LocalBlackboard(),
        # spawn 升权闸(docs/ESCALATION.md §3;E2):自动批准通道等价于生产宿主里人每次放行
        supervisor=auto_approve,
        sidecars=sidecars,
    )


class _Bus:
    def __init__(self) -> None:
        self.seen: list[Signal] = []

    async def emit(self, sig: Signal):
        self.seen.append(sig)
        return []

    def subscribe(self, pattern, handler) -> None: ...


# ---------------------------------------------------------------------------
# Blackboard 单元
# ---------------------------------------------------------------------------


def test_blackboard_put_get_and_cas():
    board = LocalBlackboard()

    async def main():
        v1 = await board.put("status", "f1", {"step": 1})
        assert v1 == 1
        value, ver = await board.get("status", "f1")
        assert value == {"step": 1} and ver == 1
        v2 = await board.put("status", "f1", {"step": 2}, cas_version=1)
        assert v2 == 2
        with pytest.raises(BlackboardConflict):
            await board.put("status", "f1", {"step": 3}, cas_version=1)

    asyncio.run(main())


def test_blackboard_publish_subscribe():
    bus = _Bus()
    board = LocalBlackboard(bus=bus)

    async def main():
        got_f2, got_f3 = [], []

        async def drain(frame_id, sink):
            async for env in board.subscribe(frame_id, "*"):
                sink.append(env)
                if len(sink) >= 2:
                    break

        t2 = asyncio.create_task(drain("f2", got_f2))
        t3 = asyncio.create_task(drain("f3", got_f3))
        await asyncio.sleep(0)
        await board.publish(Envelope(sender="f1", target="f2", type="status_update", payload={"s": 1}))
        await board.publish(Envelope(sender="f1", target="f3", type="status_update", payload={"s": 2}))
        await board.publish(Envelope(sender="f1", target="*", type="status_update", payload={"s": 3}))
        await asyncio.wait_for(asyncio.gather(t2, t3), timeout=2)
        assert [e.payload["s"] for e in got_f2] == [1, 3]
        assert [e.payload["s"] for e in got_f3] == [2, 3]
        assert any(s.name == BLACKBOARD_PUBLISH for s in bus.seen)

    asyncio.run(main())


def test_blackboard_write_signal():
    bus = _Bus()
    board = LocalBlackboard(bus=bus)

    async def main():
        await board.put("status", "f1", {"step": 1})

    asyncio.run(main())
    writes = [s for s in bus.seen if s.name == BLACKBOARD_WRITE]
    assert writes and writes[0].payload.get("ns") == "status"


# ---------------------------------------------------------------------------
# spawn 后台帧
# ---------------------------------------------------------------------------


def test_spawn_and_wait_returns_results(tmp_path):
    """spawn 两个 fib 后台帧,wait 读终态,结果正确(§3.4)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(kernel.run("test.spawn_pair", {"a": 4, "b": 3}))
    assert result == {"combined": [0, 1, 1, 2, 0, 1, 1]}


def test_spawn_children_frame_tree_and_status_channel(tmp_path):
    """子帧挂在父帧下(depth 2);runner 的 StatusBoard 在弹栈后写 done(§12.2)。"""
    kernel = _build(tmp_path)
    seen: list[Signal] = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    asyncio.run(kernel.run("test.spawn_pair", {"a": 4, "b": 3}))

    pushes = [s for s in seen if s.name == POST_FRAME_PUSH]
    child_ids = [s.payload["frame_id"] for s in pushes if s.payload["depth"] == 2]
    assert len(child_ids) == 2

    async def check_board():
        for fid in child_ids:
            value, _ = await kernel.blackboard.get("status", fid)
            assert value is not None and value["status"] == "done", (fid, value)

    asyncio.run(check_board())


def test_spawn_permission_denied(tmp_path):
    """spawn 白名单外子技能 → SkillLoadError(§9.3 回调回内核分发路径)。"""
    kernel = _build(tmp_path)
    with pytest.raises(SkillLoadError, match="白名单"):
        asyncio.run(kernel.run("test.spawn_naughty", {}))


def test_spawn_depth_limit(tmp_path):
    """spawn 的深度检查与 invoke 一致(§2.4 max_depth 兜底)。"""
    kernel = _build(tmp_path, max_depth=1)
    with pytest.raises(MaxDepthExceeded):
        asyncio.run(kernel.run("test.spawn_pair", {"a": 2, "b": 2}))


# ---------------------------------------------------------------------------
# 子树级联取消(§5.2 cancel_frame / WS1)
# ---------------------------------------------------------------------------


def slow_brain(req: ChatRequest) -> ChatResponse:
    """首步调 test.slow_tool(秒级睡眠),拿到工具结果后给最终答案。"""
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="slow-1", name="test.slow_tool", args={"seconds": 3})],
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
    """注册了 test.slow_tool(async 睡眠)的工具注册表。"""
    reg = sandbox_tools()

    @reg.tool(name="test.slow_tool", permission=Permission.READ, timeout=30)
    async def slow_tool(seconds: int) -> str:
        """睡眠 seconds 秒后返回(级联取消/中断配对测试的时间窗)。"""
        await asyncio.sleep(seconds)
        return "slept"

    return reg


class _PushCancelProbe:
    """post:frame.push 上对首个 depth==2 的帧执行一次动作(经 ctl,即 RunControl)。"""

    name: ClassVar[str] = "push-cancel-probe"
    subscriptions: ClassVar[tuple[str, ...]] = ("post:frame.push",)
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, action) -> None:
        self._action = action
        self.done = False
        self.notes: list[Any] = []

    async def on_signal(self, sig: Signal, ctl) -> Allow:
        if not self.done and sig.payload.get("depth") == 2:
            self.done = True
            await self._action(sig, ctl, self.notes)
        return Allow()


def test_cancel_parent_code_frame_cascades_to_spawned_child(tmp_path):
    """孤儿断言反转(WS1):取消父 code 帧 → 其 spawn 子帧被级联取消(不再跑到
    run 结束);wait_frame 收到 SubtreeCancelled 终态;重复 cancel 幂等。"""
    box: dict[str, Any] = {}

    async def act(sig, ctl, notes):
        kernel = box["kernel"]
        child_id = sig.payload["frame_id"]
        parent_id = kernel.stack.get(child_id).parent_id
        notes.append(await ctl.cancel_frame(parent_id, "级联取消测试"))
        # 幂等:对已取消的子树重复 cancel 不炸;未知帧记日志并返回空 ack
        notes.append(await ctl.cancel_frame(parent_id, "重复取消"))
        notes.append(await ctl.cancel_frame("no-such-frame", "未知帧"))

    probe = _PushCancelProbe(act)
    kernel = _build(tmp_path, sidecars=(probe,))
    box["kernel"] = kernel
    # demo.fib(n=4) 需要多步递归,若不取消会跑出完整序列
    result = asyncio.run(kernel.run("test.spawn_or_report", {"skill": "demo.fib", "args": {"n": 4}}))

    # wait_frame 原样上抛 SubtreeCancelled,code 技能捕获后优雅收尾,run 正常完成
    assert result["cancelled"] is True
    child_id = result["frame_id"]
    child = kernel.stack.get(child_id)
    assert child is not None and child.status is FrameStatus.FAILED
    assert isinstance(child.error, asyncio.CancelledError), "后台子帧应被 task.cancel() 终结"
    # 级联 ack:目标父帧 + spawn 子帧;重复 cancel 同集;未知帧空 ack
    parent_id = child.parent_id
    assert set(probe.notes[0]) == {parent_id, child_id}
    assert set(probe.notes[1]) == {parent_id, child_id}
    assert probe.notes[2] == []
    # StatusBoard(§12.2):子帧是 failed 而非 done——不再作为孤儿跑到 run 结束
    value, _ = asyncio.run(_board_get(kernel, child_id))
    assert value["status"] == "failed"
    assert kernel._spawned == {}, "run 收尾后后台帧登记应清空"


async def _board_get(kernel, frame_id):
    return await kernel.blackboard.get("status", frame_id)


class _DelayedCancelProbe:
    """post:llm.response 上对首个 depth==2 的帧排一个延时子树取消。

    延时把取消点推进到该帧的工具分发执行中(slow_tool 睡眠 3s,取消 0.2s 后到),
    用于直击 §3.1 中断配对路径。
    """

    name: ClassVar[str] = "delayed-cancel-probe"
    subscriptions: ClassVar[tuple[str, ...]] = ("post:llm.response",)
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, errors: list[Exception]) -> None:
        self._errors = errors
        self.done = False

    async def on_signal(self, sig: Signal, ctl) -> Allow:
        if not self.done and sig.payload.get("depth") == 2:
            self.done = True
            frame_id = sig.frame_id

            async def _later() -> None:
                try:
                    await asyncio.sleep(0.2)
                    await ctl.cancel_frame(frame_id, "中断配对测试")
                except Exception as e:  # noqa: BLE001 — 测试断言通道:取消链路任何异常都要显形
                    self._errors.append(e)

            asyncio.create_task(_later())
        return Allow()


def test_cancel_during_dispatch_writes_interrupted_placeholder(tmp_path):
    """CancelledError 配对(§3.1 P0 / §7.4 不变量 2)首个直接测试:分发中的工具
    调用被级联取消 → 帧上下文含 interrupted 占位 tool_result,配对原子性成立。"""
    errors: list[Exception] = []
    kernel = _build(
        tmp_path, sidecars=(_DelayedCancelProbe(errors),), brain=slow_brain, tools=_slow_tools()
    )
    result = asyncio.run(kernel.run("test.spawn_or_report", {"skill": "test.slow_echo"}))
    assert result["cancelled"] is True, "后台帧应被级联取消(取消点在工具执行中)"
    assert not errors, f"取消链路抛出异常: {errors}"

    frame = kernel.stack.get(result["frame_id"])
    assert frame is not None and frame.status is FrameStatus.FAILED
    # 配对原子性:assistant 的 tool_call 必有对应 tool_result;取消时由占位结果闭合
    tool_msgs = [m for m in frame.context.messages if m.role is Role.TOOL]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "slow-1" and tool_msgs[0].name == "test.slow_tool"
    payload = json.loads(tool_msgs[0].content)
    assert payload["ok"] is False and payload["error"]["kind"] == "interrupted"
    assistant_call_ids = [
        tc.id for m in frame.context.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
    ]
    assert assistant_call_ids == ["slow-1"]


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


def test_concurrent_runs_release_isolated(tmp_path):
    """跨 run 隔离(WS1):共享 kernel 的并发 run,先结束的 run 收尾只取消本 run
    桶的后台帧,不杀另一 run 的在跑后台帧。"""
    kernel = _build(tmp_path, brain=mixed_brain, tools=_slow_tools())

    async def main():
        run_b = asyncio.create_task(
            kernel.run("test.spawn_or_report", {"skill": "test.slow_echo"})
        )
        # 等 run B 的后台帧登记在册(slow_tool 睡眠 3s,时间窗足够)
        while not any(kernel._spawned.values()):
            await asyncio.sleep(0.01)
        # run A 快速跑完(n=2 是 base case,无工具调用)——其收尾不得误杀 run B
        result_a = await kernel.run("demo.fib", {"n": 2})
        assert result_a == {"seq": [0, 1]}
        tasks_b = [t for bucket in kernel._spawned.values() for _, t in bucket.values()]
        assert tasks_b and not any(t.done() for t in tasks_b), (
            "run A 的 _release_run 误杀了 run B 的在跑后台帧"
        )
        result_b = await run_b
        assert result_b["cancelled"] is False
        assert result_b["value"] == {"done": True}

    asyncio.run(main())

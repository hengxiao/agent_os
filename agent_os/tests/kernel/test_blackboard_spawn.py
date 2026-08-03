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
import textwrap

import pytest

from agent_os.api.v1 import (
    BLACKBOARD_PUBLISH,
    BLACKBOARD_WRITE,
    POST_FRAME_PUSH,
    Envelope,
    Permission,
    RunConfig,
    Signal,
    ToolPolicy,
)
from agent_os.blackboard import BlackboardConflict, LocalBlackboard
from agent_os.kernel.errors import MaxDepthExceeded, SkillLoadError
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import assemble, auto_approve

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


def _build(tmp_path, *, max_depth: int = 8):
    config = RunConfig(
        model="mock/fib",
        max_depth=max_depth,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config,
        fib_brain,
        _yaml(tmp_path, FIB_PART + SPAWN_YAML),
        blackboard=LocalBlackboard(),
        # spawn 升权闸(docs/ESCALATION.md §3;E2):自动批准通道等价于生产宿主里人每次放行
        supervisor=auto_approve,
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

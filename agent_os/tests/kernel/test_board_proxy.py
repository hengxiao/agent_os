"""黑板命名空间代理锚点测试(DESIGN.md §12;``ctx.board`` 白名单仲裁)。

固定约定:

- code 技能经 ``ctx.board`` 访问黑板;每次调用按 manifest
  ``permissions.blackboard`` 白名单仲裁,命中才透传 kernel.blackboard;
- 白名单外命名空间 → 抛 ``SkillLoadError``(帧失败,向调用方上抛);
- ``publish(ns, env)`` 同一套仲裁后透传;无黑板装配时 ``ctx.board is None``。
"""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from agent_os.api.v1 import Permission, RunConfig, ToolPolicy
from agent_os.blackboard import LocalBlackboard
from agent_os.kernel.errors import AgentOSError
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import assemble

BOARD_YAML = """
skills:
  - name: test.board_put_get
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:board_put_get
    inputs:
      type: object
      properties: { v: { type: integer } }
      required: [v]
    outputs:
      type: object
      properties:
        value: { type: integer }
        version: { type: integer }
        put_version: { type: integer }
      required: [value, version, put_version]
    permissions: { tools: [], skills: [], blackboard: [shared] }
  - name: test.board_naughty
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:board_naughty
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [], skills: [], blackboard: [shared] }
  - name: test.board_publish
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:board_publish
    inputs:
      type: object
      properties: { pct: { type: integer } }
      required: [pct]
    outputs:
      type: object
      properties: { sent: { type: boolean } }
      required: [sent]
    permissions: { tools: [], skills: [], blackboard: [shared] }
"""


def _build(tmp_path, *, blackboard=True):
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(BOARD_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config, fib_brain, p, blackboard=LocalBlackboard() if blackboard else None
    )


def test_board_put_get_via_proxy(tmp_path):
    """白名单命名空间的 KV 读写经代理透传,版本号一致(§12)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(kernel.run("test.board_put_get", {"v": 42}))
    assert result["value"] == 42
    assert result["version"] == result["put_version"]


def test_board_write_visible_on_kernel_blackboard(tmp_path):
    """代理写入落到内核黑板本体(同一命名空间可读回)。"""
    kernel = _build(tmp_path)
    asyncio.run(kernel.run("test.board_put_get", {"v": 7}))
    value, _version = asyncio.run(kernel.blackboard.get("shared", "answer"))
    assert value == 7


def test_board_namespace_whitelist_rejected(tmp_path):
    """白名单外命名空间硬拒绝:帧失败上抛(§12 内核中介仲裁)。"""
    kernel = _build(tmp_path)
    with pytest.raises(AgentOSError, match="secret"):
        asyncio.run(kernel.run("test.board_naughty", {}))


def test_board_publish_via_proxy(tmp_path):
    """publish 经同一套白名单仲裁后透传(订阅方收到 Envelope)。"""
    kernel = _build(tmp_path)

    async def main():
        received = []

        async def consume():
            async for env in kernel.blackboard.subscribe("watcher", "status_update"):
                received.append(env)
                break

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)  # 让订阅生效
        result = await kernel.run("test.board_publish", {"pct": 80})
        await asyncio.wait_for(task, timeout=2)
        return result, received

    result, received = asyncio.run(main())
    assert result == {"sent": True}
    assert received and received[0].payload == {"pct": 80}
    assert received[0].type == "status_update"

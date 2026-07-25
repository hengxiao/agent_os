"""内置工具锚点测试(DESIGN.md §4.2/§8.3)。

固定约定:

- ``LocalPythonToolRegistry.with_builtins()`` 注册 ``fs_read``(READ)/``fs_write``(WRITE)/
  ``fs_edit``(WRITE)/``shell_exec``(EXEC)/``http_fetch``(NET);
- fs 工具限定在 ``ctx.workdir`` 内,逃逸路径(``../``)返回 ``ok=False, kind=INVALID_ARGS``;
- ``fs_read`` 带行号前缀(``1\\t...``),支持 ``offset``/``limit``;
- ``fs_edit``:old_string→new_string **唯一匹配**才替换;
  未找到 / 多处匹配 → ``ok=False, kind=INVALID_ARGS``;
- ``http_fetch_tool(transport=...)`` 工厂支持注入 httpx transport(测试用 MockTransport);
- 三层权限:RunConfig 上限低于工具权限级时拒绝(§8.2)。
"""

from __future__ import annotations

import asyncio

import httpx

from agent_os.api.v1 import (
    Permission,
    SkillFrame,
    ToolCall,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.tools.builtins import http_fetch_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry, ToolDispatchContext


def _dispatch_ctx(*, allowed: list[str] | None = None, max_perm: Permission = Permission.EXEC):
    frame = SkillFrame(frame_id="f1", run_id="r1")
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=allowed or ["fs_read", "fs_write", "fs_edit", "shell_exec", "http_fetch"],
        tool_policy=ToolPolicy(max_permission=max_perm),
    )


def test_with_builtins_registers_four_tools():
    reg = LocalPythonToolRegistry.with_builtins()
    for name in ("fs_read", "fs_write", "shell_exec", "http_fetch"):
        assert reg.has(name), name
    assert reg.get("fs_read").spec.permission is Permission.READ
    assert reg.get("fs_write").spec.permission is Permission.WRITE
    assert reg.get("shell_exec").spec.permission is Permission.EXEC
    assert reg.get("http_fetch").spec.permission is Permission.NET


def test_fs_write_then_read_with_line_numbers():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        wr = await reg.dispatch(
            ToolCall(id="w1", name="fs_write", args={"path": "a.txt", "content": "hello\nworld\nfoo"}), ctx
        )
        assert wr.ok, wr.error
        rd = await reg.dispatch(ToolCall(id="r1", name="fs_read", args={"path": "a.txt"}), ctx)
        assert rd.ok, rd.error
        return rd.value

    content = asyncio.run(main())
    assert "hello" in content and "world" in content
    assert "1" in content and "\t" in content  # 行号前缀


def test_fs_read_offset_limit():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        await reg.dispatch(
            ToolCall(id="w1", name="fs_write", args={"path": "b.txt", "content": "l1\nl2\nl3\nl4\n"}), ctx
        )
        rd = await reg.dispatch(
            ToolCall(id="r1", name="fs_read", args={"path": "b.txt", "offset": 2, "limit": 2}), ctx
        )
        assert rd.ok, rd.error
        return rd.value

    content = asyncio.run(main())
    assert "l2" in content and "l3" in content
    assert "l1" not in content and "l4" not in content


def test_fs_path_traversal_rejected():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        return await reg.dispatch(
            ToolCall(id="w1", name="fs_write", args={"path": "../evil.txt", "content": "x"}), ctx
        )

    result = asyncio.run(main())
    assert not result.ok
    assert result.error is not None and result.error.kind is ToolErrorKind.INVALID_ARGS


def test_fs_edit_unique_match():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        await reg.dispatch(
            ToolCall(id="w", name="fs_write", args={"path": "a.txt", "content": "hello world"}), ctx
        )
        ed = await reg.dispatch(
            ToolCall(id="e", name="fs_edit",
                     args={"path": "a.txt", "old_string": "world", "new_string": "agent_os"}),
            ctx,
        )
        assert ed.ok, ed.error
        rd = await reg.dispatch(ToolCall(id="r", name="fs_read", args={"path": "a.txt"}), ctx)
        return rd.value

    content = asyncio.run(main())
    assert "agent_os" in content and "world" not in content


def test_fs_edit_missing_and_non_unique():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        await reg.dispatch(
            ToolCall(id="w", name="fs_write", args={"path": "b.txt", "content": "foo foo"}), ctx
        )
        missing = await reg.dispatch(
            ToolCall(id="e1", name="fs_edit", args={"path": "b.txt", "old_string": "bar", "new_string": "x"}),
            ctx,
        )
        non_unique = await reg.dispatch(
            ToolCall(id="e2", name="fs_edit", args={"path": "b.txt", "old_string": "foo", "new_string": "x"}),
            ctx,
        )
        return missing, non_unique

    missing, non_unique = asyncio.run(main())
    assert not missing.ok and missing.error.kind is ToolErrorKind.INVALID_ARGS
    assert not non_unique.ok and non_unique.error.kind is ToolErrorKind.INVALID_ARGS
    assert "唯一" in non_unique.error.message or "多处" in non_unique.error.message


def test_shell_exec_runs_in_workdir():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        return await reg.dispatch(
            ToolCall(id="s1", name="shell_exec", args={"command": "echo hi && pwd"}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    assert "hi" in result.value


def test_http_fetch_via_mock_transport():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="PAGE-CONTENT"))
    tool = http_fetch_tool(transport=transport)
    reg = LocalPythonToolRegistry()
    reg.register(tool)
    ctx = _dispatch_ctx()

    async def main():
        return await reg.dispatch(
            ToolCall(id="h1", name="http_fetch", args={"url": "https://example.com/"}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    assert result.value["status"] == 200
    assert "PAGE-CONTENT" in result.value["content"]


def test_tool_policy_caps_permission():
    """三层权限:RunConfig 上限 WRITE 时,EXEC 级 shell_exec 被拒(§8.2)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx(max_perm=Permission.WRITE)

    async def main():
        return await reg.dispatch(ToolCall(id="s1", name="shell_exec", args={"command": "echo x"}), ctx)

    result = asyncio.run(main())
    assert not result.ok
    assert result.error is not None and result.error.kind is ToolErrorKind.PERMISSION_DENIED

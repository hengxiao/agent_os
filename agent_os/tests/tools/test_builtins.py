"""内置工具锚点测试(DESIGN.md §4.2/§8.3)。

固定约定:

- ``LocalPythonToolRegistry.with_builtins()`` 注册 ``fs_read``(READ)/``fs_write``(WRITE)/
  ``fs_edit``(WRITE)/``shell_exec``(EXEC)/``http_fetch``(NET);
- fs 工具限定在 ``ctx.workdir`` 内,逃逸路径(``../``)返回 ``ok=False, kind=INVALID_ARGS``;
- ``fs_read`` 带行号前缀(``1\\t...``),支持 ``offset``/``limit``;
- ``fs_edit``:old_string→new_string **唯一匹配**才替换;
  未找到 / 多处匹配 → ``ok=False, kind=INVALID_ARGS``;
- ``http_fetch_tool(transport=...)`` 工厂支持注入 httpx transport(测试用 MockTransport);
- 三层权限:RunConfig 上限低于工具权限级时拒绝(§8.2);
- ``shell_exec`` 返回 ``{stdout, stderr, exit_code, truncated, text}``(§W0-5 结构化返回)。
"""

from __future__ import annotations

import asyncio
import subprocess

import httpx

import agent_os.tools.builtins as builtins_mod
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
    assert "hi" in result.value["stdout"]  # §W0-5 结构化返回:text 为拼接版,脚本用 stdout
    assert result.value["exit_code"] == 0


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


def _sleep_pids() -> set[str]:
    """当前存活的 sleep 进程 pid 集合(孤儿检测用;计数比较会被无关进程的进出蒙混)。"""
    out = subprocess.run(["pgrep", "-x", "sleep"], capture_output=True, text=True, check=False).stdout
    return set(out.split())


def test_shell_exec_timeout_clamped_to_spec_no_orphan(monkeypatch):
    """**双超时竞态**:模型传的 timeout 被钳在 spec.timeout 内,且不留孤儿进程。

    回归的是一组真实缺陷:注册表 spec.timeout=30 是外层硬闸门,模型传
    timeout=120 时外层先到并抛 CancelledError,内层 except 走不到,子进程永不
    被杀——一条 ``sleep 120`` 会在宿主上活满两分钟。修复途中还暴露两层:
    取消 ``communicate()`` 会让管道传输永不关闭,``proc.wait()`` 就地死锁;
    只杀 shell 不杀进程组时,``sh -c`` fork 出的孙子进程照活不误。
    """
    import time

    monkeypatch.setattr(builtins_mod, "_SHELL_SPEC_TIMEOUT", 3.0)
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()
    before = _sleep_pids()

    async def main():
        started = time.monotonic()
        result = await reg.dispatch(
            # 请求 120s,远超 spec.timeout;管道令 sh 必然 fork 出孙子进程
            ToolCall(id="s", name="shell_exec", args={"command": "sleep 61 | cat", "timeout": 120}),
            ctx,
        )
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(main())
    assert not result.ok
    assert result.error.kind is ToolErrorKind.TIMEOUT
    assert elapsed < 10, f"未在 spec.timeout 内收敛(耗时 {elapsed:.1f}s):外层竞态或死锁仍在"
    assert result.error.retryable is True
    assert "spec.timeout" in result.error.hint, "hint 须说明上限来源,否则模型只会反复加大 timeout"
    assert _sleep_pids() - before == set(), "超时后仍有存活的孙子进程(只杀了 shell,没杀进程组)"


def _read(text: str, **args) -> str:
    """写入一个文件再读回(经注册表分发,走真实的 workdir 解析路径)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        wr = await reg.dispatch(
            ToolCall(id="w", name="fs_write", args={"path": "f.txt", "content": text}), ctx
        )
        assert wr.ok, wr.error
        return await reg.dispatch(
            ToolCall(id="r", name="fs_read", args={"path": "f.txt", **args}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    return result.value


def test_fs_read_marks_truncation_with_total_and_next_offset():
    """**截断必须显式**(§3.2 契约 2 的原例):告知已读区间、总行数、续读入口。

    回归的是一个静默缺陷:窗口读满 limit 就直接返回,模型无从判断自己只看了
    开头——于是把前 200 行当全文下结论。
    """
    out = _read("\n".join(f"L{i}" for i in range(1, 5001)), offset=1, limit=200)

    assert out.splitlines()[-1] == "[已显示 1-200 行,共 5000 行;续读 fs_read(offset=201)]"
    assert "200\tL200" in out and "201\tL201" not in out, "提示行不得改变窗口本身"


def test_fs_read_no_footer_when_window_reaches_eof():
    """读全了就不加提示行——否则每次读小文件都掺进一行噪音。"""
    out = _read("a\nb\nc", limit=10)
    assert out == "1\ta\n2\tb\n3\tc"


def test_fs_read_distinguishes_empty_file_from_offset_past_eof():
    """空窗口不能返回空串:模型分不清"文件是空的"和"offset 翻过头了"。"""
    past = _read("a\nb", offset=99)
    empty = _read("", offset=1)
    assert "共 2 行" in past and "越过末尾" in past
    assert "空文件" in empty
    assert past != empty

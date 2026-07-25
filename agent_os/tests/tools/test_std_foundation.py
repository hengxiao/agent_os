"""W0 地基锚点测试(STDLIB-CATALOG §W0;STDLIB §3.2/§3.3)。

固定约定:

- **workdir 可配置**:`[run] workdir = "..."` + 三分区(`read_paths` 只读 /
  workspace 可写 / scratch 默认临时);fs 与 shell 共用一个解析器;
  默认行为(无配置 → 每 run 临时目录)不变;
- **if_match 乐观锁**:`fs_write`/`fs_edit` 接受 `if_match: str`(内容 hash),
  不匹配拒写并返回当前版本标识与"重读后重试"提示;
- **shell_exec 结构化返回**:`{stdout, stderr, exit_code, truncated, text}`;
- **错误 hint**:内置工具的错误带可执行的下一步建议(非空、含动作);
- **契约字段**:READ 档内置工具声明 `cacheable/concurrent_safe`/`idempotent`,
  ToolSpec 有 `cost_hint` 字段。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_os.api.v1 import (
    Permission,
    RunConfig,
    SkillFrame,
    ToolCall,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.tools.local_registry import LocalPythonToolRegistry, ToolDispatchContext

# ---------------------------------------------------------------------------
# W0-1 workdir 可配置
# ---------------------------------------------------------------------------


def _ctx(workdir: Path, *, read_paths: list[Path] | None = None) -> ToolDispatchContext:
    frame = SkillFrame(frame_id="f1", run_id="r1")
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=["fs_read", "fs_write", "fs_edit", "shell_exec"],
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=workdir,
        read_paths=read_paths or [],
    )


def test_default_workdir_unchanged(tmp_path):
    """无配置时保持现状:每 run 临时目录(安全边界不静默放宽)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r-default"),
        allowed_tools=["fs_write"],
        tool_policy=ToolPolicy(max_permission=Permission.WRITE),
    )

    async def main():
        wr = await reg.dispatch(ToolCall(id="w", name="fs_write", args={"path": "a.txt", "content": "x"}), ctx)
        assert wr.ok, wr.error
        rd = await reg.dispatch(ToolCall(id="r", name="fs_read", args={"path": "a.txt"}), ctx)
        return rd

    assert "x" in asyncio.run(main()).value


def test_configured_workdir_resolves_inside(tmp_path):
    """[run] workdir 指定后,fs/shell 都在其中解析(共用解析器)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path)

    async def main():
        wr = await reg.dispatch(ToolCall(id="w", name="fs_write", args={"path": "proj/a.txt", "content": "hi"}), ctx)
        assert wr.ok, wr.error
        assert (tmp_path / "proj" / "a.txt").read_text() == "hi"
        sh = await reg.dispatch(ToolCall(id="s", name="shell_exec", args={"command": "cat proj/a.txt"}), ctx)
        return sh

    result = asyncio.run(main())
    assert result.ok
    assert "hi" in result.value["stdout"]


def test_readonly_zone_rejects_writes(tmp_path):
    """read_paths 只读区:写入被拒(INVALID_ARGS),读取正常。"""
    ro = tmp_path / "vendor"
    ro.mkdir()
    (ro / "lib.py").write_text("# vendored", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, read_paths=[ro])

    async def main():
        rd = await reg.dispatch(
            ToolCall(id="r", name="fs_read", args={"path": str(ro / "lib.py")}), ctx
        )
        assert rd.ok, rd.error
        wr = await reg.dispatch(
            ToolCall(id="w", name="fs_write", args={"path": str(ro / "evil.py"), "content": "x"}), ctx
        )
        return wr

    result = asyncio.run(main())
    assert not result.ok
    assert result.error.kind is ToolErrorKind.INVALID_ARGS


def test_readonly_zone_outside_workdir_still_readable(tmp_path):
    """只读区可以在 workdir 之外(真实源码目录),而可写区外的路径仍拒绝。"""
    ro = tmp_path / "vendor"
    ro.mkdir()
    (ro / "lib.py").write_text("# vendored", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path / "ws", read_paths=[ro])
    (tmp_path / "ws").mkdir()

    async def main():
        ok = await reg.dispatch(ToolCall(id="r", name="fs_read", args={"path": str(ro / "lib.py")}), ctx)
        bad = await reg.dispatch(
            ToolCall(id="r2", name="fs_read", args={"path": "/etc/hostname"}), ctx
        )
        return ok, bad

    ok, bad = asyncio.run(main())
    assert ok.ok and "vendored" in ok.value
    assert not bad.ok and bad.error.kind is ToolErrorKind.INVALID_ARGS


# ---------------------------------------------------------------------------
# W0-3 错误 hint
# ---------------------------------------------------------------------------


def test_fs_read_missing_file_has_actionable_hint(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path)

    async def main():
        return await reg.dispatch(ToolCall(id="r", name="fs_read", args={"path": "ghost.txt"}), ctx)

    result = asyncio.run(main())
    assert not result.ok
    assert result.error.hint, "hint 必须非空(可自纠的下一步建议)"


def test_path_escape_has_actionable_hint(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path)

    async def main():
        return await reg.dispatch(ToolCall(id="r", name="fs_read", args={"path": "../x.txt"}), ctx)

    result = asyncio.run(main())
    assert not result.ok
    assert result.error.hint


# ---------------------------------------------------------------------------
# W0-4 if_match 乐观锁
# ---------------------------------------------------------------------------


def test_fs_write_if_match_optimistic_lock(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path)

    async def main():
        await reg.dispatch(ToolCall(id="w1", name="fs_write", args={"path": "a.txt", "content": "v1"}), ctx)
        # 正确 if_match(当前内容 hash)→ 成功
        ok = await reg.dispatch(
            ToolCall(id="w2", name="fs_write", args={"path": "a.txt", "content": "v2", "if_match": "v1"}), ctx
        )
        assert ok.ok, ok.error
        # 过期 if_match → 拒写
        stale = await reg.dispatch(
            ToolCall(id="w3", name="fs_write", args={"path": "a.txt", "content": "v3", "if_match": "v1"}), ctx
        )
        return stale

    stale = asyncio.run(main())
    assert not stale.ok
    assert "重读" in (stale.error.hint or stale.error.message)
    assert (tmp_path / "a.txt").read_text() == "v2"


# ---------------------------------------------------------------------------
# W0-5 shell_exec 结构化返回
# ---------------------------------------------------------------------------


def test_shell_exec_structured_result(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path)

    async def main():
        ok = await reg.dispatch(
            ToolCall(id="s1", name="shell_exec", args={"command": "echo out && echo err >&2"}), ctx
        )
        fail = await reg.dispatch(ToolCall(id="s2", name="shell_exec", args={"command": "exit 3"}), ctx)
        return ok, fail

    ok, fail = asyncio.run(main())
    assert ok.value["stdout"].strip() == "out"
    assert ok.value["stderr"].strip() == "err"
    assert ok.value["exit_code"] == 0
    assert ok.value["truncated"] is False
    assert "text" in ok.value  # 拼接版保留给模型直读
    assert fail.value["exit_code"] == 3


# ---------------------------------------------------------------------------
# W0-2 契约字段
# ---------------------------------------------------------------------------


def test_read_tools_declare_contract_fields():
    reg = LocalPythonToolRegistry.with_builtins()
    fs_read = reg.get("fs_read").spec
    assert fs_read.cacheable is True
    assert fs_read.concurrent_safe is True
    assert fs_read.idempotent is True
    assert hasattr(fs_read, "cost_hint"), "ToolSpec 需要 cost_hint 字段"

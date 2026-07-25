"""W1 核心工具锚点测试(STDLIB-CATALOG §W1;STDLIB §3.3)。

固定约定:

- `fs_list`:目录列举(glob + 默认尊重 .gitignore + 跳过 .git/node_modules/.venv),
  返回 `{entries: [{path, size, mtime, is_dir}], total, next_cursor?}`;
- `fs_search`:纯 Python 内容检索(正则 + 行号 + 上下文行 + 分页 + 跳过二进制),
  `{hits: [{path, line, text, before, after}], total, next_cursor?, spill_ref?}`;
- `now`:服务端时钟 `{iso, epoch, tz}`,声明 `replayable`(可回放机制);
- `todo_write`/`todo_update`:run 级任务清单(跨帧可见、状态栏注入、checkpoint 持久);
- `skill_search`:按子串检索已注册技能/工具,结果带 permissions。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_os.api.v1 import Permission, SkillFrame, ToolCall, ToolPolicy
from agent_os.tools.local_registry import LocalPythonToolRegistry, ToolDispatchContext


def _ctx(tmp_path: Path, allowed: list[str]) -> ToolDispatchContext:
    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=allowed,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=tmp_path,
    )


def _seed_tree(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("def main():\n    print('hello fibonacci')\n", encoding="utf-8")
    (root / "src" / "util.py").write_text("# helper\nVALUE = 42\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\nfibonacci project\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")
    (root / "ignored_dir").mkdir()
    (root / "ignored_dir" / "x.py").write_text("fibonacci hidden\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text("fibonacci dep\n", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02fibonacci\x00")


# ---------------------------------------------------------------------------
# fs_list
# ---------------------------------------------------------------------------


def test_fs_list_basic_and_gitignore(tmp_path):
    _seed_tree(tmp_path)
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["fs_list"])

    async def main():
        return await reg.dispatch(ToolCall(id="l", name="fs_list", args={"path": "."}), ctx)

    result = asyncio.run(main())
    assert result.ok, result.error
    paths = {e["path"] for e in result.value["entries"]}
    assert any("main.py" in p for p in paths)
    assert not any("ignored_dir" in p for p in paths), "默认尊重 .gitignore"
    assert not any("node_modules" in p for p in paths)
    first = result.value["entries"][0]
    assert {"path", "size", "mtime", "is_dir"} <= set(first)
    assert "total" in result.value


def test_fs_list_pagination(tmp_path):
    for i in range(15):
        (tmp_path / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["fs_list"])

    async def main():
        page1 = await reg.dispatch(
            ToolCall(id="l1", name="fs_list", args={"path": ".", "pattern": "*.txt", "max_entries": 10}), ctx
        )
        assert page1.ok, page1.error
        return page1

    page1 = asyncio.run(main())
    assert len(page1.value["entries"]) == 10
    assert page1.value["total"] == 15
    assert page1.value.get("next_cursor"), "截断时必须给 next_cursor"


# ---------------------------------------------------------------------------
# fs_search
# ---------------------------------------------------------------------------


def test_fs_search_with_context_and_binary_skip(tmp_path):
    _seed_tree(tmp_path)
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["fs_search"])

    async def main():
        return await reg.dispatch(
            ToolCall(id="s", name="fs_search", args={"pattern": "fibonacci", "context_lines": 1}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    hits = result.value["hits"]
    assert hits, "应命中多处"
    for hit in hits:
        assert hit["path"] != "bin.dat", "二进制文件必须跳过"
    main_hit = next(h for h in hits if "main.py" in h["path"])
    assert main_hit["line"] == 2
    assert main_hit["before"] and "def main" in main_hit["before"][0]


def test_fs_search_pagination_and_total(tmp_path):
    for i in range(12):
        (tmp_path / f"f{i:02d}.txt").write_text(f"needle line {i}\n", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["fs_search"])

    async def main():
        return await reg.dispatch(
            ToolCall(id="s", name="fs_search", args={"pattern": "needle", "max_hits": 5}), ctx
        )

    result = asyncio.run(main())
    assert len(result.value["hits"]) == 5
    assert result.value["total"] == 12
    assert result.value.get("next_cursor")


# ---------------------------------------------------------------------------
# now + replayable
# ---------------------------------------------------------------------------


def test_now_returns_server_clock(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["now"])

    async def main():
        return await reg.dispatch(ToolCall(id="n", name="now", args={}), ctx)

    result = asyncio.run(main())
    assert result.ok, result.error
    assert {"iso", "epoch", "tz"} <= set(result.value)
    assert isinstance(result.value["epoch"], (int, float))
    assert reg.get("now").spec.replayable is True, "now 必须声明 replayable"


def test_replayable_tool_returns_recorded_value_on_replay(tmp_path):
    """回放机制:replay 模式下,replayable 工具返回 trace 记录值而不执行(可复现)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["now"])

    async def main():
        first = await reg.dispatch(ToolCall(id="n", name="now", args={}), ctx)
        assert first.ok
        recorded = first.value
        replay_ctx = ToolDispatchContext(
            frame=SkillFrame(frame_id="f1", run_id="r1"),
            allowed_tools=["now"],
            tool_policy=ToolPolicy(max_permission=Permission.EXEC),
            workdir=tmp_path,
            replay_records={"now": [recorded]},
        )
        second = await reg.dispatch(ToolCall(id="n", name="now", args={}), replay_ctx)
        return recorded, second

    recorded, second = asyncio.run(main())
    assert second.value == recorded, "replay 必须返回记录值,不得重新取时钟"


# ---------------------------------------------------------------------------
# todo_write / todo_update
# ---------------------------------------------------------------------------


def test_todo_write_update_and_run_level_visibility(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    parent = ToolDispatchContext(
        frame=SkillFrame(frame_id="p", run_id="r1"),
        allowed_tools=["todo_write", "todo_update"],
        tool_policy=ToolPolicy(max_permission=Permission.WRITE),
        workdir=tmp_path,
    )
    child = ToolDispatchContext(
        frame=SkillFrame(frame_id="c1", run_id="r1"),
        allowed_tools=["todo_update"],
        tool_policy=ToolPolicy(max_permission=Permission.WRITE),
        workdir=tmp_path,
    )

    async def main():
        wr = await reg.dispatch(
            ToolCall(id="t1", name="todo_write",
                     args={"items": [{"id": "1", "text": "调研", "status": "doing"},
                                     {"id": "2", "text": "写报告", "status": "pending"}]}),
            parent,
        )
        assert wr.ok, wr.error
        # 子帧(同 run)可见并推进
        upd = await reg.dispatch(
            ToolCall(id="t2", name="todo_update", args={"id": "1", "status": "done", "note": "找到 3 个来源"}),
            child,
        )
        return upd

    upd = asyncio.run(main())
    assert upd.ok, upd.error
    assert upd.value["item"]["status"] == "done"


# ---------------------------------------------------------------------------
# skill_search
# ---------------------------------------------------------------------------


def test_skill_search_finds_with_permissions(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["skill_search"])

    async def main():
        return await reg.dispatch(
            ToolCall(id="q", name="skill_search", args={"query": "edit", "kind": "tool"}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    names = [r["name"] for r in result.value["results"]]
    assert "fs_edit" in names
    entry = next(r for r in result.value["results"] if r["name"] == "fs_edit")
    assert "permission" in entry or "permissions" in entry, "结果必须带权限信息(防选中无权工具)"

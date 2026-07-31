"""W1 核心工具锚点测试(STDLIB-CATALOG §W1;STDLIB §3.3)。

固定约定:

- `system.file.list`:目录列举(glob + 默认尊重 .gitignore + 跳过 .git/node_modules/.venv),
  返回 `{entries: [{path, size, mtime, is_dir}], total, next_cursor?}`;
- `system.file.search`:纯 Python 内容检索(正则 + 行号 + 上下文行 + 分页 + 跳过二进制),
  `{hits: [{path, line, text, before, after}], total, next_cursor?, spill_ref?}`;
- `system.time.now`:服务端时钟 `{iso, epoch, tz}`,声明 `replayable`(可回放机制);
- `system.task.todo_write`/`system.task.todo_update`:run 级任务清单(跨帧可见、状态栏注入、checkpoint 持久);
- `system.skill.search`:按子串检索已注册技能/工具,结果带 permissions。
- Phase 3 补齐:`system.file.stat`(不存在返回 exists=False)/`system.file.delete`
  (confirm=True,if_match 乐观锁,仅文件与空目录)/`system.file.mkdir`(幂等)/
  `system.net.http_request`(非 GET 通用 HTTP,MockTransport 注入)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from agent_os.api.v1 import Permission, SkillFrame, ToolCall, ToolErrorKind, ToolPolicy
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
# system.file.list
# ---------------------------------------------------------------------------


def test_system_file_list_basic_and_gitignore(tmp_path):
    _seed_tree(tmp_path)
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.list"])

    async def main():
        return await reg.dispatch(ToolCall(id="l", name="system.file.list", args={"path": "."}), ctx)

    result = asyncio.run(main())
    assert result.ok, result.error
    paths = {e["path"] for e in result.value["entries"]}
    assert any("main.py" in p for p in paths)
    assert not any("ignored_dir" in p for p in paths), "默认尊重 .gitignore"
    assert not any("node_modules" in p for p in paths)
    first = result.value["entries"][0]
    assert {"path", "size", "mtime", "is_dir"} <= set(first)
    assert "total" in result.value


def test_system_file_list_pagination(tmp_path):
    for i in range(15):
        (tmp_path / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.list"])

    async def main():
        page1 = await reg.dispatch(
            ToolCall(id="l1", name="system.file.list", args={"path": ".", "pattern": "*.txt", "max_entries": 10}), ctx
        )
        assert page1.ok, page1.error
        return page1

    page1 = asyncio.run(main())
    assert len(page1.value["entries"]) == 10
    assert page1.value["total"] == 15
    assert page1.value.get("next_cursor"), "截断时必须给 next_cursor"


# ---------------------------------------------------------------------------
# system.file.search
# ---------------------------------------------------------------------------


def test_system_file_search_with_context_and_binary_skip(tmp_path):
    _seed_tree(tmp_path)
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.search"])

    async def main():
        return await reg.dispatch(
            ToolCall(id="s", name="system.file.search", args={"pattern": "fibonacci", "context_lines": 1}), ctx
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


def test_system_file_search_pagination_and_total(tmp_path):
    for i in range(12):
        (tmp_path / f"f{i:02d}.txt").write_text(f"needle line {i}\n", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.search"])

    async def main():
        return await reg.dispatch(
            ToolCall(id="s", name="system.file.search", args={"pattern": "needle", "max_hits": 5}), ctx
        )

    result = asyncio.run(main())
    assert len(result.value["hits"]) == 5
    assert result.value["total"] == 12
    assert result.value.get("next_cursor")


# ---------------------------------------------------------------------------
# system.time.now + replayable
# ---------------------------------------------------------------------------


def test_system_time_now_returns_server_clock(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.time.now"])

    async def main():
        return await reg.dispatch(ToolCall(id="n", name="system.time.now", args={}), ctx)

    result = asyncio.run(main())
    assert result.ok, result.error
    assert {"iso", "epoch", "tz"} <= set(result.value)
    assert isinstance(result.value["epoch"], (int, float))
    assert reg.get("system.time.now").spec.replayable is True, "now 必须声明 replayable"


def test_system_time_now_replayable_tool_returns_recorded_value_on_replay(tmp_path):
    """回放机制:replay 模式下,replayable 工具返回 trace 记录值而不执行(可复现)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.time.now"])

    async def main():
        first = await reg.dispatch(ToolCall(id="n", name="system.time.now", args={}), ctx)
        assert first.ok
        recorded = first.value
        replay_ctx = ToolDispatchContext(
            frame=SkillFrame(frame_id="f1", run_id="r1"),
            allowed_tools=["system.time.now"],
            tool_policy=ToolPolicy(max_permission=Permission.EXEC),
            workdir=tmp_path,
            replay_records={"system.time.now": [recorded]},
        )
        second = await reg.dispatch(ToolCall(id="n", name="system.time.now", args={}), replay_ctx)
        return recorded, second

    recorded, second = asyncio.run(main())
    assert second.value == recorded, "replay 必须返回记录值,不得重新取时钟"


# ---------------------------------------------------------------------------
# system.task.todo_write / system.task.todo_update
# ---------------------------------------------------------------------------


def test_system_task_todo_write_update_and_run_level_visibility(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    parent = ToolDispatchContext(
        frame=SkillFrame(frame_id="p", run_id="r1"),
        allowed_tools=["system.task.todo_write", "system.task.todo_update"],
        tool_policy=ToolPolicy(max_permission=Permission.WRITE),
        workdir=tmp_path,
    )
    child = ToolDispatchContext(
        frame=SkillFrame(frame_id="c1", run_id="r1"),
        allowed_tools=["system.task.todo_update"],
        tool_policy=ToolPolicy(max_permission=Permission.WRITE),
        workdir=tmp_path,
    )

    async def main():
        wr = await reg.dispatch(
            ToolCall(id="t1", name="system.task.todo_write",
                     args={"items": [{"id": "1", "text": "调研", "status": "doing"},
                                     {"id": "2", "text": "写报告", "status": "pending"}]}),
            parent,
        )
        assert wr.ok, wr.error
        # 子帧(同 run)可见并推进
        upd = await reg.dispatch(
            ToolCall(id="t2", name="system.task.todo_update", args={"id": "1", "status": "done", "note": "找到 3 个来源"}),
            child,
        )
        return upd

    upd = asyncio.run(main())
    assert upd.ok, upd.error
    assert upd.value["item"]["status"] == "done"


def test_system_task_todo_read_and_status_filter(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = ToolDispatchContext(
        frame=SkillFrame(frame_id="p", run_id="r1"),
        allowed_tools=["system.task.todo_write", "system.task.todo_read"],
        tool_policy=ToolPolicy(max_permission=Permission.WRITE),
        workdir=tmp_path,
    )

    async def main():
        wr = await reg.dispatch(
            ToolCall(id="t1", name="system.task.todo_write",
                     args={"items": [{"id": "1", "text": "调研", "status": "doing"},
                                     {"id": "2", "text": "写报告", "status": "pending"},
                                     {"id": "3", "text": "审校", "status": "pending"}]}),
            ctx,
        )
        assert wr.ok, wr.error
        all_res = await reg.dispatch(
            ToolCall(id="t2", name="system.task.todo_read", args={}), ctx,
        )
        pending_res = await reg.dispatch(
            ToolCall(id="t3", name="system.task.todo_read", args={"status": "pending"}), ctx,
        )
        return all_res, pending_res

    all_res, pending_res = asyncio.run(main())
    assert all_res.ok, all_res.error
    assert all_res.value["count"] == 3
    assert {t["text"] for t in all_res.value["todos"]} == {"调研", "写报告", "审校"}
    assert pending_res.ok, pending_res.error
    assert pending_res.value["count"] == 2
    assert {t["text"] for t in pending_res.value["todos"]} == {"写报告", "审校"}


# ---------------------------------------------------------------------------
# system.skill.search
# ---------------------------------------------------------------------------


def test_system_skill_search_finds_with_permissions(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.skill.search"])

    async def main():
        return await reg.dispatch(
            ToolCall(id="q", name="system.skill.search", args={"query": "edit", "kind": "tool"}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    names = [r["name"] for r in result.value["results"]]
    assert "system.file.edit" in names
    entry = next(r for r in result.value["results"] if r["name"] == "system.file.edit")
    assert "permission" in entry or "permissions" in entry, "结果必须带权限信息(防选中无权工具)"


# ---------------------------------------------------------------------------
# system.file.stat(Phase 3)
# ---------------------------------------------------------------------------


def test_system_file_stat_existing_and_missing(tmp_path):
    _seed_tree(tmp_path)
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.stat"])

    async def main():
        hit = await reg.dispatch(
            ToolCall(id="s1", name="system.file.stat", args={"path": "README.md"}), ctx
        )
        miss = await reg.dispatch(
            ToolCall(id="s2", name="system.file.stat", args={"path": "nope.txt"}), ctx
        )
        return hit, miss

    hit, miss = asyncio.run(main())
    assert hit.ok, hit.error
    assert hit.value["exists"] is True
    assert hit.value["is_dir"] is False
    assert hit.value["size"] > 0 and hit.value["mtime"] > 0
    assert miss.ok, "不存在是合法探查结果,不是错误"
    assert miss.value == {"size": 0, "mtime": 0.0, "is_dir": False, "exists": False}


def test_system_file_stat_dir_and_traversal_rejected(tmp_path):
    _seed_tree(tmp_path)
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.stat"])

    async def main():
        d = await reg.dispatch(
            ToolCall(id="s1", name="system.file.stat", args={"path": "src"}), ctx
        )
        esc = await reg.dispatch(
            ToolCall(id="s2", name="system.file.stat", args={"path": "../../etc/hostname"}), ctx
        )
        return d, esc

    d, esc = asyncio.run(main())
    assert d.ok and d.value["exists"] is True and d.value["is_dir"] is True
    assert not esc.ok
    assert esc.error is not None and esc.error.kind is ToolErrorKind.INVALID_ARGS


# ---------------------------------------------------------------------------
# system.file.delete(Phase 3;高危,confirm=True)
# ---------------------------------------------------------------------------


def test_system_file_delete_file_with_if_match(tmp_path):
    (tmp_path / "a.txt").write_text("hello agent_os\n", encoding="utf-8")
    digest = hashlib.sha256("hello agent_os\n".encode()).hexdigest()
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.delete"])

    async def main():
        wrong = await reg.dispatch(
            ToolCall(id="d1", name="system.file.delete", args={"path": "a.txt", "if_match": "别的内容"}), ctx
        )
        still_there = (tmp_path / "a.txt").is_file()
        ok = await reg.dispatch(
            ToolCall(id="d2", name="system.file.delete", args={"path": "a.txt", "if_match": digest}), ctx
        )
        return wrong, still_there, ok

    wrong, still_there, ok = asyncio.run(main())
    assert not wrong.ok
    assert wrong.error is not None and wrong.error.kind is ToolErrorKind.INVALID_ARGS
    assert still_there, "if_match 不匹配必须拒删"
    assert ok.ok, ok.error
    assert not (tmp_path / "a.txt").exists()


def test_system_file_delete_missing_and_dirs(tmp_path):
    (tmp_path / "nonempty").mkdir()
    (tmp_path / "nonempty" / "x.txt").write_text("x", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.delete"])

    async def main():
        missing = await reg.dispatch(
            ToolCall(id="d1", name="system.file.delete", args={"path": "gone.txt"}), ctx
        )
        nonempty = await reg.dispatch(
            ToolCall(id="d2", name="system.file.delete", args={"path": "nonempty"}), ctx
        )
        empty = await reg.dispatch(
            ToolCall(id="d3", name="system.file.delete", args={"path": "empty"}), ctx
        )
        return missing, nonempty, empty

    missing, nonempty, empty = asyncio.run(main())
    assert not missing.ok
    assert missing.error is not None and missing.error.kind is ToolErrorKind.NOT_FOUND
    assert not nonempty.ok, "非空目录必须拒绝"
    assert nonempty.error is not None and nonempty.error.kind is ToolErrorKind.INVALID_ARGS
    assert (tmp_path / "nonempty" / "x.txt").is_file()
    assert empty.ok, empty.error
    assert not (tmp_path / "empty").exists()


def test_system_file_delete_confirm_and_permission_gate(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    spec = reg.get("system.file.delete").spec
    assert spec.confirm is True, "高危删除必须声明 confirm"
    assert spec.permission is Permission.WRITE
    ctx = _ctx(tmp_path, [])  # 帧白名单为空:WRITE 档必须被拒

    async def main():
        return await reg.dispatch(
            ToolCall(id="d1", name="system.file.delete", args={"path": "a.txt"}), ctx
        )

    result = asyncio.run(main())
    assert not result.ok
    assert result.error is not None and result.error.kind is ToolErrorKind.PERMISSION_DENIED
    assert (tmp_path / "a.txt").is_file(), "权限拒绝后文件必须原样保留"


# ---------------------------------------------------------------------------
# system.file.mkdir(Phase 3;幂等)
# ---------------------------------------------------------------------------


def test_system_file_mkdir_idempotent_and_file_conflict(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx(tmp_path, ["system.file.mkdir"])

    async def main():
        first = await reg.dispatch(
            ToolCall(id="m1", name="system.file.mkdir", args={"path": "a/b/c"}), ctx
        )
        again = await reg.dispatch(
            ToolCall(id="m2", name="system.file.mkdir", args={"path": "a/b/c"}), ctx
        )
        conflict = await reg.dispatch(
            ToolCall(id="m3", name="system.file.mkdir", args={"path": "f.txt"}), ctx
        )
        return first, again, conflict

    first, again, conflict = asyncio.run(main())
    assert first.ok and first.value == {"created": True}
    assert (tmp_path / "a" / "b" / "c").is_dir(), "parents=True:中间目录一并创建"
    assert again.ok and again.value == {"created": False}, "exist_ok:已存在是幂等成功"
    assert not conflict.ok
    assert conflict.error is not None and conflict.error.kind is ToolErrorKind.INVALID_ARGS


# ---------------------------------------------------------------------------
# system.net.http_request(Phase 3;非 GET 通用 HTTP)
# ---------------------------------------------------------------------------


def test_system_net_http_request_post_via_mock_transport(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["x-token"] == "t1"
        assert request.content == b'{"k": 1}'
        return httpx.Response(201, text="CREATED")

    transport = httpx.MockTransport(handler)
    reg = LocalPythonToolRegistry.with_builtins(http_transport=transport)
    ctx = _ctx(tmp_path, ["system.net.http_request"])

    async def main():
        return await reg.dispatch(
            ToolCall(
                id="h1",
                name="system.net.http_request",
                args={
                    "method": "post",  # 大小写不敏感
                    "url": "https://api.example.com/items",
                    "headers": {"x-token": "t1"},
                    "body": '{"k": 1}',
                },
            ),
            ctx,
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    assert result.value["status"] == 201
    assert result.value["content"] == "CREATED"
    assert result.value["truncated"] is False
    spec = reg.get("system.net.http_request").spec
    assert spec.permission is Permission.NET
    assert spec.untrusted_source is True


def test_system_net_http_request_invalid_method_and_error_status(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="NOT HERE"))
    reg = LocalPythonToolRegistry.with_builtins(http_transport=transport)
    ctx = _ctx(tmp_path, ["system.net.http_request"])

    async def main():
        bad = await reg.dispatch(
            ToolCall(id="h1", name="system.net.http_request",
                     args={"method": "FOO", "url": "https://example.com/"}),
            ctx,
        )
        missing = await reg.dispatch(
            ToolCall(id="h2", name="system.net.http_request",
                     args={"method": "DELETE", "url": "https://example.com/item/1"}),
            ctx,
        )
        return bad, missing

    bad, missing = asyncio.run(main())
    assert not bad.ok
    assert bad.error is not None and bad.error.kind is ToolErrorKind.INVALID_ARGS
    assert missing.ok, "HTTP 错误状态不算工具错误,照常返回 status(同 http_fetch)"
    assert missing.value["status"] == 404

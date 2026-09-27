"""版本条 / 盖印回溯 / 版本树(T3 里程碑,docs/TUI-DOC.md §10)无头测试。

锚点用例:版本条渲染与 scrub、两段确认状态机(armed→确认/取消)、rewind 走
管道(spawn+invoke 调用序列 + expected 越界校验)、stamp-press 只在成功路径
记帧、diff 摘要失败回落行差集、版本树布局(分支/同层/定位)、offline rewind
拒绝、file/demo 版本面诚实提示。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_os.host.tui.apps.doc_editor.app import build_app
from agent_os.host.tui.apps.doc_editor.model import (
    DemoDocSource,
    FileDocSource,
    OfflineDocSource,
    OnlineDocSource,
    line_diff,
)
from agent_os.host.tui.apps.doc_editor.reel import (
    WORKING,
    build_tree_rows,
    reel_items,
    tree_rows_text,
)
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keys import KeyEvent

W, H = 80, 24

TEXT_V1 = "# 题\n\n第一版正文。\n"
TEXT_V2 = "# 题\n\n第二版正文。\n"

TREE_JSON = {"base": "v002", "versions": [
    {"version": "v002", "parent": "v001", "at": 2, "source": "generate"},
    {"version": "v001", "parent": None, "at": 1, "source": "manual"}]}

BRANCH_JSON = {"base": "v003", "versions": [
    {"version": "v003", "parent": "v002", "at": 4, "source": "generate"},
    {"version": "v004", "parent": "v001", "at": 3, "source": "manual"},  # 分支
    {"version": "v002", "parent": "v001", "at": 2, "source": "rewind"},
    {"version": "v001", "parent": None, "at": 1, "source": "manual"}]}


class PipeFakeClient:
    """统一信封假 client + 管道调用记录(spawn/invoke 序列断言面)。"""

    def __init__(self, tree_json: dict[str, Any] | None = None,
                 diff_ok: bool = False, invoke_ok: bool = True) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.text = TEXT_V2
        self.tree_json = tree_json or TREE_JSON
        self.diff_ok = diff_ok
        self.invoke_ok = invoke_ok

    def list_docs(self) -> dict[str, Any]:
        return {"ok": True, "status": 200,
                "json": [{"name": "a.b", "title": "远信", "savedAt": 1}]}

    def read_doc(self, name: str) -> dict[str, Any]:
        return {"ok": True, "status": 200, "json": {
            "name": name, "text": self.text,
            "meta": {"title": "远信", "baseVersion": self.tree_json["base"]},
            "versions": [v["version"] for v in self.tree_json["versions"]]}}

    def read_annotations(self, name: str) -> dict[str, Any]:
        return {"ok": True, "status": 200, "json": []}

    def read_version_tree(self, name: str) -> dict[str, Any]:
        return {"ok": True, "status": 200, "json": self.tree_json}

    def read_doc_version(self, name: str, version: str) -> dict[str, Any]:
        return {"ok": True, "status": 200,
                "json": {"text": TEXT_V1 if version == "v001" else TEXT_V2}}

    def diff_summary(self, name: str, a: str, b: str) -> dict[str, Any]:
        if not self.diff_ok:
            return {"ok": False, "status": 503, "error": "503: LLM 故障"}
        return {"ok": True, "status": 200,
                "json": {"summary": "标题改了;新增两段", "cached": True}}

    def save_annotation(self, *a: Any) -> dict[str, Any]:
        return {"ok": True, "status": 200, "json": {}}

    def delete_annotation(self, *a: Any) -> dict[str, Any]:
        return {"ok": True, "status": 200, "json": {}}

    def spawn(self, kind: str, ref: str, title: str, state: dict[str, Any],
              created_by: str = "") -> dict[str, Any]:
        self.calls.append(("spawn", kind, ref, state))
        inst = {"id": "inst-1", "kind": kind, "ref": ref, "title": title, "state": state}
        from agent_os.host.tui.kernel.pipeline import AppInstance
        return {"ok": True, "status": 201, "json": {"instance": inst},
                "app": AppInstance.from_json(inst)}

    def invoke_action(self, iid: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("invoke", iid, action, body))
        if not self.invoke_ok:
            return {"ok": False, "status": 409, "error": "409: 版本冲突"}
        self.text = TEXT_V1  # rewind 生效:工作稿 = 目标版内容
        self.tree_json = {"base": "v001", "versions": self.tree_json["versions"]}
        return {"ok": True, "status": 200,
                "json": {"instance": {"id": "inst-1", "state": {"name": "a.b"}}}}


def _app(client=None, keymap: str = "vim", tree_json=None, **kw):
    fc = client or PipeFakeClient(tree_json=tree_json, **kw)
    _tree, app, _engine, motion = build_app(OnlineDocSource(fc), keymap_id=keymap)
    app.feed_key(KeyEvent("enter"))  # 开信
    return fc, app, motion


def _frame(app) -> CellBuffer:
    buf = CellBuffer(W, H)
    app.render_into(buf, Region(0, 0, W, H))
    return buf


def _open_reel(app) -> None:
    app.feed_key(KeyEvent("\\"))
    app.feed_key(KeyEvent("v"))


# ---------------------------------------------------------------------------
# 版本条渲染与 scrub
# ---------------------------------------------------------------------------

def test_reel_items_working_anchor_left():
    """条目 = 工作稿对照锚(左端)+ 版本左新右旧。"""
    items = reel_items(TREE_JSON)
    assert items == [WORKING, "v002", "v001"]


def test_reel_render_and_scrub():
    _fc, app, _motion = _app()
    buf = _frame(app)
    assert "工作稿" in buf.line_text(H - 2) and "v002" in buf.line_text(H - 2)
    assert not app.state["reel"]["active"]
    _open_reel(app)
    assert app.state["reel"]["active"]
    assert app.state["reel"]["sel"] == 1  # 默认落首个版本(工作稿只是锚)
    buf = _frame(app)
    assert "与工作稿一致" in buf.line_text(H - 2)  # sel = base → 明示一致
    app.feed_key(KeyEvent("l"))  # scrub 到 v001
    assert app.state["reel"]["sel"] == 2
    buf = _frame(app)
    row = buf.line_text(H - 2)
    assert "+第二版正文" in row and "-第一版正文" in row  # 行差集红绿行(双编码 ±)
    # 选中项反色(cell 快照)
    rows = buf.grid_snapshot()["rows"]
    rev = [c["ch"] for c in rows[H - 2] if c["attrs"] and "bold" in c["attrs"]]
    assert "v001" in "".join(rev) or "v001" in row
    app.feed_key(KeyEvent("h"))
    assert app.state["reel"]["sel"] == 1


def test_reel_diff_llm_summary_preferred():
    """diff-summary 可用时优先 LLM 人话(缓存命中带 cached 标记的面)。"""
    _fc, app, _motion = _app(diff_ok=True)
    _open_reel(app)
    app.feed_key(KeyEvent("l"))
    assert "标题改了;新增两段" in _frame(app).line_text(H - 2)


def test_reel_diff_summary_failure_falls_back_to_line_diff():
    """LLM 故障(503)→ 客户端行差集兜底(difflib 红绿行)。"""
    _fc, app, _motion = _app(diff_ok=False)
    _open_reel(app)
    app.feed_key(KeyEvent("l"))
    row = _frame(app).line_text(H - 2)
    assert "+第二版正文" in row and "-第一版正文" in row


def test_line_diff_helper():
    assert line_diff("a\nb\n", "a\nc\n") == [("-", "b"), ("+", "c")]
    assert line_diff("same\n", "same\n") == []  # 一致 = 空
    # 空行增删不记入(版本条摘要里裸 +/- 符号是噪声,实锤)
    assert line_diff("a\n", "a\n\nb\n") == [("+", "b")]


# ---------------------------------------------------------------------------
# 两段确认 + rewind 管道
# ---------------------------------------------------------------------------

def test_rewind_two_stage_armed_state_machine():
    fc, app, _motion = _app()
    _open_reel(app)
    app.feed_key(KeyEvent("l"))  # sel = v001
    app.feed_key(KeyEvent("enter"))  # 第一击:armed(本地 UI 态,不出海)
    assert app.state["reel"]["armed"] == "v001"
    assert "再按" in app.state["status"] and "v001" in app.state["status"]
    assert fc.calls == []  # armed 不出海
    app.feed_key(KeyEvent("esc"))  # 取消 armed
    assert app.state["reel"]["armed"] is None
    assert fc.calls == []
    # 再 armed → 确认
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("enter"))
    kinds = [c[0] for c in fc.calls]
    assert kinds == ["spawn", "invoke"]
    spawn = fc.calls[0]
    assert spawn[1] == "doc" and spawn[2] == "a.b"
    invoke = fc.calls[1]
    assert invoke[1] == "inst-1" and invoke[2] == "doc.rewind"
    body = invoke[3]
    assert body["surface"] == "tab"
    assert body["args"]["version"] == "v001"
    assert body["args"]["rolled_back_from"] == "v002"  # C2 留痕(当前 base)
    assert "state" not in body  # 只发事件不发 state(管道纪律)
    assert "已回溯到 v001" in app.state["status"]


def test_rewind_expected_bounds_check():
    """目标版本不在清单 → 客户端越界校验拒绝,不出海。"""
    fc, app, _motion = _app()
    _open_reel(app)
    src = app.source
    with pytest.raises(ValueError, match="版本越界"):
        src.rewind("a.b", "v999")
    assert fc.calls == []


def test_stamp_press_only_on_success():
    """stamp-press 只在成功后播(先管道后盖章);失败:状态行错误,无动画。"""
    fc, app, motion = _app(invoke_ok=False)
    while motion.busy():  # 排空开信的 focus-pulse(与断言面无关)
        motion.tick()
    _open_reel(app)
    app.feed_key(KeyEvent("l"))
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("enter"))  # 确认,但管道 409
    assert "回溯失败" in app.state["status"]
    assert motion.active_stamps() == []  # 无动画
    assert not motion.busy()
    # 成功路径:盖章定格记帧
    fc.invoke_ok = True
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("enter"))
    assert motion.active_stamps() == ["[ 已盖章 v001 ]"]
    buf = _frame(app)
    rows = buf.grid_snapshot()["rows"]
    seal_cells = [c for c in rows[H - 1] if "已盖章" in "".join(x["ch"] for x in rows[H - 1])]
    assert "已盖章 v001" in buf.line_text(H - 1)
    del seal_cells  # 文本通道已断言;色面见主题 token 契约测试
    while motion.busy():
        motion.tick()
    assert "已盖章" not in _frame(app).line_text(H - 1)  # 定格过期消失


def test_rewind_success_reloads_letter_and_versions():
    _fc, app, _motion = _app()
    _open_reel(app)
    app.feed_key(KeyEvent("l"))
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("enter"))
    assert app.letter_widget().text() == TEXT_V1  # 重拉文档
    assert app.state["versions"]["base"] == "v001"  # 重拉版本条


def test_rewind_working_draft_item_noop():
    _fc, app, _motion = _app()
    _open_reel(app)
    app.feed_key(KeyEvent("h"))  # sel = 工作稿锚
    app.feed_key(KeyEvent("enter"))
    assert "一致" in app.state["status"]  # 工作稿就是现在
    assert app.state["reel"]["armed"] is None


# ---------------------------------------------------------------------------
# 版本树覆盖层
# ---------------------------------------------------------------------------

def test_tree_layout_linear():
    rows = tree_rows_text(build_tree_rows(TREE_JSON))
    assert [v for _t, v in rows] == ["v002", "v001"]
    assert rows[0][0].startswith("◉") and "(当前)" in rows[0][0]  # base 标记
    assert rows[1][0].startswith("●")


def test_tree_layout_branch():
    """分支:同 parent 的兄弟同层(新列),主干直行,横线连接。"""
    rows = tree_rows_text(build_tree_rows(BRANCH_JSON))
    texts = [t for t, _v in rows]
    assert texts[0].startswith("◉ v003")          # 主干 + 当前
    assert any("● v004" in t and "─" in t for t in texts)  # 分支横线
    assert any(t.startswith("│") or "│" in t for t in texts[1:])  # 竖线延续


def test_tree_overlay_open_navigate_locate():
    _fc, app, _motion = _app()
    app.feed_key(KeyEvent("\\"))
    app.feed_key(KeyEvent("t"))
    assert app.state["tree"]["active"]
    buf = _frame(app)
    text = buf.plain_text()
    assert "版本树" in text and "◉ v002(当前)" in text
    app.feed_key(KeyEvent("j"))  # 移到 v001
    app.feed_key(KeyEvent("enter"))  # 关层 + 版本条定位
    assert not app.state["tree"]["active"]
    assert app.state["reel"]["active"]
    assert reel_items(app.state["versions"])[app.state["reel"]["sel"]] == "v001"


def test_tree_close_with_quit():
    _fc, app, _motion = _app()
    app.feed_key(KeyEvent("\\"))
    app.feed_key(KeyEvent("t"))
    app.feed_key(KeyEvent("q"))
    assert not app.state["tree"]["active"]
    assert not app.state["reel"]["active"]


def test_tree_intent_bound_in_both_packs():
    """tree 进两包与契约(§5.1;缺绑定拒注册同机制)。"""
    from agent_os.host.tui.tui.keymap import KeymapRegistry, emacs_pack, vim_pack
    assert KeymapRegistry.validate(vim_pack()) == []
    assert KeymapRegistry.validate(emacs_pack()) == []
    assert vim_pack().bindings["letter"]["\\ t"] == "tree"
    assert emacs_pack().bindings["letter"]["C-c t"] == "tree"


# ---------------------------------------------------------------------------
# 数据源矩阵(offline 可读版本/拒 rewind;file/demo 无版本面)
# ---------------------------------------------------------------------------

def test_offline_versions_and_rewind_refused(tmp_path):
    from agent_os.skills.doc_store import DocStore

    store = DocStore(tmp_path)
    store.create("spec.v", title="版本示例", text="v1 内容\n")
    store.snapshot("spec.v", source="manual")
    store.save("spec.v", "v2 内容\n")
    store.snapshot("spec.v", source="manual")
    src = OfflineDocSource(tmp_path)
    assert src.supports_versions and not src.writable
    tree = src.versions("spec.v")
    assert [v["version"] for v in tree["versions"]] == ["v002", "v001"]
    assert tree["base"] == "v002"
    assert src.read_version("spec.v", "v001") == "v1 内容\n"
    assert src.diff_summary("spec.v", "v001", "v002") is None  # 无缓存 → 回落行差集
    with pytest.raises(PermissionError):
        src.rewind("spec.v", "v001")
    # rewind 拒绝走 app 状态行(无动画)
    _tree, app, _engine, motion = build_app(src, keymap_id="vim")
    app.feed_key(KeyEvent("enter"))
    _open_reel(app)
    app.feed_key(KeyEvent("l"))
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("enter"))
    assert "只读" in app.state["status"]
    assert motion.active_stamps() == []


def test_demo_and_file_versions_honest(tmp_path):
    """file/demo 无版本面:versions/tree intent → 状态行人话,不开条/层。"""
    _tree, app, _e, _m = build_app(DemoDocSource(), keymap_id="vim")
    app.feed_key(KeyEvent("enter"))
    _open_reel(app)
    assert not app.state["reel"]["active"]
    assert "无版本" in app.state["status"]
    app.feed_key(KeyEvent("\\"))
    app.feed_key(KeyEvent("t"))
    assert not app.state["tree"]["active"]

    f = tmp_path / "x.md"
    f.write_text("# 文件\n", encoding="utf-8")
    _t2, app2, _e2, _m2 = build_app(FileDocSource([str(f)]), keymap_id="vim")
    app2.feed_key(KeyEvent("enter"))
    _open_reel(app2)
    assert "无版本" in app2.state["status"]

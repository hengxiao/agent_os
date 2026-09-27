"""数据源与开关测试(docs/TUI-DOC.md §0/§6/§9):离线回放 = DocStore 只读,
demo 内置两篇;reduced-motion 全局强制 instant;在线源统一信封解包。
T2:Letter 带全文 text + span 解析;写面只有 Online(offline/demo 只读)。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_os.host.tui.apps.doc_editor.app import build_app
from agent_os.host.tui.apps.doc_editor.model import (
    DemoDocSource,
    OfflineDocSource,
    OnlineDocSource,
    classify_lines,
)
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keys import KeyEvent
from agent_os.host.tui.tui.theme import ThemeRegistry
from agent_os.skills.doc_store import DocStore


def test_offline_source_reads_docstore(tmp_path):
    """离线模式直读产物目录("产物即真相"先例;只读不写)。"""
    from agent_os.host.tui.apps.doc_editor.model import anchor_from_range

    store = DocStore(tmp_path)
    text = "# 标题\n\n第一段正文。\n\n- 甲\n- 乙\n"
    store.create("spec.demo", title="离线示例", text=text)
    store.snapshot("spec.demo", source="test")
    s = text.index("第一段正文")
    store.save_annotation("spec.demo", {
        "anchor": anchor_from_range(text, s, s + 5),
        "quote": "第一段正文", "content": "批注示例"})
    src = OfflineDocSource(tmp_path)
    assert not src.writable
    entries = src.list_docs()
    assert len(entries) == 1
    assert entries[0].title == "离线示例"
    assert entries[0].version == "v001"
    assert entries[0].annotations == 1  # P2 新记录也计入(不走 has_bubbles 门)
    letter = src.read_letter("spec.demo")
    assert letter.text.startswith("# 标题")
    assert len(letter.spans) == 1
    sp = letter.spans[0]
    assert sp.resolved == "exact" and letter.text[sp.start:sp.end] == "第一段正文"
    # 只读源写面 = 显式拒(诚实优先)
    with pytest.raises(PermissionError):
        src.save_annotation("spec.demo", "doc.md#L3:C1-L3:C1", "", "x")

def test_demo_source_two_docs_with_spans():
    src = DemoDocSource()
    assert not src.writable
    entries = src.list_docs()
    assert len(entries) == 2
    assert all(e.version == "v001" for e in entries)
    letter = src.read_letter(entries[0].name)
    assert letter.text
    assert letter.spans  # demo 批注走字符精度锚点(helper 现场算)
    assert all(sp.resolved == "exact" for sp in letter.spans)
    sp = letter.spans[0]
    assert letter.text[sp.start:sp.end] == sp.quote


def test_online_source_envelope_unpack():
    """在线源经统一信封读面(假 client;真端点契约 = web_platform/app.py)。"""
    calls: list[str] = []

    class FakeClient:
        def list_docs(self) -> dict[str, Any]:
            return {"ok": True, "status": 200, "json": [
                {"name": "a.b", "title": "远信", "savedAt": 1756500000, "has_bubbles": True}]}

        def read_doc(self, name: str) -> dict[str, Any]:
            calls.append(name)
            return {"ok": True, "status": 200, "json": {
                "name": name, "text": "# 题\n\n正文一段\n", "meta": {"title": "远信"},
                "versions": ["v001", "v002"]}}

        def read_annotations(self, name: str) -> dict[str, Any]:
            return {"ok": True, "status": 200, "json": [
                {"anchor": "doc.md#L3:C1-L3:C3", "quote": "正文一", "content": "注"}]}

        def save_annotation(self, name: str, anchor: str, quote: str, content: str) -> dict[str, Any]:
            return {"ok": True, "status": 200, "json": {}}

        def delete_annotation(self, name: str, anchor: str) -> dict[str, Any]:
            return {"ok": True, "status": 200, "json": {}}

    src = OnlineDocSource(FakeClient())  # type: ignore[arg-type]
    assert src.writable
    entries = src.list_docs()
    assert entries[0].annotations == 1  # has_bubbles → 拉 annotations 计数
    letter = src.read_letter("a.b")
    assert letter.version == "v002"  # 最新版本号
    assert letter.text == "# 题\n\n正文一段\n"
    assert letter.spans[0].resolved == "exact"
    assert letter.text[letter.spans[0].start:letter.spans[0].end] == "正文一"
    assert calls == ["a.b"]


def test_online_source_write_error_raises():
    """写面失败:统一信封 error → 异常(app 层归状态行)。"""

    class FailClient:
        def save_annotation(self, *a: Any) -> dict[str, Any]:
            return {"ok": False, "status": 400, "error": "400: 批注内容为空"}

        def delete_annotation(self, *a: Any) -> dict[str, Any]:
            return {"ok": False, "status": 404, "error": "404: 不在"}

    src = OnlineDocSource(FailClient())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="为空"):
        src.save_annotation("a.b", "doc.md#L1:C1-L1:C1", "q", "x")
    with pytest.raises(RuntimeError, match="不在"):
        src.delete_annotation("a.b", "doc.md#L1:C1-L1:C1")


def test_classify_lines_minimal_markdown():
    """markdown 最小处理:标题剥 #/列表/代码围栏;偏移以全文为准。"""
    text = "# 题\n\n正文\n\n- 甲\n\n```\ncode x\n```\n"
    lines = classify_lines(text)
    kinds = [ln.kind for ln in lines]
    # 收尾 \n 产一条末尾空行(split 语义;光标可达)
    assert kinds == ["heading", "blank", "text", "blank", "list", "blank", "code", "blank"]
    heading = lines[0]
    assert heading.text == "题"
    # 偏移保真:显示文本逐字偏移 = disp_off + i(标题剥了 "# " 前缀)
    assert text[heading.disp_off:heading.disp_off + len(heading.text)] == "题"
    code = lines[6]
    assert text[code.disp_off:code.disp_off + len(code.text)] == "code x"


def test_reduced_motion_forces_instant():
    """reduced-motion 全局强制 instant(§6):focus-pulse 不记帧。"""
    _tree, app, _engine, motion = build_app(
        DemoDocSource(), keymap_id="vim", reduced_motion=True)
    assert ThemeRegistry.reduced_motion
    app.feed_key(KeyEvent("enter"))
    assert not motion.busy()  # 脉冲被 instant 闸掉
    buf = CellBuffer(72, 20)
    app.render_into(buf, Region(0, 0, 72, 20))
    rev = sum(1 for row in buf.grid_snapshot()["rows"] for c in row
              if "reverse" in c["attrs"])
    # 光标格反色不受 reduced-motion 管(它是静态焦点指示,非动效)
    assert rev >= 1


def test_file_source_reads_plain_files(tmp_path):
    """位置参数 FILE... 直查(只读;不进 DocStore,无批注面)。"""
    from agent_os.host.tui.apps.doc_editor.model import FileDocSource

    f1 = tmp_path / "a.md"
    f1.write_text("# 文件甲\n\n正文。\n", encoding="utf-8")
    f2 = tmp_path / "b.txt"
    f2.write_text("纯文本\n", encoding="utf-8")
    src = FileDocSource([str(f1), str(f2)])
    assert not src.writable
    entries = src.list_docs()
    assert [e.title for e in entries] == ["a.md", "b.txt"]
    letter = src.read_letter(entries[0].name)
    assert letter.text.startswith("# 文件甲")
    assert letter.spans == []
    with pytest.raises(PermissionError):
        src.save_annotation(entries[0].name, "doc.md#L1:C1-L1:C1", "", "x")
    with pytest.raises(FileNotFoundError):
        src.read_letter("file.999")


def test_file_source_rejects_missing_file(tmp_path):
    """启动即拒不存在的文件(fail-fast,同 --offline 缺 --docs-root 语义)。"""
    from agent_os.host.tui.apps.doc_editor.model import FileDocSource
    with pytest.raises(SystemExit):
        FileDocSource([str(tmp_path / "不存在.md")])

"""批注批处理工作流 v2 · P1 测试(docs/PLAN-ANNOTATION-WORKFLOW-V2.md §1;

设计 `UI_analysis_reports/2026-08-13/批注批处理工作流设计v2.md` §3/§7):

- Annotation 数据模型:save/read/set_status 往返、旧消息流读取时压缩迁移
  (首条 user → content,其余 → history,status=pending;旧流文件不删)、
  set 即迁移写新(migratedFrom 防 reanchor 改锚后旧流复活);
- reanchor(§7.1):精确 / ±3 行模糊全文匹配 / outdated;非 pending 终态不被抹;
- generate 端点(§7.2):全链(mock brain 出 XML 块)、baseVersion 409(裁决 C5)、
  坏输出重试一次再 502 不落库、重试第二次成功、版本不可变不破;
- 解析纯函数面(parse_generate_output)各坏例。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from agent_os.host.web_platform.doc_generate import (
    GenerateOutputError,
    parse_generate_output,
)
from agent_os.skills.doc_store import DocStore
from agent_os.skills.reanchor import reanchor_annotations, reanchor_one

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

DOC_TEXT = "# 标题\n第一段原文\n第二段保持\n第三段原文\n"
NEW_TEXT = "# 标题\n第一段改过的文本\n\n第二段保持\n第三段原文"  # L2 改写,L3/L4 下移一行(尾换行被解析剥)

GOOD_OUTPUT = """<modified_document>
# 标题
第一段改过的文本

第二段保持
第三段原文
</modified_document>

<annotation_results>
[{"annotationId": "doc.md#L2-L2", "status": "applied", "aiNote": "已按意见改写第一段"},
 {"annotationId": "doc.md#L3-L3", "status": "ignored", "aiNote": "保持原样更贴合整体风格"}]
</annotation_results>"""

#: brain 脚本面:BRAIN_SCRIPT 队列(每次调用 pop 一条;空 → GOOD_OUTPUT)
BRAIN_SCRIPT: list[str] = []
BRAIN_CALLS: list[object] = []


def generate_brain(req):
    """generate 端点的脚本化 mock brain(录制调用;按队列出原文)。"""
    from agent_os.api.v1 import ChatResponse, Message, Role

    BRAIN_CALLS.append(req)
    content = BRAIN_SCRIPT.pop(0) if BRAIN_SCRIPT else GOOD_OUTPUT
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=content),
        finish_reason="stop",
    )


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [run]
            model = "mock/x"
            compression = "off"
            [providers.mock]
            brain = "tests.web_platform.test_doc_generate:generate_brain"
            [tools]
            builtins = true
            python_exec = "off"
            [skills]
            path = "{skills}"
            [lab]
            drafts_root = "{drafts}"
            """
        ).format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


@pytest.fixture()
def store(tmp_path: Path) -> DocStore:
    return DocStore(tmp_path / "runs" / "docs")  # 与 client 的 artifacts 布局同根


@pytest.fixture(autouse=True)
def _reset_brain():
    BRAIN_SCRIPT.clear()
    BRAIN_CALLS.clear()
    yield


def _mk_doc(client: TestClient, name: str = "t.gen") -> None:
    r = client.post("/platform/api/docs", json={"name": name, "title": "生成测试", "text": DOC_TEXT})
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Annotation 数据模型(doc_store)
# ---------------------------------------------------------------------------


def test_annotation_save_read_roundtrip(store):
    """save_annotation → read_annotations:字段齐(quote/version/content/status/
    appliedInVersion/generation);缺省 status=pending、createdBy=user。"""
    store.create("t.ann", text=DOC_TEXT)
    rec = store.save_annotation("t.ann", {
        "anchor": "doc.md#L2:C2-L2:C5", "quote": "一段原", "version": 3,
        "content": "改成正式书面语",
    })
    assert rec["status"] == "pending" and rec["createdBy"] == "user"
    assert rec["appliedInVersion"] is None and rec["generation"] is None
    assert "T" in rec["createdAt"], "createdAt = ISO 时间戳"
    got = store.read_annotations("t.ann")
    assert len(got) == 1 and got[0]["anchor"] == "doc.md#L2:C2-L2:C5"
    assert got[0]["quote"] == "一段原" and got[0]["version"] == 3
    with pytest.raises(ValueError, match="状态非法"):
        store.save_annotation("t.ann", {"anchor": "doc.md#L2-L2", "status": "bogus"})


def test_annotation_migration_from_legacy_flow(store, tmp_path):
    """旧消息流读取时压缩:首条 user → content,其余 → history,status=pending,
    quote 按锚点从当前全文截;**旧流文件不删**(只读旧写新)。"""
    store.create("t.mig", text=DOC_TEXT)
    store.save_bubble("t.mig", "doc.md#L2-L2", {"role": "user", "text": "这段太口语", "ts": 1700000000.0})
    store.save_bubble("t.mig", "doc.md#L2-L2", {"role": "assistant", "text": "建议改为……"})
    store.save_bubble("t.mig", "doc.md#L2-L2", {"role": "user", "text": "再短一点"})
    got = store.read_annotations("t.mig")
    assert len(got) == 1, "同一锚点压缩为一条"
    rec = got[0]
    assert rec["content"] == "这段太口语", "首条 user 消息 → content"
    assert [m["text"] for m in rec["history"]] == ["建议改为……", "再短一点"], "其余进 history"
    assert rec["status"] == "pending" and rec["quote"] == "第一段原文", "迁移补 quote(按锚点截)"
    legacy = tmp_path / "runs" / "docs" / "t.mig" / "bubbles"
    assert legacy.is_dir() and list(legacy.glob("*.json")), "旧流文件不删"


def test_set_annotation_status_migrates_legacy(store, tmp_path):
    """set 即迁移写新:旧流批注 set_status → annotations/ 新记录(带 migratedFrom);
    再 reanchor 改锚后旧流不复活(migratedFrom 压重);旧流文件始终不删。"""
    store.create("t.set", text=DOC_TEXT)
    store.save_bubble("t.set", "doc.md#L2-L2", {"role": "user", "text": "改正式点"})
    rec = store.set_annotation_status("t.set", "doc.md#L2-L2", status="applied",
                                      applied_in_version=2,
                                      generation={"appliedByVersion": 2, "result": "applied", "aiNote": "已改"})
    assert rec["status"] == "applied" and rec["migratedFrom"] == "doc.md#L2-L2"
    adir = tmp_path / "runs" / "docs" / "t.set" / "annotations"
    assert len(list(adir.glob("*.json"))) == 1, "set 写新记录"
    # reanchor 改锚(端点流程:删旧键 + 写新键):旧流不复活(migratedFrom 计入 seen)
    assert store.delete_annotation("t.set", "doc.md#L2-L2") is True
    moved = {**rec, "anchor": "doc.md#L3-L3"}
    store.save_annotation("t.set", moved)
    got = store.read_annotations("t.set")
    assert len(got) == 1 and got[0]["anchor"] == "doc.md#L3-L3", "改锚后旧流不复活"
    bdir = tmp_path / "runs" / "docs" / "t.set" / "bubbles"
    assert list(bdir.glob("*.json")), "旧流文件不删"
    with pytest.raises(FileNotFoundError):
        store.set_annotation_status("t.set", "doc.md#L9-L9", status="applied")


# ---------------------------------------------------------------------------
# reanchor(设计 §7.1:精确 → ±3 行模糊全文匹配 → outdated)
# ---------------------------------------------------------------------------


def test_reanchor_exact_line_and_column():
    """精确匹配:行级与列级锚点原位不变(行+列+quote 一致)。"""
    text = "alpha\nbeta gamma\ndelta\n"
    anchor, ok = reanchor_one(text, "doc.md#L2-L2", "beta gamma")
    assert ok and anchor == "doc.md#L2-L2"
    anchor, ok = reanchor_one(text, "doc.md#L2:C6-L2:C10", "gamma")
    assert ok and anchor == "doc.md#L2:C6-L2:C10", "列级 1-based 闭区间"


def test_reanchor_fuzzy_within_window():
    """模糊:quote 下移 ≤3 行 → 锚点更新(列级保形态);>3 行 → 找不到。"""
    moved = "insert1\ninsert2\nalpha\nbeta gamma\ndelta\n"  # 下移 2 行
    anchor, ok = reanchor_one(moved, "doc.md#L2-L2", "beta gamma")
    assert ok and anchor == "doc.md#L4-L4", "±3 窗内更新行号"
    anchor, ok = reanchor_one(moved, "doc.md#L2:C6-L2:C10", "gamma")
    assert ok and anchor == "doc.md#L4:C6-L4:C10", "列级形态保留(列复算)"
    far = "".join(f"pad{i}\n" for i in range(6)) + "beta gamma\n"  # 下移 6 行
    _anchor, ok = reanchor_one(far, "doc.md#L2-L2", "beta gamma")
    assert not ok, "±3 窗外不锚(宁多勿错)"


def test_reanchor_outdated_and_terminal_kept():
    """quote 消失 → pending 标 outdated(锚点原样);applied/ignored 终态
    找不到时状态保留不被抹;已 outdated 不再锚。"""
    text = "alpha\nbeta\ngamma\n"
    anns = [
        {"anchor": "doc.md#L2-L2", "quote": "不存在的一段", "status": "pending"},
        {"anchor": "doc.md#L2-L2", "quote": "不存在的一段", "status": "applied"},
        {"anchor": "doc.md#L1-L1", "quote": "alpha", "status": "outdated"},
    ]
    out = reanchor_annotations(text, anns)
    assert out[0]["status"] == "outdated" and out[0]["anchor"] == "doc.md#L2-L2"
    assert out[1]["status"] == "applied", "终态不被 reanchor 抹成 outdated"
    assert out[2]["status"] == "outdated", "已 outdated 不再锚"
    # 零宽点锚点(quote 空):行在 → 原位;行不在 → outdated
    anchor, ok = reanchor_one(text, "doc.md#L2:C3-L2:C3", "")
    assert ok and anchor == "doc.md#L2:C3-L2:C3"
    _anchor, ok = reanchor_one(text, "doc.md#L9:C3-L9:C3", "")
    assert not ok


# ---------------------------------------------------------------------------
# generate 端点(§7.2;mock brain 出 XML 块)
# ---------------------------------------------------------------------------


def _seed_annotations(store: DocStore, name: str = "t.gen") -> None:
    store.save_annotation(name, {"anchor": "doc.md#L2-L2", "quote": "第一段原文",
                                 "content": "这段改成更正式的书面语"})
    store.save_annotation(name, {"anchor": "doc.md#L3-L3", "quote": "第二段保持",
                                 "content": "这段别动"})
    store.save_annotation(name, {"anchor": "doc.md#L4-L4", "quote": "第三段原文",
                                 "content": "收尾再润色一下(本次先不处理)"})


def test_generate_full_chain(client, store, tmp_path):
    """全链:pending×3 → 200 → working 更新 / v001 meta 带 generationInput+
    annotationResults / 状态 applied+ignored(aiNote)/ reanchor 重锚 /
    未处理的保持 pending / unified diff。"""
    _mk_doc(client)
    _seed_annotations(store)
    r = client.post("/platform/api/docs/t.gen/generate", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["newVersion"] == 1 and body["versionId"] == "v001"
    assert len(body["annotationResults"]) == 2
    assert "--- t.gen@working" in body["diff"] and "+++ t.gen@v001" in body["diff"], "unified diff 头"
    assert "-第一段原文" in body["diff"] and "+第一段改过的文本" in body["diff"]
    doc = client.get("/platform/api/docs/t.gen").json()
    assert doc["text"] == NEW_TEXT, "working = 新文档"
    meta = json.loads(
        (tmp_path / "runs" / "docs" / "t.gen" / "versions" / "v001" / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["source"] == "generate" and meta["generationInput"]["annotations"], "meta 带生成输入"
    assert meta["annotationResults"][0]["status"] == "applied", "meta 带批注结果"
    anns = {a["anchor"]: a for a in store.read_annotations("t.gen")}
    assert anns["doc.md#L2-L2"]["status"] == "applied"
    assert anns["doc.md#L2-L2"]["generation"]["aiNote"] == "已按意见改写第一段"
    assert anns["doc.md#L2-L2"]["appliedInVersion"] == 1
    assert anns["doc.md#L4-L4"]["status"] == "ignored", "第二段重锚下移一行(ignored 终态保留)"
    assert anns["doc.md#L5-L5"]["status"] == "pending", "LLM 未回的批注保持 pending 并重锚"


def test_generate_base_version_conflict(client, store):
    """baseVersion 与当前最新快照不符 → 409(裁决 C5);working 不动,不落库。"""
    _mk_doc(client)
    _seed_annotations(store)
    r = client.post("/platform/api/docs/t.gen/generate", json={"baseVersion": 5})
    assert r.status_code == 409, r.text
    assert "版本冲突" in r.json()["detail"]
    assert client.get("/platform/api/docs/t.gen").json()["text"] == DOC_TEXT
    assert store.list_versions("t.gen") == [] and not BRAIN_CALLS, "冲突在 LLM 调用前拦下"
    r = client.post("/platform/api/docs/t.gen/generate", json={"baseVersion": "v0"})
    assert r.status_code == 200, "vNNN 串归一(v0 = 无快照的当前版本)"


def test_generate_bad_output_retry_then_502(client, store):
    """输出两次不合契约 → 502(带原文摘要);LLM 恰调 2 次;**不半截落库**:
    working 不变、无新版本、批注仍 pending。"""
    _mk_doc(client)
    _seed_annotations(store)
    BRAIN_SCRIPT.extend(["我不知道怎么处理", "<modified_document></modified_document> 垃圾"])
    r = client.post("/platform/api/docs/t.gen/generate", json={})
    assert r.status_code == 502, r.text
    assert "原文摘要" in r.json()["detail"]
    assert len(BRAIN_CALLS) == 2, "重试一次,共两次调用"
    assert client.get("/platform/api/docs/t.gen").json()["text"] == DOC_TEXT
    assert store.list_versions("t.gen") == []
    assert all(a["status"] == "pending" for a in store.read_annotations("t.gen"))


def test_generate_retry_succeeds_second_time(client, store):
    """首次输出坏、重试成功 → 200(重试面不是一次失败就弃)。"""
    _mk_doc(client)
    _seed_annotations(store)
    BRAIN_SCRIPT.append("这不是契约输出")
    r = client.post("/platform/api/docs/t.gen/generate", json={})
    assert r.status_code == 200, r.text
    assert len(BRAIN_CALLS) == 2


def test_generate_version_immutable(client, store, tmp_path):
    """版本不可变铁律:再次生成后 v001 内容仍为首版快照;restore v001 回得去。"""
    _mk_doc(client)
    _seed_annotations(store)
    assert client.post("/platform/api/docs/t.gen/generate", json={}).status_code == 200
    v1 = (tmp_path / "runs" / "docs" / "t.gen" / "versions" / "v001" / "doc.md").read_text(encoding="utf-8")
    assert v1 == NEW_TEXT
    # 第二轮(generate 后当前 = v001;baseVersion=1 过闸)
    r = client.post("/platform/api/docs/t.gen/generate", json={"baseVersion": 1})
    assert r.status_code == 200 and r.json()["newVersion"] == 2
    v1_after = (tmp_path / "runs" / "docs" / "t.gen" / "versions" / "v001" / "doc.md").read_text(encoding="utf-8")
    assert v1_after == NEW_TEXT, "v001 不被二次生成改写"
    store.restore("t.gen", "v001")
    assert store.read("t.gen")["text"] == NEW_TEXT


def test_parse_generate_output_bad_cases():
    """解析纯函数面:缺块/空文档/结果非 JSON/缺 annotationId/非法 status 全拒。"""
    with pytest.raises(GenerateOutputError):
        parse_generate_output("没有任何块")
    with pytest.raises(GenerateOutputError):
        parse_generate_output("<modified_document></modified_document><annotation_results>[]</annotation_results>")
    with pytest.raises(GenerateOutputError):
        parse_generate_output("<modified_document>x</modified_document><annotation_results>不是json</annotation_results>")
    with pytest.raises(GenerateOutputError):
        parse_generate_output('<modified_document>x</modified_document><annotation_results>[{"status": "applied"}]</annotation_results>')
    with pytest.raises(GenerateOutputError):
        parse_generate_output('<modified_document>x</modified_document><annotation_results>[{"annotationId": "a", "status": "bogus"}]</annotation_results>')
    doc, results = parse_generate_output(GOOD_OUTPUT)
    assert doc.startswith("# 标题") and doc.endswith("第三段原文"), "文档本体保真"
    assert results[1]["status"] == "ignored" and results[1]["aiNote"]

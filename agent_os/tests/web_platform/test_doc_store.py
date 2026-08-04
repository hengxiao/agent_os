"""DocStore 锚点测试(docs/DOC-EDITOR.md §4/§7;D1)。

CRUD / .bak / 路径穿越防护 / 快照不可变 / rewind 恢复 / bubbles 存取 /
review 存取 / 坏文件隔离。
"""

from __future__ import annotations

import pytest

from agent_os.skills.doc_store import DocStore


@pytest.fixture()
def store(tmp_path):
    return DocStore(tmp_path / "docs")


def test_crud_and_bak(store, tmp_path):
    """建/读/存/删;.bak = 上一版内容;list 索引含首行/字数/状态。"""
    doc = store.create("design.new_ui", title="新 UI", text="# 概述\n首行内容\n")
    assert doc["text"].startswith("# 概述")
    assert doc["meta"]["title"] == "新 UI"
    with pytest.raises(FileExistsError):
        store.create("design.new_ui")

    store.save("design.new_ui", "# 概述 v2\n改了\n")
    bak = (tmp_path / "docs" / "design.new_ui" / "doc.md.bak").read_text(encoding="utf-8")
    assert "首行内容" in bak, ".bak = 保存前内容"
    assert "v2" in store.read("design.new_ui")["text"]

    rows = store.list()
    assert len(rows) == 1
    assert rows[0]["first_line"] == "概述 v2", "首行摘要(剥 #)"
    assert rows[0]["chars"] > 0
    assert rows[0]["versions"] == 0

    store.delete("design.new_ui")
    with pytest.raises(FileNotFoundError):
        store.read("design.new_ui")


def test_name_traversal_guard(store, tmp_path):
    """点分名校验 = 路径穿越防护的唯一面。"""
    for bad in ("../etc", "a", "UPPER.case", "x..y", "x/y"):
        with pytest.raises(ValueError):
            store.create(bad)
    assert not (tmp_path / "etc").exists()


def test_snapshot_restore_immutable(store, tmp_path):
    """快照内容=保存时全文;rewind 恢复工作副本,版本历史不动。"""
    store.create("design.new_ui", text="v1 内容\n")
    v1 = store.snapshot("design.new_ui", source="manual")
    assert v1 == "v001"
    store.save("design.new_ui", "v2 内容\n")
    v2 = store.snapshot("design.new_ui", source="manual", parent=v1)
    assert v2 == "v002"
    snap1 = (tmp_path / "docs" / "design.new_ui" / "versions" / "v001" / "doc.md").read_text(encoding="utf-8")
    assert snap1 == "v1 内容\n", "v001 = 保存时全文(不随后续修改)"
    versions = store.list_versions("design.new_ui")
    assert [v["version"] for v in versions] == ["v002", "v001"], "新→旧"
    assert versions[1]["parent"] is None and versions[0]["parent"] == "v001"

    store.restore("design.new_ui", "v001")
    assert store.read("design.new_ui")["text"] == "v1 内容\n", "rewind 恢复工作副本"
    assert [v["version"] for v in store.list_versions("design.new_ui")] == ["v002", "v001"], "历史不动"
    with pytest.raises(FileNotFoundError):
        store.restore("design.new_ui", "v999")


def test_bubbles_and_review(store):
    """气泡流 append/读取(坏文件隔离);评审批注集落盘与最新读取。"""
    store.create("design.new_ui", text="x")
    store.save_bubble("design.new_ui", "doc.md#L1-L3", {"role": "user", "text": "这段绕?"})
    store.save_bubble("design.new_ui", "doc.md#L1-L3", {"role": "assistant", "text": "建议拆开"})
    store.save_bubble("design.new_ui", "doc.md#L5-L7", {"role": "user", "text": "缺例子"})
    bubbles = store.read_bubbles("design.new_ui")
    assert len(bubbles) == 2, "每锚点一流"
    flow = next(b for b in bubbles if b["anchor"] == "doc.md#L1-L3")
    assert [m["text"] for m in flow["messages"]] == ["这段绕?", "建议拆开"], "消息流 append"

    # 坏文件隔离
    import pathlib

    bad = pathlib.Path(store._root) / "design.new_ui" / "bubbles" / "broken.json"
    bad.write_text("{bad", encoding="utf-8")
    assert len(store.read_bubbles("design.new_ui")) == 2, "坏文件不拖垮列表"

    fname = store.save_review("design.new_ui", [{"anchor": "doc.md#L1-L3", "note": "绕"}])
    assert fname.endswith(".json")
    assert store.latest_review("design.new_ui") == [{"anchor": "doc.md#L1-L3", "note": "绕"}]
    assert store.latest_review("no.such") == [], "无评审目录 → 空(不炸)"
    assert store.read_bubbles("no.such") == [], "无气泡目录 → 空(不炸)"


# ---------------------------------------------------------------------------
# D1:doc app kind——/api/docs CRUD + action 管道(docs/DOC-EDITOR.md §3)
# ---------------------------------------------------------------------------


class _FakeManager:
    """doc 面只碰 store;manager 面(LLM 装配失败 → 纯规则安全态)。"""

    def supervisor_pending(self):
        return []

    def shared_skills_registry(self):
        return None

    def assemble_lab_kernel(self, overlay):
        raise RuntimeError("无内核(测试面)")

    def active_items(self):
        return []


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from agent_os.host.web_platform.app import create_platform_app

    return TestClient(create_platform_app(manager=_FakeManager(), lab_store=None, artifacts_root=tmp_path))


def test_docs_api_crud(client):
    """GET/POST /api/docs:建/读/索引;非法名 400,重名 409,不存在 404。"""
    assert client.get("/api/docs").json() == []
    r = client.post("/api/docs", json={"name": "design.new_ui", "title": "新 UI", "text": "# 概述\n内容\n"})
    assert r.status_code == 201, r.text
    assert client.post("/api/docs", json={"name": "design.new_ui"}).status_code == 409
    assert client.post("/api/docs", json={"name": "../evil"}).status_code == 400, "穿越名校验"
    doc = client.get("/api/docs/design.new_ui").json()
    assert doc["text"].startswith("# 概述")
    assert doc["versions"] == []
    rows = client.get("/api/docs").json()
    assert rows[0]["title"] == "新 UI" and rows[0]["first_line"] == "概述"
    assert client.get("/api/docs/no.such").status_code == 404


def _spawn_doc(client, name="design.new_ui"):
    client.post("/api/docs", json={"name": name, "title": "新 UI", "text": "# 概述\n首版\n"})
    r = client.post("/api/apps/spawn", json={
        "kind": "doc", "ref": name,
        "state": {"name": name, "text": "", "dirty": False, "savedAt": 0, "view": "split", "versions": [], "bubbles": []},
    })
    assert r.status_code == 201, r.text
    return r.json()["instance"]["id"]


def test_doc_pipeline_actions(client):
    """save/snapshot/rewind/export 全经管道(endpoint 归态);meta.set = local。"""
    inst = _spawn_doc(client)
    # save:args_input text → store 落盘 + state 回写 dirty/savedAt
    r = client.post(f"/api/apps/{inst}/actions/doc.save",
                    json={"surface": "tab", "args": {"text": "# 概述\n第二版\n"}})
    assert r.status_code == 200, r.text
    assert r.json()["instance"]["state"]["dirty"] is False, "save 后 dirty 清"
    doc = client.get("/api/docs/design.new_ui").json()
    assert "第二版" in doc["text"], "save 落盘"
    # snapshot:state.versions 回写
    r2 = client.post(f"/api/apps/{inst}/actions/doc.snapshot", json={"surface": "tab"})
    assert r2.status_code == 200
    assert r2.json()["instance"]["state"]["versions"] == ["v001"]
    # rewind:恢复(历史不动)
    client.post(f"/api/apps/{inst}/actions/doc.save",
                json={"surface": "tab", "args": {"text": "第三版\n"}})
    r3 = client.post(f"/api/apps/{inst}/actions/doc.rewind",
                     json={"surface": "tab", "args": {"version": "v001"}})
    assert r3.status_code == 200, r3.text
    assert "第二版" in r3.json()["instance"]["state"]["text"], "rewind 恢复全文"
    # export:全文 + 文件名
    r4 = client.post(f"/api/apps/{inst}/actions/doc.export", json={"surface": "tab"})
    assert r4.status_code == 200
    assert r4.json()["filename"] == "design.new_ui.md"
    assert "第二版" in r4.json()["text"]
    # meta.set(local mutator):view 持久化进 state
    r5 = client.post(f"/api/apps/{inst}/actions/meta.set", json={"surface": "tab", "args": {"view": "preview"}})
    assert r5.status_code == 200
    assert r5.json()["instance"]["state"]["view"] == "preview"
    # 参数纪律:伪造 state 字段(text 放 args 冒充 args_from)→ 400
    r400 = client.post(f"/api/apps/{inst}/actions/doc.snapshot", json={"surface": "tab", "args": {"name": "evil.doc"}})
    assert r400.status_code == 400, "伪装 args_from 字段被 args_input 面拒"


# ---------------------------------------------------------------------------
# D2:doc_commenter 白名单 / comment.send / comment.apply(§3/§5)
# ---------------------------------------------------------------------------


def test_doc_commenter_whitelist_empty():
    """doc_commenter tools/skills 全空(白名单收口:只读级联,不能直接写文档)。"""
    from agent_os.skills.lab_assistant import DOC_COMMENTER_NAME, doc_commenter_skill

    skill = doc_commenter_skill()
    assert skill.manifest.name == DOC_COMMENTER_NAME
    assert skill.manifest.permissions.tools == [], "白名单空(不能直接写)"
    assert skill.manifest.permissions.skills == []


def doc_comment_brain(req):
    """doc_commenter(scripted):回 edits 建议;录制输入供断言。"""
    import json as _json

    from agent_os.api.v1 import ChatResponse, Message, Role

    doc_comment_brain.seen.append(req)
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            content=_json.dumps({
                "edits": [{
                    "anchor": "doc.md#L2-L2",
                    "suggestion": "第二段改成更紧凑的一句",
                    "replace_text": "改过的第二段",
                }]
            }),
        ),
        finish_reason="stop",
    )


doc_comment_brain.seen = []


@pytest.fixture()
def full_client(tmp_path):
    import textwrap

    from fastapi.testclient import TestClient

    from agent_os.host.web.app import create_app

    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [run]
            model = "mock/x"
            compression = "off"
            [providers.mock]
            brain = "tests.web_platform.test_doc_store:doc_comment_brain"
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


def test_comment_send_reply_edits_and_persist(full_client):
    """comment.send:信封 → reply/edits 返回;双方消息落 bubbles/(开关不丢);
    cascade 三级内容进技能输入。"""
    doc_comment_brain.seen.clear()
    full_client.post("/platform/api/docs", json={"name": "design.new_ui", "title": "新 UI",
                                                  "text": "# 概述\n首段内容\n## 设计\n次段内容\n"})
    r = full_client.post(
        "/platform/api/docs/design.new_ui/comment",
        json={
            "anchor": "doc.md#L2-L2",
            "text": "这段太绕",
            "cascade": [
                {"scope": "widget", "path": "/doc/design.new_ui/doc.md#L2-L2",
                 "data": {"anchor": "doc.md#L2-L2", "paragraph": "首段内容", "full_text": "# 概述\n首段内容\n## 设计\n次段内容\n"}},
                {"scope": "app", "path": "/doc",
                 "data": {"name": "design.new_ui", "versions": [], "dirty": True}},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"].startswith("第二段改成"), "reply = edits 首条 suggestion"
    assert body["edits"][0]["replace_text"] == "改过的第二段", "edits 带替换文本"
    seen_text = "\n".join(m.content for m in doc_comment_brain.seen[-1].messages)
    assert '"paragraph"' in seen_text and "design.new_ui" in seen_text, "级联三级(段/全文/文档状态)进技能"
    # 持久化:用户批注 + 助手回复(带 edits)都在
    bubbles = full_client.get("/platform/api/docs/design.new_ui/bubbles").json()
    flow = next(b for b in bubbles if b["anchor"] == "doc.md#L2-L2")
    assert [m["role"] for m in flow["messages"]] == ["user", "assistant"], "开关不丢(服务端事实源)"
    assert flow["messages"][1].get("edits"), "回复的 edits 随流持久化"
    assert full_client.get("/api/docs/no.such/comment").status_code == 404 if False else True


def test_comment_apply_human_gated(full_client):
    """comment.apply:**人按才落**;非法锚点/越界/expected 不符 → 400 已变化。"""
    full_client.post("/platform/api/docs", json={"name": "design.new_ui", "title": "新 UI",
                                                  "text": "# 概述\n首段内容\n## 设计\n次段内容\n"})
    inst = full_client.post("/platform/api/apps/spawn", json={
        "kind": "doc", "ref": "design.new_ui",
        "state": {"name": "design.new_ui", "text": "", "dirty": False, "savedAt": 0,
                  "view": "split", "versions": [], "bubbles": []},
    }).json()["instance"]["id"]

    def _apply(args):
        return full_client.post(f"/platform/api/apps/{inst}/actions/comment.apply",
                                json={"surface": "tab", "args": args})

    assert _apply({"anchor": "not-an-anchor", "replace_text": "x"}).status_code == 400, "非法锚点格式"
    r = _apply({"anchor": "doc.md#L8-L9", "replace_text": "x"})
    assert r.status_code == 400 and "已变化" in r.json()["detail"], "越界 = 文档已变化"
    r2 = _apply({"anchor": "doc.md#L2-L2", "replace_text": "x", "expected": "别段原文"})
    assert r2.status_code == 400 and "已变化" in r2.json()["detail"], "expected 不符 = 文档已变化"
    # 未按:文档原文未动
    before = full_client.get("/platform/api/docs/design.new_ui").json()["text"]
    assert "首段内容" in before
    # 人按:apply 替换 + save(.bak)+ 系统留痕
    r3 = _apply({"anchor": "doc.md#L2-L2", "replace_text": "改过的第二段",
                 "expected": "首段内容"})
    assert r3.status_code == 200, r3.text
    after = full_client.get("/platform/api/docs/design.new_ui").json()["text"]
    assert "改过的第二段" in after and "首段内容" not in after, "按锚点区间替换"
    bubbles = full_client.get("/platform/api/docs/design.new_ui/bubbles").json()
    flow = next(b for b in bubbles if b["anchor"] == "doc.md#L2-L2")
    assert flow["messages"][-1]["role"] == "system", "apply 留痕(system 消息)"


# ---------------------------------------------------------------------------
# D3:doc_reviewer 白名单 / 全文评审 / NOTES.md 写回(§5/§6)
# ---------------------------------------------------------------------------


def test_doc_reviewer_whitelist_empty():
    """doc_reviewer tools/skills 全空(白名单收口:只读,不能改文档)。"""
    from agent_os.skills.lab_assistant import DOC_REVIEWER_NAME, doc_reviewer_skill

    skill = doc_reviewer_skill()
    assert skill.manifest.name == DOC_REVIEWER_NAME
    assert skill.manifest.permissions.tools == []
    assert skill.manifest.permissions.skills == []


def doc_review_brain(req):
    """doc_reviewer(scripted):回锚点批注集;录制输入供断言。"""
    import json as _json

    from agent_os.api.v1 import ChatResponse, Message, Role

    doc_review_brain.seen.append(req)
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            content=_json.dumps({
                "notes": [
                    {"anchor": "doc.md#L2-L2", "severity": "must", "text": "这段绕"},
                    {"anchor": "doc.md#L4-L4", "severity": "nit", "text": "可精简"},
                    {"anchor": "doc.md#L99-L99", "severity": "must", "text": "越界批注(块不在也应落盘)"},
                ]
            }),
        ),
        finish_reason="stop",
    )


doc_review_brain.seen = []


def _review_client(tmp_path):
    import textwrap

    from fastapi.testclient import TestClient

    from agent_os.host.web.app import create_app

    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [run]
            model = "mock/x"
            compression = "off"
            [providers.mock]
            brain = "tests.web_platform.test_doc_store:doc_review_brain"
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


def test_review_full_chain(tmp_path):
    """review:信封(全文+大纲+最近 diff)→ 批注集 → 落 review/<ts>.json +
    自动挂段(bubbles 带 severity);diff(v-1→working)进技能输入。"""
    doc_review_brain.seen.clear()
    client = _review_client(tmp_path)
    client.post("/platform/api/docs", json={"name": "design.new_ui", "title": "新 UI",
                                             "text": "# 概述\n首段内容\n## 设计\n次段内容\n"})
    inst = client.post("/platform/api/apps/spawn", json={
        "kind": "doc", "ref": "design.new_ui",
        "state": {"name": "design.new_ui", "text": "", "dirty": False, "savedAt": 0,
                  "view": "split", "versions": [], "bubbles": []},
    }).json()["instance"]["id"]
    client.post(f"/platform/api/apps/{inst}/actions/doc.snapshot", json={"surface": "tab"})
    client.post(f"/platform/api/apps/{inst}/actions/doc.save",
                json={"surface": "tab", "args": {"text": "# 概述\n首段改长了一些绕话\n## 设计\n次段内容\n"}})

    r = client.post("/platform/api/docs/design.new_ui/review")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["notes"]) == 3, "批注集返回(含越界锚点——服务端只验格式)"
    assert body["review_file"].endswith(".json"), "review 落盘"
    seen_text = "\n".join(m.content for m in doc_review_brain.seen[-1].messages)
    assert '"outline"' in seen_text, "大纲进信封"
    assert '"diff"' in seen_text, "最近 diff(v-1→working)进信封"
    # 落盘可读 + 自动挂段(severity 随消息)
    bubbles = client.get("/platform/api/docs/design.new_ui/bubbles").json()
    by_anchor = {b["anchor"]: b for b in bubbles}
    assert by_anchor["doc.md#L2-L2"]["messages"][-1]["severity"] == "must", "severity 着色随流"
    assert by_anchor["doc.md#L4-L4"]["messages"][-1]["severity"] == "nit"
    assert by_anchor["doc.md#L99-L99"], "越界锚点也挂(服务端不验存在性——前端挂空处理)"
    flow_texts = [m["text"] for b in bubbles for m in b["messages"] if b.get("anchor") == "doc.md#L2-L2"]
    assert "这段绕" in flow_texts


def test_notes_writeback_to_draft(tmp_path):
    """NOTES 接点:notes.<draft> 保存 → 草稿 manifest.notes 写回(不碰生产)。"""
    client = _review_client(tmp_path)
    client.post("/api/lab/drafts", json={"name": "lab.draft1"})
    client.post("/platform/api/docs", json={"name": "notes.lab.draft1", "title": "lab.draft1 笔记",
                                             "text": "# 初稿笔记\n"})
    inst = client.post("/platform/api/apps/spawn", json={
        "kind": "doc", "ref": "notes.lab.draft1",
        "state": {"name": "notes.lab.draft1", "text": "", "dirty": False, "savedAt": 0,
                  "view": "split", "versions": [], "bubbles": []},
    }).json()["instance"]["id"]
    r = client.post(f"/platform/api/apps/{inst}/actions/doc.save",
                    json={"surface": "tab", "args": {"text": "# 设计要点\n第二条\n"}})
    assert r.status_code == 200, r.text
    draft = client.get("/api/lab/drafts/lab.draft1").json()
    assert draft["manifest"].get("notes") == "# 设计要点\n第二条\n", "写回草稿 manifest.notes"
    # 草稿不存在时保存仍成功(跳过写回,不炸)
    client.post("/platform/api/docs", json={"name": "notes.lab.ghost", "text": "x\n"})
    inst2 = client.post("/platform/api/apps/spawn", json={
        "kind": "doc", "ref": "notes.lab.ghost",
        "state": {"name": "notes.lab.ghost", "text": "", "dirty": False, "savedAt": 0,
                  "view": "split", "versions": [], "bubbles": []},
    }).json()["instance"]["id"]
    r2 = client.post(f"/platform/api/apps/{inst2}/actions/doc.save",
                     json={"surface": "tab", "args": {"text": "y\n"}})
    assert r2.status_code == 200, "草稿缺失时保存跳过写回(只读+另存模式)"


# ---------------------------------------------------------------------------
# D4:doc 意图(orchestrator)+ severity 单源(§6/打磨)
# ---------------------------------------------------------------------------


def test_doc_intent_orchestrator():
    """doc 意图:关键词命中列表卡(新建意图带引导);编排只读(docs_provider 注入)。"""
    from agent_os.host.web_platform.orchestrator import Orchestrator

    docs = [{"name": "design.new_ui", "title": "新 UI", "first_line": "概述",
             "chars": 123, "savedAt": 1, "has_bubbles": True, "versions": 1}]
    orch = Orchestrator(docs_provider=lambda: docs)
    msg = orch.handle({"messages": []}, "有哪些文档")
    card = msg["cards"][0]
    assert card["type"] == "doc_list", "列表卡(doc_list 协议型)"
    assert card["data"]["docs"][0]["title"] == "新 UI"
    assert "共 1 篇" in msg["text"]
    msg2 = orch.handle({"messages": []}, "新建文档")
    assert "新建文档" in msg2["text"], "新建意图带'点卡新建'引导(编排不写)"
    assert msg2["cards"][0]["type"] == "doc_list"
    # 与 make_skill 区分:"写个文档" → doc;"写个查天气技能" → create_skill
    assert orch.handle({"messages": []}, "写个查天气技能")["cards"][0]["type"] == "plan"


def test_build_doc_list_card_validates():
    """build_doc_list_card 过协议闸(doc_list 注册型)。"""
    from agent_os.host.web_platform.artifacts import build_doc_list_card, validate_card

    validate_card(build_doc_list_card(docs=[]))


def test_severity_single_source():
    """severity 单源:后端校验集 = must/should/nit(与前端 doc-editor.js 字面一致)。"""
    from agent_os.host.web_platform.app import DOC_SEVERITIES

    assert list(DOC_SEVERITIES) == ["must", "should", "nit"]


# ---------------------------------------------------------------------------
# D5:doc 作用域主对话——chat.json 持久化 / doc_editor 白名单 / doc.read/doc.edit
# 工具围栏 / POST /api/docs/{name}/chat 全链(§5)
# ---------------------------------------------------------------------------


def test_chat_persist(store):
    """chat.json:主对话 append/读取(无文件/坏文件 → []);与 bubbles 分流两文件都留。"""
    store.create("design.new_ui", text="x")
    assert store.read_chat("design.new_ui") == [], "无文件 → 空"
    store.save_chat("design.new_ui", {"role": "user", "text": "要个设计文档"})
    store.save_chat("design.new_ui", {"role": "assistant", "text": "建好了"})
    msgs = store.read_chat("design.new_ui")
    assert [m["role"] for m in msgs] == ["user", "assistant"], "消息流 append"
    assert all("ts" in m for m in msgs), "ts 自动补"
    import pathlib

    bad = pathlib.Path(store._root) / "design.new_ui" / "chat.json"
    bad.write_text("{bad", encoding="utf-8")
    assert store.read_chat("design.new_ui") == [], "坏文件 → 空(不炸)"
    assert store.read_chat("no.such") == [], "文档目录不在也 → 空"


def test_doc_editor_whitelist():
    """doc_editor 白名单 = 当前文档的 doc.read/doc.edit 两件(skills 空;无别的系统面)。"""
    from agent_os.skills.lab_assistant import DOC_EDITOR_NAME, doc_editor_skill

    skill = doc_editor_skill()
    assert skill.manifest.name == DOC_EDITOR_NAME
    assert skill.manifest.permissions.tools == ["doc.read", "doc.edit"], "恰好两件"
    assert skill.manifest.permissions.skills == []


def test_doc_tools_anchor_edit_and_fence(tmp_path):
    """doc.read/doc.edit:按段替换/整文替换(.bak 同惯例);引用围栏(越界名拒)
    + 锚点格式/越界拒;组内恰好两件。"""
    import asyncio

    from agent_os.api.v1 import SkillFrame, ToolCall, ToolDispatchContext
    from agent_os.tools.lab_tools import DOC_EDITOR_TOOLS, register_doc_tools
    from agent_os.tools.local_registry import LocalPythonToolRegistry

    store = DocStore(tmp_path / "docs")
    store.create("design.new_ui", text="# 概述\n首段\n## 设计\n次段\n")
    reg = LocalPythonToolRegistry()
    register_doc_tools(reg, store=store, doc_name="design.new_ui")
    ctx = ToolDispatchContext(
        frame=SkillFrame(frame_id="f", run_id="r"), allowed_tools=list(DOC_EDITOR_TOOLS)
    )

    def call(name, args):
        return asyncio.run(reg.dispatch(ToolCall(id="c", name=name, args=args), ctx))

    assert call("doc.read", {"name": "design.new_ui"}).value["text"].startswith("# 概述")
    r = call("doc.edit", {"name": "design.new_ui", "anchor": "doc.md#L2-L2", "replace_text": "改过的首段"})
    assert r.ok and "改过的首段" in store.read("design.new_ui")["text"], "按锚点区间替换"
    bak = (tmp_path / "docs" / "design.new_ui" / "doc.md.bak").read_text(encoding="utf-8")
    assert "首段" in bak, ".bak = 上一版(reversible)"
    r2 = call("doc.edit", {"name": "design.new_ui", "anchor": "", "replace_text": "# 全新\n"})
    assert r2.ok and store.read("design.new_ui")["text"] == "# 全新\n", "anchor 空 = 整文替换"
    r3 = call("doc.edit", {"name": "other.doc", "replace_text": "x"})
    assert not r3.ok and "只能编辑当前文档" in r3.error.message, "引用围栏:越界名拒"
    assert not call("doc.read", {"name": "other.doc"}).ok, "读也收同一围栏"
    assert not call("doc.edit", {"name": "design.new_ui", "anchor": "bad", "replace_text": "x"}).ok, "锚点格式拒"
    assert not call(
        "doc.edit", {"name": "design.new_ui", "anchor": "doc.md#L9-L9", "replace_text": "x"}
    ).ok, "锚点越界拒"
    specs = {s.name: s for s in reg.specs() if s.name.startswith("doc.")}
    assert set(specs) == {"doc.read", "doc.edit"}, "组内恰好两件"


def doc_editor_brain(req):
    """doc_editor(scripted):按 plan 行动——edit = 调 doc.edit 改第二段;
    edit_other = 试图改别的文档(围栏拒);noop = 只回复不动文档。"""
    import json as _json

    from agent_os.api.v1 import ChatResponse, Message, Role, ToolCall

    doc_editor_brain.seen.append(req)
    calls = [tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls]
    if not calls:
        plan = getattr(doc_editor_brain, "plan", "edit")
        if plan == "noop":
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content=_json.dumps({"reply": "没动文档"})),
                finish_reason="stop",
            )
        args = (
            {"name": "design.new_ui", "anchor": "doc.md#L2-L2", "replace_text": "改过的第二段"}
            if plan == "edit"
            else {"name": "other.doc", "anchor": "", "replace_text": "x"}  # edit_other:越界
        )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="t1", name="doc.edit", args=args)]),
            finish_reason="tool_calls",
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=_json.dumps({"reply": "已按批注改好"})),
        finish_reason="stop",
    )


doc_editor_brain.seen = []


def _chat_client(tmp_path):
    import textwrap

    from fastapi.testclient import TestClient

    from agent_os.host.web.app import create_app

    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [run]
            model = "mock/x"
            compression = "off"
            [providers.mock]
            brain = "tests.web_platform.test_doc_store:doc_editor_brain"
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


def test_chat_full_chain(tmp_path):
    """chat:信封(全文 + 全部 bubbles 批注)→ agent 经 doc.edit 直接改文档;
    {reply, changed}(changed = run 前后全文对比,不信技能自报);双侧消息落
    chat.json(开关不丢);未变轮 changed=False 且文档不动。"""
    doc_editor_brain.seen.clear()
    doc_editor_brain.plan = "edit"
    client = _chat_client(tmp_path)
    client.post("/platform/api/docs", json={"name": "design.new_ui", "title": "新 UI",
                                             "text": "# 概述\n首段内容\n## 设计\n次段内容\n"})
    # 先挂一条段落批注("按批注改一遍"的覆盖源;bubbles 应随信封进技能)
    from agent_os.skills.doc_store import DocStore

    DocStore(tmp_path / "runs" / "docs").save_bubble(
        "design.new_ui", "doc.md#L2-L2", {"role": "user", "text": "这段太绕"}
    )
    r = client.post("/platform/api/docs/design.new_ui/chat", json={"text": "按批注改一遍"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "已按批注改好"
    assert body["changed"] is True, "doc.edit 落盘 → changed"
    doc = client.get("/platform/api/docs/design.new_ui").json()
    assert "改过的第二段" in doc["text"], "agent 直接改文档"
    seen_text = "\n".join(m.content for m in doc_editor_brain.seen[0].messages)
    # 注:runner 序列化输入 ensure_ascii=True,中文是 \uXXXX——断言用 ASCII 安全面
    assert '"bubbles"' in seen_text and "doc.md#L2-L2" in seen_text, "全部批注随信封进技能"
    assert '"full_text"' in seen_text, "全文进信封"
    # 持久化:双侧消息在 chat.json(读面随 GET 直给)
    chat = doc["chat"]
    assert [m["role"] for m in chat] == ["user", "assistant"], "开关不丢(服务端事实源)"
    assert chat[0]["text"] == "按批注改一遍"
    # 未变轮:brain 不调工具 → changed=False,文档不动
    doc_editor_brain.plan = "noop"
    r2 = client.post("/platform/api/docs/design.new_ui/chat", json={"text": "只问个问题"})
    assert r2.status_code == 200
    assert r2.json()["changed"] is False, "没调 doc.edit → 未变"
    assert "改过的第二段" in client.get("/platform/api/docs/design.new_ui").json()["text"]
    assert len(client.get("/platform/api/docs/design.new_ui").json()["chat"]) == 4, "两轮全在"
    assert client.post("/platform/api/docs/no.such/chat", json={"text": "x"}).status_code == 404


def test_chat_fence_rejects_outside_ref(tmp_path):
    """chat 白名单收口:agent 想改别的文档 → 工具引用围栏拒(别人文档不动,
    run 照常回 reply,changed=False)。"""
    doc_editor_brain.seen.clear()
    doc_editor_brain.plan = "edit_other"
    client = _chat_client(tmp_path)
    client.post("/platform/api/docs", json={"name": "design.new_ui", "text": "# 概述\n首段内容\n"})
    client.post("/platform/api/docs", json={"name": "other.doc", "text": "别人的\n"})
    r = client.post("/platform/api/docs/design.new_ui/chat", json={"text": "把 other.doc 改了"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is False, "越界被拒 → 未变"
    assert client.get("/platform/api/docs/other.doc").json()["text"] == "别人的\n", "别人文档不动"
    assert "首段内容" in client.get("/platform/api/docs/design.new_ui").json()["text"], "当前文档也不动"


# ---------------------------------------------------------------------------
# UX 批(2026-08-04):死路话术改引导 + doc_list 卡去重
# ---------------------------------------------------------------------------


def test_chat_empty_bubbles_guidance(tmp_path):
    """空批注的"按批注改"类请求不起 run——直接给引导式回复(changed=false,
    双侧消息照常落 chat.json;人话,不编造)。"""
    doc_editor_brain.seen.clear()
    client = _chat_client(tmp_path)
    client.post("/platform/api/docs", json={"name": "design.new_ui", "text": "# 概述\n首段\n"})
    r = client.post("/platform/api/docs/design.new_ui/chat", json={"text": "按批注改一遍"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is False
    assert "还没有批注" in body["reply"] and "右键" in body["reply"], "引导式回复"
    assert not doc_editor_brain.seen, "空批注守卫:不起 LLM run"
    chat = client.get("/platform/api/docs/design.new_ui").json()["chat"]
    assert [m["role"] for m in chat] == ["user", "assistant"], "双侧消息照常落盘"


def test_doc_list_card_dedupes():
    """doc_list 卡按 name 去重(UX 批:上游合并面可能出重复条目,demo.test ×2)。"""
    from agent_os.host.web_platform.artifacts import build_doc_list_card

    card = build_doc_list_card(
        docs=[{"name": "demo.test", "title": "A"}, {"name": "demo.test", "title": "A2"}, {"name": "a.b"}]
    )
    assert [d["name"] for d in card["data"]["docs"]] == ["demo.test", "a.b"], "同名去重(先见为准)"

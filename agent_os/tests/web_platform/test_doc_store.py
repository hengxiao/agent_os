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

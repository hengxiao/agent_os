"""Flow C 迭代样板锚点测试(docs/LAB-ITERATION.md §4/§5)。

- 边注存取(save/load/latest);版本快照不可变 + rewind 恢复 working;
- diff 纯函数(字段三态/行级增删/成员级 added);
- iterate 端点(scripted brain 产候选:候选落盘、working 不动、diff 结构);
- accept(新版本 + 覆盖 + 清候选)/ discard / rewind;provider 故障 → 503。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Role, ToolCall
from agent_os.host.web.app import create_app
from agent_os.skills.iterate import manifest_diff, package_diff, text_line_diff

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def iterator_brain(req) -> ChatResponse:
    """iterator 帧:读 working → 写候选 → 汇报;其余帧:直接交付。"""
    system = req.messages[0].content if req.messages else ""
    if "迭代生成器" not in system:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "ok"})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )
    calls = [tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls]
    if not any(tc.name == "lab.cand.write" for tc in calls):
        new_prompt = "你是晚餐规划师,按天气给一份家常晚餐建议。\n雨天加班优先便携可带饭。\n"
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(id="c1", name="lab.cand.write", args={
                        "member": "weather.query",
                        "manifest": {
                            "name": "weather.query", "version": "0.2.0", "kind": "prompt",
                            "description": "按天气推荐一份家常晚餐。",
                            "inputs": {"type": "object"}, "outputs": {"type": "object"},
                            "permissions": {"tools": [], "skills": []},
                        },
                        "prompt": new_prompt,
                        "tests": {"case2b.json": {"input": {"weather": "大雨加班"}}},
                    })
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT,
                        content=json.dumps({"reply": "description 精简 + 雨天便携 + 新反例"})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.web.test_lab_iterate:iterator_brain"

[tools]
builtins = true
python_exec = "off"

[skills]
path = "{skills}"

[lab]
drafts_root = "{drafts}"
"""

_MANIFEST_V1 = {
    "name": "weather.query",
    "version": "0.1.0",
    "kind": "prompt",
    "description": "根据天气与日程推荐晚餐,综合温度、降水与用户偏好。",
    "inputs": {"type": "object"},
    "outputs": {"type": "object"},
    "permissions": {"tools": [], "skills": []},
}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"), encoding="utf-8")
    c = TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))
    c.post("/api/lab/drafts", json={"name": "weather.query"})
    c.put("/api/lab/drafts/weather.query", json={
        "manifest": _MANIFEST_V1,
        "prompt": "你是晚餐规划师,根据日期与天气给出家常晚餐建议。\n",
        "handler": None,
    })
    c._tmp_path = tmp_path
    return c


_COMMENTS = [
    {"anchor": {"member": "weather.query", "kind": "field", "path": "description"}, "text": "太啰嗦"},
    {"anchor": {"member": "weather.query", "kind": "case", "path": "tests/case2"}, "text": "加大雨加班反例"},
]


# ---------------------------------------------------------------------------
# diff 纯函数
# ---------------------------------------------------------------------------


def test_manifest_diff_three_states():
    """字段三态:added / changed / removed(name 不参与)。"""
    old = {"name": "a", "description": "旧", "keep": 1, "gone": True}
    new = {"name": "a", "description": "新", "keep": 1, "added": [1]}
    diff = {d["path"]: d["kind"] for d in manifest_diff(old, new)}
    assert diff == {"description": "changed", "gone": "removed", "added": "added"}
    assert "name" not in diff


def test_text_line_diff_add_del_same():
    """行级 diff:增/删/同分类正确。"""
    old = "第一行\n第二行\n第三行\n"
    new = "第一行\n第二行改\n第四行\n"
    kinds = [(d["kind"], d["text"]) for d in text_line_diff(old, new)]
    assert ("same", "第一行") in kinds
    assert ("del", "第二行") in kinds
    assert ("add", "第二行改") in kinds
    assert ("del", "第三行") in kinds
    assert ("add", "第四行") in kinds


def test_package_diff_member_added():
    """成员级:候选新增成员 → added;变更成员带字段与行级 diff。"""
    working = {"a.b": {"manifest": {"name": "a.b", "description": "旧"}, "prompt": "p1\n", "tests": []}}
    candidate = {
        "a.b": {"manifest": {"name": "a.b", "description": "新"}, "prompt": "p1\np2\n", "tests": ["c.json"]},
        "a.c": {"manifest": {"name": "a.c"}, "prompt": "x\n", "tests": []},
    }
    diff = package_diff(working, candidate)
    by_member = {m["member"]: m for m in diff["members"]}
    assert by_member["a.b"]["status"] == "changed"
    assert by_member["a.c"]["status"] == "added"
    assert by_member["a.b"]["tests"]["added"] == ["c.json"]
    assert diff["has_changes"] is True


# ---------------------------------------------------------------------------
# iterate 全流程:产候选 → accept → rewind;discard;503
# ---------------------------------------------------------------------------


def test_iterate_produces_candidate_without_touching_working(client):
    """iterate:边注落盘 + 候选落盘(working 不动)+ diff 返回(字段/行级/新增用例)。"""
    r = client.post("/api/lab/drafts/weather.query/iterate",
                    json={"comments": _COMMENTS, "note": "改简洁点"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidate"] is True
    assert "精简" in body["reply"]

    tmp = client._tmp_path
    assert (tmp / "drafts" / "weather.query" / "candidate" / "weather.query" / "manifest.yaml").is_file()
    working = client.get("/api/lab/drafts/weather.query").json()
    assert working["manifest"]["description"].startswith("根据天气"), "working 未被候选污染"
    assert working["prompt"].startswith("你是晚餐规划师,根据日期")

    diff = body["diff"]
    member = next(m for m in diff["members"] if m["member"] == "weather.query")
    assert member["status"] == "changed"
    fields = {f["path"]: f["kind"] for f in member["fields"]}
    assert fields.get("description") == "changed"
    assert fields.get("version") == "changed"
    assert any(d["kind"] == "add" and "便携" in d["text"] for d in member["prompt_diff"])
    assert member["tests"]["added"] == ["case2b.json"]

    # 边注已落盘(comments 端点可见)
    got = client.get("/api/lab/drafts/weather.query/comments").json()
    assert len(got["comments"]) == 2

    # GET diff 端点同构
    assert client.get("/api/lab/drafts/weather.query/candidate/diff").status_code == 200


def test_accept_snapshot_overwrite_and_rewind(client):
    """accept:v001 封存 → 候选覆盖 working → 清候选;rewind 恢复 v001,历史不动。"""
    client.post("/api/lab/drafts/weather.query/iterate", json={"comments": _COMMENTS, "note": ""})
    r = client.post("/api/lab/drafts/weather.query/candidate/accept")
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "v001"
    versions = client.get("/api/lab/drafts/weather.query/versions").json()
    assert [v["version"] for v in versions] == ["v001"]
    assert versions[0]["source"] == "iterate"
    assert versions[0]["comments_digest"] == "2 条边注"

    working = client.get("/api/lab/drafts/weather.query").json()
    assert working["manifest"]["description"] == "按天气推荐一份家常晚餐。", "候选已覆盖 working"
    assert "便携" in working["prompt"]
    assert client.get("/api/lab/drafts/weather.query/candidate/diff").status_code == 404, "候选已清"

    # 再迭代出 v002,然后 rewind 回 v001:working 恢复,版本历史不动
    client.post("/api/lab/drafts/weather.query/iterate", json={"comments": [], "note": ""})
    client.post("/api/lab/drafts/weather.query/candidate/accept")
    assert [v["version"] for v in client.get("/api/lab/drafts/weather.query/versions").json()] == ["v002", "v001"]
    r = client.post("/api/lab/drafts/weather.query/rewind", json={"version": "v001"})
    assert r.status_code == 200
    restored = client.get("/api/lab/drafts/weather.query").json()
    assert restored["manifest"]["description"].startswith("根据天气"), "rewind 恢复 v001 内容"
    versions2 = client.get("/api/lab/drafts/weather.query/versions").json()
    assert [v["version"] for v in versions2] == ["v002", "v001"], "版本不可变:rewind 不改历史"


def test_discard_and_provider_error(client, monkeypatch):
    """discard 清候选;provider 故障 → 503 明确错误(前端显示"助手暂不可用")。"""
    client.post("/api/lab/drafts/weather.query/iterate", json={"comments": [], "note": ""})
    assert (client._tmp_path / "drafts" / "weather.query" / "candidate").is_dir()
    r = client.post("/api/lab/drafts/weather.query/candidate/discard")
    assert r.status_code == 200
    assert not (client._tmp_path / "drafts" / "weather.query" / "candidate").exists()

    from agent_os.api.v1 import ProviderError, ProviderErrorKind
    from agent_os.providers.mock import MockProvider

    def boom(req):
        raise ProviderError(ProviderErrorKind.UNAVAILABLE, "凭证过期(假)")

    monkeypatch.setattr(MockProvider, "chat", boom)
    r = client.post("/api/lab/drafts/weather.query/iterate", json={"comments": [], "note": ""})
    assert r.status_code == 503
    assert "助手暂不可用" in r.json()["detail"]

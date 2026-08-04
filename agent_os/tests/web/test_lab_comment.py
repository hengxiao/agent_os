"""W2 锚点评论端点测试(docs/WIDGETS.md W-bubble;APP-MODEL §16 首个 cascade 消费者)。

POST /api/lab/drafts/{name}/comment:信封(anchor+text+cascade)→ 评论技能
(tools=[] 白名单收口,只读级联)→ {reply};草稿缺失 404;provider 故障 503。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import ChatResponse, Message, Role
from agent_os.host.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def comment_brain(req) -> ChatResponse:
    """评论技能(scripted):看到级联信封就回建议;录制输入供断言。"""
    comment_brain.seen.append(req)
    user = req.messages[-1].content if req.messages else ""
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"reply": f"建议:按批注改({len(user)}字上下文)"})),
        finish_reason="stop",
    )


comment_brain.seen = []

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.web.test_lab_comment:comment_brain"

[tools]
builtins = true
python_exec = "off"

[skills]
path = "{skills}"

[lab]
drafts_root = "{drafts}"
"""


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"), encoding="utf-8")
    c = TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))
    c.post("/api/lab/drafts", json={"name": "lab.d"})
    return c


def test_comment_endpoint_reply_with_cascade(client):
    """信封(anchor+text+cascade 三级)→ 评论技能回复;cascade 进了技能输入。"""
    comment_brain.seen.clear()
    cascade = [
        {"scope": "widget", "path": "/lab/iterate/member/lab.d/span/prompt/span/0",
         "data": {"span": {"start": 0}, "paragraph": "段", "full_text": "全"}},
        {"scope": "app", "path": "/lab/iterate", "data": {"draft": "lab.d", "tier": "none"}},
    ]
    r = client.post(
        "/api/lab/drafts/lab.d/comment",
        json={"anchor": {"member": "lab.d", "kind": "span", "path": "prompt", "span": {"start": 0}},
              "text": "这段精简点", "cascade": cascade},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"].startswith("建议:"), "回复建议进气泡"
    seen_text = "\n".join(m.content for m in comment_brain.seen[-1].messages)
    assert "全" in seen_text and "lab.d" in seen_text, "级联上下文(全文+草稿状态)进了技能输入"


def test_comment_endpoint_404(client):
    """草稿不存在 → 404。"""
    r = client.post("/api/lab/drafts/lab.nope/comment", json={"anchor": {}, "text": "x", "cascade": []})
    assert r.status_code == 404

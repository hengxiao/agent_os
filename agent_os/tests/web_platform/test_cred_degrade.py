"""N7 凭证降级(O7;docs/FLOWS-OPTIMIZATION.md 循环 3)。

401/ProviderError 两处人话化:
1. LLM 意图路由:凭证故障 → 回落规则 + meta.reason=llm_unavailable
   (前端据此出"模型服务暂不可用,已用规则模式"系统气泡,copy 六主题)——
   不 500、不静默、不裸英文类名;
2. assistant 动作端点(cards/action 的 LLM 动作,如 iterate.generate):
   ProviderError → 503 人话 detail(不 500 不裸错)。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import ProviderError, ProviderErrorKind
from agent_os.host.web.app import create_app
from agent_os.providers.mock import MockProvider

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SKILLS_YAML = """
skills: []
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.web.test_lab_testrun:lab_brain"

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
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def _boom_auth(self, req):
    raise ProviderError(ProviderErrorKind.AUTH, "401 token expired")


def test_llm_route_cred_degrade(client, monkeypatch):
    """路由凭证 401:消息照常 200(规则接住),meta 标降级;响应面无英文类名。"""
    monkeypatch.setattr(MockProvider, "chat", _boom_auth)
    sid = client.post("/platform/api/sessions").json()["id"]
    r = client.post(f"/platform/api/sessions/{sid}/messages", json={"text": "帮我做个 weather 技能"})
    assert r.status_code == 200, "凭证故障不 500(fail-safe)"
    msg = r.json()
    assert msg["meta"]["route"] == "rule"
    assert msg["meta"]["reason"] == "llm_unavailable", "降级机器码(前端人话气泡的依据)"
    assert "ProviderError" not in r.text and "401" not in msg["text"], "不裸错"


def test_assistant_action_cred_degrade(client, monkeypatch):
    """assistant 动作(iterate.generate)凭证故障 → 503 人话,不 500 不裸错。"""
    client.post("/api/lab/drafts", json={"name": "lab.x"})
    monkeypatch.setattr(MockProvider, "chat", _boom_auth)
    r = client.post(
        "/platform/api/cards/action",
        json={"action_id": "iterate.generate", "payload": {"name": "lab.x", "comments": []}},
    )
    assert r.status_code == 503, f"凭证故障归 503 而非 500: {r.status_code}"
    detail = r.json()["detail"]
    assert "模型服务暂不可用" in detail
    assert "ProviderError" not in detail and "401" not in detail, "人话 detail,不裸英文类名"

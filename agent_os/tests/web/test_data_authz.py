"""D3-lite 多用户 Web 映射锚点测试(docs/DATA-AUTHZ.md §2.2;``[web.tokens]``)。

固定约定:

- ``[web.tokens]`` 命中映射的 Bearer token → 逐请求 principal(issuer=``api-token``),
  创建 run 时经 ``request.state.principal`` → ``start_run`` 透传,内核帧身份即映射 subject
  (观测面:产物 checkpoint.json 的帧 principal);
- 未命中/无 token → 单用户语义(``web_single_user_principal``,issuer=``web-session``),
  行为与引入映射前完全一致;
- 静态 token 门禁(docs/RUNNERS.md §4.5)下,映射 token 同为合法凭证(不 401)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import web_single_user_principal
from agent_os.host.web.app import create_app
from tests.helpers.config import write_config

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

TOKEN_MAP = '[web.tokens]\n"tok-alice" = "user:alice"\n"tok-bot" = "service:ci-bot"\n'


def _client(tmp_path, *, token: str | None = None, extra: str = "") -> TestClient:
    cfg = write_config(tmp_path, extra=extra)
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs", token=token))


def _run_frame_principal(client: TestClient, artifacts: Path, headers: dict | None = None) -> dict:
    """发起一个 wait run,读产物 checkpoint 的帧 principal(数据层身份观测面)。"""
    r = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 2}, "wait": True},
        headers=headers or {},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "done", body
    ckpt = json.loads((artifacts / "runs" / body["run_id"] / "checkpoint.json").read_text())
    principals = {f["principal"]["subject"]: f["principal"] for f in ckpt["frames"]}
    assert len(principals) == 1, "一个 run 内所有帧共享同一 principal(§2.3 身份不变量)"
    return next(iter(principals.values()))


def test_mapped_token_run_uses_mapped_principal(tmp_path):
    """命中映射:run 的帧 principal.subject 为映射值,issuer=api-token。"""
    client = _client(tmp_path, extra=TOKEN_MAP)
    doc = _run_frame_principal(
        client, tmp_path / "runs", headers={"Authorization": "Bearer tok-alice"}
    )
    assert doc["subject"] == "user:alice"
    assert doc["issuer"] == "api-token"


def test_unmapped_token_falls_back_to_single_user(tmp_path):
    """未命中映射(且无静态门禁)= 免认证单用户:principal 为部署者,issuer=web-session。"""
    client = _client(tmp_path, extra=TOKEN_MAP)
    doc = _run_frame_principal(
        client, tmp_path / "runs", headers={"Authorization": "Bearer nobody"}
    )
    assert doc["issuer"] == "web-session"
    assert doc["subject"] == web_single_user_principal().subject


def test_no_token_regression_single_user(tmp_path):
    """回归锚:配了 [web.tokens] 但请求不带 token → 单用户行为与引入映射前一致。"""
    client = _client(tmp_path, extra=TOKEN_MAP)
    doc = _run_frame_principal(client, tmp_path / "runs")
    assert doc["issuer"] == "web-session"


def test_no_token_map_regression_no_middleware(tmp_path):
    """回归锚:无 [web.tokens] 且无静态 token → 不装门禁/映射,行为与今完全一致。"""
    client = _client(tmp_path)
    assert client.get("/api/runs").status_code == 200
    doc = _run_frame_principal(client, tmp_path / "runs")
    assert doc["issuer"] == "web-session"


def test_mapped_token_passes_static_gate(tmp_path):
    """静态 token 门禁(§4.5)下,映射 token 同为合法凭证:命中 → 200 且身份按映射;
    未命中且不等于静态 token → 401(与映射引入前一致)。"""
    client = _client(tmp_path, token="s3cr3t-token", extra=TOKEN_MAP)
    r = client.get("/api/runs", headers={"Authorization": "Bearer tok-bot"})
    assert r.status_code == 200
    assert client.get("/api/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    doc = _run_frame_principal(
        client, tmp_path / "runs", headers={"Authorization": "Bearer tok-bot"}
    )
    assert doc["subject"] == "service:ci-bot"
    assert doc["issuer"] == "api-token"

"""Web 鉴权锚点测试(docs/RUNNERS.md §4.5)。

固定约定:

- ``create_app(..., token=...)`` 非 None 时全站要求 ``Authorization: Bearer <token>``;
  SSE(EventSource 不能带自定义头)可用 ``?token=`` 查询串;
- 缺省 ``token=None`` = 无认证,**只可用于 loopback**;
- ``serve.py`` 在绑定非 loopback 且未给 token 时**拒绝启动**(而非降级为无认证)。

回归的是一个真实缺陷:docs/RUNNERS.md §4.5 明写"绑定非 loopback 时要求 --token",
而实现里 token/auth 出现 0 次——``--host 0.0.0.0`` 即把能跑 ``shell_exec``
的执行面开放到网络。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from agent_os.host.web.serve import _is_loopback
from tests.helpers.config import write_config

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

TOKEN = "s3cr3t-token"


def _client(tmp_path, token=TOKEN) -> TestClient:
    cfg = write_config(tmp_path)
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs", token=token))


def test_requests_without_token_are_rejected(tmp_path):
    r = _client(tmp_path).get("/api/runs")
    assert r.status_code == 401
    assert "Bearer" in r.json()["detail"]


def test_bearer_header_grants_access(tmp_path):
    r = _client(tmp_path).get("/api/runs", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_query_token_grants_access_for_sse(tmp_path):
    """EventSource 不支持自定义头,SSE 只能走查询串——该通道必须同样受保护。"""
    client = _client(tmp_path)
    assert client.get("/api/runs", params={"token": TOKEN}).status_code == 200
    assert client.get("/api/runs", params={"token": "wrong"}).status_code == 401


def test_wrong_token_rejected_and_write_paths_too(tmp_path):
    """执行面(POST /api/runs)与读面同样受保护——前者才是真正的风险。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "fib", "input": {"n": 2}, "wait": True},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_no_token_means_no_auth(tmp_path):
    """缺省无认证(loopback dev 工具),行为与加固前一致。"""
    assert _client(tmp_path, token=None).get("/api/runs").status_code == 200


@pytest.mark.parametrize(
    "host,loopback",
    [("127.0.0.1", True), ("::1", True), ("localhost", True),
     ("0.0.0.0", False), ("192.168.1.5", False), ("example.com", False)],
)
def test_loopback_detection(host: str, loopback: bool):
    """主机名一律按**非** loopback 处理:解析结果取决于 DNS,不能当安全边界。"""
    assert _is_loopback(host) is loopback


def test_serve_refuses_non_loopback_without_token(monkeypatch):
    """启动闸门:非 loopback + 无 token → 拒绝启动(不得静默降级为无认证)。"""
    import sys

    from agent_os.host.web import serve

    monkeypatch.setattr(sys, "argv", ["agent-os-web", "--host", "0.0.0.0"])
    monkeypatch.delenv("AGENT_OS_WEB_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        serve.main()
    assert exc.value.code != 0

"""token_refresh 单元测试:续期逻辑(到期临近才刷/回写/环境变量/失败容忍)。

不碰真实网络:urllib.request.urlopen 打桩;凭证文件走 tmp_path。
"""

from __future__ import annotations

import json
import time
from unittest import mock

from agent_os.host.web import token_refresh as tr


def _write_cred(path, *, expires_in=900, with_refresh=True):
    data = {
        "access_token": "old-token",
        "expires_at": time.time() + expires_in,
        "expires_in": expires_in,
        "token_type": "Bearer",
    }
    if with_refresh:
        data["refresh_token"] = "rt-1"
    path.write_text(json.dumps(data))


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_refresh_when_near_expiry(tmp_path, monkeypatch):
    """距过期 <180s → 调 refresh 端点,回写文件并更新环境变量。"""
    cred = tmp_path / "kimi-code.json"
    _write_cred(cred, expires_in=60)  # 临近过期
    monkeypatch.setenv("MOONSHOT_API_KEY", "old-token")
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["body"] = req.data.decode()
        return _Resp({"access_token": "new-token", "expires_in": 900, "refresh_token": "rt-2"})

    monkeypatch.setattr(tr.urllib.request, "urlopen", fake_urlopen)
    assert tr._refresh_once(cred) is True
    assert seen["url"] == tr._TOKEN_URL
    assert "grant_type=refresh_token" in seen["body"] and "rt-1" in seen["body"]
    data = json.loads(cred.read_text())
    assert data["access_token"] == "new-token"
    assert data["refresh_token"] == "rt-2"  # 轮换写回
    assert tr.os.environ["MOONSHOT_API_KEY"] == "new-token"


def test_no_refresh_when_fresh(tmp_path, monkeypatch):
    """凭证还很新 → 不动作(不发请求)。"""
    cred = tmp_path / "kimi-code.json"
    _write_cred(cred, expires_in=600)
    called = []
    monkeypatch.setattr(tr.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    assert tr._refresh_once(cred) is False
    assert called == []


def test_failure_is_tolerated(tmp_path, monkeypatch):
    """网络错误/坏响应不外抛,下 tick 再试。"""
    cred = tmp_path / "kimi-code.json"
    _write_cred(cred, expires_in=10)
    monkeypatch.setattr(
        tr.urllib.request, "urlopen",
        mock.Mock(side_effect=OSError("network down")))
    assert tr._refresh_once(cred) is False  # 不外抛
    assert json.loads(cred.read_text())["access_token"] == "old-token"  # 文件未动

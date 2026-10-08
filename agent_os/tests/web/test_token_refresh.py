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


def test_env_syncs_from_file(tmp_path, monkeypatch):
    """文件被外部(CLI)刷新且未到期 → env 跟随文件(本进程不刷也不同步落后)。"""
    cred = tmp_path / "kimi-code.json"
    _write_cred(cred, expires_in=600)  # 新票在文件里
    monkeypatch.setenv("MOONSHOT_API_KEY", "stale-token")  # 进程 env 是旧票
    monkeypatch.setattr(tr.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该发请求")))
    assert tr._refresh_once(cred) is False  # 不续期(文件很新)
    assert tr.os.environ["MOONSHOT_API_KEY"] == "old-token"  # ← 文件内容 = _write_cred 写的 old-token
    # 文件与 env 不同 → env 采用文件值(此场景文件值恰为 old-token,改名验证)
    data = json.loads(cred.read_text())
    data["access_token"] = "cli-fresh-token"
    cred.write_text(json.dumps(data))
    tr._refresh_once(cred)
    assert tr.os.environ["MOONSHOT_API_KEY"] == "cli-fresh-token"


def test_failure_is_tolerated(tmp_path, monkeypatch):
    """网络错误/坏响应不外抛,下 tick 再试。"""
    cred = tmp_path / "kimi-code.json"
    _write_cred(cred, expires_in=10)
    monkeypatch.setattr(
        tr.urllib.request, "urlopen",
        mock.Mock(side_effect=OSError("network down")))
    assert tr._refresh_once(cred) is False  # 不外抛
    assert json.loads(cred.read_text())["access_token"] == "old-token"  # 文件未动


# ---------------------------------------------------------------------------
# 生命周期(P2:挪 host/shared 后 chat 宿主共用;本文件钉 web 兼容壳路径)
# ---------------------------------------------------------------------------


def test_start_missing_credentials_degrades(tmp_path, monkeypatch, capsys):
    """缺凭据文件:返回 None 不启动 + 一行 stderr 提示(_log.info 默认不可见)。"""
    monkeypatch.setenv("KIMI_CODE_CREDENTIALS", str(tmp_path / "nope.json"))
    monkeypatch.setattr(tr, "_started", None)  # 进程级单例复位(其它用例可能已起)
    assert tr.start_token_refresher() is None
    assert "token 续期未启用" in capsys.readouterr().err


def test_start_and_stop_lifecycle(tmp_path, monkeypatch):
    """启动 → 幂等(单例)→ stop 事件收尾线程退出。"""
    cred = tmp_path / "kimi-code.json"
    _write_cred(cred, expires_in=600)  # 很新:不刷、不发请求
    monkeypatch.setenv("MOONSHOT_API_KEY", "old-token")
    monkeypatch.setattr(
        tr.urllib.request, "urlopen",
        mock.Mock(side_effect=AssertionError("凭证很新,不该发请求")),
    )
    monkeypatch.setattr(tr, "_started", None)
    monkeypatch.setattr(tr, "_CHECK_INTERVAL_S", 0.02)  # 测试节拍
    stop = __import__("threading").Event()
    thread = tr.start_token_refresher(stop)
    assert thread is not None and thread.is_alive()
    assert tr.start_token_refresher(stop) is thread, "进程级幂等:重复 start 不堆积线程"
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive(), "stop 事件须让 daemon 线程退出"
    monkeypatch.setattr(tr, "_started", None)  # 收尾复位(下个用例干净)


def test_refreshed_env_visible_to_provider(tmp_path, monkeypatch):
    """贯通:refresher 续期写 env 后,``api_key_env`` 配置的 provider 下次访问即读新票
    (providers/openai_compatible.py 的 api_key property 每次访问重读 os.environ)。"""
    from agent_os.providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(api_key_env="MOONSHOT_API_KEY")
    monkeypatch.setenv("MOONSHOT_API_KEY", "old-token")
    assert provider.api_key == "old-token"
    cred = tmp_path / "kimi-code.json"
    _write_cred(cred, expires_in=60)  # 临近过期 → 触发续期
    monkeypatch.setattr(
        tr.urllib.request, "urlopen",
        lambda req, timeout=0: _Resp({"access_token": "new-token", "expires_in": 900}),
    )
    assert tr._refresh_once(cred) is True
    assert provider.api_key == "new-token"

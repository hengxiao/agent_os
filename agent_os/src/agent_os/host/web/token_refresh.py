"""OAuth token 自动续期(daemon 线程;kimi-code 订阅凭证 15 分钟过期)。

机制:每 60s 检查凭证文件的 ``expires_at``,距过期 <180s 时用 refresh_token
调 ``https://auth.kimi.com/api/oauth/token``(form: grant_type=refresh_token +
client_id)换新 access_token;成功后**回写凭证文件**并与 CLI 兼容
(新 refresh_token 若轮换也写回),同时更新 ``os.environ["MOONSHOT_API_KEY"]``
——provider 每次装配内核时现读环境变量(runtime/config.py 的 api_key_env),
新 run 自动用上新票;在途 run 沿用旧票,不中断。

失败(网络/401/坏文件)只记日志,下个 tick 重试;线程是 daemon,不阻塞退出。
端点与 client_id 来自 kimi-code 扩展的协议实现(auth.kimi.com)。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

_log = logging.getLogger("agent_os.token_refresh")

_TOKEN_URL = "https://auth.kimi.com/api/oauth/token"
_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"  # kimi-code 公开 OAuth client(非密)
_CHECK_INTERVAL_S = 60
_REFRESH_BEFORE_S = 180  # 距过期 3 分钟内就续(15 分钟票留足余量)


def _cred_path() -> Path:
    return Path(
        os.environ.get("KIMI_CODE_CREDENTIALS")
        or Path.home() / ".kimi-code" / "credentials" / "kimi-code.json"
    )


def _refresh_once(path: Path) -> bool:
    """同步+续期;返回是否发生了续期。异常不外抛(记日志)。

    两个职责:
    1. **文件→环境同步**:CLI 与本 refresher 共用同一 refresh_token(轮换制),
       谁先刷文件不一定——所以每 tick 先把"文件里的新票"同步进 env
       (覆盖 CLI 刷新而本进程 env 还停留在旧票的场景);
    2. **到期续期**:距过期 <_REFRESH_BEFORE_S 且本地无新票时,调 refresh 端点。
    """
    try:
        data = json.loads(path.read_text())
        access = data.get("access_token")
        if access and access != os.environ.get("MOONSHOT_API_KEY"):
            os.environ["MOONSHOT_API_KEY"] = access  # 文件较新(CLI 刷新):直接采用
            print(f"[token_refresh] 采用凭证文件中的新 token(外部刷新)", flush=True)
        expires_at = float(data.get("expires_at") or 0)
        if expires_at - time.time() >= _REFRESH_BEFORE_S:
            return False
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            _log.warning("凭证 %s 无 refresh_token,无法续期", path)
            return False
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CLIENT_ID,
        }).encode()
        req = urllib.request.Request(_TOKEN_URL, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
        access = payload.get("access_token")
        if not access:
            _log.warning("续期响应无 access_token: %s", list(payload))
            return False
        data["access_token"] = access
        if payload.get("refresh_token"):
            data["refresh_token"] = payload["refresh_token"]  # 轮换则写回
        if payload.get("expires_in"):
            data["expires_in"] = payload["expires_in"]
            data["expires_at"] = time.time() + float(payload["expires_in"])
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.environ["MOONSHOT_API_KEY"] = access
        print(f"[token_refresh] OAuth token 已续期(expires_in={payload.get('expires_in')})", flush=True)
        return True
    except Exception as e:  # noqa: BLE001 — 续期失败不致命,下 tick 重试(可能 CLI 已代刷)
        print(f"[token_refresh] 续期失败(下 tick 重试): {e!r}", flush=True)
        return False


def start_token_refresher(stop: threading.Event | None = None) -> threading.Thread | None:
    """启动 daemon 续期线程;凭证文件不存在时不启动(返回 None)。"""
    path = _cred_path()
    if not path.exists():
        _log.info("未找到 kimi-code 凭证(%s),token 续期未启用", path)
        return None

    def _loop() -> None:
        while not (stop and stop.is_set()):
            _refresh_once(path)
            time.sleep(_CHECK_INTERVAL_S)

    t = threading.Thread(target=_loop, name="agent-os-token-refresh", daemon=True)
    t.start()
    print(f"[token_refresh] OAuth token 自动续期已启动(每 {_CHECK_INTERVAL_S}s 检查 {path})", flush=True)
    return t

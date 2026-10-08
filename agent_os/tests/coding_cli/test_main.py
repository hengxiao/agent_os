"""agent-os-chat 进程入口(P2-M2)的装配侧锚点测试。

- 启动时接 token 刷新(host/shared/token_refresh.py,F1):带 stop 事件调用,
  REPL 退出后事件置位收尾(长会话 15 分钟 401 的修复点)。
- 退出码:--input 畸形 JSON → 2;配置缺失 → 4;--resume 未知会话 → 2。
"""

from __future__ import annotations

import threading

import agent_os.host.coding_cli.__main__ as chat_main
from tests.helpers.config import write_config as _write_config


def test_main_starts_and_stops_token_refresher(tmp_path, monkeypatch):
    """接线:main() 起 refresher(stop 事件传入),REPL 返回后 stop.set() 收尾。"""
    cfg = _write_config(tmp_path)
    seen: dict = {}

    def _fake_start(stop):
        seen["stop"] = stop  # 返回 None = 缺凭据文件的退化形态

    monkeypatch.setattr(chat_main, "start_token_refresher", _fake_start)
    monkeypatch.setattr(chat_main.Repl, "run", lambda self: 0)  # 不进输入循环
    rc = chat_main.main(
        ["--config", str(cfg), "--artifacts", str(tmp_path / "arts"), "--session-id", "t1"]
    )
    assert rc == 0
    assert isinstance(seen["stop"], threading.Event)
    assert seen["stop"].is_set(), "REPL 退出后须置位 stop 事件收尾刷新线程"


def test_main_bad_input_json_exit_2(tmp_path):
    cfg = _write_config(tmp_path)
    rc = chat_main.main(
        ["demo.fib", "--input", "{bad", "--config", str(cfg),
         "--artifacts", str(tmp_path / "arts")]
    )
    assert rc == 2


def test_main_missing_config_exit_4(tmp_path):
    rc = chat_main.main(["demo.fib", "--config", str(tmp_path / "nope.toml")])
    assert rc == 4


def test_main_resume_unknown_session_exit_2(tmp_path):
    cfg = _write_config(tmp_path)
    rc = chat_main.main(
        ["--resume", "no-such-session", "--config", str(cfg),
         "--artifacts", str(tmp_path / "arts")]
    )
    assert rc == 2

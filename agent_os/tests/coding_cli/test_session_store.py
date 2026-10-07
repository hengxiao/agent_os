"""SessionStore 测试:创建/读回/追加多轮/list_sessions/半写防御/原子写无残留 tmp。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_os.host.coding_cli import SessionStore


def _turn(run_id: str, status: str = "done") -> dict[str, object]:
    return {
        "run_id": run_id,
        "input": {"prompt": f"输入-{run_id}"},
        "status": status,
        "checkpoint_path": f"runs/{run_id}/checkpoint.json",
        "summary": f"摘要-{run_id}",
    }


def test_create_and_load_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    doc = store.create("s1", "demo-skill", "/etc/agent-os/config.toml")
    assert doc["session_id"] == "s1"
    assert doc["skill"] == "demo-skill"
    assert doc["config_path"] == "/etc/agent-os/config.toml"
    assert doc["created_at"]
    assert doc["updated_at"]
    assert doc["turns"] == []
    loaded = store.load("s1")
    assert loaded == doc


def test_create_existing_raises(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create("s1", "demo-skill", "config.toml")
    with pytest.raises(FileExistsError):
        store.create("s1", "demo-skill", "config.toml")


def test_append_turns_order_and_updated_at(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create("s1", "demo-skill", "config.toml")
    store.append_turn("s1", _turn("r1"))
    doc = store.append_turn("s1", _turn("r2", status="paused"))
    assert [t["run_id"] for t in doc["turns"]] == ["r1", "r2"]
    assert doc["turns"][1]["status"] == "paused"
    assert doc["turns"][1]["checkpoint_path"] == "runs/r2/checkpoint.json"
    assert doc["updated_at"] >= doc["created_at"]
    # 重载后顺序与轮数不变(原子重写持久化)
    assert [t["run_id"] for t in store.load("s1")["turns"]] == ["r1", "r2"]


def test_append_turn_missing_field_raises(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create("s1", "demo-skill", "config.toml")
    with pytest.raises(ValueError):
        store.append_turn("s1", {"run_id": "r1"})


def test_list_sessions_sorted_and_skips_corrupt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create("old", "demo-skill", "config.toml")
    store.create("new", "demo-skill", "config.toml")
    store.append_turn("old", _turn("r-old"))
    # 手写截断 JSON 模拟半写损坏文件
    (tmp_path / "sessions" / "broken.json").write_text('{"session_id": "bro', encoding="utf-8")
    sessions = store.list_sessions()
    assert [s["session_id"] for s in sessions] == ["old", "new"]
    assert sessions[0]["updated_at"] >= sessions[1]["updated_at"]


def test_load_corrupt_raises_value_error(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create("s1", "demo-skill", "config.toml")
    (tmp_path / "sessions" / "s1.json").write_text('{"session_id": "s1", ', encoding="utf-8")
    with pytest.raises(ValueError):
        store.load("s1")


def test_load_missing_raises_file_not_found(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load("nope")


def test_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create("s1", "demo-skill", "config.toml")
    store.append_turn("s1", _turn("r1"))
    store.append_turn("s1", _turn("r2"))
    sessions_dir = tmp_path / "sessions"
    assert list(sessions_dir.glob("*.tmp")) == []
    # 落盘内容本身是可解析的完整 JSON
    json.loads((sessions_dir / "s1.json").read_text(encoding="utf-8"))

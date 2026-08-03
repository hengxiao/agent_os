"""会话与消息模型(docs/WEB-PLATFORM.md §3;方案 A 对话中枢)。

会话 = 一场与 Agent OS 助手的持续对话;消息 = 用户意图或 agent 回应
(文本 + 产物卡列表)。**刷新不丢**:文件持久化到
``<artifacts_root>/platform_sessions/<id>.json``(单文件单会话,
与 runs 产物同目录语义——平台状态也是宿主产物)。

任务/产物列表(§2.1 左栏)= 会话里 artifact 卡的索引:本期先落
``list()`` 摘要(标题/最近时间/卡数),前端下一步渲染。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

#: 单条消息的形态(docstring 钉死,前端据此渲染):
#: {"id", "role": "user"|"agent", "text": str|None, "cards": [artifact], "ts": float}
Message = dict[str, Any]


def new_message(role: str, *, text: str | None = None, cards: list[dict[str, Any]] | None = None) -> Message:
    """构造一条消息(id/ts 由模型统一打戳,防调用方各写各的)。"""
    return {
        "id": uuid.uuid4().hex[:12],
        "role": role,
        "text": text,
        "cards": cards or [],
        "ts": time.time(),
    }


class SessionStore:
    """会话存取:创建 / 读取 / 追加 / 列表(``<root>/<id>.json``)。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        # id 只允许 hex(创建时统一生成;读路径时防穿越
        if not session_id or not all(c in "0123456789abcdef" for c in session_id):
            raise FileNotFoundError(f"会话 id 不合法: {session_id!r}")
        return self._root / f"{session_id}.json"

    def create(self) -> dict[str, Any]:
        """创建空会话(标题取首条用户消息,追加时回填)。"""
        session = {
            "id": uuid.uuid4().hex,
            "title": "",
            "created_at": time.time(),
            "messages": [],
        }
        self._write(session)
        return session

    def get(self, session_id: str) -> dict[str, Any]:
        """读会话(不存在 → FileNotFoundError,路由层归 404)。"""
        path = self._path(session_id)
        if not path.is_file():
            raise FileNotFoundError(f"会话不存在: {session_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def append(self, session_id: str, message: Message) -> dict[str, Any]:
        """追加消息并落盘;首条用户消息回填会话标题(列表可读性)。"""
        session = self.get(session_id)
        session["messages"].append(message)
        if not session["title"] and message["role"] == "user" and message.get("text"):
            session["title"] = message["text"][:40]
        session["last_at"] = message["ts"]
        self._write(session)
        return session

    def put_fields(self, session_id: str, **fields: Any) -> None:
        """写会话级元字段并落盘(M4b:主动汇报的 presented_runs 游标;
        与消息同文件,刷新/重启不丢)。"""
        session = self.get(session_id)
        session.update(fields)
        self._write(session)

    def list(self) -> list[dict[str, Any]]:
        """会话摘要列表(最近在前):{id, title, created_at, last_at, messages, cards}。"""
        out = []
        for f in sorted(self._root.glob("*.json"), key=lambda f: -f.stat().st_mtime):
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # 坏文件隔离(产物目录可被外部清理)
            msgs = s.get("messages", [])
            out.append(
                {
                    "id": s["id"],
                    "title": s.get("title") or "(空会话)",
                    "created_at": s.get("created_at", 0.0),
                    "last_at": s.get("last_at", s.get("created_at", 0.0)),
                    "messages": len(msgs),
                    "cards": sum(len(m.get("cards", [])) for m in msgs),
                }
            )
        return out

    def _write(self, session: dict[str, Any]) -> None:
        (self._root / f"{session['id']}.json").write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
        )

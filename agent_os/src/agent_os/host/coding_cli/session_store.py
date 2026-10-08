"""
会话持久化层(docs/RUNNERS.md §2.2 产物布局、§2.5、§3.3 错误归类):
管理 ``<root>/sessions/<session_id>.json`` 会话文档。

「暂停/恢复」会话模型(WS2):一个交互会话 = 长存 run + checkpoint resume。
宿主在进程被 kill/重启后依据会话文档重新定位会话:每轮 turn 记录
``{run_id, input, status, checkpoint_path, summary}``,其中 ``checkpoint_path``
是 RunPaused → checkpoint 落盘 → resume 的数据侧锚点;status 区分
``done``/``paused``/``failed``/``aborted``(§3.3 错误归类)。

写侧原子落盘(tmp + os.replace,shared/artifacts.py 先例):进程被 kill 时
不留半写 JSON,否则恢复时会话损坏。读侧半写防御(host/web/escalations.py
降级先例,语义不同):会话恢复涉及写入与 resume,损坏会话不应静默恢复,
故 :meth:`SessionStore.load` 选择抛 ``ValueError`` 阻断 resume;而
:meth:`SessionStore.list_sessions` 是枚举展示,选择跳过损坏文件静默降级。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TURN_FIELDS = ("run_id", "input", "status", "checkpoint_path", "summary")


class SessionStore:
    """
    管理 ``<root>/sessions/<session_id>.json`` 会话文档(暂停/恢复会话模型)。

    线程安全:本类不提供锁;调用方须自行串行化对同一会话的写操作
    (如 ``create``/``append_turn``),否则并发写可能相互覆盖丢数据。

    会话文档字段演进要保持向后兼容:新增字段只对写侧生效,读侧
    (含本类 ``list_sessions`` 与外部消费方)对缺失键一律用 ``dict.get`` 兜底。
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._sessions_dir = self._root / "sessions"
        # 预建会话目录(仿 artifacts 先例,避免首写时再处理目录竞态)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.json"

    def _write_json(self, path: Path, doc: dict[str, Any]) -> None:
        """
        原子落盘:同目录 tmp + ``os.replace``(同文件系统 rename 原子)——
        读者只见完整旧/新内容,堵半写窗口(进程被 kill 不留截断 JSON)。
        """
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=repr), encoding="utf-8")
        os.replace(tmp, path)

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        """读 JSON,半写防御:OSError/JSONDecodeError 返回 None(降级先例)。"""
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def create(
        self, session_id: str, skill: str, config_path: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        新建会话文档并返回;session_id 已存在时抛 ``FileExistsError``。

        文档字段:session_id/skill/config_path/overrides/created_at/updated_at/turns,
        turns 初始为空列表(§2.2 产物布局)。``overrides``(P2-M3):建会话时的 run
        覆盖选项(K1 §2.5,如 workdir)随文档落盘——``--resume`` 重启进程后由
        SessionRunner 继承,会话的工作目录/模型覆盖不丢(显式 flag 优先于存档)。
        """
        path = self._path(session_id)
        if path.exists():
            raise FileExistsError(f"会话已存在: {session_id}")
        now = datetime.now(UTC).isoformat()
        doc: dict[str, Any] = {
            "session_id": session_id,
            "skill": skill,
            "config_path": config_path,
            "overrides": dict(overrides or {}),
            "created_at": now,
            "updated_at": now,
            "turns": [],
        }
        self._write_json(path, doc)
        return doc

    def load(self, session_id: str) -> dict[str, Any]:
        """
        读回会话文档;缺失抛 ``FileNotFoundError``。

        半写文件(截断/非法 JSON)抛 ``ValueError``:损坏会话不应静默恢复,
        阻断 resume(§3.3 错误归类;与 list_sessions 的展示式降级语义不同)。
        """
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"会话不存在: {session_id}")
        doc = self._read_json(path)
        if doc is None:
            raise ValueError(f"会话文件损坏(半写或非法 JSON): {path}")
        return doc

    def append_turn(self, session_id: str, turn: dict[str, Any]) -> dict[str, Any]:
        """
        追加一轮 turn(须含 TURN_FIELDS:run_id/input/status/checkpoint_path/summary),
        更新 updated_at 后原子重写并返回更新后文档。

        ``checkpoint_path`` 是 RunPaused → checkpoint 落盘 → resume 的锚点(WS2)。
        """
        missing = [f for f in TURN_FIELDS if f not in turn]
        if missing:
            raise ValueError(f"turn 缺少必要字段: {missing}")
        doc = self.load(session_id)
        doc["turns"].append(dict(turn))
        doc["updated_at"] = datetime.now(UTC).isoformat()
        self._write_json(self._path(session_id), doc)
        return doc

    def latest_turn(self, session_id: str) -> dict[str, Any] | None:
        """
        返回会话 ``turns`` 列表的最后一个 turn(docs/RUNNERS.md §2.2 产物布局)。

        会话尚无 turn(空列表)时返回 ``None``。复用 :meth:`SessionStore.load`
        读档,异常语义与其一致:会话不存在抛 ``FileNotFoundError``、
        文件损坏(半写/非法 JSON)抛 ``ValueError``(§3.3 错误归类)。
        """
        doc = self.load(session_id)
        turns = doc.get("turns") or []
        return turns[-1] if turns else None

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        枚举全部会话,按 updated_at 降序(最新在前)。

        损坏文件静默跳过(escalations 式降级):列表是展示用途,单个坏文件
        不应拖垮整个枚举(与 load 的阻断式语义不同)。
        """
        docs: list[dict[str, Any]] = []
        for path in sorted(self._sessions_dir.glob("*.json")):
            doc = self._read_json(path)
            if doc is not None:
                docs.append(doc)
        return sorted(docs, key=lambda d: d.get("updated_at", ""), reverse=True)

"""RunRecord(docs/RUNNERS.md §3.3 输出契约;coding agent 的消费面)。

CLI 各子命令的 stdout JSON 统一经 :func:`dumps` 序列化——coding agent 只依赖
这一个 JSON schema,版本化 ``"v": 1``(§3.5)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: RunRecord JSON schema 版本(§3.3)
RUN_RECORD_VERSION = 1

#: run 终态三值(§3.3)
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_ABORTED = "aborted"


def make_record(
    *,
    run_id: str,
    status: str,
    result: Any,
    error: str | None,
    usage: dict[str, Any],
    frames: int,
    run_dir: Path,
) -> dict[str, Any]:
    """组一版 RunRecord dict(``"v": 1``;artifacts 三件套路径,§2.2)。"""
    return {
        "v": RUN_RECORD_VERSION,
        "run_id": run_id,
        "status": status,
        "result": result,
        "error": error,
        "usage": usage,
        "frames": frames,
        "artifacts": {
            "dir": str(run_dir),
            "trace": str(run_dir / "trace.jsonl"),
            "checkpoint": str(run_dir / "checkpoint.json"),
        },
    }


def dumps(record: dict[str, Any]) -> str:
    """RunRecord → 单行 JSON(全部子命令的统一序列化出口,§3.5)。"""
    return json.dumps(record, ensure_ascii=False, default=repr)

"""失败定位(docs/RUNNERS.md §4.4 "Jump to first error";R4):从产物读首个错误的结构化位置。

数据源是该 run 产物目录的 ``result.json`` + ``checkpoint.json``(§2.2;checkpoint
帧的完整上下文是 RCA 核心,§2.3)。检测顺序:

1. 按 checkpoint 帧的**入栈顺序**(文件内列表序,``FrameStack.tree()`` 即插入序)
   逐帧扫描 ``context.messages``,找第一条 ``ok=false`` 的 TOOL 消息(错误观察,
   content 为 ``{"ok": false, "error": {...}}`` 的 JSON):
   ``error.kind == "vetoed"`` → ``vetoed``(sidecar 否决,理由回写,§5.2),
   其余 → ``tool_error``;``call`` 用该消息的 ``tool_call_id`` 反查同帧 assistant
   消息的 tool_calls 得 ``{"name", "args"}``;
2. 无错误观察且 run 已 failed/aborted → ``aborted``:message 取 result.json 的
   error,帧取**最深的未完成帧**(checkpoint 状态非 "done" 者中 depth 最大);
3. 都没有 → ``first_error = null``(run 健康,或仍在进行)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_os.host.shared.artifacts import read_checkpoint, read_result

#: usage 面板按帧分列的字段(§4.4:steps/tokens/cache/thinking/cost,Usage 子集)
USAGE_FRAME_KEYS = (
    "steps",
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "cost",
)


def _error_observation(content: Any) -> dict[str, Any] | None:
    """TOOL 消息 content → 错误观察 payload;非 JSON / 非 ok=false 时返回 None。"""
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return None
    if not isinstance(payload.get("error"), dict):
        return None
    return payload


def _find_call(messages: list[dict[str, Any]], tool_call_id: Any) -> dict[str, Any] | None:
    """用 ``tool_call_id`` 反查同帧 assistant 消息的 tool_calls → ``{"name", "args"}``。"""
    if tool_call_id is None:
        return None
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if call.get("id") == tool_call_id:
                return {"name": call.get("name"), "args": call.get("args")}
    return None


def _deepest_unfinished(frames: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最深的未完成帧(checkpoint 状态非 "done");全部完成时退回全树最深帧。"""
    unfinished = [f for f in frames if f.get("status") != "done"]
    pool = unfinished or frames
    return max(pool, key=lambda f: f.get("depth") or 0, default=None)


def locate_first_error(run_dir: str | Path) -> dict[str, Any]:
    """产物目录 → ``{"status", "first_error": {...} | None}``(检测顺序见模块 docstring)。

    result.json / checkpoint.json 缺失或半写(落盘窗口)按空文档降级,不抛错——
    在途 run 也能查询,只是 ``first_error`` 为 null。
    """
    try:
        result_doc = read_result(run_dir)
    except (OSError, json.JSONDecodeError):
        result_doc = {}
    try:
        checkpoint = read_checkpoint(run_dir)
    except (OSError, json.JSONDecodeError):
        checkpoint = {}
    status = result_doc.get("status")
    frames = checkpoint.get("frames") or []
    for frame in frames:  # 文件内列表序 = 入栈顺序(§4.4)
        messages = (frame.get("context") or {}).get("messages") or []
        for msg in messages:
            if msg.get("role") != "tool":
                continue
            payload = _error_observation(msg.get("content"))
            if payload is None:
                continue
            error = payload["error"]
            return {
                "status": status,
                "first_error": {
                    "kind": "vetoed" if error.get("kind") == "vetoed" else "tool_error",
                    "frame_id": frame.get("frame_id"),
                    "skill": frame.get("skill"),
                    "call": _find_call(messages, msg.get("tool_call_id")),
                    "message": error.get("message"),
                },
            }
    if status in ("failed", "aborted"):
        frame = _deepest_unfinished(frames)
        return {
            "status": status,
            "first_error": {
                "kind": "aborted",
                "frame_id": (frame or {}).get("frame_id"),
                "skill": (frame or {}).get("skill"),
                "call": None,
                "message": result_doc.get("error"),
            },
        }
    return {"status": status, "first_error": None}


def usage_panel(run_dir: str | Path) -> dict[str, Any]:
    """产物目录 → ``{"run": {...九字段}, "frames": [...]}``(§4.4 usage 面板按帧分列)。

    数据源只有 checkpoint.json:run 级为九字段 Usage 全量,帧级为
    ``USAGE_FRAME_KEYS`` 子集 + frame_id/skill/depth/status。
    """
    checkpoint = read_checkpoint(run_dir)
    frames = [
        {
            "frame_id": f.get("frame_id"),
            "skill": f.get("skill"),
            "depth": f.get("depth"),
            "status": f.get("status"),
            **{k: (f.get("usage") or {}).get(k, 0) for k in USAGE_FRAME_KEYS},
        }
        for f in checkpoint.get("frames") or []
    ]
    return {"run": (checkpoint.get("run") or {}).get("usage") or {}, "frames": frames}

"""复现层(RUNNERS.md §3.4;replay 脚本重建与 run 结构化 diff,两个 runner 共用)。

replay:runner 只在帧上下文追加 LLM 响应(§3.1),故 trace 中**第 N 个属于帧 F 的
``post:llm.response`` 信号 ↔ checkpoint 中帧 F 的第 N 条 role=="assistant" 消息**;
按信号出现顺序取出这些消息即可重建 MockProvider 脚本,确定性重放整棵帧树
(不碰真实 API;工具副作用仍按真实环境执行,§3.4 边界)。

diff:两次 run 的信号序列按 ``(name, payload.skill, payload.tool, payload.ok,
payload.depth)`` 逐位比较(缺失为 None,忽略 frame_id/ts 等动态值),外加
result 与 usage 汇总——回归判断(§3.4 用途 A)与复现排查的消费面。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_os.api.v1 import (
    POST_LLM_RESPONSE,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    Source,
    ToolCall,
)
from agent_os.host.shared.artifacts import read_checkpoint, read_result, read_trace
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider


def _message_from_dict(data: dict[str, Any]) -> Message:
    """checkpoint 消息 dict → 契约 Message(与 §10.2 checkpoint schema v1 序列化互逆)。"""
    return Message(
        role=Role(data["role"]),
        content=data.get("content", ""),
        tool_calls=[
            ToolCall(id=tc.get("id", ""), name=tc.get("name", ""), args=tc.get("args", {}))
            for tc in data.get("tool_calls", [])
        ],
        tool_call_id=data.get("tool_call_id"),
        name=data.get("name"),
        reasoning=data.get("reasoning"),
        source=Source(data.get("source", "system")),
        meta=data.get("meta", {}),
    )


def build_mock_script(run_dir: str | Path) -> list[ChatResponse]:
    """产物目录 → 按 trace 顺序重放的 MockProvider 脚本(§3.4;对齐规则见模块 docstring)。

    ``finish_reason`` 按有无 tool_calls 定;``usage`` 取信号载荷(prompt/completion/
    cost,其余维度 trace 未记录,置零)。信号缺对应帧或 assistant 消息时抛
    ``ValueError``(宿主归退出码 2;trace/checkpoint 文件畸形同样归此)。
    """
    rows = read_trace(run_dir)
    checkpoint = read_checkpoint(run_dir)
    frames = {f["frame_id"]: f for f in checkpoint.get("frames", [])}
    responses = [
        row
        for row in rows
        if row.get("type") == "signal" and row.get("name") == POST_LLM_RESPONSE
    ]
    seen: dict[str, int] = {}  # frame_id → 该帧已对齐的响应数
    script: list[ChatResponse] = []
    for row in responses:
        frame_id = row.get("frame_id")
        n = seen.get(frame_id, 0)
        seen[frame_id] = n + 1
        frame = frames.get(frame_id)
        if frame is None:
            raise ValueError(f"post:llm.response 的帧 {frame_id} 不在 checkpoint 中")
        assistants = [
            m
            for m in (frame.get("context") or {}).get("messages", [])
            if m.get("role") == "assistant"
        ]
        if n >= len(assistants):
            raise ValueError(f"帧 {frame_id} 的第 {n + 1} 个响应信号无对应 assistant 消息")
        message = _message_from_dict(assistants[n])
        usage = (row.get("payload") or {}).get("usage") or {}
        script.append(
            ChatResponse(
                message=message,
                finish_reason="tool_calls" if message.tool_calls else "stop",
                usage=ChatUsage(
                    prompt=usage.get("prompt", 0),
                    completion=usage.get("completion", 0),
                    cost=usage.get("cost", 0.0),
                ),
            )
        )
    return script


def replace_providers(kernel: Any, script: list[ChatResponse]) -> None:
    """把内核的 provider 面整体替换为回放脚本(MockProvider 单例 + 新 ProviderManager)。

    MockProvider 注册名取 config model 的前缀(``"mock/fib"`` → ``"mock"``),
    模型路由与原 run 自然吻合;原 run 用真实 provider 时回放同样落在 mock 上。
    """
    prefix = (kernel.config.model or "").partition("/")[0]
    kernel.providers = ProviderManager([MockProvider(script=script, name=prefix or None)])


#: diff 逐信号比较的载荷字段(§3.2;缺失为 None,frame_id/ts 等动态值忽略)
_DIFF_PAYLOAD_KEYS = ("skill", "tool", "ok", "depth")


def _signal_key(row: dict[str, Any]) -> tuple[Any, ...]:
    payload = row.get("payload") or {}
    return (row.get("name"), *(payload.get(k) for k in _DIFF_PAYLOAD_KEYS))


def diff_runs(run_dir_a: str | Path, run_dir_b: str | Path) -> dict[str, Any]:
    """两次 run 的结构化 diff(§3.2;查询而非判决,报告不含退出语义)。

    ``first_divergence``:首个信号 key 不一致的下标;一方是另一方前缀时为
    较短者的长度(即首个"一边有信号、另一边没有"的位置);完全一致为 None。
    """
    result_a = read_result(run_dir_a)
    result_b = read_result(run_dir_b)
    keys_a = [_signal_key(r) for r in read_trace(run_dir_a) if r.get("type") == "signal"]
    keys_b = [_signal_key(r) for r in read_trace(run_dir_b) if r.get("type") == "signal"]
    first = next((i for i, (ka, kb) in enumerate(zip(keys_a, keys_b)) if ka != kb), None)
    if first is None and len(keys_a) != len(keys_b):
        first = min(len(keys_a), len(keys_b))
    return {
        "v": 1,
        "result_equal": result_a.get("result") == result_b.get("result"),
        "signals_equal": keys_a == keys_b,
        "signal_counts": {"a": len(keys_a), "b": len(keys_b)},
        "usage": {"a": result_a.get("usage") or {}, "b": result_b.get("usage") or {}},
        "first_divergence": first,
    }

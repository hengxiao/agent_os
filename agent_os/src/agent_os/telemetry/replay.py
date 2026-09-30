"""replay 脚本重建(docs/RUNNERS.md §3.4)——自 host/shared/replay.py 下沉(2026-09-30)。

下沉动机:register() 验证门的录制重放证据(skills/register_smoke.py)同样要消费
``build_mock_script``/``replace_providers``,而 skills/ 层不反向依赖 host/;
telemetry/ 层(只依赖 api.*/providers.*)是双方都能到达的最低点。
host/shared/replay.py 保留 re-export,CLI/Web 消费面(cli/main.py、cli/debug.py、
web/run_manager.py、web/app.py)零改动。

对齐规则(模块级契约):runner 只在帧上下文追加**主循环** LLM 响应(§3.1),故
trace 中**第 N 个属于帧 F 的 ``post:llm.response`` 信号 ↔ checkpoint 中帧 F 的
第 N 条 role=="assistant" 消息**;按信号出现顺序取出这些消息即可重建
MockProvider 脚本,确定性重放整棵帧树(不碰真实 API;工具副作用仍按真实环境
执行,§3.4 边界)。

**压缩再排放必须过滤**:压缩链排干(kernel/runner.py ``_drain_compress_usage``)
会为每次压缩 LLM 调用补发 ``payload.source == "compress"`` 的
``post:llm.response``(让 BudgetGuard/遥测看到完整成本)——这类信号**没有对应的
assistant 消息**(压缩改写既有消息,不向帧上下文追加)。若计入对齐,压缩信号会
消耗主循环响应的下标:轻则重建脚本错位/少一条响应,重则直接 ValueError
("帧 F 的第 N 个响应信号无对应 assistant 消息")。故本模块按
``payload.source == "compress"`` 过滤后再对齐——这同时修复了压缩 run 的
CLI replay(此前必然错位或报错,属有意的行为修正)。
"""

from __future__ import annotations

import json
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
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider


def _read_trace(run_dir: str | Path) -> list[dict[str, Any]]:
    """读产物目录的 trace.jsonl,逐行 JSON(host/shared/artifacts.py read_trace 同款,
    私有化防反向依赖;版本头行原样保留,由调用方过滤)。"""
    return [
        json.loads(line)
        for line in (Path(run_dir) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_checkpoint(run_dir: str | Path) -> dict[str, Any]:
    """读产物目录的 checkpoint.json(§10.2 schema v1;私有化理由同上)。"""
    return json.loads((Path(run_dir) / "checkpoint.json").read_text(encoding="utf-8"))


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

    ``finish_reason`` 按有无 tool_calls 定;``usage`` 取信号载荷——prompt/
    completion/cost 为基线三维,cache/thinking 三维与 ttft/total_ms 读回 trace
    已记录维度(additive 扩展,见 runner._usage_payload;旧 trace 缺键置零)。
    压缩排干补发的 ``payload.source == "compress"`` 信号**不计入对齐**(无对应
    assistant 消息,见模块 docstring)。信号缺对应帧或 assistant 消息时抛
    ``ValueError``(宿主归退出码 2;trace/checkpoint 文件畸形同样归此)。
    """
    rows = _read_trace(run_dir)
    checkpoint = _read_checkpoint(run_dir)
    frames = {f["frame_id"]: f for f in checkpoint.get("frames", [])}
    responses = [
        row
        for row in rows
        if row.get("type") == "signal"
        and row.get("name") == POST_LLM_RESPONSE
        and (row.get("payload") or {}).get("source") != "compress"  # 压缩再排放不对齐
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
                    cache_read=usage.get("cache_read_tokens", 0),
                    cache_write=usage.get("cache_write_tokens", 0),
                    thinking=usage.get("thinking_tokens", 0),
                ),
                ttft_ms=usage.get("ttft_ms", 0),
                total_ms=usage.get("total_ms", 0),
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

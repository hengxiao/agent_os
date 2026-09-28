"""std/task 技能 handler(WS1;STDLIB §4 任务调度面)。

``set_timer``:``system.timer.set`` 内核原语的技能形态——技能是经白名单的薄适配
(参数校验/钳制/生命周期都在工具层一份,见 agent_os.tools.timer),参数透传,
返回 ``{timer_id}``。
"""

from __future__ import annotations

from typing import Any


async def set_timer(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{delay_seconds?|interval_seconds?, count?, note?}`` → ``{timer_id}``:透传到 system.timer.set。"""
    tool_args: dict[str, Any] = {}
    for key in ("delay_seconds", "interval_seconds", "count", "note"):
        if args.get(key) is not None:
            tool_args[key] = args[key]
    payload = await ctx.call_tool("system.timer.set", tool_args)
    if not payload.get("ok"):
        error = payload.get("error") or {}
        raise ValueError(
            f"set_timer 调用失败({error.get('kind', '?')}): {error.get('message', '?')}"
        )
    return {"timer_id": str(payload["value"]["timer_id"])}

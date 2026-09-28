"""std/user 技能 handler(WS1;STDLIB §4 用户交互面)。

``ask_human``:``system.user.ask`` 工具面的技能形态——技能是经白名单的薄适配
(交互语义/宿主通道都在工具层一份,见 agent_os.tools.builtins.ask_user_tool),
``context`` 拼进问题文本(宿主通道契约只有 ``ask(question: str)`` 一个槽位)。
"""

from __future__ import annotations

from typing import Any


async def ask_human(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{question, context?}`` → ``{answer}``:经白名单调 system.user.ask,原样返回回答。"""
    question = str(args["question"])
    context = args.get("context")
    if context:
        question = f"{question}\n\n背景: {context}"
    payload = await ctx.call_tool("system.user.ask", {"question": question})
    if not payload.get("ok"):
        error = payload.get("error") or {}
        raise ValueError(
            f"ask_human 调用失败({error.get('kind', '?')}): {error.get('message', '?')}"
        )
    return {"answer": str(payload.get("value") or "")}

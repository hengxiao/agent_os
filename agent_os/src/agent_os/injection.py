"""指令注入扫描共用正则集(ch08③轻量版,中英常见注入短语)。

提取自 context/manager.py 的经验参考段扫描(MCP 工具描述扫描同用,
docs/DESIGN.md §8.3 供应链清单):不可信文本(历史 run 的工具产出 / 第三方
MCP server 的工具描述)命中任一短语即视为可疑;降级策略由调用方定——
recall 命中即跳过整条经验(宁缺毋滥),MCP 命中即整段弃用工具描述。
"""

from __future__ import annotations

import re

__all__ = ["INJECTION_RES", "looks_suspicious"]

#: 注入扫描正则集:命中即视为可疑(轻量启发式,宁可误伤不可放过)
INJECTION_RES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"忽略(之前|以上|上述)(的)?(指令|指示|消息|prompt)",
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|messages?|prompts?)",
        r"disregard\s+(all\s+)?(previous|prior|above)",
        r"系统(指令|设定|提示词)",
        r"(从现在|以后|接下来)(开始|起)?你(必须|要|应该)",
        r"you\s+(must|shall|should)\s+(always|from\s+now\s+on)",
        r"always\s+do\s",
        r"system\s*:\s",
        r"new\s+instructions?\s*:",
        r"override\s+(your\s+)?(instructions?|rules?|system\s+prompt)",
    )
)


def looks_suspicious(content: str) -> bool:
    """注入扫描:内容命中任一注入短语即视为可疑(降级策略由调用方决定)。"""
    return any(rx.search(content) for rx in INJECTION_RES)

"""PII 脱敏 hook(docs/DESIGN.md §10.2 "PII 脱敏 hook" 条:落盘前可插拔清洗,默认关闭)。

§10.2 的混合方案(regex 快筛 + 本地小模型深扫)本模块只做 **regex 快筛**
一翼:五形态模式表与 ``std/transform.py`` 的 ``_PII_PATTERNS``
(W2-13 redact_pii 技能)逐字同源——``std/`` 是按路径加载的 skillset 目录
(非可导入包:无 ``__init__.py``、不在 ``src/agent_os`` 轮子内),无法干净
import,此处内联复刻(标记重复,同 register_smoke 里程碑对 learn_handlers
的先例);形态/正则语义改动须两处同步。

替换形为 ``[EMAIL]`` 式大写占位(与 redact_pii 技能一致);重叠区间按模式
表序先到先得(如 18 位身份证先于 16-19 位卡号认领,同 std 先例)。
"""

from __future__ import annotations

import re
from typing import Any

#: 五形态 PII 快筛表(std/transform.py ``_PII_PATTERNS`` 的内联复刻,见模块 docstring)
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone_cn", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card_cn", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("api_key", re.compile(
        r"\b(?:sk|pk)-[A-Za-z0-9]{16,}\b|\bAKIA[0-9A-Z]{16}\b|(?i:bearer\s+[A-Za-z0-9._-]{16,})")),
]


def _redact_text(text: str) -> str:
    """单串脱敏:按表序认领命中区间(重叠跳过),从左到右替换为 ``[KIND]`` 占位。"""
    hits: list[tuple[int, int, str]] = []
    for kind, pattern in _PII_PATTERNS:
        for m in pattern.finditer(text):
            # 已被更高优先级形态认领的区间跳过(如 18 位身份证先于卡号)
            if any(m.start() < e and s < m.end() for s, e, _ in hits):
                continue
            hits.append((m.start(), m.end(), kind))
    if not hits:
        return text
    hits.sort()
    out: list[str] = []
    pos = 0
    for s, e, kind in hits:
        out.append(text[pos:s])
        out.append(f"[{kind.upper()}]")
        pos = e
    out.append(text[pos:])
    return "".join(out)


def redact_payload(value: Any) -> Any:
    """递归脱敏:dict/list/tuple 逐层下钻,str 走五形态快筛,其余类型原样返回。

    - dict 只脱敏 **值**(键是内核生成的字段名,脱敏会破坏 WAL schema 稳定);
    - 幂等:``[EMAIL]`` 等占位不再命中任何模式,重复脱敏结果不变;
    - 容器重建新对象,入参不被改写;
    - 不记录/不外发原文(脱敏器自身不得成为泄露面)。
    """
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {k: redact_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_payload(item) for item in value)
    return value

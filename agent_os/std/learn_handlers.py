"""std/learn handler(STDLIB-CATALOG §W4-4;STDLIB §4.2 后的自进化闭环)。

``verify_before_store``:入库闸门——重放结果与期望做**结构归一后的深比较**,
pass 才准入库(书的论断:没有验证门,自我改进循环必然腐烂;跑通的一次性
编排脚本 + verify_before_store → learned/ code skill,是自进化闭环的最短路径)。
纯计算,零 LLM、零外部依赖。
"""

from __future__ import annotations

import json
from typing import Any


def _normalize(value: Any) -> Any:
    """结构归一:dict 键排序(递归);tuple → list;int/float 统一为 float;
    bool 加类型标签(Python 里 ``True == 1.0``,不打标会被数字混同);
    非 JSON 类型按"类型名 + 字符串化"兜底。"""
    if isinstance(value, dict):
        return {str(k): _normalize(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, bool):
        return ("__bool__", value)
    if isinstance(value, (int, float)):
        return float(value)
    if value is None or isinstance(value, str):
        return value
    return (type(value).__name__, str(value))


async def verify_before_store(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """入库闸门:replay_result == expected(结构归一后)→ pass,否则 fail 并给差异。"""
    replay = _normalize(args.get("replay_result"))
    expected = _normalize(args.get("expected"))
    if replay == expected:
        return {"verdict": "pass", "reason": "重放结果与期望一致(结构归一后)"}
    return {
        "verdict": "fail",
        "reason": "重放结果与期望不一致: replay="
        + json.dumps(replay, ensure_ascii=False, default=str)
        + " expected="
        + json.dumps(expected, ensure_ascii=False, default=str),
    }

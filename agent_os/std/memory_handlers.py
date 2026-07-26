"""std/memory 文件版 handler(STDLIB-CATALOG §W4-2;STDLIB §4.7)。

- ``memory_consolidate``:周期重构——重要性评分(访问频率 + 时间衰减)剪枝 +
  近重复去重(唯一性启发);低分与重复项进 ``pruned`` 并带理由,不静默丢弃。
- ``memory_check``:约束交叉校验——同实体同属性两个不同值即冲突(规则化
  "主语+属性→值"抽取,无需完美 NLP,覆盖数字+单位与"是/为/:"两种锚)。

两件都是纯计算,零 LLM、零外部依赖;时钟可经 ``today`` 形参注入(缺省本机日),
测试可钉死。
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

#: 保留阈值:score = 2·log1p(access_count) + 1/(1+age_days/180);低于则剪枝
_KEEP_THRESHOLD = 1.0

#: 近重复判定:词集合 Jaccard 上限(达到即视为同一事实的两种说法)
_DUP_JACCARD = 0.8

#: "值锚"正则:数字 + 常见单位(年月日/元/个数/次/岁/% 等)
_VALUE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(年|月|日|号|元|块|公斤|千克|斤|米|厘米|个|只|张|次|岁|天|小时|分钟|%|度|层|辆|台|件|条|本|部|篇|章|节)"
)

#: "X 是/为/: Y" 锚(键值最直白的形态)
_BE_RE = re.compile(r"^(?P<key>.+?)(?:是|为|:|：)(?P<value>.+)$")

_TRAILING_VERBS_RE = re.compile(r"(?:是|为|有|于|在|到|至)$")
_KEY_STRIP_CHARS = " ,，、。.;；"


def _parse_day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def _score(entry: dict[str, Any], today: date) -> float:
    """重要性评分:访问频率(对数压缩) ×2 + 时间衰减(半年约 0.5)。"""
    access = entry.get("access_count") or 0
    freq = math.log1p(float(access))
    created = _parse_day(entry.get("created_at"))
    age_days = max(0, (today - created).days) if created else 3650
    return 2.0 * freq + 1.0 / (1.0 + age_days / 180.0)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[一-鿿]", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


async def memory_consolidate(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """评分剪枝 + 近重复去重,返回 {entries: [保留项], pruned: [带理由的被剪枝项]}。"""
    entries = [dict(e) for e in args.get("entries") or [] if isinstance(e, dict) and e.get("fact")]
    today = _parse_day(args.get("today")) or date.today()
    threshold = float(args.get("keep_threshold", _KEEP_THRESHOLD))
    for e in entries:
        e["score"] = round(_score(e, today), 4)
    kept: list[dict[str, Any]] = []
    pruned: list[dict[str, Any]] = []
    for e in sorted(entries, key=lambda item: -item["score"]):
        dup_of = next(
            (
                k
                for k in kept
                if _jaccard(_tokens(str(k["fact"])), _tokens(str(e["fact"]))) >= _DUP_JACCARD
            ),
            None,
        )
        if dup_of is not None:
            pruned.append({**e, "reason": f"与保留项近重复: {dup_of['fact']!r}"})
        elif e["score"] < threshold:
            pruned.append({**e, "reason": f"评分 {e['score']} 低于保留阈值 {threshold}"})
        else:
            kept.append(e)
    return {"entries": kept, "pruned": pruned}


def _claim(fact: str) -> tuple[str, str] | None:
    """从一条事实抽取 (键, 值):优先 "X 是/为/: Y",其次首个"数字+单位"锚。

    键 = 值锚前的名词性前缀(去尾部动词/介词/空白,空白归一);值 = 锚文本。
    无锚 → None(该条不参与冲突判定)。
    """
    m = _BE_RE.match(fact.strip())
    if m:
        key = m.group("key").strip(_KEY_STRIP_CHARS)
        value = re.split(r"[，,。;；]", m.group("value"))[0].strip()
        if key and value:
            return (re.sub(r"\s+", "", key), re.sub(r"\s+", "", value))
    m2 = _VALUE_RE.search(fact)
    if m2:
        key = fact[: m2.start()].strip(_KEY_STRIP_CHARS)
        key = _TRAILING_VERBS_RE.sub("", key).strip()
        if key:
            return (re.sub(r"\s+", "", key), f"{m2.group(1)}{m2.group(2)}")
    return None


async def memory_check(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """同键不同值 → 冲突(如"护照有效期"记下两个年份,不得并存)。"""
    claims: dict[str, set[str]] = {}
    for e in args.get("entries") or []:
        if not isinstance(e, dict):
            continue
        claim = _claim(str(e.get("fact") or ""))
        if claim is None:
            continue
        key, value = claim
        claims.setdefault(key, set()).add(value)
    conflicts = [
        {"key": key, "values": sorted(values)}
        for key, values in claims.items()
        if len(values) > 1
    ]
    conflicts.sort(key=lambda c: c["key"])
    return {"ok": not conflicts, "conflicts": conflicts}

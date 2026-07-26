"""std/eval code handler(STDLIB-CATALOG §W3-4/W3-5;STDLIB §4.4)。

- ``calibrate_judge``:零 LLM 纯计算——金标准人工分 × judge 分 → 一致率 +
  Cohen's kappa(质量体系的元层本身是确定性的,"能不用 LLM 就不用")。ctx 不触碰。
- ``pairwise_compare``:TRUSTED 编排——经 ``ctx.invoke`` 对同包私有依赖
  ``pairwise_judge_once`` 做内建交换顺序两评,两评胜者标签不一致判平
  (W3-5:位置偏见防控是技能定义的一部分,不是可选后处理)。
"""

from __future__ import annotations

from typing import Any

#: kappa 通过门禁(STDLIB §4.4:kappa ≥ 0.7 是 judge 进 std 的门禁)
KAPPA_THRESHOLD = 0.7


def _cohen_kappa(human: list[Any], judge: list[Any]) -> float:
    """Cohen's kappa(类别标签口径)。

    pe=1(两侧边际分布都只剩单一类别)时 kappa 数学上无定义,按约定退化:
    全一致 → 1.0,否则 → 0.0。类别标签须可哈希(标量),否则 TypeError 自然上抛。
    """
    n = len(human)
    po = sum(1 for h, j in zip(human, judge) if h == j) / n
    categories = set(human) | set(judge)
    pe = sum((human.count(c) / n) * (judge.count(c) / n) for c in categories)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


async def calibrate_judge(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{gold: [{item, human_score}], judge_scores: [...]}`` → ``{agreement, kappa, passes}``。

    judge_scores 与 gold 按下标一一对齐;长度不等直接报错(静默截断会校准出假一致性)。
    """
    gold = input.get("gold")
    judge_scores = input.get("judge_scores")
    if not isinstance(gold, list) or not gold:
        raise TypeError("gold 必须是非空数组")
    if not isinstance(judge_scores, list):
        raise TypeError("judge_scores 必须是数组")
    if len(gold) != len(judge_scores):
        raise ValueError(
            f"judge_scores 与 gold 长度不等({len(judge_scores)} != {len(gold)}):"
            "两者须按下标一一对齐"
        )
    human = [g.get("human_score") if isinstance(g, dict) else None for g in gold]
    judge = list(judge_scores)
    # 类别口径要求标签可哈希;非标量在此报可读错误,而不是让 set() 抛
    # "unhashable type"(§W0-3:错误须指向下一步动作)。schema 层已收紧 items,
    # 这里是直接调用路径的防御。
    for label, seq in (("gold[].human_score", human), ("judge_scores[]", judge)):
        bad = next((v for v in seq if isinstance(v, (dict, list, set))), None)
        if bad is not None:
            raise TypeError(
                f"{label} 必须是标量类别标签(字符串/数字/布尔/null),得到 "
                f"{type(bad).__name__};judge_scores 是与 gold 等长的**扁平**分数数组,"
                "不是 [{item, score}] 对象数组"
            )
    agreement = sum(1 for h, j in zip(human, judge) if h == j) / len(human)
    kappa = _cohen_kappa(human, judge)
    return {
        "agreement": round(agreement, 6),
        "kappa": round(kappa, 6),
        "passes": kappa >= KAPPA_THRESHOLD,
    }


def _winner_label(value: Any) -> str | None:
    """评审输出归一化为 A/B 标签;无法识别按 None 处理(落入判平分支)。"""
    label = str(value or "").strip().upper()
    return label if label in ("A", "B") else None


async def pairwise_compare(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{a, b, question}`` → 内建交换顺序两评 → ``{winner, rounds}``。

    第二轮把 a/b 对调后再评。判定看的是**同一份内容**是否两轮都赢,而槽位标签
    随对调必然翻转,故:

    - ``w1 != w2``(A→B 或 B→A):同一内容两轮都赢 → 结论稳定,取该内容为胜者
      (``w1 == "A"`` ⇒ 第一轮槽 A 是 ``a`` 获胜 ⇒ 胜者为 ``a``);
    - ``w1 == w2``:裁判两轮都选了**同一个槽位** → 位置偏见,结论不可信 → 判平;
    - 任一轮标签无法识别 → 判平。

    轮数恒为 2。位置偏见防控是本技能定义的一部分,不是可选后处理(§W3-5)。
    """
    a, b, question = input.get("a"), input.get("b"), input.get("question")
    first = await ctx.invoke("pairwise_judge_once", {"a": a, "b": b, "question": question})
    second = await ctx.invoke("pairwise_judge_once", {"a": b, "b": a, "question": question})
    w1 = _winner_label(first.get("winner") if isinstance(first, dict) else None)
    w2 = _winner_label(second.get("winner") if isinstance(second, dict) else None)
    if w1 is not None and w2 is not None and w1 != w2:
        return {"winner": a if w1 == "A" else b, "rounds": 2}
    return {"winner": "tie", "rounds": 2}

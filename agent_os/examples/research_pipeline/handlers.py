"""research_pipeline 示例的 code 技能 handlers(调试用途)。

供 ``skills.yaml`` 中 11 个 code 技能经 dotted path ``handlers:<name>`` 惰性加载;
全部为确定性纯函数式协程(无 LLM、无 IO),便于 CLI/Web 调试时逐帧核对输入输出。
签名约定(DESIGN.md §6.3,同 tests/code_skill_helpers.py):
``async def run(input: dict, ctx: LogicContext) -> dict``。
"""

from __future__ import annotations

import hashlib
from typing import Any

#: fetch_source 的预制语料(≥3 种);按主题哈希确定性选段,句读保证 extract_facts 有事实可切。
_CORPUS = [
    (
        "菲波拉契数列由递推定义,前两项为 0 与 1。相邻两项之比趋近黄金分割。"
        "该数列出现在花瓣排列中。它在算法教学中常被用作递归示例。"
    ),
    (
        "黄金分割约为 1.618。它在建筑与绘画构图中被广泛使用。"
        "许多自然形态近似符合该比例。它与连分数展开密切相关。"
    ),
    (
        "递归需要明确的基例。每层递归都会消耗栈空间。尾递归可以被部分解释器优化。"
        "不当的递归会导致栈溢出。"
    ),
    (
        "分治策略把问题拆成子问题。归并排序是分治的典型例子。动态规划复用子问题解。"
        "贪心算法每步取局部最优。回溯法会系统搜索解空间。"
    ),
]


def _digest(text: str) -> int:
    """跨进程稳定的文本散列(内置 hash 有盐,不可用于确定性 demo)。"""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)


async def plan_topics(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """确定性返回恰好 3 个主题:按空白切词去重,不足以"问题·方面 i"补齐,超出截断。"""
    question = input["question"]
    topics: list[str] = []
    for word in question.split():
        if word not in topics:
            topics.append(word)
    i = 1
    while len(topics) < 3:
        candidate = f"{question}·方面{i}"
        if candidate not in topics:
            topics.append(candidate)
        i += 1
    return {"topics": topics[:3]}


async def fetch_source(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """按主题哈希从预制语料确定性选段(同一主题永远同一段)。"""
    return {"text": _CORPUS[_digest(input["topic"]) % len(_CORPUS)]}


async def verify_facts(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """确定性过滤:去重、丢弃不足 2 字的碎片,保序。"""
    verified: list[str] = []
    for fact in input["facts"]:
        if len(fact) >= 2 and fact not in verified:
            verified.append(fact)
    return {"verified": verified}


async def merge_summaries(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """把各主题摘要按序拼成一份素材(一行一条)。"""
    return {"merged": "\n".join(input["summaries"])}


async def fact_check(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """确定性核查:草稿非空且含"结论"即通过。"""
    draft = input["draft"]
    issues = [] if draft.strip() and "结论" in draft else ["草稿为空或缺少结论段"]
    return {"ok": not issues, "issues": issues}


async def finalize_report(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """草稿加引用拼终稿;words = report 按空白分词数(锚点断言口径)。"""
    draft = input["draft"]
    citations = input["citations"]
    refs = "\n".join(f"- {c}" for c in citations)
    report = f"# 研究报告\n\n{draft}\n\n## 引用\n{refs}\n"
    return {"report": report, "words": len(report.split()), "citations": citations}


async def format_markdown(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """给纯文本包一层 markdown 标题(凑数工具技能)。"""
    return {"markdown": f"# 文档\n\n{input['text']}\n"}


async def count_words(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """按空白分词计数(凑数工具技能)。"""
    return {"count": len(input["text"].split())}


async def validate_report(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """确定性结构校验:必须含 markdown 标题与引用节(凑数工具技能)。"""
    report = input["report"]
    problems = []
    if "#" not in report:
        problems.append("缺少标题行")
    if "引用" not in report:
        problems.append("缺少引用节")
    return {"valid": not problems, "problems": problems}


async def split_tasks(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """把任务确定性拆成 3 个子任务(凑数工具技能)。"""
    task = input["task"]
    return {"subtasks": [f"{task}·子任务{i}" for i in range(1, 4)]}


async def citation_builder(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """由来源文本构造确定性引用串(凑数工具技能)。"""
    source = input["source"]
    return {"citation": f"资料[{source[:12]}](编号 {_digest(source) % 10000:04d})"}

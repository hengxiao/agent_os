"""检索排序纯函数(docs/STDLIB-CATALOG.md §W2-9/W2-10;WS-A,M6 Memory 铺路)。

std 检索技能(``std/transform.py`` 的 ``common.retrieval.bm25_score`` /
``common.retrieval.rrf_merge`` handler)与 MemoryService baseline
(``agent_os.memory.local_file.LocalFileMemoryService.search``)共用的**唯一**
BM25/RRF 实现——STDLIB-CATALOG.md:215 划线:bm25 是 ``memory_search`` 的默认实现,
不允许第二份实现口径漂移。全部零依赖(仅标准库)、确定性、无状态
(索引持久化是调用方的事);实现自 ``std/transform.py`` 迁入,算法与常量逐字不变。
"""

from __future__ import annotations

import math
import re
from typing import Any

_BM25_K1 = 1.5
_BM25_B = 0.75
_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]+")


def tokenize(text: str) -> list[str]:
    """ASCII 词小写整词;CJK 连续段取字 bigram(单字取 unigram)——零依赖检索口径。

    来源:``std/transform.py`` W2-9 的 ``_tokenize``(逐字迁入,改为公开名)。
    """
    tokens: list[str] = []
    for piece in _TOKEN_RE.findall(text.lower()):
        if piece.isascii() or len(piece) == 1:
            tokens.append(piece)
        else:
            tokens.extend(piece[i : i + 2] for i in range(len(piece) - 1))
    return tokens


def bm25_score(
    query: str, docs: list[dict[str, Any]], k: int | None = None
) -> list[dict[str, Any]]:
    """纯 Python BM25(k1=1.5, b=0.75)无状态打分;返回 ``[{id, score}]`` 降序(同分按 id 字典序)。

    来源:``std/transform.py`` W2-9 handler 的算法本体(逐字迁入);``docs`` 条目
    形状 ``{id, text}``,``k`` 非 None 时截前 k 条。STDLIB-CATALOG.md:215 划线
    bm25 为 ``memory_search`` 的默认实现,MemoryService baseline 复用本函数。
    """
    query_terms = tokenize(query)
    doc_tokens = [(tokenize(str(d.get("text") or "")), str(d.get("id"))) for d in docs]
    n = len(doc_tokens)
    avgdl = sum(len(t) for t, _ in doc_tokens) / n if n else 0.0
    df: dict[str, int] = {}
    for tokens, _ in doc_tokens:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    ranked: list[dict[str, Any]] = []
    for tokens, doc_id in doc_tokens:
        tf: dict[str, int] = {}
        for term in tokens:
            tf[term] = tf.get(term, 0) + 1
        dl = len(tokens)
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf[term] + _BM25_K1 * (1 - _BM25_B + _BM25_B * (dl / avgdl if avgdl else 0.0))
            score += idf * (tf[term] * (_BM25_K1 + 1)) / denom
        ranked.append({"id": doc_id, "score": round(score, 6)})
    ranked.sort(key=lambda r: (-r["score"], r["id"]))
    if k is not None:
        ranked = ranked[: int(k)]
    return ranked


def rrf_merge(lists: list[list[Any]], k: int = 60) -> list[str]:
    """RRF:score = Σ 1/(k+rank)(rank 1 起,k 默认 60);只看排名,丢弃原始分数。

    来源:``std/transform.py`` W2-10 handler 的算法本体(逐字迁入);同分按首见序稳定。
    """
    if k <= 0:
        raise ValueError(f"k 必须为正,得到: {k}")
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for lst in lists:
        for rank, item in enumerate(lst, start=1):
            key = str(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(key, len(first_seen))
    return sorted(scores, key=lambda item: (-scores[item], first_seen[item]))

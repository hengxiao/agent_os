"""std/transform 纯函数技能包(STDLIB-CATALOG §W2;STDLIB §4.1)。

全部 code 技能 handler:``async def run(input: dict, ctx) -> dict``(DESIGN.md §6.3);
零 LLM、零外部依赖(仅标准库)、确定性(同输入同输出、无时间戳/随机数)。
ctx(TRUSTED 档 LogicContext)一律不触碰——纯转换不需要编排能力。

容错哲学(W2-1 决策):按既定顺序做有限兜底,全失败即报错;**不做"智能修复"**
(补引号/猜结构),静默猜测比失败更危险。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import string
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# W2-1 extract_json — 从杂文本抠 JSON
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_TAIL_COMMA_RE = re.compile(r",\s*([}\]])")


def _try_parse(candidate: str) -> Any:
    """直接 parse,失败后仅修尾逗号再试一次;仍失败返回 None(不做智能修复)。"""
    candidate = candidate.strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    fixed = _TAIL_COMMA_RE.sub(r"\1", candidate)
    if fixed == candidate:
        return None
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def _bracket_spans(text: str) -> list[str]:
    """括号配对扫描:找出全部顶层层级的 ``{...}`` / ``[...]`` 子串(字符串感知)。"""
    spans: list[str] = []
    stack: list[str] = []
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            if not stack:
                start = i
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                continue
            open_ch = stack.pop()
            if (open_ch, ch) not in (("{", "}"), ("[", "]")):
                stack.clear()  # 配对失败,放弃本段
                continue
            if not stack and start >= 0:
                spans.append(text[start : i + 1])
    return spans


def _extract_candidates(text: str) -> list[Any]:
    """容错顺序(§W2-1):直接 parse → 剥 ```json 栅栏 → 括号配对扫描。"""
    whole = _try_parse(text)
    if whole is not None:
        return [whole]
    values: list[Any] = []
    for m in _FENCE_RE.finditer(text):
        value = _try_parse(m.group(1))
        if value is not None:
            values.append(value)
    if values:
        return values
    for span in _bracket_spans(text):
        value = _try_parse(span)
        if value is not None:
            values.append(value)
    return values


async def extract_json(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """从杂文本抠 JSON:单值 → ``{"data": ...}``;多个 → ``{"items": [...]}``。"""
    text = str(input.get("text") or "")
    mode = input.get("mode") or "first"
    if mode not in ("first", "all"):
        raise ValueError(f"mode 应为 first|all,得到: {mode!r}")
    values = _extract_candidates(text)
    if not values:
        raise ValueError("文本中找不到合法 JSON(直接 parse/剥栅栏/括号扫描/修尾逗号均失败)")
    if mode == "first" or len(values) == 1:
        return {"data": values[0]}
    return {"items": values}


# ---------------------------------------------------------------------------
# W2-2 template_render — 安全模板渲染(str.format 风格)
# ---------------------------------------------------------------------------

_FORMATTER = string.Formatter()


def _render_template(template: str, data: dict[str, Any], strict: bool) -> str:
    out: list[str] = []
    pos = 0
    for literal, field, spec, conversion in _FORMATTER.parse(template):
        out.append(literal)
        pos += len(literal)
        if field is None:
            continue
        expr = "{" + field
        if conversion:
            expr += "!" + conversion
        if spec:
            expr += ":" + spec
        expr += "}"
        field_start = pos
        pos += len(expr)
        root = field.split(".")[0].split("[")[0]
        if not root or root not in data:
            if strict:
                raise ValueError(f"模板缺键 {root!r}(位置 {field_start} 处 {expr})")
            out.append(expr)  # 非 strict:占位符原样保留
            continue
        value, _ = _FORMATTER.get_field(field, (), data)
        if conversion:
            value = _FORMATTER.convert_field(value, conversion)
        out.append(format(value, spec))
    return "".join(out)


async def template_render(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``str.format`` 风格渲染;strict 缺键报错并指明位置,非 strict 原样保留占位符。"""
    template = input.get("template")
    data = input.get("data")
    if not isinstance(template, str):
        raise TypeError("template 必须是字符串")
    if not isinstance(data, dict):
        raise TypeError("data 必须是 object")
    return {"text": _render_template(template, data, bool(input.get("strict", False)))}


# ---------------------------------------------------------------------------
# W2-3 diff_text — 结构化行级 diff
# ---------------------------------------------------------------------------


async def diff_text(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """行级 diff:``{hunks: [{op, line, text}], added, removed}``(op ∈ add/remove)。"""
    import difflib

    a, b = input.get("a"), input.get("b")
    if not isinstance(a, str) or not isinstance(b, str):
        raise TypeError("a 与 b 必须都是字符串")
    a_lines, b_lines = a.splitlines(), b.splitlines()
    hunks: list[dict[str, Any]] = []
    added = removed = 0
    matcher = difflib.SequenceMatcher(a=a_lines, b=b_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            for i in range(i1, i2):
                hunks.append({"op": "remove", "line": i + 1, "text": a_lines[i]})
                removed += 1
        if tag in ("replace", "insert"):
            for j in range(j1, j2):
                hunks.append({"op": "add", "line": j + 1, "text": b_lines[j]})
                added += 1
    return {"hunks": hunks, "added": added, "removed": removed}


# ---------------------------------------------------------------------------
# W2-4 word_count / token_estimate — 计量
# ---------------------------------------------------------------------------


async def word_count(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{chars, words, lines}``:chars 码点数;words 任意空白切分;lines 逻辑行数。"""
    text = str(input.get("text") or "")
    return {
        "chars": len(text),
        "words": len(text.split()),
        "lines": len(text.splitlines()),
    }


async def token_estimate(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """token 粗估;**必须复用内核 TokenEstimator 口径**(§W2-4 决策,不另立算法)。"""
    from agent_os.context.estimator import TokenEstimator

    text = str(input.get("text") or "")
    return {"tokens": TokenEstimator().estimate_text(text)}


# ---------------------------------------------------------------------------
# W2-5 hash_digest — 内容指纹
# ---------------------------------------------------------------------------


async def hash_digest(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{text|ref, algo?}`` → ``{hex, algo}``;ref 为文件路径时哈希其字节内容。"""
    algo = input.get("algo") or "sha256"
    try:
        digest = hashlib.new(algo)
    except ValueError:
        raise ValueError(f"未知哈希算法: {algo!r}") from None
    text = input.get("text")
    ref = input.get("ref")
    if text is not None:
        digest.update(str(text).encode("utf-8"))
    elif ref is not None:
        digest.update(Path(str(ref)).read_bytes())
    else:
        raise ValueError("text 与 ref 至少给一个")
    return {"hex": digest.hexdigest(), "algo": algo}


# ---------------------------------------------------------------------------
# W2-6 csv_to_rows / rows_to_markdown — 表格进出(小表)
# ---------------------------------------------------------------------------


async def csv_to_rows(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """CSV 文本 → ``[{列名: 值}]``(首行表头;只处理小表,大表用 python_exec)。"""
    text = input.get("text")
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    reader = csv.DictReader(io.StringIO(text))
    return {"rows": [dict(row) for row in reader]}


async def rows_to_markdown(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``[{列名: 值}]`` → Markdown 表格(列序 = 首行键序,后续行新键追加)。"""
    rows = input.get("rows")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise TypeError("rows 必须是 object 数组")
    if not rows:
        return {"text": ""}
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    def cell(value: Any) -> str:
        return ("" if value is None else str(value)).replace("|", "\\|")

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(row.get(c)) for c in columns) + " |")
    return {"text": "\n".join(lines)}


# ---------------------------------------------------------------------------
# W2-7 slugify / normalize_whitespace — 字符串清洗
# ---------------------------------------------------------------------------


async def slugify(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """小写 ASCII slug:NFKD 后丢非 ASCII,非 [a-z0-9] 折叠为单个 ``-``。"""
    text = str(input.get("text") or "")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return {"text": re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")}


async def normalize_whitespace(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """任意连续空白(含 \\t\\n)折叠为单个空格,首尾去空白。"""
    text = str(input.get("text") or "")
    return {"text": " ".join(text.split())}


# ---------------------------------------------------------------------------
# W2-8 chunk_text — 切分(fixed / recursive)
# ---------------------------------------------------------------------------

#: recursive 模式的边界优先级:段落空行 → 单行换行 → 句末标点 → 硬切
_SPLIT_PATTERNS = [r"(\n{2,})", r"(\n)", r"(?<=[。!?;!?;])"]


def _split_units(text: str, start: int, end: int, pattern: str) -> list[tuple[int, int]]:
    """按 pattern(含捕获组)切 text[start:end],返回保偏移的 (s, e) 单元。"""
    units: list[tuple[int, int]] = []
    pos = start
    for piece in re.split(pattern, text[start:end]):
        if piece:
            units.append((pos, pos + len(piece)))
        pos += len(piece)
    return units


def _emit_recursive(
    text: str, start: int, end: int, size: int, depth: int, chunks: list[dict[str, Any]]
) -> None:
    span = text[start:end]
    if not span.strip():
        return
    if end - start <= size:
        chunks.append({"text": span, "start": start, "end": end})
        return
    if depth >= len(_SPLIT_PATTERNS):
        for s in range(start, end, size):
            e = min(s + size, end)
            chunks.append({"text": text[s:e], "start": s, "end": e})
        return
    units = _split_units(text, start, end, _SPLIT_PATTERNS[depth])
    if len(units) <= 1:
        _emit_recursive(text, start, end, size, depth + 1, chunks)
        return
    cur_s = cur_e = None
    for s, e in units:
        if cur_s is None:
            if text[s:e].strip():  # 不以纯空白单元开场
                cur_s, cur_e = s, e
            continue
        if e - cur_s <= size:
            cur_e = e
        else:
            _emit_recursive(text, cur_s, cur_e, size, depth + 1, chunks)
            cur_s, cur_e = (s, e) if text[s:e].strip() else (None, None)
    if cur_s is not None:
        _emit_recursive(text, cur_s, cur_e, size, depth + 1, chunks)


async def chunk_text(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{chunks: [{text, start, end}]}``;fixed 定长+overlap,recursive 边界优先。"""
    text = input.get("text")
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    size_raw = input.get("size")
    size = 512 if size_raw is None else int(size_raw)
    overlap = input.get("overlap")
    overlap = int(size * 0.15) if overlap is None else int(overlap)
    mode = input.get("mode") or "fixed"
    if size <= 0:
        raise ValueError(f"size 必须为正,得到: {size}")
    if not 0 <= overlap < size:
        raise ValueError(f"overlap 必须满足 0 <= overlap < size,得到: {overlap} (size={size})")
    if mode == "fixed":
        step = size - overlap
        chunks = [
            {"text": text[s : s + size], "start": s, "end": min(s + size, len(text))}
            for s in range(0, len(text), step)
        ]
        return {"chunks": chunks}
    if mode == "recursive":
        chunks: list[dict[str, Any]] = []
        _emit_recursive(text, 0, len(text), size, 0, chunks)
        return {"chunks": chunks}
    raise ValueError(f"mode 应为 fixed|recursive,得到: {mode!r}")


# ---------------------------------------------------------------------------
# W2-9 bm25_score — 稀疏检索打分(无状态)
# ---------------------------------------------------------------------------

_BM25_K1 = 1.5
_BM25_B = 0.75
_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]+")


def _tokenize(text: str) -> list[str]:
    """ASCII 词小写整词;CJK 连续段取字 bigram(单字取 unigram)——零依赖检索口径。"""
    tokens: list[str] = []
    for piece in _TOKEN_RE.findall(text.lower()):
        if piece.isascii() or len(piece) == 1:
            tokens.append(piece)
        else:
            tokens.extend(piece[i : i + 2] for i in range(len(piece) - 1))
    return tokens


async def bm25_score(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """纯 Python BM25(k1=1.5, b=0.75)无状态打分;``{ranked: [{id, score}]}`` 降序。"""
    query = input.get("query")
    docs = input.get("docs")
    if not isinstance(query, str) or not isinstance(docs, list):
        raise TypeError("query 必须是字符串,docs 必须是数组")
    k = input.get("k")
    query_terms = _tokenize(query)
    doc_tokens = [(_tokenize(str(d.get("text") or "")), str(d.get("id"))) for d in docs]
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
    return {"ranked": ranked}


# ---------------------------------------------------------------------------
# W2-10 rrf_merge — 多路检索融合
# ---------------------------------------------------------------------------


async def rrf_merge(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """RRF:score = Σ 1/(k+rank)(rank 1 起,k 默认 60);只看排名,丢弃原始分数。"""
    lists = input.get("lists")
    if not isinstance(lists, list) or any(not isinstance(lst, list) for lst in lists):
        raise TypeError("lists 必须是数组的数组")
    k = int(input.get("k") or 60)
    if k <= 0:
        raise ValueError(f"k 必须为正,得到: {k}")
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for lst in lists:
        for rank, item in enumerate(lst, start=1):
            key = str(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(key, len(first_seen))
    ranked = sorted(scores, key=lambda item: (-scores[item], first_seen[item]))
    return {"ranked": ranked}


# ---------------------------------------------------------------------------
# W2-11 retrieval_metrics — 检索评测
# ---------------------------------------------------------------------------


async def retrieval_metrics(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{recall_at_k, mrr, ndcg}``;二值相关性,nDCG 按 log2 折损。"""
    predicted = input.get("predicted")
    relevant = input.get("relevant")
    if not isinstance(predicted, list) or not isinstance(relevant, list):
        raise TypeError("predicted 与 relevant 必须是数组")
    k = int(input.get("k") or 10)
    if k <= 0:
        raise ValueError(f"k 必须为正,得到: {k}")
    rel = {str(r) for r in relevant}
    pred = [str(p) for p in predicted]
    top = pred[:k]
    recall_at_k = len({p for p in top} & rel) / len(rel) if rel else 0.0
    mrr = 0.0
    for rank, p in enumerate(pred, start=1):
        if p in rel:
            mrr = 1.0 / rank
            break
    dcg = sum(1.0 / math.log2(i + 2) for i, p in enumerate(top) if p in rel)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(rel), k)))
    ndcg = dcg / idcg if idcg else 0.0
    return {"recall_at_k": recall_at_k, "mrr": mrr, "ndcg": ndcg}


# ---------------------------------------------------------------------------
# W2-12 injection_scan — 注入模式扫描(正则层快筛)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_previous", re.compile(
        r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?)", re.IGNORECASE)),
    ("disregard_previous", re.compile(
        r"\bdisregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\b", re.IGNORECASE)),
    ("forget_everything", re.compile(
        r"\bforget\s+(?:everything|all\s+(?:previous|prior)|your\s+(?:instructions?|training))",
        re.IGNORECASE)),
    ("reveal_system_prompt", re.compile(
        r"\b(?:reveal|show|output|print|leak|repeat|display)\b[^\n.]{0,40}?\bsystem\s+prompt\b",
        re.IGNORECASE)),
    ("system_prompt_probe", re.compile(
        r"\bwhat\s+(?:are|were)\s+your\s+(?:original\s+)?(?:instructions?|system\s+prompt|rules?)",
        re.IGNORECASE)),
    ("zh_ignore_instructions", re.compile(r"忽略(?:之前|先前|上述|以上|所有|全部|你的)?[^\n。]{0,8}?(?:指令|指示|命令|提示词)")),
    ("role_jailbreak", re.compile(
        r"\b(?:act|pretend)\s+as\s+(?:if\s+you\s+(?:are|were)|an?\b)|"
        r"\byou\s+are\s+now\s+(?:an?\b|in\b)|\bdo\s+anything\s+now\b", re.IGNORECASE)),
    ("override_rules", re.compile(
        r"\b(?:override|bypass|disable)\s+(?:your\s+)?(?:safety|instructions?|rules?|filters?|guardrails?)",
        re.IGNORECASE)),
    ("tag_injection", re.compile(r"</?(?:system|im_start|im_end)>|<<\s*SYS\s*>>", re.IGNORECASE)),
]


async def injection_scan(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """正则层注入快筛:``{suspicious: [{pattern, span}], score}``;语义判断留调用方。"""
    text = str(input.get("text") or "")
    suspicious = [
        {"pattern": name, "span": [m.start(), m.end()]}
        for name, pattern in _INJECTION_PATTERNS
        for m in pattern.finditer(text)
    ]
    suspicious.sort(key=lambda hit: hit["span"][0])
    score = min(1.0, 0.4 * len(suspicious))
    return {"suspicious": suspicious, "score": score}


# ---------------------------------------------------------------------------
# W2-13 redact_pii — PII 脱敏(本地正则层)
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone_cn", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card_cn", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("api_key", re.compile(
        r"\b(?:sk|pk)-[A-Za-z0-9]{16,}\b|\bAKIA[0-9A-Z]{16}\b|(?i:bearer\s+[A-Za-z0-9._-]{16,})")),
]


async def redact_pii(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """PII 正则快筛脱敏:``{text, redacted: [{kind, span}]}``(span 为原文 [start, end))。"""
    text = str(input.get("text") or "")
    kinds = input.get("kinds")
    wanted = {str(k) for k in kinds} if kinds else None
    hits: list[dict[str, Any]] = []
    for kind, pattern in _PII_PATTERNS:
        if wanted is not None and kind not in wanted:
            continue
        for m in pattern.finditer(text):
            # 已被更高优先级形态认领的区间跳过(如 18 位身份证先于卡号)
            if any(m.start() < h["span"][1] and h["span"][0] < m.end() for h in hits):
                continue
            hits.append({"kind": kind, "span": [m.start(), m.end()]})
    hits.sort(key=lambda h: h["span"][0])
    out: list[str] = []
    pos = 0
    for hit in hits:
        s, e = hit["span"]
        out.append(text[pos:s])
        out.append(f"[{hit['kind'].upper()}]")
        pos = e
    out.append(text[pos:])
    return {"text": "".join(out), "redacted": hits}


# ---------------------------------------------------------------------------
# W2-14 identifier_guard — 标识符保真校验
# ---------------------------------------------------------------------------

_IDENTIFIER_PATTERNS = [
    r"https?://[^\s\"'<>),,。]+",  # URL
    r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b",  # UUID
    r"#\d+",  # PR/issue 号
    r"\b[\w./-]+/[\w./-]*\.[A-Za-z0-9]{1,6}\b",  # 路径形态文件名
    r":\d{2,5}\b",  # 端口
    r"\b[0-9a-f]{7,64}\b",  # git hash 等 hex 指纹
]


def _extract_identifiers(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in _IDENTIFIER_PATTERNS:
        found.update(re.findall(pattern, text))
    return found


async def identifier_guard(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """抽取 UUID/hash/URL/文件名/PR号/端口 集合前后比对:``{ok, missing, added}``。"""
    before, after = input.get("before"), input.get("after")
    if not isinstance(before, str) or not isinstance(after, str):
        raise TypeError("before 与 after 必须都是字符串")
    before_set, after_set = _extract_identifiers(before), _extract_identifiers(after)
    missing = sorted(before_set - after_set)
    added = sorted(after_set - before_set)
    return {"ok": not missing and not added, "missing": missing, "added": added}


# ---------------------------------------------------------------------------
# W2-15 make_handoff — 交接包构造
# ---------------------------------------------------------------------------


async def make_handoff(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """交接包 ``{task, facts, constraints, artifacts}``;artifacts 只收路径引用。

    **schema 层面拒绝塞入完整轨迹**(inputs 里 artifacts items 限定 string);
    此处再做一层运行期防御,dict/消息形态直接报错(§W2-15 决策)。
    """
    task = input.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task 必须是非空字符串")
    artifacts = input.get("artifacts") or []
    for artifact in artifacts:
        if not isinstance(artifact, str):
            raise TypeError(
                "artifacts 只收路径引用(string),拒绝塞入完整轨迹/消息对象; "
                "需要传递对话内容时请落盘成文件再传路径"
            )
    package = {
        "schema": "agent-os/handoff@1",
        "task": task,
        "facts": [str(f) for f in (input.get("facts") or [])],
        "constraints": [str(c) for c in (input.get("constraints") or [])],
        "artifacts": list(artifacts),
    }
    return {"package": package}


# ---------------------------------------------------------------------------
# W2-16 date_normalize / citation_check — 规范校验器
# ---------------------------------------------------------------------------

_CN_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


async def date_normalize(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """中文日期(2026年7月24日)与 MM/DD/YYYY → ISO 8601;非法日期原样保留。"""
    text = str(input.get("text") or "")
    found: list[str] = []

    def _iso(y: int, m: int, d: int, original: str) -> str:
        try:
            date(y, m, d)
        except ValueError:
            return original  # 不存在的日期(如 2 月 30 日)不动,避免静默捏造
        iso = f"{y:04d}-{m:02d}-{d:02d}"
        found.append(iso)
        return iso

    text = _CN_DATE_RE.sub(
        lambda m: _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(0)), text
    )
    text = _US_DATE_RE.sub(
        lambda m: _iso(int(m.group(3)), int(m.group(1)), int(m.group(2)), m.group(0)), text
    )
    return {"text": text, "dates": found}


_CITATION_RE = re.compile(r"\[(\d+)\]")


async def citation_check(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``[n]`` 引用与 sources 列表对齐:``{ok, missing, uncited}``(1 起编号)。"""
    text = input.get("text")
    sources = input.get("sources")
    if not isinstance(text, str) or not isinstance(sources, list):
        raise TypeError("text 必须是字符串,sources 必须是数组")
    cited = sorted({int(n) for n in _CITATION_RE.findall(text)})
    missing = [n for n in cited if n < 1 or n > len(sources)]
    uncited = [i + 1 for i in range(len(sources)) if i + 1 not in cited]
    return {"ok": not missing, "missing": missing, "uncited": uncited}

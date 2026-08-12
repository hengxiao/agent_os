"""generate 端点的 prompt 模板与输出解析(批注批处理工作流 v2,设计 §7.2/§7.3;P1)。

纯函数面,与 app.py 端点分离(pytest 直接锁解析面):

- :func:`build_generate_messages` —— §7.3 模板:system = 角色 + 要求 + 输出格式;
  user = 完整文档 + chat 上下文 + 批注列表 + 附加指令(批注以 anchor 为 id,
  要求 LLM 在结果里原样带回);
- :func:`parse_generate_output` —— 提取 ``<modified_document>`` /
  ``<annotation_results>`` 两块;annotation_results 内是 JSON 数组,逐条
  schema 校验(annotationId 非空、status ∈ applied|ignored|partial、aiNote 串);
  任何不合 → :class:`GenerateOutputError`(端点据此重试一次,再败 502,
  不半截落库)。
"""

from __future__ import annotations

import json
import re
from typing import Any

#: 输出块契约(设计 §7.3;前后 \s* 只吃标签外的空白,文档本体逐字保真)
_DOC_BLOCK_RE = re.compile(r"<modified_document>\s*(.*?)\s*</modified_document>", re.DOTALL)
_RESULTS_BLOCK_RE = re.compile(r"<annotation_results>\s*(.*?)\s*</annotation_results>", re.DOTALL)

#: annotation_results 每条的 status 白名单(设计 §4.1 annotationResults)
RESULT_STATUSES = ("applied", "ignored", "partial")

_SYSTEM_PROMPT = """你是一个文档编辑助手。用户正在修改一份文档,请你根据输入生成修改后的新版本。

## 要求
1. 请生成完整的修改后文档,不要只输出 diff。
2. 对于每条批注,请在回复中说明你是如何处理它的(已应用 applied / 已忽略 ignored / 部分应用 partial + 原因)。
3. 保持文档的整体风格一致性。
4. 如果没有明确修改必要的地方,保持原样。

## 输出格式
<modified_document>
[完整的修改后文档]
</modified_document>

<annotation_results>
[每条批注的处理结果 JSON 数组,形如:[{"annotationId": "<批注的 annotationId 原样带回>", "status": "applied|ignored|partial", "aiNote": "处理说明"}]]
</annotation_results>"""


class GenerateOutputError(ValueError):
    """LLM 生成输出不合契约(缺块/空文档/结果非 JSON/schema 不合)。"""


def build_generate_messages(
    *,
    document: str,
    base_version: int,
    chat_context: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    user_prompt: str | None,
) -> list[dict[str, str]]:
    """§7.3 模板 → ``[{"role": "system", ...}, {"role": "user", ...}]``。"""
    chat_text = (
        "\n".join(f"- {m.get('role', '?')}: {m.get('text', '')}" for m in chat_context) or "(无)"
    )
    ann_text = "\n".join(
        f"- annotationId: {a.get('anchor', '')}"
        + (f"\n  原文: {a['quote']}" if a.get("quote") else "")
        + f"\n  意见: {a.get('content', '')}"
        for a in annotations
    ) or "(无)"
    user = f"""## 当前文档内容(版本 {base_version})
{document}

## Chat 上下文(用户与 AI 之前的讨论)
{chat_text}

## 用户批注(针对具体段落的修改意见)
{ann_text}

## 用户附加指令
{str(user_prompt or "").strip() or "(无)"}"""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_generate_output(raw: str) -> tuple[str, list[dict[str, Any]]]:
    """解析 LLM 原文 → ``(modified_document, annotation_results)``。

    标签间内容按非贪婪 ``(.*?)`` 取,首尾空白被剥(markdown 首尾空行无
    语义);文档内部逐字保真。annotation_results 逐条校验,不合即
    :class:`GenerateOutputError`(宁缺毋滥,不半截放行)。
    """
    raw = str(raw or "")
    m = _DOC_BLOCK_RE.search(raw)
    r = _RESULTS_BLOCK_RE.search(raw)
    if not m or not r:
        raise GenerateOutputError("输出缺少 <modified_document> 或 <annotation_results> 块")
    doc = m.group(1)
    if not doc.strip():
        raise GenerateOutputError("<modified_document> 为空")
    try:
        results = json.loads(r.group(1))
    except json.JSONDecodeError as e:
        raise GenerateOutputError(f"<annotation_results> 不是合法 JSON: {e}") from e
    if not isinstance(results, list):
        raise GenerateOutputError("<annotation_results> 不是 JSON 数组")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(results):
        if not isinstance(item, dict):
            raise GenerateOutputError(f"annotation_results[{i}] 不是对象")
        aid = str(item.get("annotationId") or item.get("anchor") or "").strip()
        status = item.get("status")
        if not aid:
            raise GenerateOutputError(f"annotation_results[{i}] 缺 annotationId")
        if status not in RESULT_STATUSES:
            raise GenerateOutputError(
                f"annotation_results[{i}] status 非法: {status!r}(须 ∈ {RESULT_STATUSES})"
            )
        out.append({"annotationId": aid, "status": status, "aiNote": str(item.get("aiNote") or "")})
    return doc, out

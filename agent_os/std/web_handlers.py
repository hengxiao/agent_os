"""std/web 技能 handler(STDLIB-CATALOG §W4-3;STDLIB §4.6)。

- ``fetch_page``(code):同名工具的 run() 适配——技能面(kernel.run 入口)与
  工具面(帧内分发)同名共存;逻辑只在工具层一份(http_fetch + 正文抽取 +
  注入隔离包裹,见 agent_os.tools.std_web),技能是经白名单的薄适配。
- ``research_iterative``(code):迭代式研究——检索 → 自评(sufficient /
  refined_query)→ 精化 → 再检索(充分即停,默认 ≤3 轮)。
- ``parse_html`` / ``extract_links``(code):纯本地 HTML 解析——正文清洗
  复用工具层 ``agent_os.tools.std_web._extract_text``(同一口径一份),
  href 抽取去重保序;extract_links 经 urljoin 把相对链接转绝对。
- ``source_rank``(code):按 query 给来源排序,打分复用
  ``transform.bm25_score`` 的零依赖 BM25 口径。

research_iterative 为什么是 code 而不是 prompt:内核帧循环把"无 tool_calls
的响应"判为最终答案(§3.1 步骤 5),而自评回合(输出中间 JSON 后继续检索)
在 prompt 形态下会终止循环,协议走不通。故由 code 技能经 ctx.chat(§9.3 编排
者通道)自驾驶对话:模型负责"抓什么/够不够/怎么答",驱动方负责协议推进与
轮次记账。自评不充分时,驱动方按 refined_query 执行下一次检索(真实模型常把
自评与下次抓取合并为同一回合;此处由驱动方代为行动,transcript 上每个
tool_call 都对应一次真实工具执行,结果如实回贴)。
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin

from agent_os.api.v1 import Message, Role, Source, ToolCall
from agent_os.tools.std_web import _extract_text

#: 迭代协议总步数兜底(检索/自评/收束都算;防模型不配合时跑飞,预算另有 max_steps 闸)
_MAX_STEPS = 8

_SYSTEM = """你是迭代式研究助手。严格按协议工作:
1. 收到问题后,先调用 common.web.fetch_page 抓取最相关页面。
2. 每轮抓取后先输出自评 JSON:sufficient 表示信息是否充分;不充分时给 refined_query(精化后的检索目标)。先输出自评,再行动。
3. 信息充分时,只输出最终答案 JSON:answer 为综合回答,sources 为实际采用的页面 URL 数组。"""


async def fetch_page(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """fetch_page 技能形态:经白名单调同名工具,原样返回其契约 dict。"""
    tool_args: dict[str, Any] = {"url": args["url"]}
    if args.get("max_chars") is not None:
        tool_args["max_chars"] = args["max_chars"]
    payload = await ctx.call_tool("common.web.fetch_page", tool_args)
    if not payload.get("ok"):
        error = payload.get("error") or {}
        raise ValueError(
            f"fetch_page 调用失败({error.get('kind', '?')}): {error.get('message', '?')}"
        )
    return payload["value"]


async def research_iterative(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """迭代式研究驱动(协议与形态取舍见模块 docstring)。"""
    query = str(args["query"])
    max_rounds = max(1, int(args.get("max_rounds") or 3))
    chat = getattr(ctx, "chat", None)
    if chat is None:
        raise ValueError("research_iterative 需要 TRUSTED 档(ctx.chat 通道),SANDBOX 档不支持")
    messages = [
        Message(role=Role.SYSTEM, content=_SYSTEM, source=Source.SYSTEM),
        Message(
            role=Role.USER,
            content=json.dumps({"query": query}, ensure_ascii=False),
            source=Source.PARENT_INPUT,
        ),
    ]
    sources: list[str] = []
    fetches = 0

    async def _dispatch(call: ToolCall) -> None:
        """执行一次抓取并回贴 tool_result;非 fetch_page 的调用回错误观察(保配对)。"""
        nonlocal fetches
        if call.name == "common.web.fetch_page":
            fetches += 1
            payload = await ctx.call_tool("common.web.fetch_page", dict(call.args))
            value = payload.get("value") if payload.get("ok") else None
            if isinstance(value, dict) and value.get("source") and value["source"] not in sources:
                sources.append(value["source"])
        else:
            payload = {
                "ok": False,
                "value": None,
                "error": {
                    "kind": "invalid_args",
                    "message": f"研究协议只支持 common.web.fetch_page,收到 {call.name}",
                    "retryable": False,
                    "hint": "",
                },
            }
        messages.append(
            Message(
                role=Role.TOOL,
                content=json.dumps(payload, ensure_ascii=False),
                tool_call_id=call.id,
                name=call.name,
                source=Source.TOOL_RESULT,
            )
        )

    final: dict[str, Any] | None = None
    for step in range(_MAX_STEPS):
        resp = await chat(messages)
        msg = resp.message
        messages.append(msg)
        if msg.tool_calls:
            for call in msg.tool_calls:
                await _dispatch(call)
            continue
        try:
            data = json.loads(msg.content or "")
        except (TypeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and data.get("answer"):
            final = data
            break
        if isinstance(data, dict) and data.get("sufficient") is False and fetches < max_rounds:
            # 自评不充分:驱动方按 refined_query 执行精化检索(理由见模块 docstring)
            refined = str(data.get("refined_query") or query)
            call = ToolCall(id=f"refine-{step}", name="common.web.fetch_page", args={"url": refined})
            messages.append(
                Message(role=Role.ASSISTANT, tool_calls=[call], source=Source.SYSTEM)
            )
            await _dispatch(call)
            continue
        # 充分/形态不明/轮次用尽:请模型基于已有材料收束
        messages.append(
            Message(
                role=Role.USER,
                content="请基于已收集的材料输出最终答案 JSON(answer, sources)。",
                source=Source.SYSTEM,
            )
        )
    answer = (
        str(final.get("answer"))
        if final and final.get("answer")
        else "未能在限定轮次内收敛,已收集来源见 sources。"
    )
    result_sources = final.get("sources") if final else None
    if not isinstance(result_sources, list) or not result_sources:
        result_sources = sources
    return {"answer": answer, "sources": [str(s) for s in result_sources], "rounds": fetches}


# ---------------------------------------------------------------------------
# parse_html / extract_links —— 纯本地 HTML 解析(不触网)
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(
    r"<a\b[^>]*?\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.IGNORECASE
)
_BLANK_RUN_RE = re.compile(r"\s+")


def _hrefs(html: str) -> list[str]:
    """抽取全部 <a href> 值,去重保序(双引号/单引号/裸值三种形态)。"""
    seen: set[str] = set()
    links: list[str] = []
    for m in _HREF_RE.finditer(html):
        url = next(g for g in m.groups() if g is not None)
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


async def parse_html(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{html}`` → ``{text, links, title}``;正文清洗复用工具层 ``_extract_text`` 口径。"""
    html = args.get("html")
    if not isinstance(html, str) or not html.strip():
        raise ValueError("html 必填(HTML 文本)")
    m = _TITLE_RE.search(html)
    title = _BLANK_RUN_RE.sub(" ", unescape(m.group(1))).strip() if m else ""
    return {"text": _extract_text(html), "links": _hrefs(html), "title": title}


async def extract_links(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{html, base_url}`` → ``{links}``:href 去重保序,相对链接经 urljoin 转绝对。"""
    html = args.get("html")
    base_url = args.get("base_url")
    if not isinstance(html, str):
        raise TypeError("html 必须是字符串")
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("base_url 必填(相对链接的解析基准 URL)")
    return {"links": [urljoin(base_url, href) for href in _hrefs(html)]}


# ---------------------------------------------------------------------------
# source_rank —— 来源排序(复用 transform.bm25_score 的零依赖 BM25 口径)
# ---------------------------------------------------------------------------


async def source_rank(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{sources, query, k?}`` → ``{ranked: [{id, score}]}`` 降序(同分按 id 字典序)。

    来源条目 ``{id|url, text|title}``:id 缺省回落 url,text 缺省回落 title。
    """
    sources = args.get("sources")
    query = args.get("query")
    if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
        raise TypeError("sources 必须是 object 数组")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必填(非空字符串)")
    docs = [
        {
            "id": str(s.get("id") or s.get("url") or ""),
            "text": str(s.get("text") or s.get("title") or ""),
        }
        for s in sources
    ]
    from transform import bm25_score

    return await bm25_score({"query": query, "docs": docs, "k": args.get("k")}, ctx)

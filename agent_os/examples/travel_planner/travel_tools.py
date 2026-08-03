"""travel_planner 的真实联网工具:Wikivoyage 开放 API(零 key,目的地无关)。

- ``wiki_search(query)``:opensearch API → ``{"titles": [...]}`` 候选条目标题;
- ``wiki_fetch(title, chars_limit=6000)``:extracts API(explaintext 纯文本)→
  ``{"title", "text", "truncated"}``;正文超 ``chars_limit`` 显式截断并在文末注明;
- 错误一律结构化(``ToolResult.error``:kind/message/retryable/hint):网络故障
  retryable 并附修复建议;条目不存在 → NOT_FOUND 不 retry;两个工具均声明
  ``untrusted_source=True``(§2.2 来源标记,正文是不可信内容,防注入)。

装配:``[tools.custom] module = "travel_tools:register"``(docs/RUNNERS.md §2.1,
模块须在 PYTHONPATH 上,见本目录 agent-os.toml 头注释)。
"""

from __future__ import annotations

from typing import Any

import httpx

from agent_os.api.v1 import Permission, ToolError, ToolErrorKind, ToolResult

#: Wikivoyage API 端点(en 站条目最全;中文目的地的英文页同样含 ¥ 价目)
API_URL = "https://en.wikivoyage.org/w/api.php"

#: Wikimedia 机器人政策要求可识别的 User-Agent;缺省 UA 可能被限流
_HEADERS = {"User-Agent": "agent-os-travel-planner/0.1 (live demo; contact: local-run)"}

#: httpx 客户端超时(工具 spec.timeout 兜底在其外,留 5s 余量)
_HTTP_TIMEOUT = 15.0


def _net_error(exc: httpx.HTTPError, tool: str) -> ToolResult:
    """网络层异常 → 结构化错误(retryable,带修复 hint)。"""
    if isinstance(exc, httpx.TimeoutException):
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.TIMEOUT,
                message=f"{tool} 请求 Wikivoyage 超时: {exc}",
                retryable=True,
                hint="wikivoyage.org 响应迟缓,可直接重试;连续超时检查本机网络",
            ),
        )
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.INTERNAL,
            message=f"{tool} 网络错误: {type(exc).__name__}: {exc}",
            retryable=True,
            hint="检查本机到 wikivoyage.org 的连通性(DNS/代理)后重试",
        ),
    )


def _http_status_error(tool: str, status: int) -> ToolResult:
    """非 200 状态码 → 结构化错误(5xx 可重试,4xx 换参数)。"""
    retryable = status >= 500
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.INTERNAL,
            message=f"{tool} Wikivoyage API 返回 HTTP {status}",
            retryable=retryable,
            hint="服务端错误,稍后重试" if retryable else "客户端请求被拒,检查 query/title 参数",
        ),
    )


async def wiki_search(query: str) -> dict[str, Any] | ToolResult:
    """搜索 Wikivoyage 条目,返回候选标题 ``{"titles": [...]}``。

    Use when 需要按地名找 Wikivoyage 条目标题(中文地名建议先译成英文,如 西安 → Xi'an);
    Do not use when 已知精确条目标题(直接 wiki_fetch 取正文)。
    """
    params = {"action": "opensearch", "search": query, "limit": 5, "format": "json"}
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=_HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(API_URL, params=params)
    except httpx.HTTPError as e:
        return _net_error(e, "project.travel_planner.wiki_search")
    if resp.status_code != 200:
        return _http_status_error("project.travel_planner.wiki_search", resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INTERNAL,
                message="wiki_search 响应不是合法 JSON",
                retryable=False,
                hint="Wikivoyage API 形态可能已变化,检查响应原文",
            ),
        )
    # opensearch 响应形态:[query, [titles...], [descriptions...], [urls...]]
    titles = [str(t) for t in data[1]] if isinstance(data, list) and len(data) > 1 else []
    return {"titles": titles}


async def wiki_fetch(title: str, chars_limit: int = 6000) -> dict[str, Any] | ToolResult:
    """取 Wikivoyage 条目正文(explaintext 纯文本),返回 ``{title, text, truncated}``。

    正文超 ``chars_limit`` 时显式截断:``truncated=true`` 且文末附截断注记。
    标题经 httpx params 自动 URL encode(空格、撇号等,如 "Xi'an")。

    Use when 需要目的地的一手攻略资料(景点/餐饮/住宿/交通价目);
    Do not use when 只想确认条目是否存在(用 wiki_search)。
    """
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": 1,
        "format": "json",
        "titles": title,
        "redirects": 1,  # 跟随站内重定向(如别名页),减少 NOT_FOUND 误报
    }
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=_HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(API_URL, params=params)
    except httpx.HTTPError as e:
        return _net_error(e, "project.travel_planner.wiki_fetch")
    if resp.status_code != 200:
        return _http_status_error("project.travel_planner.wiki_fetch", resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INTERNAL,
                message="wiki_fetch 响应不是合法 JSON",
                retryable=False,
                hint="Wikivoyage API 形态可能已变化,检查响应原文",
            ),
        )
    pages = (data.get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), {})
    if "-1" in pages or "missing" in page:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"Wikivoyage 条目不存在: {title}",
                retryable=False,
                hint="先用 wiki_search 确认精确条目标题(注意大小写与拼写,如 \"Xi'an\")",
            ),
        )
    text = str(page.get("extract") or "")
    if not text.strip():
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"条目无正文(extract 为空): {title}",
                retryable=False,
                hint="条目可能是消歧义/列表页,换 wiki_search 结果中的其他标题重试",
            ),
        )
    truncated = len(text) > chars_limit
    if truncated:
        text = text[:chars_limit] + (
            f"\n[truncated: 正文共 {len(text)} 字符,已按 chars_limit={chars_limit} 截断,"
            "需要后段请调大 chars_limit]"
        )
    return {"title": str(page.get("title") or title), "text": text, "truncated": truncated}


def register(registry: Any) -> None:
    """``[tools.custom]`` 装配钩子:把 wiki_search/wiki_fetch 注册进工具表(NET 档)。"""
    registry.tool(
        permission=Permission.NET,
        timeout=20.0,
        untrusted_source=True,
        cost_hint="~1s,取决于网络",
        name="project.travel_planner.wiki_search",
    )(wiki_search)
    registry.tool(
        permission=Permission.NET,
        timeout=20.0,
        untrusted_source=True,
        cost_hint="~1s,取决于网络与正文长度",
        name="project.travel_planner.wiki_fetch",
    )(wiki_fetch)
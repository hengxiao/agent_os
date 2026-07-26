"""std/web 工具面(STDLIB-CATALOG §W4-3;STDLIB §4.6):``fetch_page``。

``fetch_page`` = http_fetch + 正文抽取 + 超长 spill;outputs 强制 ``source``
字段,正文以 ``<external_content source="...">`` 包裹——每个感知工具都是注入
入口,包裹标记是零成本的第一道防线(``citation_style`` 管的是输出侧格式,
替代不了输入侧隔离)。

**注册点在 ``LocalPythonToolRegistry`` 构造器**(而非 ``with_builtins``):
std/skills.yaml 中 ``fetch_page`` / ``research_one`` / ``research_iterative``
的 ``permissions.tools`` 都声明 ``[fetch_page]``,而 §6.1 装配闸门要求"声明的
工具对注册表可见"——既有锚点(test_std_transform / test_std_nlp 等)用**空
工具表**加载同一份 skills.yaml,挂在 with_builtins 会让那些装配红掉。构造器
注册使每个注册表自带 fetch_page;``http_fetch`` 依赖在**调用时**按名查找
(测试可同名覆盖 mock;缺 http_fetch 时回结构化 NOT_FOUND,不崩溃)。
"""

from __future__ import annotations

import re
from html import unescape
from typing import TYPE_CHECKING, Any

from agent_os.api.v1 import (
    Permission,
    Tool,
    ToolContext,
    ToolError,
    ToolErrorKind,
    ToolResult,
)
from agent_os.tools.local_registry import _FunctionTool, derive_spec

if TYPE_CHECKING:
    from agent_os.tools.local_registry import LocalPythonToolRegistry

#: 正文保留上限(字符):超出截断并显式标注,全文 spill 至 blob(§W4-3 超长 spill)
_PAGE_MAX_CHARS = 20_000

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\s+")


def _extract_text(html: str) -> str:
    """基础正文清洗:去 script/style 块 → 去标签 → 实体反转义 → 折叠空白。"""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    return _BLANK_RE.sub(" ", unescape(text)).strip()


def fetch_page_tool(registry: LocalPythonToolRegistry) -> Tool:
    """构造 ``fetch_page``(NET 级):闭包持有 registry,调用时按名查找 ``http_fetch``。"""

    async def fetch_page(
        url: str, max_chars: int = _PAGE_MAX_CHARS, ctx: ToolContext | None = None
    ) -> dict[str, Any] | ToolResult:
        """抓取网页并提取正文,返回 {source, content, truncated, status}(大页加 spill_ref)。

        Use when 要把网页内容喂给模型:正文已去标签清洗,并以
        <external_content source="..."> 包裹做注入隔离(§W4-3 契约,来源可溯);
        Do not use when 需要原始 HTML 结构(用 http_fetch)或登录态页面。
        超长正文截断并显式标注,全文 spill 至 blob(凭 spill_ref 分页取)。
        """
        try:
            fetcher = registry.get("http_fetch")
        except KeyError:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.NOT_FOUND,
                    message="fetch_page 依赖 http_fetch,当前工具表未注册它",
                    retryable=False,
                    hint="用 LocalPythonToolRegistry.with_builtins() 装配,或先注册 http_fetch",
                ),
            )
        result = await fetcher({"url": url, "max_bytes": max(10_000, max_chars * 4)}, ctx)
        if not result.ok:
            return result  # http_fetch 的结构化错误原样透传(含 hint)
        payload = result.value if isinstance(result.value, dict) else {}
        text = _extract_text(str(payload.get("content") or ""))
        truncated = bool(payload.get("truncated"))
        note = ""
        spill_ref = ""
        if len(text) > max_chars:
            if ctx is not None and ctx.blob is not None:
                spill_ref = await ctx.blob.put(text.encode("utf-8"), ctx.run_id)
            text = text[:max_chars]
            truncated = True
            note = "\n[truncated] 正文过长已截断" + (f",全文见 {spill_ref}" if spill_ref else "")
        out: dict[str, Any] = {
            "source": url,
            "content": f'<external_content source="{url}">\n{text}{note}\n</external_content>',
            "truncated": truncated,
            "status": payload.get("status", 0),
        }
        if spill_ref:
            out["spill_ref"] = spill_ref
        return out

    return _FunctionTool(
        fetch_page,
        derive_spec(
            fetch_page,
            permission=Permission.NET,
            timeout=45.0,
            untrusted_source=True,
            cost_hint="~1s 起,取决于网络与页面大小",
        ),
    )

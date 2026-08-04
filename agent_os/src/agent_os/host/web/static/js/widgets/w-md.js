/* W-md — Markdown 查看器(docs/WIDGETS.md §2;agent 消息、NOTES.md、报告)。

   **安全渲染**:先整体转义,再做白名单 markdown 渲染——不 innerHTML 原文
   (XSS 向量:<script>/onerror 属性/javascript: 链接全部无害化);
   白名单:标题(#/##/###)、列表(- /*)、代码块(``` 与 `行内`)、粗斜体、
   链接(仅 https?:// 与 / 相对)、表格(| … |)。代码块 mono。 */

import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const MD_VIEWER_DEF = registerWidgetDef({
  kind: "md-viewer",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { source: "" },
  actions: [],
  events: [],
  aria: { role: "document", keys: [] },
  surfaces: ["card", "tab"],
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* 链接白名单:仅 http(s) 与站内相对;javascript:/data:/vbscript: 一律剥壳成纯文本 */
function _safeHref(url) {
  const u = String(url ?? "").trim();
  if (/^https?:\/\//i.test(u)) return u;
  if (u.startsWith("/") && !u.startsWith("//")) return u;
  return null;
}

/* 行内元素(先转义后的文本上加工;返回 html) */
function _inline(text) {
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/(^|\W)\*([^*\n]+)\*(?=\W|$)/g, "$1<i>$2</i>");
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, url) => {
    const href = _safeHref(url);
    return href
      ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`
      : `${label}(${esc(url)})`; // 非法 scheme:剥壳成纯文本(不渲染成链接)
  });
  return s;
}

/* 是否含 markdown 结构(保守启用:普通纯文本消息不走 md 渲染) */
export function looksMarkdown(text) {
  return /(^|\n)#{1,3}\s|^```|\*\*[^*\n]+\*\*|^\s*[-*]\s+\S|^\s*\|.+\|\s*$|\[[^\]]+\]\([^)]+\)/m.test(
    String(text ?? "")
  );
}

/* markdown → 安全 html(白名单;先转义后加工) */
export function mdToHtml(md) {
  const lines = String(md ?? "").split("\n");
  const out = [];
  let list = null;
  let inCode = false;
  let codeBuf = [];
  let table = null;
  const flushList = () => {
    if (list) {
      out.push(`<ul>${list.join("")}</ul>`);
      list = null;
    }
  };
  const flushTable = () => {
    if (table) {
      const [head, ...rows] = table;
      out.push(
        `<table class="wd-md-table"><thead><tr>${head.map((c) => `<th>${_inline(c)}</th>`).join("")}</tr></thead>` +
          `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${_inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`
      );
      table = null;
    }
  };
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        out.push(`<pre class="mono wd-md-code">${esc(codeBuf.join("\n"))}</pre>`);
        inCode = false;
        codeBuf = [];
      } else {
        flushList();
        flushTable();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      continue;
    }
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      flushTable();
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flushList();
      flushTable();
      const level = heading[1].length + 3; // h4-h6(页面已有 h1-h3 语义层)
      out.push(`<h${level}>${_inline(heading[2])}</h${level}>`);
      continue;
    }
    const item = /^[-*]\s+(.*)$/.exec(trimmed);
    if (item) {
      flushTable();
      (list ??= []).push(`<li>${_inline(item[1])}</li>`);
      continue;
    }
    if (/^\|.+\|$/.test(trimmed)) {
      flushList();
      const cells = trimmed.slice(1, -1).split("|").map((c) => c.trim());
      if (/^[-\s|:]+$/.test(trimmed.replace(/[^|\s-:]/g, "")) && table) {
        continue; // 分隔行
      }
      (table ??= []).push(cells);
      continue;
    }
    flushList();
    flushTable();
    out.push(`<p>${_inline(trimmed)}</p>`);
  }
  flushList();
  flushTable();
  if (inCode) out.push(`<pre class="mono wd-md-code">${esc(codeBuf.join("\n"))}</pre>`);
  return out.join("");
}

/* 挂进宿主:source(markdown 原文;state 可序列化) */
export function mountMarkdownViewer(host, { source = "", path = "", onRegister = null, onUnregister = null } = {}) {
  const widget = createWidget(MD_VIEWER_DEF, { path, state: { source }, onRegister, onUnregister });
  host.innerHTML = `<div class="wd-md" role="document">${mdToHtml(source)}</div>`;
  widget.set_source = (source) => {
    widget.state.source = source;
    host.innerHTML = `<div class="wd-md" role="document">${mdToHtml(source)}</div>`;
  };
  widget.register(host.dataset.summary ?? "");
  return widget;
}

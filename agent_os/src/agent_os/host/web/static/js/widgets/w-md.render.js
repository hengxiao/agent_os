/* W-md 渲染面(docs/WIDGET-ARCH.md §1.1/§2.12;W5.3):
   ``renderMarkdownViewer(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class,视觉全走契约 token。

   **安全渲染**(mdToHtml 也随渲染面迁此):先整体转义,再做白名单 markdown
   渲染——不 innerHTML 原文(XSS 向量:<script>/onerror 属性/javascript:
   链接全部无害化);白名单:标题(#/##/###)、列表(- /*)、代码块(``` 与
   `行内`)、粗斜体、链接(仅 https?:// 与 / 相对)、表格(| … |)。
   代码块 mono 卡片 + 复制钮(data-md-copy = 代码块序号,逻辑面按序取文)。 */

import { copy } from "../themes.js";
import { relTime, textEditorMicro } from "./w-text.render.js";

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
      : ""; // 非法 scheme:**整块不渲染,不留残迹**(§3.12 设计终稿,替代剥壳留文)
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
      const level = heading[1].length + 3; // h4-h6(页面已有 h1-h3 语义层;视觉阶梯在 CSS)
      out.push(`<h${level}>${_inline(heading[2])}</h${level}>`);
      continue;
    }
    const quote = /^>\s+(.*)$/.exec(trimmed); // 引用块(§3.12:左 3px 粗条 + 弱色)
    if (quote) {
      flushList();
      flushTable();
      out.push(`<blockquote class="wd-md-quote">${_inline(quote[1])}</blockquote>`);
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

/* state → html(纯);state 面:{source, title?, view?("preview"|"source")} */
export function renderMarkdownViewer(state, { surface = "tab" } = {}) {
  if (surface === "card") return _mdCardHtml(state);
  const view = state.view ?? "preview";
  if (!String(state.source ?? "").trim() && view === "preview") {
    return (
      `<div class="wd-md" role="document">` +
      `<div class="wd-empty-box"><span class="wd-empty-ico" aria-hidden="true">¶</span>` +
      `<span class="wd-empty-guide">${esc(copy("w.md.empty"))}</span></div></div>`
    );
  }
  const barInner =
    (state.title ? `<span class="wd-pane-title">${esc(state.title)} · md</span>` : "") +
    `<span class="wd-md-bar-side">` +
    `<span class="wd-seg">` +
    `<button class="wd-seg-btn" data-md-view="preview"${view === "preview" ? ' data-on="1"' : ""}>${esc(copy("w.md.preview"))}</button>` +
    `<button class="wd-seg-btn" data-md-view="source"${view === "source" ? ' data-on="1"' : ""}>${esc(copy("w.md.source"))}</button>` +
    `</span>` +
    `<button type="button" class="wd-md-copyall" data-md-copyall="1" title="${esc(copy("w.md.copy"))}"` +
    ` aria-label="${esc(copy("w.md.copy"))}">⧉</button>` +
    `</span>`;
  const bar = state.title ? `<div class="wd-pane-head">${barInner}</div>` : `<div class="wd-md-bar">${barInner}</div>`;
  if (view === "source") {
    return `<div class="wd-md" role="document">${bar}${_mdSourceHtml(state)}</div>`;
  }
  let idx = 0; // 局部计数(纯函数内,无副作用外泄)
  const body = mdToHtml(state.source ?? "").replace(/<pre class="mono wd-md-code">/g, () => {
    const tag =
      `<pre class="mono wd-md-code"><button type="button" class="wd-md-copy" data-md-copy="${idx}"` +
      ` title="${esc(copy("w.md.copy"))}" aria-label="${esc(copy("w.md.copy"))}">⧉</button>`;
    idx += 1;
    return tag;
  });
  return `<div class="wd-md" role="document">${bar}${body}</div>`;
}

/* 源码态(§3.12 v2 · 用户裁决):W-text readonly 同族视觉——行号槽 +
   mono pre + 右下微标;内容为原始 markdown(不重取数据,同一份 source) */
function _mdSourceHtml(state) {
  const src = String(state.source ?? "");
  const lines = src.split("\n");
  return (
    `<div class="wd-md-src">` +
    `<div class="wd-editor">` +
    `<div class="wd-gutter" aria-hidden="true"><div class="wd-gutter-in">` +
    lines.map((_, i) => `<span class="wd-gl" data-line="${i + 1}">${i + 1}</span>`).join("") +
    `</div></div>` +
    `<div class="wd-code wd-md-src-code"><pre class="wd-md-src-pre">${esc(src)}\n</pre>` +
    `<span class="wd-micro-box"><span class="wd-micro">${esc(textEditorMicro(src))}</span></span>` +
    `</div></div></div>`
  );
}

/* card 面(§3.12):首个标题 + 首段摘录(跳过代码块/列表/表格行;
   引用块取纯文本——剥 `>`/行内符号,不带着原始标记进卡,F7) */
function _mdCardHtml(state) {
  const lines = String(state.source ?? "").split("\n");
  let title = "";
  const excerpt = [];
  let inCode = false;
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      inCode = !inCode;
      continue;
    }
    if (inCode) continue;
    let t = line.trim();
    if (!t) {
      if (excerpt.length) break; // 首段结束
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(t);
    if (h) {
      if (!title) title = h[2];
      continue;
    }
    if (/^[-*]\s+\S/.test(t) || /^\|/.test(t)) continue; // 摘录取首段正文(跳过列表/表格)
    t = t.replace(/^>+\s*/, ""); // 引用块剥 > 取纯文本(F7)
    excerpt.push(_plainText(t));
  }
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    (title ? `<span class="wd-md-cardtitle wd-card-line">${esc(title)}</span>` : "") +
    (excerpt.length ? `<span class="wd-card-excerpt">${esc(excerpt.join(" "))}</span>` : "") +
    (!title && !excerpt.length ? `<span class="wd-card-line">${esc(copy("w.card.empty"))}</span>` : "") +
    `<span class="wd-card-meta"><span class="wd-card-meta-txt">${esc(_mdMeta(state))}</span>` +
    `<span class="wd-card-all">${esc(copy("w.card.go"))}</span></span>` +
    `</div>`
  );
}

/* 摘录纯文本化:剥行内标记(粗斜体/行内 code/链接取文本) */
function _plainText(t) {
  return String(t ?? "")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, "$1")
    .replace(/[*_`]+/g, "");
}

/* card meta:Markdown · 大小 · 相对时间(updated_at 缺省省略) */
function _mdMeta(state) {
  const src = String(state.source ?? "");
  const n = new TextEncoder().encode(src).length;
  const parts = ["Markdown", n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`];
  if (state.updated_at) parts.push(relTime(state.updated_at));
  return parts.join(" · ");
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

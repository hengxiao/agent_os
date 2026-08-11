/* md-viewer-mi(widget-libs 试点 3:markdown-it 对照实验件)——
   与 W-md 同 state 面({source, view, title})、同 events(copy/open)、同双形态、
   同 mount options({source, title, bar})。**不动 W-md 一行**;
   preview = markdown-it 渲染 + 我们的白名单后处理;view source/card 面直接
   复用 W-md 的 renderMarkdownViewer(引擎无关,逐字同源)。

   白名单/安全对齐(docs/WIDGET-DESIGN.md §3.12;W6.4 裁决逐条不破):
   1. 原始 HTML 注入 → markdown-it `html: false`(原始标签全部转义);
   2. javascript:/data:/vbscript: 链接 → 预扫源文整块移除(与手写 _safeHref
      同规则:<a> 只允许 https?:// 与 / 站内相对),markdown-it 自带
      validateLink 是第二道(它不渲染为链接,但留源文残迹——我们预扫先剥);
   3. 链接 target/rel → 后处理统一补 target="_blank" rel="noopener noreferrer",
      并再过一道白名单(防御纵深,不应到达);
   4. 结构白名单 → 后处理映射到我们的视觉族(h1-3→h4-6 阶梯/table/blockquote
      /code class),markdown-it 产出的其余标签(em/strong/ul/ol/pre)与手写
      白名单同族;
   5. 代码块复制钮 → 后处理注入(序号为渲染序;复制文本取 DOM code 原文,
      不经源文围栏再解析——两引擎围栏语义差的坑位规避)。

   铁律:零 fetch;render 纯函数;事件上行;vendor 只在渲染面调用。 */

import MarkdownIt from "../../vendor/markdown-it/markdown-it.mjs";
import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderMarkdownViewer } from "./w-md.render.js";

const _mi = new MarkdownIt({ html: false, linkify: true }); // typographer 不开(与手写版同语义面)

/* 源文预扫(白名单裁决,与手写 _safeHref 同规则):非法 scheme 链接整块移除 */
export function miSanitizeSource(src) {
  return String(src ?? "").replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
    const u = String(url ?? "").trim();
    if (/^https?:\/\//i.test(u)) return m;
    if (u.startsWith("/") && !u.startsWith("//")) return m;
    return ""; // 非法 scheme:整块不渲染,不留残迹(W6.4 裁决)
  });
}

/* markdown-it → 我们的 html(纯):渲染 + 后处理(结构映射/链接补强/复制钮) */
export function mdToHtmlMi(md) {
  let html = _mi.render(miSanitizeSource(md));
  // 标题阶梯:h1-3 → h4-6(页面已有 h1-h3 语义层;与手写版同裁决),≥h4 → h6
  html = html.replace(/<(\/?)h([1-6])([^>]*)>/g, (_m, close, n, rest) =>
    `<${close}h${Math.min(6, Number(n) + 3)}${rest}>`);
  // 视觉族钩子(与手写版同 class 族):先块后行内——块级 <pre><code> 打临时
  // 标记再类化行内 <code>,最后摘帽(块级不带 mono 类,与手写版同形)
  html = html
    .replace(/<blockquote>/g, '<blockquote class="wd-md-quote">')
    .replace(/<table>/g, '<table class="wd-md-table">')
    .replace(/<pre><code([^>]*)>/g, '<pre class="mono wd-md-code"><code data-blk$1>')
    .replace(/<code>/g, '<code class="mono">')
    .replace(/<code data-blk/g, "<code");
  // 链接:白名单复核 + target/rel(防御纵深;预扫已剥,此面兜底)
  html = html.replace(/<a href="([^"]*)"([^>]*)>/g, (_m, href, rest) => {
    const ok = /^https?:\/\//i.test(href) || (href.startsWith("/") && !href.startsWith("//"));
    if (!ok) return "";
    return `<a href="${href}" target="_blank" rel="noopener noreferrer"${rest}>`;
  });
  // 代码块复制钮(渲染序即复制序;文本取 DOM code,不经源文再解析)
  let idx = 0;
  html = html.replace(/<pre class="mono wd-md-code"><code([^>]*)>/g, (_m, attrs) => {
    const tag =
      `<pre class="mono wd-md-code"><button type="button" class="wd-md-copy" data-md-copy="${idx}"` +
      ` title="${esc(copy("w.md.copy"))}" aria-label="${esc(copy("w.md.copy"))}">⧉</button><code${attrs}>`;
    idx += 1;
    return tag;
  });
  return html;
}

/* 面板头(与 W-md 同 chrome;引擎标记 ·mi 供对照识别) */
function _miBarHtml(state, bar) {
  if (!bar) return "";
  const view = state.view ?? "preview";
  const inner =
    (state.title ? `<span class="wd-pane-title">${esc(state.title)} · mi</span>` : "") +
    `<span class="wd-md-bar-side">` +
    `<span class="wd-seg">` +
    `<button class="wd-seg-btn" data-md-view="preview"${view === "preview" ? ' data-on="1"' : ""}>${esc(copy("w.md.preview"))}</button>` +
    `<button class="wd-seg-btn" data-md-view="source"${view === "source" ? ' data-on="1"' : ""}>${esc(copy("w.md.source"))}</button>` +
    `</span>` +
    `<button type="button" class="wd-md-copyall" data-md-copyall="1" title="${esc(copy("w.md.copy"))}"` +
    ` aria-label="${esc(copy("w.md.copy"))}">⧉</button>` +
    `</span>`;
  return state.title ? `<div class="wd-pane-head">${inner}</div>` : `<div class="wd-md-bar">${inner}</div>`;
}

/* state → html(纯);card/source 面复用 W-md(引擎无关),preview 走 markdown-it */
export function renderMarkdownViewerMi(state, { surface = "tab", bar = true } = {}) {
  if (surface === "card") return renderMarkdownViewer(state, { surface, bar });
  const view = state.view ?? "preview";
  if (view === "source") return renderMarkdownViewer(state, { surface, bar }); // 源码态引擎无关,同源复用
  if (!String(state.source ?? "").trim()) {
    return (
      `<div class="wd-md" role="document">` +
      `<div class="wd-empty-box"><span class="wd-empty-ico" aria-hidden="true">¶</span>` +
      `<span class="wd-empty-guide">${esc(copy("w.md.empty"))}</span></div></div>`
    );
  }
  return `<div class="wd-md" role="document">${_miBarHtml(state, bar)}${mdToHtmlMi(state.source ?? "")}</div>`;
}

export const MD_VIEWER_MI_DEF = registerWidgetDef({
  kind: "md-viewer-mi",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { source: "", view: "preview" },
  actions: [],
  events: ["copy", "open"], // 与 W-md 同事件面
  aria: { role: "document", keys: [] },
  surfaces: ["card", "tab"],
  render: renderMarkdownViewerMi,
  mount: mountMarkdownViewerMi,
});

/* 挂进宿主(与 W-md 同签名/同行为面;复制钮文本取渲染态 DOM code 原文) */
export function mountMarkdownViewerMi(host, { source = "", title = "", bar = true, path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(MD_VIEWER_MI_DEF, { path, state: { source, title }, onRegister, onUnregister });
  const render = () => {
    host.innerHTML = renderMarkdownViewerMi(widget.state, { surface, bar });
  };
  widget.set_source = (source) => {
    widget.state.source = source;
    render();
  };
  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("click", (e) => {
    const v = e.target.closest?.("[data-md-view]");
    if (v) {
      widget.state.view = v.dataset.mdView === "source" ? "source" : "preview";
      return render();
    }
    const all = e.target.closest?.("[data-md-copyall]");
    if (all) {
      const text = widget.state.source;
      globalThis.navigator?.clipboard?.writeText?.(text);
      widget.emit("copy", { text });
      all.textContent = "✓";
      setTimeout(() => {
        all.textContent = "⧉";
      }, 1000);
      return;
    }
    const btn = e.target.closest?.("[data-md-copy]");
    if (!btn) return;
    const blocks = host.querySelectorAll("pre code"); // 渲染序 = 注入序(同一 html 内)
    const text = blocks[Number(btn.dataset.mdCopy)]?.textContent ?? "";
    globalThis.navigator?.clipboard?.writeText?.(text);
    widget.emit("copy", { text });
    btn.textContent = "✓";
    setTimeout(() => {
      btn.textContent = "⧉";
    }, 1000);
  });
  }
  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

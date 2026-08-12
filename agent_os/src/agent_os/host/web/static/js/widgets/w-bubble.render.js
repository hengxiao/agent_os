/* W-bubble 渲染面(docs/WIDGET-DESIGN.md §3.13 **v4** · 批注批处理工作流 v2.1):
   ``renderBubble(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class,视觉全走契约 token。

   v4 = 单条批注卡(消息流/队列/typing/pill/未读分隔线全部退役):
   - state{anchor, quote, content, status, createdAt, view, draft, severity, generation};
   - view = composing(输入态:v2.1 §3 硬规格——textarea 1→3 行 auto-grow、
     [取消][添加]、空添加禁用)+ expanded(展示态:quote + content + 状态徽标
     + 编辑;删除在卡头 🗑 两击,裁决 C3);
   - 状态徽标 data-status(pending/applied/ignored/outdated;色值走 token,
     见 widgets.css);outdated 进入编辑时 quote 区前缀「原文快照:」提醒(v2.1 §1.2);
   - card 面同步简化:状态徽标 + 内容摘录(计数/未读徽标退役)。 */

import { copy } from "../themes.js";
import { relTime } from "./w-text.render.js";

/* state → html(纯);view 缺省按 content 推(有内容 = 展示态) */
export function renderBubble(state, { surface = "tab" } = {}) {
  if (surface === "card") return _bubbleCardHtml(state);
  const anchor = state.anchor ?? {};
  const anchorTo = [anchor.member, anchor.path].filter(Boolean).join(" · ");
  const lineNo = /#L(\d+)/.exec(anchor.path ?? "")?.[1] ?? "";
  const status = state.status ?? "pending";
  const view = state.view ?? (state.content ? "expanded" : "composing");
  const quote = String(state.quote ?? anchor.quote ?? anchorTo ?? "");
  const head =
    `<div class="w-bubble-head">` +
    `<span class="w-bubble-title">${esc(copy("w.bubble.comment"))} · ${esc(anchorTo)}` +
    (lineNo ? ` <span class="wd-badge mono">L${esc(lineNo)}</span>` : "") +
    `</span>` +
    `<span class="w-bubble-acts">` +
    `<button type="button" class="w-bubble-x w-bubble-del" data-bubble-del="1" ` +
    `aria-label="${esc(copy("w.bubble.del"))}" title="${esc(copy("w.bubble.del"))}">🗑</button>` +
    `<button type="button" class="w-bubble-x" data-bubble-x="1" aria-label="${esc(copy("w.bubble.close"))}">✕</button>` +
    `</span>` +
    `</div>`;
  const quoteBlock = quote
    ? `<div class="w-bubble-quote">` +
      (view === "composing" && status === "outdated"
        ? `<span class="w-bubble-quote-tag">${esc(copy("w.bubble.snapshot"))}</span>`
        : "") +
      `<span class="w-bubble-quote-tx">${esc(quote)}</span>` +
      `</div>`
    : "";
  if (view === "composing") {
    const draft = String(state.draft ?? "");
    return (
      `<div class="w-bubble" role="dialog" data-view="composing" data-status="${esc(status)}" aria-label="${esc(anchorTo)}">` +
      head +
      quoteBlock +
      `<div class="w-bubble-compose">` +
      `<textarea class="input" data-bubble-draft="" rows="1" placeholder="${esc(copy("w.bubble.ph"))}"` +
      ` aria-label="${esc(copy("w.bubble.ph"))}">${esc(draft)}</textarea>` +
      `<div class="w-bubble-compose-bar">` +
      `<span class="w-bubble-hint" data-bubble-hint="1" hidden></span>` +
      `<button type="button" class="w-bubble-cancel" data-bubble-cancel="1">${esc(copy("w.bubble.cancel"))}</button>` +
      `<button type="button" class="wd-btn-primary" data-bubble-send="1"${!draft.trim() ? " disabled" : ""}>${esc(copy("w.bubble.add"))}</button>` +
      `</div></div></div>`
    );
  }
  const gen = state.generation ?? null;
  const createdTs = Date.parse(state.createdAt ?? "") / 1000; // ISO → epoch 秒(NaN→不出时间)
  return (
    `<div class="w-bubble" role="dialog" data-view="expanded" data-status="${esc(status)}" aria-label="${esc(anchorTo)}">` +
    head +
    quoteBlock +
    `<div class="w-bubble-content">${esc(state.content ?? "")}</div>` +
    (gen?.aiNote
      ? `<div class="w-bubble-ainote">${esc(copy("w.bubble.ainote"))}${esc(gen.aiNote)}</div>`
      : "") +
    `<div class="w-bubble-foot">` +
    `<span class="w-bubble-status" data-status="${esc(status)}">${esc(copy(`w.bubble.status.${status}`))}</span>` +
    (createdTs ? ` <span class="w-bubble-time">${esc(relTime(createdTs))}</span>` : "") +
    `<button type="button" class="w-bubble-edit" data-bubble-edit="1">${esc(copy("w.bubble.edit"))}</button>` +
    `</div></div>`
  );
}

/* card 面(v4):状态徽标 + 内容摘录(计数/未读实心徽标退役) */
function _bubbleCardHtml(state) {
  const status = state.status ?? "pending";
  const content = String(state.content ?? "");
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="w-bubble-status" data-status="${esc(status)}">${esc(copy(`w.bubble.status.${status}`))}</span>` +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    (content ? `<span class="wd-card-line">${esc(content)}</span>` : "") +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

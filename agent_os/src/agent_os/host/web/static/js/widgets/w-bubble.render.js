/* W-bubble 渲染面(docs/WIDGET-ARCH.md §1.1/§2.13;W5.3 自渲染;W6.4 按
   docs/WIDGET-DESIGN.md §3.13 重做视觉):
   ``renderBubble(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class,视觉全走契约 token。

   tab(§3.13;**切割线不动**:浮出定位壳/小箭头/段旁圆标在宿主 doc-editor,
   本控件只负责卡本体):头部(批注 · 锚点 + ✕ 收起)+ 锚点引用块(左 3px
   --live 条 + 原文摘录 2 行截断 + 「L7」mono 徽标,从 anchor.path 的 #L{n}
   提取)+ 消息流(图标圆 20px + 名称 500 + 相对时间弱 + 内容;Notion 式
   左右同侧)+ typing 三点(busy)+ 失败行内红条 + 重试 + 输入区(Enter
   发送/Shift+Enter 换行 + 发送钮 --live 实心,空输入禁用)。
   card(§3.13):消息计数 + 未读实心徽标(>0 才显)+ 最后一条摘录;
   无输入框。 */

import { copy } from "../themes.js";
import { relTime } from "./w-text.render.js";

/* state → html(纯);state 面:{anchor:{member,path,span?,quote?}, messages,
   busy, draft, unread, error?, lastText?} */
export function renderBubble(state, { surface = "tab" } = {}) {
  if (surface === "card") return _bubbleCardHtml(state);
  const anchor = state.anchor ?? {};
  const anchorTo = [anchor.member, anchor.path].filter(Boolean).join(" · ");
  const msgs = state.messages ?? [];
  const lineNo = /#L(\d+)/.exec(anchor.path ?? "")?.[1] ?? "";
  const draft = String(state.draft ?? "");
  return (
    `<div class="w-bubble" role="dialog" aria-label="${esc(anchorTo)}">` +
    `<div class="w-bubble-head">` +
    `<span class="w-bubble-title">${esc(copy("w.bubble.comment"))} · ${esc(anchorTo)}</span>` +
    `<button type="button" class="w-bubble-x" data-bubble-x="1" aria-label="${esc(copy("w.bubble.close"))}">✕</button>` +
    `</div>` +
    `<div class="w-bubble-quote">` +
    `<span class="w-bubble-quote-tx">${esc(anchor.quote ?? anchorTo)}</span>` +
    (lineNo ? `<span class="wd-badge mono">L${esc(lineNo)}</span>` : "") +
    `</div>` +
    `<div class="w-bubble-log" role="log">` +
    msgs
      .map((m, i) => {
        const who = m.role === "assistant" ? "Agent" : (m.name ?? copy("w.bubble.you"));
        return (
          `<div class="w-bubble-msg" data-role="${esc(m.role)}">` +
          `<span class="w-bubble-av" data-role="${esc(m.role)}" aria-hidden="true">${m.role === "assistant" ? "✦" : esc(who.slice(0, 1))}</span>` +
          `<span class="w-bubble-body">` +
          `<span class="w-bubble-meta"><b>${esc(who)}</b>` +
          (m.ts ? ` <span class="w-bubble-time">${esc(relTime(m.ts))}</span>` : "") +
          `</span>` +
          `<span class="w-bubble-tx">${esc(m.text)}</span>` +
          (m.role === "assistant"
            ? `<button class="w-bubble-apply" data-apply="${i}">${esc(copy("w.bubble.apply"))}</button>`
            : "") +
          `</span></div>`
        );
      })
      .join("") +
    (state.busy
      ? `<div class="w-bubble-msg" data-role="assistant">` +
        `<span class="w-bubble-av" data-role="assistant" aria-hidden="true">✦</span>` +
        `<span class="w-bubble-typing" aria-hidden="true"><i></i><i></i><i></i></span></div>`
      : "") +
    (state.error
      ? `<div class="w-bubble-fail">⚠ ${esc(state.error)}` +
        `<button type="button" class="w-bubble-retry" data-bubble-retry="1">${esc(copy("w.bubble.retry"))}</button></div>`
      : "") +
    `</div>` +
    `<div class="w-bubble-input">` +
    `<input class="input" data-bubble-draft="" placeholder="${esc(copy("w.bubble.ph"))}"` +
    ` aria-label="${esc(copy("w.bubble.ph"))}" value="${esc(draft)}">` +
    `<button type="button" class="wd-btn-primary" data-bubble-send="1"${state.busy || !draft.trim() ? " disabled" : ""}>${esc(copy("w.bubble.send"))}</button>` +
    `</div></div>`
  );
}

/* card 面(§3.13):计数徽标 + 未读实心徽标(--live 实底白字;>0 才显)+
   最后一条摘录 */
function _bubbleCardHtml(state) {
  const msgs = state.messages ?? [];
  const last = msgs.at(-1);
  const unread = Number(state.unread ?? 0);
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-badge">${esc(copy("w.card.msgs").replace("{n}", String(msgs.length)))}</span>` +
    (unread > 0
      ? `<span class="wd-badge wd-badge-solid" data-tone="live">${esc(copy("w.card.unread").replace("{n}", String(unread)))}</span>`
      : "") +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    (last ? `<span class="wd-card-line">${esc(last.text)}</span>` : "") +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

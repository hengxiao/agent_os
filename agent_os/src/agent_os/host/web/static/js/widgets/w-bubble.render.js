/* W-bubble 渲染面(docs/WIDGET-ARCH.md §1.1/§2.13;W5.3):
   ``renderBubble(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class,视觉全走契约 token。
   效果(§2.13):气泡卡(锚点引用行[高亮槽]+消息流[role=log]+输入框);
   浮出定位/小箭头/收起态小圆标在宿主层(doc-editor 的 wrap/marker,
   不在本控件内)。 */

import { copy } from "../themes.js";

/* state → html(纯);state 面:{anchor:{member,path,span?}, messages, busy, draft} */
export function renderBubble(state) {
  const anchor = state.anchor ?? {};
  const anchorTo = [anchor.member, anchor.path].filter(Boolean).join(" · ");
  const msgs = state.messages ?? [];
  return (
    `<div class="w-bubble" role="dialog" aria-label="${esc(anchorTo)}">` +
    `<div class="w-bubble-anchor mono">${esc(anchorTo)}` +
    (anchor?.span ? ` <span class="pf-dim">¶${esc(String(anchor.span.start ?? ""))}</span>` : "") +
    `</div>` +
    `<div class="w-bubble-log" role="log">` +
    msgs
      .map(
        (m, i) =>
          `<div class="w-bubble-msg" data-role="${esc(m.role)}">` +
          `<span class="w-bubble-tx">${esc(m.text)}</span>` +
          (m.role === "assistant"
            ? `<button class="w-bubble-apply" data-apply="${i}">${esc(copy("w.bubble.apply"))}</button>`
            : "") +
          `</div>`
      )
      .join("") +
    (state.busy
      ? `<div class="w-bubble-msg" data-role="assistant"><span class="pf-skel-line"></span></div>`
      : "") +
    `</div>` +
    `<div class="w-bubble-input">` +
    `<input class="input" data-bubble-draft="" placeholder="${esc(copy("w.bubble.ph"))}"` +
    ` aria-label="${esc(copy("w.bubble.ph"))}" value="${esc(state.draft ?? "")}">` +
    `<button class="btn" data-bubble-send${state.busy ? " disabled" : ""}>${esc(copy("w.bubble.send"))}</button>` +
    `</div></div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

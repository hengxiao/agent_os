/* W-bubble 渲染面(docs/WIDGET-ARCH.md §1.1/§2.13;W5.3 自渲染;
   v2 · 用户验收反馈 2026-08-11,docs/WIDGET-DESIGN.md §3.13 v2):
   ``renderBubble(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class,视觉全走契约 token。

   tab(v2;**切割线不动**:浮出定位壳/小箭头/段旁圆标/最大高度算法在宿主
   doc-editor,本控件只负责卡本体)——自上而下固定四区:
   ① header:批注 · 锚点 + L7 徽标 + ✕ 收起;
   ② quote:锚段摘录 2 行截断 + 左 3px --live 条(保持);
   ③ log = **唯一滚动区**(flex:1 + min-height:0 + overflow-y:auto):
      消息 = 图标圆 20 + 名称 500 + 相对时间弱 + 内容;typing 三点在 log 尾部;
      未读分隔线(「以下是新消息」细线,首次打开插在游标处,state.newFrom);
      长单条消息折叠(>6 行/-webkit-line-clamp:6 + 展开/折叠钮);
      失败行内红条 + 重试(保持);
   ④ composer 钉底:autosize textarea(1 行起 4 行止,超出内滚),
      Enter 发送 / Shift+Enter 换行,发送钮空输入禁用(busy 不禁用——
      发送中可续写,连发进队列,见逻辑面 v2)。
   card(§3.13 保持):消息计数 + 未读实心徽标(>0 才显)+ 最后一条摘录;
   无输入框。 */

import { copy } from "../themes.js";
import { relTime } from "./w-text.render.js";

/* 长消息判定(纯):显式换行 >6 或超 ~240 字(约 6 行视宽) → 折叠面 */
export const bubbleLongMsg = (text) =>
  String(text ?? "").split("\n").length > 6 || String(text ?? "").length > 240;

/* 未读游标(纯):unread = 未见 assistant 条数 → 首个未读消息的下标
   (分隔线插在它前面);无未读/无命中 → -1(不出线) */
export function bubbleNewFrom(messages, unread) {
  const msgs = messages ?? [];
  const n = Number(unread) || 0;
  if (n <= 0) return -1;
  let seen = 0;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i]?.role === "assistant") {
      seen += 1;
      if (seen === n) return i;
    }
  }
  return -1;
}

/* state → html(纯);state 面:{anchor:{member,path,span?,quote?}, messages
   [{role,text,ts,expanded?}], busy, draft, unread, error?, newFrom?, newpill?} */
export function renderBubble(state, { surface = "tab" } = {}) {
  if (surface === "card") return _bubbleCardHtml(state);
  const anchor = state.anchor ?? {};
  const anchorTo = [anchor.member, anchor.path].filter(Boolean).join(" · ");
  const msgs = state.messages ?? [];
  const lineNo = /#L(\d+)/.exec(anchor.path ?? "")?.[1] ?? "";
  const draft = String(state.draft ?? "");
  const newFrom = Number.isInteger(state.newFrom) ? state.newFrom : -1;
  return (
    `<div class="w-bubble" role="dialog" aria-label="${esc(anchorTo)}">` +
    // ① header:批注 · 锚点 + L# 徽标 + 🗑 删除(v3)+ ✕ 收起
    `<div class="w-bubble-head">` +
    `<span class="w-bubble-title">${esc(copy("w.bubble.comment"))} · ${esc(anchorTo)}` +
    (lineNo ? ` <span class="wd-badge mono">L${esc(lineNo)}</span>` : "") +
    `</span>` +
    `<span class="w-bubble-acts">` +
    `<button type="button" class="w-bubble-x w-bubble-del" data-bubble-del="1" ` +
    `aria-label="${esc(copy("w.bubble.del"))}" title="${esc(copy("w.bubble.del"))}">🗑</button>` +
    `<button type="button" class="w-bubble-x" data-bubble-x="1" aria-label="${esc(copy("w.bubble.close"))}">✕</button>` +
    `</span>` +
    `</div>` +
    // ② quote(保持:左 3px live 条 + 2 行截断)
    `<div class="w-bubble-quote">` +
    `<span class="w-bubble-quote-tx">${esc(anchor.quote ?? anchorTo)}</span>` +
    `</div>` +
    // ③ log = 唯一滚动区
    `<div class="w-bubble-log" role="log">` +
    msgs
      .map((m, i) => {
        const who = m.role === "assistant" ? "Agent" : (m.name ?? copy("w.bubble.you"));
        const long = bubbleLongMsg(m.text);
        const clamped = long && !m.expanded;
        return (
          (i === newFrom
            ? `<div class="w-bubble-newline"><span>${esc(copy("w.bubble.newhere"))}</span></div>`
            : "") +
          `<div class="w-bubble-msg" data-role="${esc(m.role)}">` +
          `<span class="w-bubble-av" data-role="${esc(m.role)}" aria-hidden="true">${m.role === "assistant" ? "✦" : esc(who.slice(0, 1))}</span>` +
          `<span class="w-bubble-body">` +
          `<span class="w-bubble-meta"><b>${esc(who)}</b>` +
          (m.ts ? ` <span class="w-bubble-time">${esc(relTime(m.ts))}</span>` : "") +
          `</span>` +
          `<span class="w-bubble-tx${clamped ? " is-clamped" : ""}">${esc(m.text)}</span>` +
          (long
            ? `<button type="button" class="w-bubble-more" data-more="${i}">${esc(copy(m.expanded ? "w.bubble.collapse" : "w.bubble.expand"))}</button>`
            : "") +
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
    // 「↓ 新消息」pill(非底部新消息提示;逻辑面控 hidden)
    `<button type="button" class="w-bubble-pill" data-bubble-pill="1"${state.newpill ? "" : " hidden"}>${esc(copy("w.bubble.newpill"))}</button>` +
    // ④ composer 钉底(autosize textarea;发送钮空输入禁用,busy 不吞)
    `<div class="w-bubble-input">` +
    `<textarea class="input" data-bubble-draft="" rows="1" placeholder="${esc(copy("w.bubble.ph"))}"` +
    ` aria-label="${esc(copy("w.bubble.ph"))}">${esc(draft)}</textarea>` +
    `<button type="button" class="wd-btn-primary" data-bubble-send="1"${!draft.trim() ? " disabled" : ""}>${esc(copy("w.bubble.send"))}</button>` +
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

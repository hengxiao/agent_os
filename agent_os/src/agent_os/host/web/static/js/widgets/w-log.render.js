/* W-log 渲染面(docs/WIDGET-ARCH.md §1.1/§2.10;W5.3):
   ``renderLogViewer(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.10):mono 滚动区;kind 左侧色条(--sig-* 信号色,data-kind 驱动,
   CSS 面);暂停跟随才显示"回到底部"浮钮;顶部过滤框;空态。 */

import { copy } from "../themes.js";

/* 过滤后的可见行(纯;逻辑面共用) */
export function visibleLogLines(state) {
  const filter = state.filter ?? "";
  return (state.lines ?? []).filter(
    (l) => !filter || l.text.toLowerCase().includes(filter.toLowerCase()) || l.kind.includes(filter)
  );
}

/* state → html(纯);state 面:{lines:[{kind,text}], follow, filter} */
export function renderLogViewer(state) {
  const vis = visibleLogLines(state);
  return (
    `<div class="wd-log">` +
    `<div class="wd-log-bar">` +
    `<input class="input wd-log-filter" data-wlog-filter="1" placeholder="${esc(copy("w.log.filter"))}"` +
    ` aria-label="${esc(copy("w.log.filter"))}" value="${esc(state.filter ?? "")}">` +
    `<button class="wd-mini" data-wlog-copy="1">${esc(copy("w.log.copy"))}</button>` +
    `</div>` +
    `<div class="wd-log-box" role="log" data-wlog-box="1">` +
    vis
      .map(
        (l) =>
          `<div class="wd-log-line" data-kind="${esc(l.kind)}">` +
          `<span class="wd-log-kind mono">${esc(l.kind)}</span> ${esc(l.text)}</div>`
      )
      .join("") +
    (vis.length ? "" : `<div class="wd-empty">${esc(copy("w.log.empty"))}</div>`) +
    `</div>` +
    (state.follow ? "" : `<button class="wd-log-bottom" data-wlog-bottom="1">${esc(copy("w.log.bottom"))}</button>`) +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

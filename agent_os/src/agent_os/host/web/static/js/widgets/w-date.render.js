/* W-date 渲染面(docs/WIDGET-ARCH.md §1.1/§2.8;W5.3):
   ``renderDatePicker(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.8):输入框 + 日历弹层(state.open 控制,Esc 收层)、月网格
   (今天圆点、选中区间高亮带)、快捷项左列(range)。 */

import { copy } from "../themes.js";

/* 月历网格(自绘;cell = data-day 的按钮;今天高亮;选中高亮) */
export function monthGridHtml(year, month, { selectedStart = "", selectedEnd = "" } = {}) {
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7;
  const today = _iso(new Date());
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(`<span class="wd-day" data-empty="1"></span>`);
  for (let d = 1; d <= days; d++) {
    const iso = _iso(new Date(year, month, d));
    const sel =
      iso === selectedStart || iso === selectedEnd || (selectedStart && selectedEnd && iso > selectedStart && iso < selectedEnd);
    cells.push(
      `<button class="wd-day" data-day="${iso}"${iso === today ? ' data-today="1"' : ""}${sel ? ' data-selected="1"' : ""}>${d}</button>`
    );
  }
  return `<div class="wd-grid" role="grid">${cells.join("")}</div>`;
}

const _iso = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/* state → html(纯);state 面:{value, mode, inverted, open, cursor:{year, month}} */
export function renderDatePicker(state) {
  const v = state.value;
  const mode = state.mode;
  const cursor = state.cursor;
  const inputs =
    mode === "range"
      ? `<input class="input wd-date-in" data-wd-start="1" value="${esc(v.start)}" placeholder="YYYY-MM-DD" aria-label="start">` +
        `<input class="input wd-date-in" data-wd-end="1" value="${esc(v.end)}" placeholder="YYYY-MM-DD" aria-label="end">` +
        (state.inverted ? `<span class="wd-errbar">${esc(copy("w.date.inverted"))}</span>` : "")
      : `<input class="input wd-date-in" data-wd-value="1" value="${esc(v)}"` +
        ` placeholder="${mode === "datetime" ? "YYYY-MM-DDThh:mm" : "YYYY-MM-DD"}" aria-label="date">`;
  return (
    `<div class="wd-date">` +
    inputs +
    (mode === "range"
      ? `<span class="wd-quick">` +
        ["today", "yesterday", "week", "lastweek"]
          .map((q) => `<button class="wd-quick-btn" data-wd-quick="${q}">${esc(copy(`w.date.${q}`))}</button>`)
          .join("") +
        `</span>`
      : "") +
    // 日历弹层(§2.8):open 才出;Esc 收层在逻辑面
    (state.open === false
      ? ""
      : `<div class="wd-date-layer" data-wd-layer="1">` +
        `<div class="wd-month">` +
        `<button class="wd-nav" data-wd-prev aria-label="${esc(copy("w.date.prev"))}">‹</button>` +
        `<span class="wd-month-title">${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}</span>` +
        `<button class="wd-nav" data-wd-next aria-label="${esc(copy("w.date.next"))}">›</button>` +
        `</div>` +
        monthGridHtml(cursor.year, cursor.month, {
          selectedStart: mode === "range" ? v.start : v,
          selectedEnd: mode === "range" ? v.end : "",
        }) +
        `</div>`) +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

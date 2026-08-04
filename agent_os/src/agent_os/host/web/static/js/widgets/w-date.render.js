/* W-date 渲染面(docs/WIDGET-ARCH.md §1.1/§2.8;W5.3):
   ``renderDatePicker(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.8):输入框 + 日历弹层(state.open 控制,Esc 收层)、月网格
   (今天圆点、选中区间高亮带)、快捷项左列(range)。双形态(§1.4,W5.6):
   card = 当前值/区间摘要 + 快捷徽标,tab 完整交互。 */

import { copy } from "../themes.js";
import { quickRange } from "./w-date.js"; // 循环引用安全:仅在渲染期调用(提升的函数声明)

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

/* state → html(纯);state 面:{value, mode, inverted, open, cursor:{year, month}}
   双形态(§1.4):surface="card" → 当前日期/区间(起 → 止)+ 快捷标签徽标
   (值命中 today/yesterday/week/lastweek 时);不开日历层,无输入框 */
export function renderDatePicker(state, { surface = "tab" } = {}) {
  if (surface === "card") return _dateCardHtml(state);
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

/* card 面(§1.4):当前值一行(range = 起 → 止;缺侧给 …;空值给占位)+
   快捷标签徽标(仅当值精确命中某快捷区间);不开日历层 */
function _dateCardHtml(state) {
  const v = state.value;
  const mode = state.mode;
  let text;
  let quick = "";
  if (mode === "range") {
    text = `${v?.start || "…"} → ${v?.end || "…"}`;
    if (v?.start && v?.end) {
      for (const q of ["today", "yesterday", "week", "lastweek"]) {
        const r = quickRange(q);
        if (r && r.start === v.start && r.end === v.end) {
          quick = q;
          break;
        }
      }
    }
  } else {
    text = v || copy("w.card.empty");
    if (v) {
      for (const q of ["today", "yesterday"]) {
        if (quickRange(q)?.start === v) {
          quick = q;
          break;
        }
      }
    }
  }
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-line mono">${esc(text)}</span>` +
    (quick
      ? `<span class="wd-card-head"><span class="wd-badge">${esc(copy(`w.date.${quick}`))}</span></span>`
      : "") +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
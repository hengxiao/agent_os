/* W-date 渲染面(docs/WIDGET-ARCH.md §1.1/§2.8;W5.3 自渲染;W6.3 按
   docs/WIDGET-DESIGN.md §3.8 重做视觉):
   ``renderDatePicker(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。

   tab(§3.8):输入框 📅 图标 + 焦点环(手输非法 → 红边 + 行内 ⚠ 提示);
   快捷 chips(选中 = --live 浅底 + --live 字,命中在渲染层按 quickRange 算);
   弹层(--shadow-md + 140ms 入场;**range 双月并排**,单月单历;星期头 11px
   弱;日期格 28×28 圆角 6:hover --bg-2;今天 --live 描边圈;选中端点
   --live 实底;区间 = 连续 --live 12% 带(整格贯通);端点=今天叠底点双编码;
   非当月 35% 透明);纠序提示(↔ 已自动调整顺序,--live 文案 + 1.5s 闪)。
   card(§3.8):日期/区间 mono 单行 + 命中快捷徽标 + meta(mode · 共 N 天)。 */

import { copy } from "../themes.js";
import { quickRange } from "./w-date.js"; // 循环引用安全:仅在渲染期调用(提升的函数声明)

const _iso = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/* 月历网格(自绘;星期头 + 前导/后随邻月日 35% 透明可点;区间带 is-range
   整格贯通(跨格续接在 CSS:格距由 wrapper 承担);端点 is-end 实底;
   今天 data-today(端点时叠底点双编码)) */
export function monthGridHtml(year, month, { selectedStart = "", selectedEnd = "" } = {}) {
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7;
  const today = _iso(new Date());
  const cell = (d, iso, out) => {
    const isStart = Boolean(selectedStart) && iso === selectedStart;
    const isEnd = Boolean(selectedEnd) && iso === selectedEnd;
    const endpoint = isStart || isEnd;
    const inRange = selectedStart && selectedEnd && iso > selectedStart && iso < selectedEnd;
    return (
      `<span class="wd-dcell${inRange ? " is-range" : ""}${out ? " is-out" : ""}">` +
      `<button type="button" class="wd-day${endpoint ? " is-end" : ""}" data-day="${iso}"` +
      `${iso === today ? ' data-today="1"' : ""}${endpoint ? ' data-selected="1"' : ""}>${d}</button></span>`
    );
  };
  const cells = [];
  const prevDays = new Date(year, month, 0).getDate();
  for (let i = lead - 1; i >= 0; i--) cells.push(cell(prevDays - i, _iso(new Date(year, month - 1, prevDays - i)), true));
  for (let d = 1; d <= days; d++) cells.push(cell(d, _iso(new Date(year, month, d)), false));
  const rem = (7 - (cells.length % 7)) % 7;
  for (let i = 1; i <= rem; i++) cells.push(cell(i, _iso(new Date(year, month + 1, i)), true));
  const week = [...copy("w.date.weekdays")].map((w) => `<span class="wd-wd">${esc(w)}</span>`).join("");
  return `<div class="wd-week" aria-hidden="true">${week}</div><div class="wd-grid" role="grid">${cells.join("")}</div>`;
}

/* 快捷命中(与 card 徽标同源):值精确等于某快捷区间 → 其 key,否则 "" */
function _quickHit(state) {
  const v = state.value;
  if (state.mode === "range" && v?.start && v?.end) {
    for (const q of ["today", "yesterday", "week", "lastweek"]) {
      const r = quickRange(q);
      if (r && r.start === v.start && r.end === v.end) return q;
    }
  } else if (state.mode === "date" && v) {
    for (const q of ["today", "yesterday"]) if (quickRange(q)?.start === v) return q;
  }
  return "";
}

/* 月块(导航钮位置由 nav 给定;双月:‹ 在左块、› 在右块,macOS 同律) */
function _monthBlock(year, month, nav, sel) {
  const title = copy("w.date.monthtitle").replace("{y}", String(year)).replace("{m}", String(month + 1));
  return (
    `<div class="wd-monthblk">` +
    `<div class="wd-month">` +
    (nav.includes("prev")
      ? `<button class="wd-nav" data-wd-prev aria-label="${esc(copy("w.date.prev"))}">‹</button>`
      : `<span class="wd-nav-sp" aria-hidden="true"></span>`) +
    `<span class="wd-month-title" data-ym="${year}-${String(month + 1).padStart(2, "0")}">${esc(title)}</span>` +
    (nav.includes("next")
      ? `<button class="wd-nav" data-wd-next aria-label="${esc(copy("w.date.next"))}">›</button>`
      : `<span class="wd-nav-sp" aria-hidden="true"></span>`) +
    `</div>` +
    monthGridHtml(year, month, sel) +
    `</div>`
  );
}

/* state → html(纯);state 面:{value, mode, inverted, open, cursor:{year, month},
   invalid?:{which, text}, notice?, title?} */
export function renderDatePicker(state, { surface = "tab" } = {}) {
  if (surface === "card") return _dateCardHtml(state);
  const v = state.value;
  const mode = state.mode;
  const cursor = state.cursor;
  const invalid = state.invalid ?? null;
  const field = (which, val, ph) =>
    `<span class="wd-date-field${invalid?.which === which ? " is-invalid" : ""}">` +
    `<span class="wd-date-ico" aria-hidden="true">📅</span>` +
    `<input class="wd-date-in" data-wd-${which}="1" value="${esc(invalid?.which === which ? invalid.text : val)}"` +
    ` placeholder="${ph}" aria-label="${esc(which)}">` +
    `</span>`;
  const inputs =
    mode === "range"
      ? field("start", v.start, "YYYY-MM-DD") +
        `<span class="wd-date-arrow" aria-hidden="true">→</span>` +
        field("end", v.end, "YYYY-MM-DD")
      : field("value", v, mode === "datetime" ? "YYYY-MM-DDThh:mm" : "YYYY-MM-DD");
  const hit = _quickHit(state);
  const quick =
    mode === "range"
      ? `<span class="wd-quick">` +
        ["today", "yesterday", "week", "lastweek"]
          .map(
            (q) =>
              `<button class="wd-chip" data-wd-quick="${q}" data-on="${hit === q ? "1" : "0"}">${esc(copy(`w.date.${q}`))}</button>`
          )
          .join("") +
        `</span>`
      : "";
  const sel = { selectedStart: mode === "range" ? v.start : v, selectedEnd: mode === "range" ? v.end : "" };
  const nm = { year: cursor.year + (cursor.month > 10 ? 1 : 0), month: (cursor.month + 1) % 12 };
  const layer =
    state.open === false
      ? ""
      : `<div class="wd-date-layer" data-wd-layer="1">` +
        `<div class="wd-months${mode === "range" ? " two" : ""}">` +
        _monthBlock(cursor.year, cursor.month, mode === "range" ? ["prev"] : ["prev", "next"], sel) +
        (mode === "range" ? _monthBlock(nm.year, nm.month, ["next"], sel) : "") +
        `</div>` +
        `</div>`;
  return (
    `<div class="wd-date">` +
    (state.title ? `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title)} · ${esc(mode)}</span></div>` : "") +
    `<div class="wd-date-inputs">${inputs}</div>` +
    (invalid ? `<span class="wd-errbar">${esc(copy("w.date.invalid"))}</span>` : "") +
    quick +
    `<div class="wd-date-hint">${esc(copy("w.date.hint"))}</div>` +
    (state.notice === "fix" ? `<div class="wd-date-fix">${esc(copy("w.date.fix"))}</div>` : "") +
    layer +
    `</div>`
  );
}

/* card 面(§3.8):当前值一行(range = 起 → 止;缺侧给 …;空值给占位)+
   快捷标签徽标(仅精确命中)+ meta(mode · 共 N 天);不开日历层 */
function _dateCardHtml(state) {
  const v = state.value;
  const mode = state.mode;
  let text;
  const meta = [mode];
  if (mode === "range") {
    text = `${v?.start || "…"} → ${v?.end || "…"}`;
    if (v?.start && v?.end && v.start <= v.end) {
      const days = Math.round((new Date(v.end) - new Date(v.start)) / 86400000) + 1;
      meta.push(copy("w.date.days").replace("{n}", String(days)));
    }
  } else {
    text = v || copy("w.card.empty");
  }
  const quick = _quickHit(state);
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    (state.title ? `<span class="wd-card-dim">${esc(state.title)}</span>` : "") +
    `<span class="wd-card-line mono">${esc(text)}</span>` +
    (quick
      ? `<span class="wd-card-head"><span class="wd-chip" data-tone="live">${esc(copy(`w.date.${quick}`))}</span></span>`
      : "") +
    `<span class="wd-card-meta"><span class="wd-card-meta-txt">${esc(meta.join(" · "))}</span>` +
    `<span class="wd-card-all">${esc(copy("w.card.go"))}</span></span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

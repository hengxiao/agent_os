/* W-date — 日期时间控件(docs/WIDGETS.md §2;browse 时间窗/报告区间)。

   state{value: iso | {start, end}, mode: "date"|"datetime"|"range", min?, max?};
   actions 全 local:set(键盘输入,即时校验 ISO)/prev/next(翻月)/pick(日历点选)/
   quick(今天/昨天/本周/上周 快捷项);
   细节:输入与日历双通道、range 倒置即时警示、翻页键盘可达(←→)、
   本地时区显示(不引入时区选择,§7 不做)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const DATE_EDITOR_DEF = registerWidgetDef({
  kind: "date-picker",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { value: "", mode: "date", inverted: false },
  actions: [
    { id: "set", exec: "local", args_input: { value: { type: "string" } } },
    { id: "prev", exec: "local" },
    { id: "next", exec: "local" },
    { id: "pick", exec: "local", args_input: { day: { type: "string" } } },
    { id: "quick", exec: "local", args_input: { which: { type: "string" } } },
  ],
  events: ["change"],
  aria: { role: "group", keys: ["ArrowLeft", "ArrowRight"] },
  surfaces: ["card", "tab"],
});

/* ISO 校验:date = YYYY-MM-DD;datetime = 允许 T hh:mm(:ss);非法 → null */
export function parseIso(text, mode = "date") {
  const t = String(text ?? "").trim();
  if (!t) return null;
  const re =
    mode === "datetime" ? /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$/ : /^\d{4}-\d{2}-\d{2}$/;
  if (!re.test(t)) return null;
  const d = new Date(t.length === 10 ? `${t}T00:00:00` : t);
  return Number.isNaN(d.getTime()) ? null : d;
}

const _iso = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/* 快捷项(本地时区):今天/昨天/本周(周一起)/上周 */
export function quickRange(which, now = new Date()) {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const shift = (n) => new Date(day.getFullYear(), day.getMonth(), day.getDate() + n);
  if (which === "today") return { start: _iso(day), end: _iso(day) };
  if (which === "yesterday") return { start: _iso(shift(-1)), end: _iso(shift(-1)) };
  const dow = (day.getDay() + 6) % 7; // 周一 = 0
  if (which === "week") return { start: _iso(shift(-dow)), end: _iso(day) };
  if (which === "lastweek") return { start: _iso(shift(-dow - 7)), end: _iso(shift(-dow - 1)) };
  return null;
}

/* range 倒置警示(start > end → true) */
export function rangeInverted(value) {
  if (!value?.start || !value?.end) return false;
  return value.start > value.end;
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

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

/* 挂进宿主:mode/date|datetime|range + 初始 value;quick 钮 + 翻月 + 点选 + 输入 */
export function mountDatePicker(
  host,
  { mode = "date", value = null, path = "", onRegister = null, onUnregister = null } = {}
) {
  const widget = createWidget(DATE_EDITOR_DEF, {
    path,
    state: {
      mode,
      value: mode === "range" ? (value ?? { start: "", end: "" }) : (value ?? ""),
      inverted: false,
    },
    onRegister,
    onUnregister,
  });
  // 翻月游标:初始取 start/value 所在月,否则本月
  const base = parseIso(mode === "range" ? widget.state.value.start : widget.state.value, mode) ?? new Date();
  let cursor = { year: base.getFullYear(), month: base.getMonth() };

  const _changed = () => widget.emit("change", { value: widget.state.value });

  function render() {
    const v = widget.state.value;
    const inputs =
      mode === "range"
        ? `<input class="input wd-date-in" data-wd-start="1" value="${esc(v.start)}" placeholder="YYYY-MM-DD" aria-label="start">` +
          `<input class="input wd-date-in" data-wd-end="1" value="${esc(v.end)}" placeholder="YYYY-MM-DD" aria-label="end">` +
          (widget.state.inverted ? `<span class="wd-errbar">${esc(copy("w.date.inverted"))}</span>` : "")
        : `<input class="input wd-date-in" data-wd-value="1" value="${esc(v)}"` +
          ` placeholder="${mode === "datetime" ? "YYYY-MM-DDThh:mm" : "YYYY-MM-DD"}" aria-label="date">`;
    host.innerHTML =
      `<div class="wd-date">` +
      inputs +
      (mode === "range"
        ? `<span class="wd-quick">` +
          ["today", "yesterday", "week", "lastweek"]
            .map((q) => `<button class="wd-quick-btn" data-wd-quick="${q}">${esc(copy(`w.date.${q}`))}</button>`)
            .join("") +
          `</span>`
        : "") +
      `<div class="wd-month">` +
      `<button class="wd-nav" data-wd-prev aria-label="${esc(copy("w.date.prev"))}">‹</button>` +
      `<span class="wd-month-title">${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}</span>` +
      `<button class="wd-nav" data-wd-next aria-label="${esc(copy("w.date.next"))}">›</button>` +
      `</div>` +
      monthGridHtml(cursor.year, cursor.month, {
        selectedStart: mode === "range" ? v.start : v,
        selectedEnd: mode === "range" ? v.end : "",
      }) +
      `</div>`;
  }

  widget.set = (which, text) => {
    const d = parseIso(text, mode === "datetime" ? "datetime" : "date");
    if (mode === "range") {
      widget.state.value = { ...widget.state.value, [which]: text };
      widget.state.inverted = rangeInverted(widget.state.value);
    } else if (d || !text) {
      widget.state.value = text;
    }
    render();
    _changed();
  };
  widget.pick = (day) => {
    if (mode === "range") {
      // 简化策略:起点空 → 填起点;否则填终点并自动纠正顺序
      const v = { ...widget.state.value };
      if (!v.start || (v.start && v.end)) {
        v.start = day;
        v.end = "";
      } else {
        v.end = day;
        if (v.end < v.start) [v.start, v.end] = [v.end, v.start]; // 点选自动纠正(输入通道才警示)
      }
      widget.state.value = v;
      widget.state.inverted = rangeInverted(v);
    } else {
      widget.state.value = day;
    }
    render();
    _changed();
  };
  widget.quick = (which) => {
    const r = quickRange(which);
    if (r) {
      widget.state.value = mode === "range" ? r : r.start;
      widget.state.inverted = false;
      render();
      _changed();
    }
  };
  widget.prev = () => {
    cursor.month -= 1;
    if (cursor.month < 0) {
      cursor.month = 11;
      cursor.year -= 1;
    }
    render();
  };
  widget.next = () => {
    cursor.month += 1;
    if (cursor.month > 11) {
      cursor.month = 0;
      cursor.year += 1;
    }
    render();
  };

  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-wd-start]")) widget.set("start", e.target.value);
    if (e.target.closest("[data-wd-end]")) widget.set("end", e.target.value);
    if (e.target.closest("[data-wd-value]")) widget.set("value", e.target.value);
  });
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-wd-prev]")) return widget.prev();
    if (e.target.closest("[data-wd-next]")) return widget.next();
    const q = e.target.closest("[data-wd-quick]");
    if (q) return widget.quick(q.dataset.wdQuick);
    const day = e.target.closest("[data-day]");
    if (day) return widget.pick(day.dataset.day);
  });
  host.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") widget.prev(); // 翻页键盘可达(§2)
    if (e.key === "ArrowRight") widget.next();
  });

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

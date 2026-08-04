/* W-date 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.8;W5.3 新形态:自渲染)。

   state{value: iso | {start, end}, mode: "date"|"datetime"|"range",
   inverted, open, cursor:{year, month}};
   actions 全 local:set(键盘输入,即时校验 ISO)/prev/next(翻月)/pick(日历点选)/
   quick(今天/昨天/本周/上周 快捷项);
   细节:输入与日历双通道、range 点选倒置自动纠序(输入通道给警示)、
   翻页键盘可达(←→)、**Esc 收层**(open=false;输入聚焦/点选即 reopen)、
   本地时区显示(不引入时区选择,§7 不做)。
   铁律:本文件不拼 HTML(渲染全在 w-date.render.js);零 fetch;监听委托在 host。 */

import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";
import { monthGridHtml, renderDatePicker } from "./w-date.render.js";

export { monthGridHtml }; // 兼容面(迁至渲染面;原从本文件导出)

export const DATE_EDITOR_DEF = registerWidgetDef({
  kind: "date-picker",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { value: "", mode: "date", inverted: false, open: true, cursor: null },
  actions: [
    { id: "set", exec: "local", args_input: { value: { type: "string" } } },
    { id: "prev", exec: "local" },
    { id: "next", exec: "local" },
    { id: "pick", exec: "local", args_input: { day: { type: "string" } } },
    { id: "quick", exec: "local", args_input: { which: { type: "string" } } },
  ],
  events: ["change"],
  aria: { role: "group", keys: ["ArrowLeft", "ArrowRight", "Escape"] },
  surfaces: ["card", "tab"],
  render: renderDatePicker, // W5.3:render 面进 def(registry 校验形态)
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

/* 挂进宿主:mode/date|datetime|range + 初始 value;quick 钮 + 翻月 + 点选 + 输入 */
export function mountDatePicker(
  host,
  { mode = "date", value = null, path = "", onRegister = null, onUnregister = null } = {}
) {
  const initial = mode === "range" ? (value ?? { start: "", end: "" }) : (value ?? "");
  const base = parseIso(mode === "range" ? initial.start : initial, mode) ?? new Date();
  const widget = createWidget(DATE_EDITOR_DEF, {
    path,
    state: {
      mode,
      value: initial,
      inverted: false,
      open: true, // 弹层默认开(§2.8;Esc 收层)
      cursor: { year: base.getFullYear(), month: base.getMonth() }, // 翻月游标:start/value 所在月,否则本月
    },
    onRegister,
    onUnregister,
  });

  const render = () => {
    host.innerHTML = renderDatePicker(widget.state);
  };
  const _changed = () => widget.emit("change", { value: widget.state.value });

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
    widget.state.open = true; // 点选即开层(收层后点选语义保持)
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
    widget.state.cursor.month -= 1;
    if (widget.state.cursor.month < 0) {
      widget.state.cursor.month = 11;
      widget.state.cursor.year -= 1;
    }
    render();
  };
  widget.next = () => {
    widget.state.cursor.month += 1;
    if (widget.state.cursor.month > 11) {
      widget.state.cursor.month = 0;
      widget.state.cursor.year += 1;
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
    // 输入区点击 = 开层(收层后的回归路径)
    if (e.target.closest("[data-wd-start],[data-wd-end],[data-wd-value]") || e.target.closest(".wd-date-in")) {
      if (!widget.state.open) {
        widget.state.open = true;
        render();
      }
    }
  });
  host.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") widget.prev(); // 翻页键盘可达(§2)
    if (e.key === "ArrowRight") widget.next();
    if (e.key === "Escape") {
      // Esc 收层(§2.8 弹层面)
      widget.state.open = false;
      render();
    }
  });

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

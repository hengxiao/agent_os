/* W-date 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.8;W5.3 新形态:自渲染;
   W6.3 视觉按 docs/WIDGET-DESIGN.md §3.8)。

   state{value: iso | {start, end}, mode: "date"|"datetime"|"range",
   inverted, open, cursor:{year, month}, invalid:{which,text}|null, notice,
   title};
   actions 全 local:set(键盘输入,即时校验 ISO)/prev/next(翻月)/pick(日历点选)/
   quick(今天/昨天/本周/上周 快捷项);
   细节:输入与日历双通道、**range 倒置自动纠序 + 闪提示**(§3.8 设计批准的
   行为变更:输入通道原来只警示,现在与点选同律纠序 + 1.5s 提示)、
   手输非法 → 红边 + 行内提示(state.invalid,值不丢)、翻页键盘可达(←→;
   焦点在日上时方向键走格,Enter 选定)、**Esc 收层 + 点外收层**、
   弹层贴近视口底自动上翻(_flipLayer)、本地时区显示(不引入时区选择,§7 不做)。
   铁律:本文件不拼 HTML(渲染全在 w-date.render.js);零 fetch;监听委托在 host。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget, preserveSelection } from "./widget.js";
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
  events: ["change", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "group", keys: ["ArrowLeft", "ArrowRight", "Escape"] },
  surfaces: ["card", "tab"],
  render: renderDatePicker, // W5.3:render 面进 def(registry 校验形态)
  mount: mountDatePicker,
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

/* 挂进宿主:mode/date|datetime|range + 初始 value + title(面板头,可选);
   quick 钮 + 翻月 + 点选 + 输入 */
export function mountDatePicker(
  host,
  { mode = "date", value = null, title = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}
) {
  const initial = mode === "range" ? (value ?? { start: "", end: "" }) : (value ?? "");
  const base = parseIso(mode === "range" ? initial.start : initial, mode) ?? new Date();
  const widget = createWidget(DATE_EDITOR_DEF, {
    path,
    state: {
      mode,
      value: initial,
      inverted: false,
      open: true, // 弹层默认开(§2.8;Esc/点外收层)
      cursor: { year: base.getFullYear(), month: base.getMonth() }, // 翻月游标:start/value 所在月,否则本月
      invalid: null, // {which, text}——手输非法(§3.8 红边 + 行内提示,值不丢)
      notice: "", // "fix" = 纠序闪提示(1.5s)
      title,
    },
    onRegister,
    onUnregister,
  });
  let _noticeTimer = null;

  const render = () => {
    host.innerHTML = renderDatePicker(widget.state, { surface });
    _flipLayer(); // 弹层贴近视口底部时自动上翻(§3.8)
  };
  const _changed = () => widget.emit("change", { value: widget.state.value });

  /* 弹层边缘翻转(§3.8):下方不够长就上翻,右缘出视口就右对齐
     (stub/无测量环境安全跳过) */
  const _flipLayer = () => {
    const layer = host.querySelector("[data-wd-layer]");
    if (!layer?.getBoundingClientRect || typeof globalThis.innerHeight !== "number") return;
    const r = layer.getBoundingClientRect();
    const up = r.bottom > globalThis.innerHeight && r.top - r.height > 0;
    layer.classList?.toggle("up", up);
    // 右缘翻转(§3.8 v2):右出视口 → 贴宿主右边
    const right = typeof globalThis.innerWidth === "number" && r.right > globalThis.innerWidth;
    layer.classList?.toggle("right", Boolean(right));
  };

  /* 纠序闪提示(§3.8:自动纠序是可感知反馈,不静默改值) */
  const _flashFix = () => {
    widget.state.notice = "fix";
    if (_noticeTimer) clearTimeout(_noticeTimer);
    _noticeTimer = setTimeout(() => {
      widget.state.notice = "";
      render();
    }, 1500);
  };
  /* range 倒置 → 交换 + 闪提示(输入/点选同律,§3.8) */
  const _fixOrder = () => {
    const val = widget.state.value;
    if (val?.start && val?.end && val.start > val.end) {
      widget.state.value = { start: val.end, end: val.start };
      _flashFix();
    }
  };

  widget.set = (which, text) => {
    const d = parseIso(text, mode === "datetime" ? "datetime" : "date");
    if (mode === "range") {
      widget.state.value = { ...widget.state.value, [which]: text };
      widget.state.invalid = !text || d ? null : { which, text }; // 非法:红边提示,值保留
      if (!widget.state.invalid) _fixOrder(); // 倒置纠序 + 闪提示(§3.8)
    } else if (d || !text) {
      widget.state.value = text;
      widget.state.invalid = null;
    } else {
      widget.state.invalid = { which: "value", text };
    }
    // §3.8 v2(用户验收):输入逐字重渲会丢焦点/光标——选区保留重渲
    // (W5.1 基座同一答案;定位面 = 对应输入框的 data 属性选择器)
    preserveSelection(host, render, { selector: `[data-wd-${which}="1"]` });
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
      }
      widget.state.value = v;
      _fixOrder(); // 点选纠序 + 闪提示(§3.8)
    } else {
      widget.state.value = day;
    }
    widget.state.invalid = null;
    widget.state.open = true; // 点选即开层(收层后的回归路径)
    render();
    _changed();
  };
  widget.quick = (which) => {
    const r = quickRange(which);
    if (r) {
      widget.state.value = mode === "range" ? r : r.start;
      widget.state.invalid = null;
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

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
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
    // 焦点在日格上:方向键走格(§3.8 键盘全程;Enter = 原生钮点击选定)
    const day = e.target.closest?.("[data-day]");
    if (day && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
      const d = parseIso(day.dataset.day);
      if (!d) return;
      const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
      const nd = new Date(d.getFullYear(), d.getMonth(), d.getDate() + delta);
      const niso = _iso(nd);
      const btn = host.querySelector(`[data-day="${niso}"]`);
      if (btn) {
        btn.focus?.(); // 目标格在当前弹层(双月覆盖邻月首尾)
      } else {
        if (niso < _iso(new Date(widget.state.cursor.year, widget.state.cursor.month, 1))) widget.prev();
        else widget.next();
        host.querySelector(`[data-day="${niso}"]`)?.focus?.();
      }
      return;
    }
    if (day) return; // 日格上的其他键不翻月
    if (e.key === "ArrowLeft") widget.prev(); // 翻页键盘可达(§2)
    if (e.key === "ArrowRight") widget.next();
    if (e.key === "Escape") {
      // Esc 收层(§2.8 弹层面)
      widget.state.open = false;
      render();
    }
  });
  // 点外收层(§3.8;委托挂 document 的 **capture 阶段**——pick/翻月会整树
  // 重渲,若用 bubble 阶段,document 在重渲之后才收到 click,目标元素已
  // 脱离文档,contains 判 false → 把弹层内点击误判为「外」而误收层;
  // capture 在宿主重渲之前执行,contains 仍然成立。destroy 时摘除)
  const _doc = host.ownerDocument ?? globalThis.document;
  const _onDocClick = (e) => {
    if (!widget.state.open) return;
    if (e.target && host.contains?.(e.target)) return;
    widget.state.open = false;
    render();
  };
  _doc?.addEventListener?.("click", _onDocClick, true);
  const _destroy = widget.destroy.bind(widget);
  widget.destroy = () => {
    if (_noticeTimer) clearTimeout(_noticeTimer);
    _doc?.removeEventListener?.("click", _onDocClick); // 点外收层监听随 destroy 摘除
    _destroy();
  };
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

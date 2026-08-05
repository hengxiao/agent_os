/* W-chart 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.9;W5.3 新形态:自渲染)。

   state{series: [{name, points: [{x, y}]}], type: "line"|"bar"|"spark",
   view: "chart"|"table", hidden: [names]};
   actions 全 local:set_series/toggle_view/toggle_series(图例显隐);
   细节:空态("还没有数据");>500 点抽稀;hover 读值(title);
   **a11y 硬规则:等价数据表备选**——同一 series 可切"表格视图"。
   铁律:本文件不拼 HTML(渲染全在 w-chart.render.js);零依赖零 fetch;
   监听委托在 host。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { chartTipHtml, downsample, renderChart } from "./w-chart.render.js";

// 兼容面:纯函数迁至渲染面(W5.3),此处原样 re-export(测试与消费方在用)
export { chartSvg, chartTableHtml, downsample, niceTicks } from "./w-chart.render.js";

export const CHART_DEF = registerWidgetDef({
  kind: "chart",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { series: [], type: "line", view: "chart", hidden: [] },
  actions: [
    { id: "set_series", exec: "local" },
    { id: "toggle_view", exec: "local" },
    { id: "toggle_series", exec: "local", args_input: { name: { type: "string" } } },
  ],
  events: ["change", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "img", keys: [] },
  surfaces: ["card", "tab"],
  render: renderChart, // W5.3:render 面进 def(registry 校验形态)
});

/* 挂进宿主:series + type + label(aria 摘要)+ title(可选,缺省用 label);
   toggle 切"图表/表格"两视图;legend 点击显隐。
   W6.4(§3.9):抽稀留痕(state.trimmed → 右上弱提示);hover tooltip 槽
   (mousemove → 最近 x 档位,贴点不贴鼠)。 */
export function mountChart(host, { series = [], type = "line", label = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(CHART_DEF, {
    path,
    state: { series: _prep(series), type, view: "chart", hidden: [], trimmed: _trimNote(series) },
    onRegister,
    onUnregister,
  });
  const render = () => {
    host.innerHTML = renderChart(widget.state, { label, surface });
  };

  widget.set_series = (series) => {
    widget.state.series = _prep(series);
    widget.state.trimmed = _trimNote(series);
    render();
    widget.emit("change", { series: widget.state.series });
  };
  widget.toggle_view = () => {
    widget.state.view = widget.state.view === "chart" ? "table" : "chart";
    render();
  };
  widget.toggle_series = (name) => {
    const cur = new Set(widget.state.hidden ?? []);
    if (cur.has(name)) cur.delete(name);
    else cur.add(name);
    widget.state.hidden = [...cur];
    render();
  };

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-chart-toggle]")) return widget.toggle_view();
    const s = e.target.closest("[data-chart-series]");
    if (s) return widget.toggle_series(s.dataset.chartSeries);
  });
  // hover:垂直参考线 + tooltip 卡(§3.9;贴点不贴鼠——按最近 x 档位锚定)
  host.addEventListener("mousemove", (e) => {
    const tip = host.querySelector(".wd-chart-tip");
    const svg = host.querySelector("svg.wd-chart");
    if (!tip || !svg?.getBoundingClientRect || typeof e.clientX !== "number") return;
    const visible = (widget.state.series ?? []).filter((s) => !(widget.state.hidden ?? []).includes(s.name));
    const n = visible[0]?.points?.length ?? 0;
    if (!n) return;
    const r = svg.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left - 34) / Math.max(1, r.width - 40)));
    const idx = Math.round(frac * (n - 1));
    tip.innerHTML = chartTipHtml(visible, idx);
    tip.style.left = `${Math.min(90, Math.max(4, frac * 100))}%`;
    tip.hidden = false;
  });
  host.addEventListener("mouseleave", () => {
    const tip = host.querySelector(".wd-chart-tip");
    if (tip) tip.hidden = true;
  });
  }

  render();
  widget.register(label);
  return widget;
}

/* 入列预处理 + 抽稀留痕(§3.9:右上弱提示「已抽稀 12,000 → 500」) */
function _prep(series) {
  return (series ?? []).map((s) => ({ name: s.name ?? "", points: downsample(s.points) }));
}

/* 抽稀留痕(§3.9:右上弱提示「已抽稀 12,000 → 500」) */
function _trimNote(series) {
  const orig = Math.max(0, ...(series ?? []).map((s) => (s.points ?? []).length));
  return orig > 500 ? String(orig).replace(/\B(?=(\d{3})+(?!\d))/g, ",") : "";
}

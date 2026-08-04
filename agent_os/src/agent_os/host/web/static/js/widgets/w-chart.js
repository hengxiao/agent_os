/* W-chart 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.9;W5.3 新形态:自渲染)。

   state{series: [{name, points: [{x, y}]}], type: "line"|"bar"|"spark",
   view: "chart"|"table", hidden: [names]};
   actions 全 local:set_series/toggle_view/toggle_series(图例显隐);
   细节:空态("还没有数据");>500 点抽稀;hover 读值(title);
   **a11y 硬规则:等价数据表备选**——同一 series 可切"表格视图"。
   铁律:本文件不拼 HTML(渲染全在 w-chart.render.js);零依赖零 fetch;
   监听委托在 host。 */

import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";
import { downsample, renderChart } from "./w-chart.render.js";

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
  events: ["change"],
  aria: { role: "img", keys: [] },
  surfaces: ["card", "tab"],
  render: renderChart, // W5.3:render 面进 def(registry 校验形态)
});

/* 挂进宿主:series + type + label(aria 摘要);toggle 切"图表/表格"两视图 */
export function mountChart(host, { series = [], type = "line", label = "", path = "", onRegister = null, onUnregister = null } = {}) {
  const widget = createWidget(CHART_DEF, {
    path,
    state: { series: series.map((s) => ({ name: s.name ?? "", points: downsample(s.points) })), type, view: "chart", hidden: [] },
    onRegister,
    onUnregister,
  });
  const render = () => {
    host.innerHTML = renderChart(widget.state, { label });
  };

  widget.set_series = (series) => {
    widget.state.series = series.map((s) => ({ name: s.name ?? "", points: downsample(s.points) }));
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

  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-chart-toggle]")) return widget.toggle_view();
    const s = e.target.closest("[data-chart-series]");
    if (s) return widget.toggle_series(s.dataset.chartSeries);
  });

  render();
  widget.register(label);
  return widget;
}

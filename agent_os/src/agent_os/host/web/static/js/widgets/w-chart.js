/* W-chart — 图表(docs/WIDGETS.md §2;纯 SVG 直绘,零依赖,不引图表库)。

   state{series: [{name, points: [{x, y}]}], type: "line"|"bar"|"spark",
   extent: {xmin,xmax,ymin,ymax} | "auto"};
   细节:网格/轴刻度自动;空态("还没有数据");spark 变体(无轴行内迷你图);
   >500 点抽稀;hover 读值(title 属性 + 读值行);
   **a11y 硬规则:等价数据表备选**——同一 series 可切"表格视图";
   图本体 role=img + aria-label 摘要;颜色只消费契约 token(var(--live) 系,
   不内嵌调色板)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const CHART_DEF = registerWidgetDef({
  kind: "chart",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { series: [], type: "line", view: "chart" },
  actions: [
    { id: "set_series", exec: "local" },
    { id: "toggle_view", exec: "local" },
    { id: "toggle_series", exec: "local", args_input: { name: { type: "string" } } },
  ],
  events: ["change"],
  aria: { role: "img", keys: [] },
  surfaces: ["card", "tab"],
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* >500 点抽稀(等距抽样保首尾) */
export function downsample(points, max = 500) {
  if (!Array.isArray(points) || points.length <= max) return points ?? [];
  const stride = (points.length - 1) / (max - 1);
  const out = [];
  for (let i = 0; i < max - 1; i++) out.push(points[Math.round(i * stride)]);
  out.push(points[points.length - 1]);
  return out;
}

const _extent = (series) => {
  const xs = [];
  const ys = [];
  for (const s of series) for (const p of s.points ?? []) {
    xs.push(Number(p.x));
    ys.push(Number(p.y));
  }
  if (!xs.length) return null;
  return { xmin: Math.min(...xs), xmax: Math.max(...xs), ymin: Math.min(0, ...ys), ymax: Math.max(...ys) };
};

/* 轴刻度(1/2/5 × 10^n 档) */
export function niceTicks(min, max, count = 4) {
  if (min === max) return [min];
  const step0 = (max - min) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) {
    ticks.push(Math.abs(v) < step / 1e6 ? 0 : Number(v.toPrecision(10)));
  }
  return ticks;
}

/* 等价数据表(a11y 硬规则:同一份 series 的表格视图) */
export function chartTableHtml(series) {
  const rows = [];
  for (const s of series ?? []) {
    for (const p of s.points ?? []) {
      rows.push(`<tr><td>${esc(s.name ?? "")}</td><td>${esc(String(p.x))}</td><td>${esc(String(p.y))}</td></tr>`);
    }
  }
  return (
    `<table class="wd-chart-table"><thead><tr><th>series</th><th>x</th><th>y</th></tr></thead>` +
    `<tbody>${rows.join("")}</tbody></table>`
  );
}

/* SVG 直绘(line = polyline + 网格;bar = 柱;spark = 无轴迷你) */
export function chartSvg(series, { type = "line", width = 280, height = 80, label = "" } = {}) {
  const ext = _extent(series);
  if (!ext) {
    return `<div class="wd-empty">${esc(copy("w.chart.empty"))}</div>`;
  }
  if (type === "spark") {
    const w = width;
    const h = height / 2;
    const span = (v, lo, hi, n) => (hi === lo ? 0.5 : (v - lo) / (hi - lo));
    const pts = (series[0]?.points ?? []).map((p, i, arr) => {
      const px = span(i, 0, Math.max(1, arr.length - 1)) * w;
      const py = h - span(Number(p.y), ext.ymin, ext.ymax) * h;
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    });
    return (
      `<svg class="wd-chart-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img"` +
      ` aria-label="${esc(label)}">` +
      `<polyline points="${pts.join(" ")}" fill="none" stroke="var(--live)" stroke-width="1.5"/></svg>`
    );
  }
  const padL = 34;
  const padB = 18;
  const padT = 6;
  const padR = 6;
  const iw = width - padL - padR;
  const ih = height - padT - padB;
  const sx = (x) => padL + ((x - ext.xmin) / (ext.xmax - ext.xmin || 1)) * iw;
  const sy = (y) => padT + ih - ((y - ext.ymin) / (ext.ymax - ext.ymin || 1)) * ih;
  const ticks = niceTicks(ext.ymin, ext.ymax);
  const grid = ticks
    .map(
      (t) =>
        `<line x1="${padL}" y1="${sy(t)}" x2="${width - padR}" y2="${sy(t)}" class="wd-chart-grid"/>` +
        `<text x="2" y="${sy(t) + 3}" class="wd-chart-tick">${esc(String(t))}</text>`
    )
    .join("");
  const xticks = niceTicks(ext.xmin, ext.xmax)
    .map((t) => `<text x="${sx(t)}" y="${height - 4}" class="wd-chart-tick">${esc(String(t))}</text>`)
    .join("");
  const body =
    type === "bar"
      ? (series[0]?.points ?? [])
          .map((p) => {
            const bw = Math.max(2, iw / Math.max(1, (series[0]?.points ?? []).length) - 4);
            const x = sx(Number(p.x)) - bw / 2;
            const y = sy(Math.max(0, Number(p.y)));
            return (
              `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}"` +
              ` height="${(sy(0) - y).toFixed(1)}" class="wd-chart-bar">` +
              `<title>${esc(`${p.x}: ${p.y}`)}</title></rect>`
            );
          })
          .join("")
      : series
          .map((s, i) => {
            const pts = (s.points ?? []).map((p) => `${sx(Number(p.x)).toFixed(1)},${sy(Number(p.y)).toFixed(1)}`);
            const color = i === 0 ? "var(--live)" : "var(--perm-write)";
            const circles = (s.points ?? [])
              .map(
                (p) =>
                  `<circle cx="${sx(Number(p.x)).toFixed(1)}" cy="${sy(Number(p.y)).toFixed(1)}" r="1.6"` +
                  ` fill="${color}"><title>${esc(`${s.name ?? ""} ${p.x}: ${p.y}`)}</title></circle>`
              )
              .join("");
            return (
              `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.5"/>` + circles
            );
          })
          .join("");
  return (
    `<svg class="wd-chart" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"` +
    ` role="img" aria-label="${esc(label)}">` +
    `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${height - padB}" class="wd-chart-axis"/>` +
    `<line x1="${padL}" y1="${height - padB}" x2="${width - padR}" y2="${height - padB}" class="wd-chart-axis"/>` +
    grid + xticks + body +
    `</svg>`
  );
}

/* 挂进宿主:series + type + label(aria 摘要);toggle 切"图表/表格"两视图 */
export function mountChart(host, { series = [], type = "line", label = "", path = "", onRegister = null, onUnregister = null } = {}) {
  const widget = createWidget(CHART_DEF, {
    path,
    state: { series: series.map((s) => ({ name: s.name ?? "", points: downsample(s.points) })), type, view: "chart", hidden: [] },
    onRegister,
    onUnregister,
  });

  const visibleSeries = () =>
    widget.state.series.filter((s) => !(widget.state.hidden ?? []).includes(s.name));

  function render() {
    host.innerHTML =
      `<div class="wd-chart-wrap">` +
      `<div class="wd-chart-bar">` +
      `<button class="wd-mini" data-chart-toggle>${esc(
        copy(widget.state.view === "chart" ? "w.chart.tableview" : "w.chart.chartview")
      )}</button>` +
      (widget.state.series.length > 1
        ? widget.state.series
            .map(
              (s) =>
                `<button class="wd-mini" data-chart-series="${esc(s.name)}"` +
                `${(widget.state.hidden ?? []).includes(s.name) ? ' data-off="1"' : ""}>${esc(s.name)}</button>`
            )
            .join("")
        : "") +
      `</div>` +
      (widget.state.view === "chart"
        ? chartSvg(visibleSeries(), { type: widget.state.type, label })
        : chartTableHtml(visibleSeries())) +
      `</div>`;
  }

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

/* W-chart 渲染面(docs/WIDGET-ARCH.md §1.1/§2.9;W5.3):
   **纯函数**集合——无副作用、不写 state、不发事件、不调后端;
   纯 SVG 直绘零依赖;颜色只消费契约 token(var(--live)/--perm-write 系)。
   效果(§2.9):网格 + 刻度;line/bar/spark 三型;hover 读值点(title);
   图例显隐钮;"表格视图"切换;空态"还没有数据";>500 点抽稀(逻辑面调用)。 */

import { copy } from "../themes.js";

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
    const span = (v, lo, hi) => (hi === lo ? 0.5 : (v - lo) / (hi - lo));
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

/* state → html(纯);state 面:{series, type, view, hidden:[names]};
   label(aria 摘要)经 opts——挂载期常量,不进 state */
export function renderChart(state, { label = "" } = {}) {
  const hidden = state.hidden ?? [];
  const visible = (state.series ?? []).filter((s) => !hidden.includes(s.name));
  return (
    `<div class="wd-chart-wrap">` +
    `<div class="wd-chart-bar">` +
    `<button class="wd-mini" data-chart-toggle>${esc(
      copy(state.view === "chart" ? "w.chart.tableview" : "w.chart.chartview")
    )}</button>` +
    ((state.series ?? []).length > 1
      ? (state.series ?? [])
          .map(
            (s) =>
              `<button class="wd-mini" data-chart-series="${esc(s.name)}"` +
              `${hidden.includes(s.name) ? ' data-off="1"' : ""}>${esc(s.name)}</button>`
          )
          .join("")
      : "") +
    `</div>` +
    (state.view === "chart"
      ? chartSvg(visible, { type: state.type, label })
      : chartTableHtml(visible)) +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

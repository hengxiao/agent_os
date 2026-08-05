/* W-chart 渲染面(docs/WIDGET-ARCH.md §1.1/§2.9;W5.3 自渲染;W6.4 按
   docs/WIDGET-DESIGN.md §3.9 重做视觉):
   **纯函数**集合——无副作用、不写 state、不发事件、不调后端;
   纯 SVG 直绘零依赖;颜色只消费契约 token(--live/--sig-* 序列)。

   tab(§3.9):头部 = 标题 500 + 图例(色点 + 名,点击显隐,隐藏 40% 透明)
   + 右上 segmented「图表 | 表格」+ 抽稀弱提示;网格 = 水平虚线(≤5 条
   nice ticks,无垂直线无轴);折线 2px 序列色,端点无圆点;首序列面积渐变
   --live 10% → 0;hover 参考线 + tooltip 卡在逻辑面(贴点不贴鼠);
   隐藏全部序列 → 空态(不是空白坐标系)。
   card(§3.9):序列名 · 最新值 + 20px 600 mono 大读数 + 涨跌徽标(▲/▼
   带百分比)+ 120×40 迷你折线(末端亮点)+ meta(近 N 点 · 均值)。 */

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

/* 序列色板(§3.9:--live / --sig-* 系;图例色点与折线同序) */
const PALETTE = ["var(--live)", "var(--sig-compress)", "var(--warn)", "var(--sig-llm)", "var(--sig-tool)", "var(--sig-sidecar)"];

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

/* SVG 直绘(§3.9:水平虚线网格无垂直线;折线 2px 无端点圆点;首序列面积
   渐变;bar = 柱;spark = 无轴迷你)。渐变定义幂等(固定 id,同义重复无害)。 */
export function chartSvg(series, { type = "line", width = 560, height = 240, label = "" } = {}) {
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
  let ticks = niceTicks(ext.ymin, ext.ymax);
  while (ticks.length > 5) ticks = ticks.filter((_, i) => i % 2 === 0); // ≤5 条(§3.9)
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
  let body;
  if (type === "bar") {
    body = (series[0]?.points ?? [])
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
      .join("");
  } else {
    const areaOf = (s) => {
      const pts = s.points ?? [];
      if (pts.length < 2) return "";
      const line = pts.map((p) => `${sx(Number(p.x)).toFixed(1)},${sy(Number(p.y)).toFixed(1)}`);
      const base = sy(ext.ymin);
      return (
        `<path d="M${sx(Number(pts[0].x)).toFixed(1)} ${base} L${line.join(" L")}` +
        ` L${sx(Number(pts.at(-1).x)).toFixed(1)} ${base} Z" fill="url(#wd-ag-fill)"/>`
      );
    };
    body =
      `<defs><linearGradient id="wd-ag-fill" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0" stop-color="var(--live)" stop-opacity="0.1"/>` +
      `<stop offset="1" stop-color="var(--live)" stop-opacity="0"/></linearGradient></defs>` +
      areaOf(series[0] ?? {}) + // 首序列面积渐变(§3.9)
      series
        .map((s, i) => {
          const pts = (s.points ?? []).map((p) => `${sx(Number(p.x)).toFixed(1)},${sy(Number(p.y)).toFixed(1)}`);
          return `<polyline points="${pts.join(" ")}" fill="none" stroke="${PALETTE[i % PALETTE.length]}" stroke-width="2"/>`;
        })
        .join("");
  }
  return (
    `<svg class="wd-chart" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"` +
    ` role="img" aria-label="${esc(label)}">` +
    grid + xticks + body +
    `</svg>`
  );
}

/* state → html(纯);state 面:{series, type, view, hidden, trimmed?};
   label(aria 摘要)经 opts——挂载期常量,不进 state(W5.3 注) */
export function renderChart(state, { label = "", surface = "tab" } = {}) {
  if (surface === "card") return _chartCardHtml(state, { label });
  const hidden = state.hidden ?? [];
  const series = state.series ?? [];
  const visible = series.filter((s) => !hidden.includes(s.name));
  const view = state.view ?? "chart";
  return (
    `<div class="wd-chart-wrap">` +
    `<div class="wd-chart-head">` +
    `<span class="wd-chart-title">${esc(label)}</span>` +
    (series.length > 1
      ? series
          .map(
            (s, i) =>
              `<button class="wd-legend" data-chart-series="${esc(s.name)}"${hidden.includes(s.name) ? ' data-off="1"' : ""}>` +
              `<span class="wd-dot" style="--c:${PALETTE[i % PALETTE.length]}" aria-hidden="true"></span>${esc(s.name)}</button>`
          )
          .join("")
      : "") +
    `<span class="wd-chart-side">` +
    (state.trimmed
      ? `<span class="wd-chart-trim">${esc(copy("w.chart.trimmed").replace("{from}", state.trimmed).replace("{to}", "500"))}</span>`
      : "") +
    `<span class="wd-seg">` +
    `<button class="wd-seg-btn" data-chart-toggle${view === "chart" ? ' data-on="1"' : ""}>${esc(copy("w.chart.chartview"))}</button>` +
    `<button class="wd-seg-btn" data-chart-toggle${view === "table" ? ' data-on="1"' : ""}>${esc(copy("w.chart.tableview"))}</button>` +
    `</span></span></div>` +
    (view === "chart"
      ? (visible.length ? chartSvg(visible, { type: state.type, label }) : `<div class="wd-empty">${esc(copy("w.chart.empty"))}</div>`) + // 隐藏全部 → 空态(§3.9)
        `<div class="wd-chart-tip" hidden></div>` // hover tooltip 槽(逻辑面填,贴点不贴鼠)
      : chartTableHtml(visible)) +
    `</div>`
  );
}

/* card 面(§3.9):最新值大读数 + 涨跌徽标 + 120×40 迷你折线(末端亮点)+
   meta(近 N 点 · 均值);图例压成…取消,序列数不再出徽标(终稿) */
function _chartCardHtml(state, { label = "" } = {}) {
  const hidden = state.hidden ?? [];
  const visible = (state.series ?? []).filter((s) => !hidden.includes(s.name));
  const first = visible[0];
  const pts = first?.points ?? [];
  const last = pts.at(-1);
  const prev = pts.at(-2);
  let trend = "";
  if (last && prev && Number(prev.y) !== 0) {
    const pct = ((Number(last.y) - Number(prev.y)) / Math.abs(Number(prev.y))) * 100;
    const up = pct >= 0;
    trend = `<span class="wd-badge" data-tone="${up ? "ok" : "danger"}">${up ? "▲" : "▼"} ${Math.abs(pct).toFixed(1)}%</span>`;
  }
  const mean = pts.length ? pts.reduce((n, p) => n + Number(p.y), 0) / pts.length : 0;
  const meanTxt = Number.isInteger(mean) ? String(mean) : mean.toFixed(2);
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-dim">${esc(first?.name ?? label)} · ${esc(copy("w.chart.latest"))}</span>` +
    `<span class="wd-card-head">` +
    (last ? `<span class="wd-card-num wd-card-big">${esc(String(last.y))}</span>` + trend : "") +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    (visible.length && _extent(visible)
      ? _chartMiniSvg(visible, { type: state.type, label })
      : `<div class="wd-empty">${esc(copy("w.chart.empty"))}</div>`) +
    (pts.length
      ? `<span class="wd-card-meta"><span class="wd-card-meta-txt">${esc(copy("w.chart.meta").replace("{n}", String(pts.length)).replace("{v}", meanTxt))}</span></span>`
      : "") +
    `</div>`
  );
}

/* card 迷你折线/柱(§3.9):120×40,无轴无网格,末端亮点 */
function _chartMiniSvg(series, { type = "line", label = "" } = {}) {
  const ext = _extent(series);
  if (!ext) return "";
  const w = 120;
  const h = 40;
  const span = (v, lo, hi) => (hi === lo ? 0.5 : (v - lo) / (hi - lo));
  const sx = (i, n) => span(i, 0, Math.max(1, n - 1)) * (w - 6) + 2;
  const sy = (y) => h - 2 - span(Number(y), ext.ymin, ext.ymax) * (h - 6);
  let body;
  if (type === "bar") {
    const pts = series[0]?.points ?? [];
    const slot = (w - 4) / Math.max(1, pts.length);
    const bw = Math.max(2, slot - 2);
    body = pts
      .map((p, i) => {
        const y = sy(Math.max(0, Number(p.y)));
        return (
          `<rect x="${(2 + i * slot + (slot - bw) / 2).toFixed(1)}" y="${y.toFixed(1)}"` +
          ` width="${bw.toFixed(1)}" height="${(sy(0) - y).toFixed(1)}" class="wd-chart-bar"/>`
        );
      })
      .join("");
  } else {
    body = series
      .map((s, i) => {
        const pts = s.points ?? [];
        const color = PALETTE[i % PALETTE.length];
        const line = pts.map((p, j) => `${sx(j, pts.length).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
        const last = pts[pts.length - 1];
        const dot = last
          ? `<circle cx="${sx(pts.length - 1, pts.length).toFixed(1)}" cy="${sy(last.y).toFixed(1)}" r="2" fill="${color}"/>`
          : "";
        return `<polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.5"/>${dot}`;
      })
      .join("");
  }
  return (
    `<svg class="wd-chart-mini" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img"` +
    ` aria-label="${esc(label)}">${body}</svg>`
  );
}

/* hover tooltip 内容(§3.9;逻辑面 mousemove 时填入 .wd-chart-tip 槽,贴点不贴鼠) */
export function chartTipHtml(series, idx) {
  const x = series?.[0]?.points?.[idx]?.x;
  return (
    `<div class="wd-chart-tip-t">${esc(String(x ?? ""))}</div>` +
    (series ?? [])
      .map((s, i) => {
        const p = s.points?.[Math.min(idx, (s.points?.length ?? 1) - 1)];
        return p
          ? `<div class="wd-chart-tip-r"><span class="wd-dot" style="--c:${PALETTE[i % PALETTE.length]}"></span>${esc(s.name)} <b class="mono">${esc(String(p.y))}</b></div>`
          : "";
      })
      .join("")
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

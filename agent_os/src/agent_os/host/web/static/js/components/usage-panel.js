/* Usage 视图(docs/WEB-UI.md §4.5):Workbench 底部可折叠栏——按帧表格
   (frame 短码/skill/depth/steps/prompt/completion/cache_read/cache_write/thinking/cost)、
   数字列点击排序、cost 列内联条形、cache 读写分色、合计行固定底部;
   标题行(折叠态也可见)显示 run 合计 steps/cost。

   纯函数(不碰 DOM,node 单测可载):
     aggregateUsage(frames)          帧行数组 → 七字段合计(steps/tokens/cost)
     sortRows(rows, key, dir)        列排序(数字列按数值,其余按字符串;稳定,返回新数组)
     costBarWidth(cost, max)         cost 内联条宽度百分比(0–100)
     usageTableHtml(usage, opts)     表格 HTML(thead 排序钮 / tbody / tfoot 合计行)
   mountUsagePanel 是唯一碰 DOM 的部分:<details> 展开懒加载、排序/重试动作
   经 workbench 事件委托(data-action="us-sort" / "us-retry")转发。 */

import { emptyBlock, esc, fmtCost, shortId, shortSkill } from "../util.js";
import { mountChart } from "../widgets/index.js";

/* ── 列定义(纯数据):key 对齐后端 usage 端点帧行字段(rca.py USAGE_FRAME_KEYS)── */
export const USAGE_COLUMNS = [
  { key: "frame_id", label: "frame", numeric: false },
  { key: "skill", label: "skill", numeric: false },
  { key: "depth", label: "depth", numeric: true },
  { key: "steps", label: "steps", numeric: true },
  { key: "prompt_tokens", label: "prompt", numeric: true },
  { key: "completion_tokens", label: "completion", numeric: true },
  { key: "cache_read_tokens", label: "cache_read", numeric: true, tone: "read" },
  { key: "cache_write_tokens", label: "cache_write", numeric: true, tone: "write" },
  { key: "thinking_tokens", label: "thinking", numeric: true },
  { key: "cost", label: "cost", numeric: true },
];

/* 合计字段(数字列全量;frame_id/skill/depth 不参与合计) */
export const USAGE_SUM_KEYS = USAGE_COLUMNS.filter(
  (c) => c.numeric && c.key !== "depth").map((c) => c.key);

const fmtInt = (n) => (Number(n) || 0).toLocaleString("en-US");

/* ── aggregateUsage:帧行 → 合计(run 级权威值缺失时的聚合/校验源)── */
export function aggregateUsage(frames) {
  const totals = Object.fromEntries(USAGE_SUM_KEYS.map((k) => [k, 0]));
  for (const f of Array.isArray(frames) ? frames : []) {
    for (const k of USAGE_SUM_KEYS) totals[k] += Number(f?.[k]) || 0;
  }
  return totals;
}

/* ── sortRows:稳定排序(V8 sort 稳定),不改动入参 ─────────────── */
export function sortRows(rows, key, dir = "asc") {
  const col = USAGE_COLUMNS.find((c) => c.key === key);
  const arr = [...(Array.isArray(rows) ? rows : [])];
  if (!col) return arr;
  const mul = dir === "desc" ? -1 : 1;
  arr.sort((a, b) => {
    if (col.numeric) return ((Number(a?.[key]) || 0) - (Number(b?.[key]) || 0)) * mul;
    const sa = String(a?.[key] ?? "");
    const sb = String(b?.[key] ?? "");
    return (sa < sb ? -1 : sa > sb ? 1 : 0) * mul;
  });
  return arr;
}

/* ── costBarWidth:内联条宽度%(按最大值比例,§4.5)────────────── */
export function costBarWidth(cost, max) {
  const c = Number(cost) || 0;
  const m = Number(max) || 0;
  if (m <= 0 || c <= 0) return 0;
  return Math.min(100, Math.round((c / m) * 1000) / 10);
}

/* 合计行数据源:run 级权威值优先,缺省字段回退帧聚合(§4.5 合计行) */
function totalsOf(usage) {
  const agg = aggregateUsage(usage?.frames);
  const run = usage?.run ?? {};
  for (const k of USAGE_SUM_KEYS) {
    if (Number.isFinite(Number(run[k]))) agg[k] = Number(run[k]);
  }
  return agg;
}

/* 标题行合计摘要(折叠态也可见,§4.5) */
export const totalsLine = (usage) => {
  const t = totalsOf(usage ?? {});
  return `${fmtInt(t.steps)} steps · ${fmtCost(t.cost)}`;
};

/* ── 渲染 ─────────────────────────────────────────────────── */

function cellHtml(col, row, maxCost) {
  const v = row?.[col.key];
  if (col.key === "frame_id") {
    return `<td class="mono" title="${esc(v ?? "")}">f-${esc(shortId(v))}</td>`;
  }
  if (col.key === "skill") return `<td>${esc(shortSkill(v))}</td>`;
  if (col.key === "cost") {
    const w = costBarWidth(v, maxCost);
    return (
      `<td class="num us-cost">` +
      `<span class="us-bar" aria-hidden="true"><span style="width:${w}%"></span></span>` +
      `<span class="mono">${esc(fmtCost(v))}</span></td>`
    );
  }
  const tone = col.tone ? ` us-cache-${col.tone}` : "";
  return `<td class="num${tone}">${esc(fmtInt(v))}</td>`;
}

/* 表格 HTML:sortKey/sortDir 驱动列头箭头与行序;tfoot 合计行固定底部。 */
export function usageTableHtml(usage, { sortKey = null, sortDir = "asc" } = {}) {
  const frames = Array.isArray(usage?.frames) ? usage.frames : [];
  if (!frames.length) return emptyBlock("无 usage 数据", "checkpoint.json 缺失或未记录帧用量", "chart");
  const rows = sortKey ? sortRows(frames, sortKey, sortDir) : frames;
  const maxCost = Math.max(0, ...frames.map((f) => Number(f?.cost) || 0));
  const head = USAGE_COLUMNS.map((c) => {
    const active = sortKey === c.key;
    const arrow = active ? (sortDir === "desc" ? " ▼" : " ▲") : "";
    return (
      `<th class="${c.numeric ? "num" : ""}${active ? " is-sorted" : ""}" scope="col">` +
      `<button class="us-sort" data-action="us-sort" data-key="${esc(c.key)}"` +
      ` title="按 ${esc(c.label)} 排序">${esc(c.label)}${arrow}</button></th>`
    );
  }).join("");
  const body = rows
    .map(
      (r) =>
        `<tr data-frame-id="${esc(r?.frame_id ?? "")}">` +
        USAGE_COLUMNS.map((c) => cellHtml(c, r, maxCost)).join("") +
        `</tr>`)
    .join("");
  const totals = totalsOf(usage);
  const foot =
    `<tr class="us-totals-row">` +
    `<td colspan="3">合计(${frames.length} 帧)</td>` +
    USAGE_COLUMNS.filter((c) => c.numeric && c.key !== "depth")
      .map((c) =>
        c.key === "cost"
          ? `<td class="num us-cost"><span class="mono">${esc(fmtCost(totals.cost))}</span></td>`
          : `<td class="num${c.tone ? ` us-cache-${c.tone}` : ""}">${esc(fmtInt(totals[c.key]))}</td>`)
      .join("") +
    `</tr>`;
  return (
    `<table class="us-table">` +
    `<thead><tr>${head}</tr></thead>` +
    `<tbody>${body}</tbody>` +
    `<tfoot>${foot}</tfoot>` +
    `</table>`
  );
}

/* ── DOM 挂载(唯一碰 DOM 的部分)──────────────────────────────
   mountUsagePanel(details, { load }) → { sortBy, reload, destroy }。
   <details> 首次展开时懒加载(load 由 workbench 注入,返回 usage JSON);
   排序/重试经 workbench 事件委托转发到 sortBy/reload。 */
export function mountUsagePanel(details, { load } = {}) {
  const state = { status: "idle", usage: null, sortKey: null, sortDir: "asc", destroyed: false };
  details.innerHTML =
    `<summary>Usage 按帧 <span class="us-summary-totals"></span></summary>` +
    `<div class="wb-usage-body us-body"></div>`;
  const body = details.querySelector(".us-body");
  const summaryTotals = details.querySelector(".us-summary-totals");

  const skeleton =
    `<div class="skeleton-stack skeleton-pad">` +
    `<span class="skeleton skeleton-line w-70"></span>` +
    `<span class="skeleton skeleton-line w-40"></span>` +
    `</div>`;

  function render() {
    if (state.destroyed || !body) return;
    if (state.status === "loading" || state.status === "idle") {
      body.innerHTML = skeleton;
      return;
    }
    if (state.status === "error") {
      body.innerHTML =
        `<div class="panel-error">` +
        `<span class="error-msg">加载 usage 失败</span>` +
        `<button class="btn" data-action="us-retry">重试</button>` +
        `</div>`;
      return;
    }
    body.innerHTML =
      `<div class="us-chart" data-us-chart="1"></div>` +
      usageTableHtml(state.usage, {
        sortKey: state.sortKey,
        sortDir: state.sortDir,
      });
    // W4(W-chart 并列装配):帧 cost 折线图;等价数据表 = 下方 usage 表
    // (本面板原生,硬规则天然满足)+ 控件自带表格切换;面板语义不变(并列,不替换)
    const chartHost = body.querySelector("[data-us-chart]");
    if (chartHost) {
      const frames = state.usage?.frames ?? [];
      mountChart(chartHost, {
        series: [{ name: "cost", points: frames.map((f, i) => ({ x: i + 1, y: Number(f?.cost) || 0 })) }],
        type: "line",
        label: `usage cost per frame(${frames.length} 帧)`,
      });
    }
    if (summaryTotals) summaryTotals.textContent = `· ${totalsLine(state.usage)}`;
  }

  async function loadNow() {
    if (state.destroyed || state.status === "loading") return;
    state.status = "loading";
    render();
    try {
      state.usage = await load?.();
      state.status = "ready";
    } catch {
      state.status = "error";
    }
    render();
  }

  const onToggle = () => {
    if (details.open && state.status === "idle") loadNow(); // 展开懒加载(§4.5 默认收起)
  };
  details.addEventListener("toggle", onToggle);
  render();

  return {
    /* 列点击排序(§4.5 数字列):新列 → 数字列降序/文本列升序;同列 → 切换方向 */
    sortBy(key) {
      const col = USAGE_COLUMNS.find((c) => c.key === key);
      if (!col || state.status !== "ready") return;
      if (state.sortKey === key) {
        state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = key;
        state.sortDir = col.numeric ? "desc" : "asc";
      }
      render();
    },
    reload() {
      if (state.status !== "loading") loadNow();
    },
    destroy() {
      state.destroyed = true;
      details.removeEventListener("toggle", onToggle);
    },
  };
}

/* 信号时间线(WEB-UI.md §4.2 中栏):按 step 分组的信号流 + IconBadge + 选中联动。

   纯函数(不碰 DOM,node 单测可载):
     signalKind(name)                     信号名 → 类型键(llm/tool/sidecar/compress/budget/frame)
     isAnomalousSignal(sig)               异常信号判定(veto / ok:false / run.aborted / budget.exceeded)
     groupSignals(signals, opts)          §4.2 规则 2:信号流 → step 分组(每组 = 一次 loop 迭代)
     deriveTimelineView(signals, selection, opts)  §4.2 规则 1:过滤/聚焦/展开态的派生视图
     summarizeSignal(sig)                 关键 payload 摘要(tool 名/模型/ok…)
     windowRange(total, scrollTop, rowH, viewportH, buffer)  §6.3 窗口化可见行区间
     flattenTimelineRows(groups)          派生视图 → 平铺渲染行(组头 + 展开行,带估算高)
     findRowPosition(rows, signalIndex)   信号下标 → 平铺行位置(窗口化滚动定位用)
   renderTimeline 返回 HTML 字符串(opts.window 传窗口时只渲染切片 + 上下占位行);
   滚动/事件由 workbench 承担。

   分组规则(§4.2 规则 2):每组 = 一次 loop 迭代——pre:step 开新组(组头取 step 号
   与帧 skill,pre:step 本身不占行),帧边界(frame_id 变化)强制收尾当前组;
   异常组(veto / ok:false / run.aborted / 失败帧)打 anomaly 标记:左侧红条 +
   自动展开(即使用户折叠过)。veto 在信号流中无专用事件(§5.2:被否决的调用不发
   post:tool.call),由"pre:tool.call 之后同帧下一个 tool.call 信号不是 post"推断。 */

import { absTs, esc, fmtCost, shortSkill } from "../util.js";

/* ── IconBadge(§3.3):信号类型字母章,色随 §3.1 信号语义色 ────────── */

const KIND_LETTERS = {
  llm: "L",
  tool: "T",
  sidecar: "S",
  compress: "C",
  budget: "B",
  frame: "F",
};

export function signalKind(name) {
  const n = String(name ?? "");
  if (n.includes("llm")) return "llm";
  if (n.includes("tool") || n.includes("logic")) return "tool";
  if (n.includes("budget")) return "budget";
  if (n.includes("compress")) return "compress";
  if (n.includes("veto") || n.includes("sidecar")) return "sidecar";
  return "frame"; // frame.* / step / skill.invoke / run.*
}

export const iconBadge = (kind) =>
  `<span class="icon-badge" data-kind="${esc(kind)}" aria-hidden="true">${KIND_LETTERS[kind] ?? "?"}</span>`;

/* ── 异常判定 ─────────────────────────────────────────────────── */

export function isAnomalousSignal(sig) {
  if (!sig || typeof sig !== "object") return false;
  const p = sig.payload ?? {};
  return (
    sig.name === "run.aborted" ||
    sig.name === "budget.exceeded" ||
    /veto/i.test(String(sig.name ?? "")) ||
    p.ok === false ||
    p.status === "failed"
  );
}

/* veto 推断:帧内 tool.call 严格顺序(单帧 loop 串行分发);pre 之后同帧下一个
   tool.call 类信号若不是 post,说明该调用被否决/中断(§5.2:不发 post:tool.call)。
   返回被否决信号在 signals 中的下标集合。 */
export function findVetoedCalls(signals) {
  const vetoed = new Set();
  const isCall = (s) => s && (s.name === "pre:tool.call" || s.name === "post:tool.call");
  (signals ?? []).forEach((sig, i) => {
    if (!sig || sig.name !== "pre:tool.call") return;
    for (let j = i + 1; j <= signals.length; j += 1) {
      const nxt = signals[j];
      if (j === signals.length || (isCall(nxt) && (nxt.frame_id ?? null) === (sig.frame_id ?? null))) {
        if (!nxt || nxt.name !== "post:tool.call") vetoed.add(i);
        break;
      }
    }
  });
  return vetoed;
}

/* ── §4.2 规则 2:groupSignals ───────────────────────────────────
   signals: trace 信号行(含全局下标语义,保序);
   opts.failedFrameIds: 失败帧 id 集(来自 detail.frames,失败帧所在组整体标异常)。
   返回 [{ key, frameId, step, skill, startIndex, items: [{ signal, index, vetoed }], anomaly }]。 */
export function groupSignals(signals, opts = {}) {
  const failed = new Set(opts.failedFrameIds ?? []);
  const rows = Array.isArray(signals) ? signals : [];
  const groups = [];
  let current = null;
  const open = (sig, i, step) => {
    current = {
      key: `g${groups.length}`,
      frameId: sig?.frame_id ?? null,
      step: step ?? null,
      skill: shortSkill(sig?.payload?.skill),
      startIndex: i,
      items: [],
      anomaly: false,
    };
    groups.push(current);
  };
  rows.forEach((sig, i) => {
    if (!sig || typeof sig !== "object") return;
    if (sig.name === "pre:step") {
      open(sig, i, sig.payload?.step ?? null); // 组头边界:pre:step 占组头,不占行
      return;
    }
    if (!current || (sig.frame_id ?? null) !== current.frameId) open(sig, i, null);
    current.items.push({ signal: sig, index: i, vetoed: false });
  });

  const vetoedIdx = findVetoedCalls(rows);
  for (const g of groups) {
    if (g.frameId != null && failed.has(g.frameId)) g.anomaly = true;
    for (const it of g.items) {
      if (vetoedIdx.has(it.index)) it.vetoed = true;
      if (it.vetoed || isAnomalousSignal(it.signal)) g.anomaly = true;
    }
  }
  return groups;
}

/* ── §4.2 规则 1:deriveTimelineView ─────────────────────────────
   selection = { frameId, signalIndex, source } | null:
     source "tree"     → 过滤到该帧(filtered=true);
     source "timeline" → 不过滤,focused 指向该信号(供高亮 + scrollIntoView)。
   opts.collapsed: 用户折叠的组 key 集;异常组无视折叠强制展开(自动展开)。
   返回 { groups(可见,含 expanded), all(全量组,聚焦查组用), focused,
   filtered, filterFrameId, hiddenCount }。 */
export function deriveTimelineView(signals, selection, opts = {}) {
  const groups = groupSignals(signals, opts);
  const collapsed = opts.collapsed ?? new Set();
  // 过滤仅"帧树选帧"(source "tree");timeline/rca 来源保持全量 + 聚焦滚动(§4.2/§4.4)
  const filtering = Boolean(selection?.frameId) && selection?.source === "tree";
  const visible = filtering ? groups.filter((g) => g.frameId === selection.frameId) : groups;
  let focused = null;
  if (selection?.signalIndex != null) {
    const g = groups.find((gr) => gr.items.some((it) => it.index === selection.signalIndex));
    focused = {
      signalIndex: selection.signalIndex,
      groupKey: g?.key ?? null,
      visible: Boolean(g) && (!filtering || g.frameId === selection.frameId),
    };
  }
  return {
    groups: visible.map((g) => ({ ...g, expanded: g.anomaly || !collapsed.has(g.key) })),
    all: groups,
    focused,
    filtered: filtering,
    filterFrameId: filtering ? selection.frameId : null,
    hiddenCount: filtering ? groups.length - visible.length : 0,
  };
}

/* ── 关键 payload 摘要(tool 名 / 模型 / ok;§4.2)────────────────── */

const shortJson = (v, max = 48) => {
  let s;
  try {
    s = JSON.stringify(v);
  } catch {
    s = String(v);
  }
  return s.length > max ? `${s.slice(0, max)}…` : s;
};

export function summarizeSignal(sig) {
  const p = sig?.payload ?? {};
  const name = String(sig?.name ?? "");
  const okMark = p.ok === true ? " ✓" : p.ok === false ? " ✗" : "";
  if (name === "pre:llm.request") return String(p.model ?? "");
  if (name === "post:llm.response") {
    const u = p.usage ?? {};
    const tok = `${u.prompt ?? 0}/${u.completion ?? 0} tok`;
    return `${p.model ?? ""} · ${tok} · ${fmtCost(u.cost)}`;
  }
  if (name === "pre:tool.call") return `${p.tool ?? ""}(${shortJson(p.args ?? {})})`;
  if (name === "post:tool.call") return `${p.tool ?? ""}${okMark}`;
  if (name.endsWith("skill.invoke")) return `${p.skill ?? ""}${okMark}`;
  if (name.endsWith("logic.exec")) return `${p.tool ?? p.language ?? ""}${okMark}`;
  if (name.endsWith("frame.push") || name.endsWith("frame.pop")) {
    return `${shortSkill(p.skill)} · d${p.depth ?? "?"}`;
  }
  if (name === "post:step") return `${(p.calls ?? []).length} calls`;
  if (name === "run.started") return shortSkill(p.skill);
  if (name.startsWith("budget.")) return shortJson(p);
  return "";
}

/* ── 渲染(HTML 字符串;DOM 接线在 workbench)───────────────────── */

/* 信号名去 pre:/post: 前缀显示;前缀以 muted 小字保留(阶段语义) */
function sigNameHtml(name) {
  const n = String(name ?? "");
  const m = n.match(/^(pre:|post:)(.*)$/);
  if (!m) return `<span class="tl-name">${esc(n)}</span>`;
  return `<span class="tl-phase">${esc(m[1])}</span><span class="tl-name">${esc(m[2])}</span>`;
}

function groupLabel(g) {
  if (g.step != null) return `step ${g.step}`;
  if (g.frameId == null) return "run";
  return "帧边界";
}

function rowHtml({ signal, index, vetoed }, selection, t0) {
  const kind = signalKind(signal.name);
  const sel = selection?.signalIndex === index;
  const ts = Number(signal.ts);
  const rel = Number.isFinite(ts) && Number.isFinite(t0) ? `+${Math.max(0, ts - t0).toFixed(2)}s` : "—";
  const summary = summarizeSignal(signal);
  return (
    `<div class="tl-row" data-signal-index="${index}" data-frame-id="${esc(signal.frame_id ?? "")}"` +
    ` role="option" tabindex="0" aria-selected="${sel}"${vetoed ? ` data-vetoed="true"` : ""}` +
    ` title="${esc(signal.name ?? "")} · ${esc(absTs(signal.ts))}">` +
    iconBadge(kind) +
    sigNameHtml(signal.name) +
    (vetoed ? `<span class="tl-veto">vetoed</span>` : "") +
    `<span class="tl-sum">${esc(summary)}</span>` +
    `<span class="tl-time" title="${esc(absTs(signal.ts))}">${esc(rel)}</span>` +
    `</div>`
  );
}

function groupHtml(g, selection, t0) {
  const head =
    `<div class="tl-group-head" data-action="tl-toggle" data-group="${esc(g.key)}"` +
    ` role="button" tabindex="0" aria-expanded="${g.expanded}" title="点击折叠/展开本组">` +
    `<svg class="tl-chev" viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor"` +
    ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    `<path d="M6 3 L11 8 L6 13"/></svg>` +
    `<span class="tl-group-title">${esc(groupLabel(g))}</span>` +
    (g.skill && g.skill !== "—" ? `<span class="tl-group-skill">${esc(g.skill)}</span>` : "") +
    (g.anomaly ? `<span class="tl-warn" title="组内含异常信号">⚠</span>` : "") +
    `<span class="tl-count">${g.items.length}</span>` +
    `</div>`;
  const items = g.expanded
    ? `<div class="tl-items">${g.items.map((it) => rowHtml(it, selection, t0)).join("")}</div>`
    : "";
  return (
    `<div class="tl-group" data-group="${esc(g.key)}"${g.anomaly ? ` data-anomaly="true"` : ""}>` +
    head +
    items +
    `</div>`
  );
}

/* ── 窗口化(§6.3:>500 信号行只渲染视窗 ±buffer,上下占位行)─────── */

export const WINDOW_THRESHOLD = 500; // 渲染行数超过此值才启用窗口化
export const WINDOW_BUFFER = 50; // 视窗上下各多渲染的行数
export const ROW_H = 28; // 信号行估算高(px,与 .tl-row 行高一致)
export const HEAD_H = 32; // 组头估算高(px)

/* 可见窗口纯函数:总行数 + 滚动位置 → [start, end) 行区间(边界 clamp)。 */
export function windowRange(total, scrollTop, rowH, viewportH, buffer = WINDOW_BUFFER) {
  const t = Math.max(0, Math.floor(Number(total) || 0));
  if (!t) return { start: 0, end: 0 };
  const rh = Number(rowH) > 0 ? Number(rowH) : ROW_H;
  const st = Math.max(0, Number(scrollTop) || 0);
  const vh = Math.max(0, Number(viewportH) || 0);
  const buf = Math.max(0, Math.floor(Number(buffer) || 0));
  const start = Math.max(0, Math.min(t - 1, Math.floor(st / rh)) - buf);
  const end = Math.min(t, Math.max(Math.ceil((st + vh) / rh), Math.floor(st / rh) + 1) + buf);
  return { start, end: Math.max(start, end) };
}

/* 派生视图 → 平铺渲染行(组头 + 展开组的信号行,折叠组只出头);
   每行带估算高 h(占位行高度前缀和用)。 */
export function flattenTimelineRows(groups) {
  const rows = [];
  for (const g of groups ?? []) {
    rows.push({ type: "head", group: g, h: HEAD_H });
    if (g.expanded) for (const it of g.items) rows.push({ type: "row", item: it, group: g, h: ROW_H });
  }
  return rows;
}

/* 信号全局下标 → 平铺行位置 { index(行下标), offset(距顶估算 px) };
   行不可见(折叠组内)返回 null。 */
export function findRowPosition(rows, signalIndex) {
  let offset = 0;
  for (let i = 0; i < (rows ?? []).length; i += 1) {
    const r = rows[i];
    if (r.type === "row" && r.item.index === signalIndex) return { index: i, offset };
    offset += r.h;
  }
  return null;
}

/* 窗口切片 → 组序列(复用 groupHtml):首片落在组中时补该组头(保持语境),
   切片内行全部展开渲染。 */
function sliceRowsToGroups(rows, start, end) {
  const out = [];
  let cur = null;
  for (let i = start; i < end && i < rows.length; i += 1) {
    const r = rows[i];
    if (!r) continue;
    if (r.type === "head") {
      cur = { ...r.group, items: [], expanded: true };
      out.push(cur);
    } else {
      if (!cur) {
        cur = { ...r.group, items: [], expanded: true };
        out.push(cur);
      }
      cur.items.push(r.item);
    }
  }
  return out;
}

/* 占位行(§6.3:"还有 N 条"):高度 = 被裁行估算高之和,维持滚动位置;
   被裁段全是折叠组头(count=0)时仍补高度,只省略计数文案。 */
function gapHtml(count, height, side) {
  if (height <= 0) return "";
  return (
    `<div class="tl-gap" data-side="${side}" style="height:${Math.max(0, Math.round(height))}px"` +
    ` aria-hidden="true">` +
    (count > 0 ? `<span class="tl-gap-text">还有 ${count} 条信号</span>` : "") +
    `</div>`
  );
}

/* 时间线整体 HTML:过滤提示条(§4.2 规则 1:"已过滤 f-x,点击清除")+ 分组列表。
   opts.window(窗口化,§6.3)= { start, end, topPad, bottomPad, topCount, bottomCount }:
   只渲染 [start,end) 平铺行,上下以占位行补齐高度;缺省渲染全部。 */
export function renderTimeline(signals, view, { selection = null, frameSkill = null, window: win = null } = {}) {
  const rows = Array.isArray(signals) ? signals : [];
  if (!rows.length) return "";
  const t0 = Number(rows[0]?.ts);
  const filterBar = view.filtered
    ? `<div class="tl-filter" data-action="tl-clear" role="button" tabindex="0"` +
      ` title="点击清除过滤,显示全部信号">` +
      `已过滤 <b>${esc(frameSkill ?? "—")} · f-${esc(String(view.filterFrameId).slice(0, 6))}</b>` +
      `<span class="tl-filter-hint">点击清除 ✕</span>` +
      `</div>`
    : "";
  const groupsHtml = win
    ? gapHtml(win.topCount, win.topPad, "top") +
      sliceRowsToGroups(flattenTimelineRows(view.groups), win.start, win.end)
        .map((g) => groupHtml(g, selection, t0))
        .join("") +
      gapHtml(win.bottomCount, win.bottomPad, "bottom")
    : view.groups.map((g) => groupHtml(g, selection, t0)).join("");
  return (
    filterBar +
    `<div class="tl-list" role="listbox" aria-label="信号时间线">` +
    groupsHtml +
    `</div>`
  );
}

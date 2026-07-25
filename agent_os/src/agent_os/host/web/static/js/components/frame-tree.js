/* 帧树(WEB-UI.md §4.2 左栏 320px):嵌套跨度块(nested span blocks)——
   每帧一个圆角块递归嵌套,左侧 3px 状态 accent 边条;块头单行(chevron + 状态点 +
   skill 名 + kind chip + 右侧 metadata:steps · tok · cost · 时长);兄弟块间 1px 连接线;
   选中态(--bg-2 + accent 加粗 + --live 边框),样式在 app.css(ft-block / ft-kids)。

   纯函数(不碰 DOM,node 单测可载):
     buildFrameTree(frames, pushOrder)  帧摘要平表 → 嵌套树
     defaultCollapsedIds(roots)         初始折叠:depth>3 且有子帧(§4.2/§6.3)
     findPath(roots, frameId)           帧 id → 根到该帧的节点路径(时间线联动展开祖先用)
     flattenVisible(roots, collapsed)   可见行平铺(折叠语义单测用)
     frameDurations(signals)            帧首末信号 ts 差 → Map<frameId, ms>
     spanBlockModel(frame, extras)      帧摘要 + 旁挂数据(kind/tokens/cost/duration)→ 视图模型
   renderFrameTree 返回 HTML 字符串(递归嵌套块);选中/滚动等 DOM 更新由 workbench 承担。 */

import { esc, fmtCost, shortSkill } from "../util.js";
import { normalizeStatus } from "./status-pill.js";

/* 帧摘要平表 → 嵌套树。detail.frames 由后端 frame_tree 按 depth 稳定排序;
   可选 pushOrder(pre:frame.push 信号序 = 真实压栈 DFS 序)修正同 depth 交叠
   场景的兄弟归属;无 pushOrder 时退化为"最近的更浅帧为父"的栈式重建。 */
export function buildFrameTree(frames, pushOrder = null) {
  const list = [...(Array.isArray(frames) ? frames : [])];
  if (Array.isArray(pushOrder) && pushOrder.length) {
    const rank = new Map();
    pushOrder.forEach((id, i) => {
      if (!rank.has(id)) rank.set(id, i);
    });
    list.sort(
      (a, b) =>
        (rank.get(a?.frame_id) ?? Number.MAX_SAFE_INTEGER) -
        (rank.get(b?.frame_id) ?? Number.MAX_SAFE_INTEGER));
  }
  const roots = [];
  const stack = []; // [{ node, depth }],深度递增
  for (const f of list) {
    if (!f || typeof f !== "object") continue;
    const depth = Number(f.depth) || 1;
    const node = { frame: f, depth, children: [] };
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
    if (stack.length) stack[stack.length - 1].node.children.push(node);
    else roots.push(node);
    stack.push({ node, depth });
  }
  return roots;
}

/* 初始折叠集:depth>3 且有子帧的节点(默认全展,深嵌套子树收起,§4.2/§6.3)。 */
export function defaultCollapsedIds(roots) {
  const ids = new Set();
  const walk = (node) => {
    if (node.depth > 3 && node.children.length) ids.add(node.frame.frame_id);
    node.children.forEach(walk);
  };
  (roots ?? []).forEach(walk);
  return ids;
}

/* 帧 id → 从根到该节点的路径(含自身);未找到返回 null。 */
export function findPath(roots, frameId) {
  const dfs = (node, trail) => {
    if (node.frame.frame_id === frameId) return [...trail, node];
    for (const c of node.children) {
      const hit = dfs(c, [...trail, node]);
      if (hit) return hit;
    }
    return null;
  };
  for (const r of roots ?? []) {
    const hit = dfs(r, []);
    if (hit) return hit;
  }
  return null;
}

/* 可见行平铺(折叠子树不输出);每行 { node, hasChildren, collapsed }。 */
export function flattenVisible(roots, collapsed) {
  const rows = [];
  const walk = (node) => {
    const isCollapsed = collapsed.has(node.frame.frame_id);
    rows.push({ node, hasChildren: node.children.length > 0, collapsed: isCollapsed });
    if (!isCollapsed) node.children.forEach(walk);
  };
  (roots ?? []).forEach(walk);
  return rows;
}

/* 帧时长(§4.2 块头 metadata):该帧首末信号 ts 差 → Map<frameId, ms>。
   frame_id 为空 / ts 非数值的信号跳过;单信号帧时长 0。 */
export function frameDurations(signals) {
  const span = new Map(); // fid -> [minTs, maxTs]
  for (const s of Array.isArray(signals) ? signals : []) {
    const fid = s?.frame_id;
    const ts = Number(s?.ts);
    if (!fid || !Number.isFinite(ts)) continue;
    const cur = span.get(fid);
    if (!cur) span.set(fid, [ts, ts]);
    else {
      if (ts < cur[0]) cur[0] = ts;
      if (ts > cur[1]) cur[1] = ts;
    }
  }
  const out = new Map();
  for (const [fid, [lo, hi]] of span) out.set(fid, Math.max(0, (hi - lo) * 1000));
  return out;
}

/* tokens 紧凑格式化:128 → "128",1540 → "1.5k" */
export const fmtTokens = (n) => {
  const v = Number(n) || 0;
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v);
};

/* 帧时长格式化:3.8ms / 20ms / 1.4s / 12s / 1m 5s(非法/非正值 → 空串) */
export function fmtDuration(ms) {
  const v = Number(ms);
  if (!Number.isFinite(v) || v <= 0) return "";
  if (v < 10) return `${Math.round(v * 10) / 10}ms`;
  if (v < 1000) return `${Math.round(v)}ms`;
  const s = v / 1000;
  if (s < 10) return `${Math.round(s * 10) / 10}s`;
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

/* 帧块视图模型:帧摘要 + extras({ kind, steps, tokens, cost, durationMs })→ 渲染字段。
   meta 段按"无值省略"组装(0 steps / 0 tok / $0.00 / 0ms 不出现,§4.2:0 值是噪音),
   每段带 key(app.css 按块宽 container query 逐级丢弃低优先级段:cost → tok → dur → steps);
   steps/cost 缺省回落 frame.usage,kind 非空字符串才出 chip。 */
export function spanBlockModel(frame, extras = null) {
  const f = frame ?? {};
  const ex = extras ?? {};
  const steps = Number(ex.steps ?? f.usage?.steps) || 0;
  const tokens = Number(ex.tokens) || 0;
  const cost = Number(ex.cost ?? f.usage?.cost) || 0;
  const durRaw = Number(ex.durationMs);
  const durationMs = Number.isFinite(durRaw) && durRaw > 0 ? durRaw : null;
  const meta = [];
  if (steps > 0) meta.push({ key: "steps", text: steps === 1 ? "1 step" : `${steps} steps` });
  if (tokens > 0) meta.push({ key: "tok", text: `${fmtTokens(tokens)} tok` });
  if (cost > 0) meta.push({ key: "cost", text: fmtCost(cost) });
  if (durationMs != null) meta.push({ key: "dur", text: fmtDuration(durationMs) });
  return {
    fid: String(f.frame_id ?? ""),
    skill: shortSkill(f.skill),
    fullSkill: String(f.skill ?? ""),
    status: normalizeStatus(f.status),
    kind: typeof ex.kind === "string" && ex.kind ? ex.kind : null,
    steps,
    tokens,
    cost,
    durationMs,
    meta,
    metaText: meta.map((s) => s.text).join(" · "),
  };
}

/* 状态点(StatusPill 的点,§4.2:状态图标用 StatusPill 点;颜色 app.css 按 data-status) */
export const statusDot = (status) =>
  `<span class="status-dot" data-status="${normalizeStatus(status)}"` +
  ` title="${esc(status ?? "unknown")}" aria-hidden="true"></span>`;

/* 块头(单行不折行):chevron(仅有子帧)+ 状态点 + skill 名(600)+ 折叠 +N 计数 +
   kind chip + 右侧 metadata(弱色 mono," · " 连接)+ running 旋转指示(§4.3)。 */
function headHtml(node, model, { collapsed, selection }) {
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsed.has(model.fid);
  const selected = selection?.frameId === node.frame.frame_id;
  const toggle = hasChildren
    ? `<button class="icon-btn ft-toggle" data-action="ft-toggle" data-frame-id="${esc(model.fid)}"` +
      ` aria-label="${isCollapsed ? "展开子帧" : "折叠子帧"}" aria-expanded="${!isCollapsed}">` +
      `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor"` +
      ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
      `<path d="M6 3 L11 8 L6 13"/></svg></button>`
    : `<span class="ft-toggle-spacer" aria-hidden="true"></span>`;
  // metadata 分段渲染:分隔符并入段首(某段被 container query 隐藏时不留 dangling "·");
  // title 兜底完整 meta(窄块隐藏段悬停可见)
  const metaHtml = model.meta
    .map(
      (s, i) =>
        `<span class="ft-seg ft-seg-${s.key}">${esc((i > 0 ? " · " : "") + s.text)}</span>`)
    .join("");
  return (
    `<div class="ft-row" data-frame-id="${esc(model.fid)}" role="treeitem" tabindex="0"` +
    ` aria-selected="${selected}"` +
    (hasChildren ? ` aria-expanded="${!isCollapsed}"` : "") +
    ` title="${esc(model.fullSkill)} · ${esc(model.fid)}">` +
    toggle +
    statusDot(node.frame.status) +
    `<span class="ft-skill">${esc(model.skill)}</span>` +
    (isCollapsed && hasChildren
      ? `<span class="ft-count" title="${node.children.length} 个子帧已折叠">+${node.children.length}</span>`
      : "") +
    (model.kind
      ? `<span class="ft-kind" data-kind="${esc(model.kind)}">${esc(model.kind)}</span>`
      : "") +
    `<span class="ft-meta"${model.metaText ? ` title="${esc(model.metaText)}"` : ""}>` +
    metaHtml +
    `</span>` +
    (model.status === "running"
      ? `<span class="ft-spin" title="运行中" aria-label="运行中"></span>`
      : "") +
    `</div>`
  );
}

/* 跨度块递归:块 = 头部 + 子块容器(折叠时不渲染;连接线在 app.css 经 ::before/::after) */
function blockHtml(node, ctx) {
  const model = spanBlockModel(node.frame, ctx.extraOf(node.frame));
  const isCollapsed = ctx.collapsed.has(model.fid);
  const kids =
    node.children.length > 0 && !isCollapsed
      ? `<div class="ft-kids" role="group">` +
        node.children.map((c) => blockHtml(c, ctx)).join("") +
        `</div>`
      : "";
  return (
    `<div class="ft-block" data-frame-id="${esc(model.fid)}" data-depth="${node.depth}"` +
    ` data-status="${model.status}">` +
    headHtml(node, model, ctx) +
    kids +
    `</div>`
  );
}

/* 树 HTML:根块平铺(块间距 app.css);extras 为 Map<frameId, 旁挂数据> 或 (frame) => 旁挂数据。 */
export function renderFrameTree(roots, { collapsed = new Set(), selection = null, extras = null } = {}) {
  if (!roots?.length) return "";
  const extraOf =
    typeof extras === "function" ? extras : (f) => extras?.get?.(f?.frame_id) ?? null;
  const ctx = { collapsed, selection, extraOf };
  return (
    `<div class="ft-tree" role="tree" aria-label="帧树">` +
    roots.map((r) => blockHtml(r, ctx)).join("") +
    `</div>`
  );
}

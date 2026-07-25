/* 帧树(WEB-UI.md §4.2 左栏 260px):树形渲染(缩进引导线 / 状态点 / 展开折叠)、
   每帧行 skill 名 + chips(steps / cost)、选中态(--bg-2 + 左侧 --live 边条,样式在 app.css)。

   纯函数(不碰 DOM,node 单测可载):
     buildFrameTree(frames, pushOrder)  帧摘要平表 → 嵌套树
     defaultCollapsedIds(roots)         初始折叠:depth>3 且有子帧(§4.2/§6.3)
     findPath(roots, frameId)           帧 id → 根到该帧的节点路径(时间线联动展开祖先用)
     flattenVisible(roots, collapsed)   可见行平铺(渲染用)
   renderFrameTree 返回 HTML 字符串;选中/滚动等 DOM 更新由 workbench 承担。 */

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

/* 状态点(StatusPill 的点,§4.2:状态图标用 StatusPill 点;颜色 app.css 按 data-status) */
export const statusDot = (status) =>
  `<span class="status-dot" data-status="${normalizeStatus(status)}"` +
  ` title="${esc(status ?? "unknown")}" aria-hidden="true"></span>`;

/* 帧行元信息(§4.2):steps/cost 弱色纯文本(非重描边 chip);
   0 值是噪音(0 steps / $0.00),不渲染。 */
function frameChips(frame) {
  const steps = Number(frame?.usage?.steps) || 0;
  const cost = Number(frame?.usage?.cost) || 0;
  let html = "";
  if (steps > 0) html += `<span class="ft-meta">${esc(steps)} steps</span>`;
  if (cost > 0) html += `<span class="ft-meta mono">${esc(fmtCost(cost))}</span>`;
  return html;
}

function rowHtml({ node, hasChildren, collapsed }, selection) {
  const f = node.frame;
  const fid = String(f.frame_id ?? "");
  const selected = selection?.frameId === f.frame_id;
  const toggle = hasChildren
    ? `<button class="icon-btn ft-toggle" data-action="ft-toggle" data-frame-id="${esc(fid)}"` +
      ` aria-label="${collapsed ? "展开子帧" : "折叠子帧"}" aria-expanded="${!collapsed}">` +
      `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor"` +
      ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
      `<path d="M6 3 L11 8 L6 13"/></svg></button>`
    : `<span class="ft-toggle-spacer" aria-hidden="true"></span>`;
  return (
    `<div class="ft-row" data-frame-id="${esc(fid)}" data-depth="${node.depth}"` +
    ` role="treeitem" tabindex="0" aria-selected="${selected}"` +
    (hasChildren ? ` aria-expanded="${!collapsed}"` : "") +
    ` title="${esc(f.skill ?? "")} · ${esc(fid)}">` +
    toggle +
    statusDot(f.status) +
    `<span class="ft-skill">${esc(shortSkill(f.skill))}</span>` +
    // running 帧行内旋转指示(§4.3:不止文本"运行中";reduced-motion 全局关闭)
    (normalizeStatus(f.status) === "running"
      ? `<span class="ft-spin" title="运行中" aria-label="运行中"></span>`
      : "") +
    `<span class="ft-chips">${frameChips(f)}</span>` +
    `</div>`
  );
}

/* 树 HTML:引导线由 .ft-guides(每深度一条,样式 app.css)承担。 */
export function renderFrameTree(roots, { collapsed = new Set(), selection = null } = {}) {
  const rows = flattenVisible(roots, collapsed);
  if (!rows.length) return "";
  return (
    `<div class="ft-tree" role="tree" aria-label="帧树">` +
    rows
      .map((r) => {
        const guides = Array.from({ length: Math.max(0, r.node.depth - 1) }, () =>
          `<span class="ft-guide" aria-hidden="true"></span>`).join("");
        return (
          `<div class="ft-line" data-depth="${r.node.depth}">` +
          guides +
          rowHtml(r, selection) +
          `</div>`
        );
      })
      .join("") +
    `</div>`
  );
}

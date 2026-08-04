/* W-tree 渲染面(docs/WIDGET-ARCH.md §1.1/§2.7;W5.3):
   ``renderTreeWidget(state, opts) -> html`` **纯函数**(opts: leafHtml / surface)——
   无副作用、不写 state、
   不发事件、不调后端;只产出语义 class,视觉全走契约 token。
   效果(§2.7):缩进层级 + 折叠箭头 + 计数徽标(委托 ns-tree.js 纯函数);
   **过滤命中自动展开祖先链**(过滤态 = 过滤树内全部命名空间视为展开);
   当前项浅底(data-current 标记,缺省 leaf 行)。 */

import { copy } from "../themes.js";
import {
  allNamespaces,
  buildNsTree,
  filterNsTree,
  nsTreeHtml,
} from "../components/ns-tree.js";

/* state → html(纯);state 面:{nodes, expanded, selected, filter};
   opts.leafHtml = 自定义叶子行(视图面扩展点;缺省 = 名字行带 data-wt-leaf,
   当前项带 data-current="1" 浅底)。
   双形态(§1.4):surface="card" → 当前路径 + 叶子计数;无过滤框/树体 */
export function renderTreeWidget(state, { leafHtml = null, surface = "tab" } = {}) {
  if (surface === "card") return _treeCardHtml(state);
  const tree = buildNsTree(state.nodes ?? []);
  const filtered = state.filter ? filterNsTree(tree, state.filter) : tree;
  // §2.7:过滤态祖先链自动展开(过滤树内的命名空间全展开)
  const expanded = state.filter ? allNamespaces(filtered) : new Set(state.expanded ?? []);
  const _leafHtml =
    leafHtml ??
    ((leaf) =>
      `<div class="ns-row" style="--ns-depth:1" data-wt-leaf="${esc(leaf.name)}"` +
      `${state.selected === leaf.name ? ' data-current="1"' : ""}>` +
      `<span class="ns-name mono">${esc(leaf.name)}</span></div>`);
  return (
    `<div class="wd-tree">` +
    `<input class="input wd-tree-filter" data-wt-filter placeholder="${esc(copy("w.tree.filter"))}"` +
    ` aria-label="${esc(copy("w.tree.filter"))}" value="${esc(state.filter ?? "")}">` +
    `<div role="tree">` +
    nsTreeHtml(filtered, { expanded, leafHtml: _leafHtml }) +
    `</div></div>`
  );
}

/* card 面(§1.4):当前路径(mono,未选给占位)+ 叶子计数徽标 */
function _treeCardHtml(state) {
  const nodes = state.nodes ?? [];
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-line mono">${esc(state.selected || "—")}</span>` +
    `<span class="wd-card-head">` +
    `<span class="wd-badge">${esc(copy("w.card.leaves").replace("{n}", String(nodes.length)))}</span>` +
    `</span></div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
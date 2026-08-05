/* 命名空间树(docs/NAMING.md §2 点分层级;Skills/Tools 浏览器共用,纯函数不碰 DOM)。

   把平铺的点分名(system.file.read、ops.scan.workspace)建成可折叠树:
   - 单层链折叠(文件管理器惯例):命名空间只有一个子命名空间且自身无叶子时
     合并显示(system + file → "system.file" 一行,而不是两级各一行);
   - 过滤:命中叶子保留 + 祖先链自动展开(过滤态下全部命名空间视为展开);
   - 默认展开:一级命名空间展开、其余折叠;叶子总数 < SMALL_NS_THRESHOLD 的
     命名空间默认展开(小树不藏,大树不糊屏)。

   节点形态:{ seg, full, children: [node], leaf: item|null, count }
   —— leaf 是恰好落在本层的原始条目(count = 本命名空间叶子总数,供计数徽标)。 */

import { copy } from "../themes.js";
import { esc } from "../util.js";

export const SMALL_NS_THRESHOLD = 8;

const _nameOf = (item) => String(typeof item === "string" ? item : item?.name ?? "");

/* 点分名列表 → 树(children 按 seg 字母序;单层链已折叠) */
export function buildNsTree(items) {
  const root = { seg: "", full: "", children: [], leaf: null, count: 0 };
  const byFull = new Map([["", root]]);
  for (const item of items ?? []) {
    const name = _nameOf(item);
    if (!name) continue;
    const segs = name.split(".");
    let node = root;
    for (let i = 0; i < segs.length; i++) {
      const full = segs.slice(0, i + 1).join(".");
      let child = byFull.get(full);
      if (!child) {
        child = { seg: segs[i], full, children: [], leaf: null, count: 0 };
        byFull.set(full, child);
        node.children.push(child);
      }
      node.count += 1;
      node = child;
    }
    node.leaf = item; // 恰好落在本层的条目(名字到顶)
    node.count += 1; // 循环只增量祖先,末端条目自身的计数在这里补(既叶子又命名空间的情形)
  }
  _sort(root);
  return _collapse(root);
}

const _sort = (node) => {
  node.children.sort((a, b) => a.seg.localeCompare(b.seg));
  node.children.forEach(_sort);
};

/* 单层链折叠:无叶子且只有一个子命名空间 → 与子节点合并(seg 用 . 连接) */
const _collapse = (node) => {
  node.children = node.children.map(_collapse);
  while (
    node.full &&
    node.leaf === null &&
    node.children.length === 1 &&
    node.children[0].children.length > 0 // 唯一子项是命名空间(非叶子条目)
  ) {
    const child = node.children[0];
    node.seg = `${node.seg}.${child.seg}`;
    node.full = child.full;
    node.children = child.children;
    node.leaf = child.leaf;
    node.count = child.count;
  }
  return node;
};

/* 过滤:命中叶子(全名含 query,大小写不敏感)保留,祖先链保留;树被剪枝(新对象)。 */
export function filterNsTree(tree, query) {
  const q = String(query ?? "").trim().toLowerCase();
  if (!q) return tree;
  const keep = (node) => {
    const children = node.children.map(keep).filter(Boolean);
    const leafHit = node.leaf && _nameOf(node.leaf).toLowerCase().includes(q);
    if (!children.length && !leafHit) return null;
    return { ...node, children, leaf: leafHit ? node.leaf : null, count: _recount(children, leafHit) };
  };
  const pruned = keep(tree);
  return pruned ?? { ...tree, children: [], leaf: null, count: 0 };
}

const _recount = (children, leafHit) =>
  children.reduce((n, c) => n + c.count, leafHit ? 1 : 0);

/* 默认展开集合(无过滤时的初始折叠态):一级命名空间 + 叶子总数 < 阈值的命名空间。 */
export function defaultExpanded(tree, threshold = SMALL_NS_THRESHOLD) {
  const expanded = new Set();
  const walk = (node, depth) => {
    for (const child of node.children) {
      if (child.count > 0 && (depth === 0 || child.count < threshold)) {
        expanded.add(child.full);
      }
      walk(child, depth + 1);
    }
  };
  walk(tree, 0);
  return expanded;
}

/* 树内全部命名空间路径(过滤态的"祖先链自动展开"用) */
export function allNamespaces(tree) {
  const expanded = new Set();
  const walk = (node) => {
    for (const child of node.children) {
      expanded.add(child.full);
      walk(child);
    }
  };
  walk(tree);
  return expanded;
}

/* 深度优先的叶子条目序列("选中首项"等场景替代原平铺列表) */
export function flattenLeaves(tree) {
  const out = [];
  const walk = (node) => {
    if (node.leaf) out.push(node.leaf);
    node.children.forEach(walk);
  };
  walk(tree);
  return out;
}

/* 树渲染(两视图共用;leafHtml 由视图决定叶子行长相)。
   expanded: Set(命名空间 full 路径);onToggle 由事件层按 data-ns-toggle 委托。 */
/* 树渲染(两视图共用;leafHtml 由视图决定叶子行长相)。
   expanded: Set(命名空间 full 路径);onToggle 由事件层按 data-ns-toggle 委托。
   nsExtra(可选):命名空间行的视图侧扩展点(Skills 页的包徽标,P4)。
   nsAttrs(可选;W6.3):命名空间行的**属性**扩展点(如 data-chain/data-focus),
   返回字符串拼进 .ns-row 开标签;缺省 null = 原样(共享组件零行为变化)。 */
export function nsTreeHtml(tree, { expanded, leafHtml, nsExtra = null, nsAttrs = null }) {
  const render = (node, depth) => {
    const parts = [];
    if (node.leaf) parts.push(leafHtml(node.leaf, depth));
    for (const child of node.children) {
      // 叶子条目(无子节点的条目)渲染为叶子行;只有真命名空间才出折叠行
      if (child.leaf && child.children.length === 0) {
        parts.push(leafHtml(child.leaf, depth));
        continue;
      }
      const open = expanded.has(child.full);
      parts.push(
        `<div class="ns-row" style="--ns-depth:${depth}"${nsAttrs ? nsAttrs(child) : ""}>` +
        `<button class="ns-toggle" data-ns-toggle="${esc(child.full)}"` +
        ` aria-expanded="${open}" aria-label="${esc(copy("ns.toggle"))} ${esc(child.full)}">` +
        `<span aria-hidden="true">${open ? "▾" : "▸"}</span></button>` +
        `<span class="ns-name mono" title="${esc(child.full)}">${esc(child.seg)}</span>` +
        (nsExtra ? nsExtra(child) : "") +
        `<span class="ns-count">${child.count}</span>` +
        `</div>` +
        (open ? render(child, depth + 1) : ""),
      );
    }
    return parts.join("");
  };
  return render(tree, 0);
}

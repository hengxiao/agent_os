/* W-tree 渲染面(docs/WIDGET-ARCH.md §1.1/§2.7;W5.3 自渲染;W6.3 按
   docs/WIDGET-DESIGN.md §3.7 重做视觉):
   ``renderTreeWidget(state, opts) -> html`` **纯函数**(opts: leafHtml / surface)——
   无副作用、不写 state、不发事件、不调后端;只产出语义 class,视觉全走契约 token。

   tab(§3.7):目录行 500 + 右侧叶子数胶囊(ns-count);叶子 mono;hover --bg-2;
   当前项 --live 2px 左条 + 8% 浅底,**父链名称转正文色**(data-chain);
   **过滤 = 命中叶子 --live 文字高亮(wd-hit)+ 祖先链自动展开 + 非命中
   50% 透明(wd-dim)——调光不剪枝**(§3.7 效果图;剪枝语义留在共享
   filterNsTree,本控件不再用它做渲染裁剪);键盘焦点行 data-focus(与 hover
   同视觉)。深层横向滚动不截断缩进(CSS 面)。
   card(§3.7):「当前位置」+ 面包屑路径(a / b / c,未段转正色)+
   「N 叶子」「M 命名空间」徽标 + 打开 →;整卡 = open 入口。 */

import { copy } from "../themes.js";
import { allNamespaces, buildNsTree, nsTreeHtml } from "../components/ns-tree.js";

/* 当前项的祖先链命名空间集合(父链名称转正色用;纯) */
function _chainOf(selected) {
  const chain = new Set();
  const segs = String(selected ?? "").split(".");
  for (let i = 1; i < segs.length; i++) chain.add(segs.slice(0, i).join("."));
  return chain;
}

/* 命中子串高亮(与 W-list 同律:--live 文字色;先转义再包 mark) */
function _hitHtml(text, filter) {
  const raw = String(text ?? "");
  const q = String(filter ?? "").trim();
  if (!q) return esc(raw);
  const idx = raw.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return esc(raw);
  return (
    esc(raw.slice(0, idx)) +
    `<mark class="wd-hit">${esc(raw.slice(idx, idx + q.length))}</mark>` +
    esc(raw.slice(idx + q.length))
  );
}

/* state → html(纯);state 面:{nodes, expanded, selected, filter, focus?, title?} */
export function renderTreeWidget(state, { leafHtml = null, surface = "tab" } = {}) {
  if (surface === "card") return _treeCardHtml(state);
  const tree = buildNsTree(state.nodes ?? []);
  const filter = String(state.filter ?? "").trim();
  // §3.7:过滤态 = 祖先链全展开 + 非命中调光(不剪枝)
  const expanded = filter ? allNamespaces(tree) : new Set(state.expanded ?? []);
  const chain = _chainOf(state.selected);
  const q = filter.toLowerCase();
  const nsHasHit = (node) => {
    if (!q) return true;
    if (node.leaf && String(typeof node.leaf === "string" ? node.leaf : node.leaf?.name ?? "").toLowerCase().includes(q)) return true;
    return node.children.some(nsHasHit);
  };
  const focusKey = state.focusKey ?? "";
  const _leafHtml =
    leafHtml ??
    ((leaf) => {
      const name = String(typeof leaf === "string" ? leaf : leaf?.name ?? "");
      const hit = q && name.toLowerCase().includes(q);
      return (
        `<div class="ns-row${q && !hit ? " wd-dim" : ""}" style="--ns-depth:1" data-wt-leaf="${esc(name)}"` +
        `${state.selected === name ? ' data-current="1"' : ""}${`leaf:${name}` === focusKey ? ' data-focus="1"' : ""}>` +
        `<span class="ns-name mono">${_hitHtml(name, filter)}</span></div>`
      );
    });
  const nsAttrs = (child) =>
    `${chain.has(child.full) ? ' data-chain="1"' : ""}${q && !nsHasHit(child) ? ' data-dim="1"' : ""}${`ns:${child.full}` === focusKey ? ' data-focus="1"' : ""}`;
  return (
    `<div class="wd-tree">` +
    (state.title ? `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title)}</span></div>` : "") +
    `<div class="wd-list-search">` +
    `<span class="wd-list-ico" aria-hidden="true">🔍</span>` +
    `<input class="wd-list-filter" data-wt-filter placeholder="${esc(copy("w.tree.filter"))}"` +
    ` aria-label="${esc(copy("w.tree.filter"))}" value="${esc(state.filter ?? "")}">` +
    `<span class="wd-list-esc" aria-hidden="true">${esc(copy("w.list.esc"))}</span>` +
    `</div>` +
    `<div role="tree">` +
    nsTreeHtml(tree, { expanded, leafHtml: _leafHtml, nsAttrs }) +
    (filter && !nsHasHit(tree)
      ? `<div class="wd-empty-box"><span class="wd-empty-guide">${esc(copy("w.list.nomatch").replace("{q}", filter))}</span></div>`
      : "") +
    `</div></div>`
  );
}

/* card 面(§3.7):面包屑当前路径(段间 / 弱色,末段转正色)+ 计数徽标 */
function _treeCardHtml(state) {
  const nodes = state.nodes ?? [];
  const segs = String(state.selected ?? "").split(".").filter(Boolean);
  const nsCount = allNamespaces(buildNsTree(nodes)).size;
  const crumb = segs.length
    ? segs
        .map((s, i) => {
          const last = i === segs.length - 1;
          return (
            (i ? `<span class="wd-card-dim"> / </span>` : "") +
            `<span class="mono${last ? " wd-card-name" : ""}">${esc(s)}</span>`
          );
        })
        .join("")
    : `<span class="wd-card-line">${esc(copy("w.card.empty"))}</span>`;
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-dim">${esc(copy("w.tree.current"))}</span>` +
    `<span class="wd-card-line">${crumb}</span>` +
    `<span class="wd-card-head">` +
    `<span class="wd-badge">${esc(copy("w.card.leaves").replace("{n}", String(nodes.length)))}</span>` +
    `<span class="wd-badge">${esc(copy("w.tree.ns").replace("{n}", String(nsCount)))}</span>` +
    `</span>` +
    `<span class="wd-card-meta"><span class="wd-card-meta-txt"></span>` +
    `<span class="wd-card-all">${esc(copy("w.card.go"))}</span></span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

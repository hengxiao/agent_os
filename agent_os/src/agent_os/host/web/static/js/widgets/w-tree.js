/* W-tree — 命名空间树(docs/WIDGETS.md §2;ns-tree 控件化)。

   **不重写 components/ns-tree.js**(既有稳定组件,§7"不重写"):本控件是
   协议薄封装——树的构建/过滤/默认展开/渲染全部委托 ns-tree.js 的纯函数
   (buildNsTree/filterNsTree/defaultExpanded/nsTreeHtml),控件层只负责:
   state{nodes, expanded, selected, filter}、toggle/select/filter actions、
   事件上行(与 W 协议对齐)。 */

import { copy } from "../themes.js";
import { buildNsTree, defaultExpanded, filterNsTree, nsTreeHtml } from "../components/ns-tree.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const TREE_EDITOR_DEF = registerWidgetDef({
  kind: "ns-tree",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { nodes: [], expanded: [], selected: null, filter: "" },
  actions: [
    { id: "toggle", exec: "local", args_input: { path: { type: "string" } } },
    { id: "select", exec: "local", args_input: { id: { type: "string" } } },
    { id: "filter", exec: "local", args_input: { text: { type: "string" } } },
  ],
  events: ["select", "change"],
  aria: { role: "tree", keys: ["ArrowLeft", "ArrowRight"] },
  surfaces: ["card", "tab"],
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* 挂进宿主:items(ns-tree 的扁平条目:[{name, ...leaf}]) + leafHtml(叶子行,
   缺省 = 名字行带 data-wt-leaf);expanded 缺省 = defaultExpanded(ns-tree 语义) */
export function mountNsTreeWidget(
  host,
  { items = [], leafHtml = null, expanded = null, path = "", onRegister = null, onUnregister = null } = {}
) {
  const widget = createWidget(TREE_EDITOR_DEF, {
    path,
    state: {
      nodes: items,
      expanded: [...(expanded ?? defaultExpanded(buildNsTree(items)))],
      selected: null,
      filter: "",
    },
    onRegister,
    onUnregister,
  });
  const _leafHtml =
    leafHtml ??
    ((leaf) =>
      `<div class="ns-row" style="--ns-depth:1" data-wt-leaf="${esc(leaf.name)}">` +
      `<span class="ns-name mono">${esc(leaf.name)}</span></div>`);

  const _tree = () => {
    const tree = buildNsTree(widget.state.nodes);
    return widget.state.filter ? filterNsTree(tree, widget.state.filter) : tree;
  };

  function render() {
    host.innerHTML =
      `<div class="wd-tree">` +
      `<input class="input wd-tree-filter" data-wt-filter placeholder="${esc(copy("w.tree.filter"))}"` +
      ` aria-label="${esc(copy("w.tree.filter"))}" value="${esc(widget.state.filter)}">` +
      `<div role="tree">` +
      nsTreeHtml(_tree(), { expanded: new Set(widget.state.expanded), leafHtml: _leafHtml }) +
      `</div></div>`;
  }

  widget.toggle = (nsPath) => {
    const cur = new Set(widget.state.expanded);
    if (cur.has(nsPath)) cur.delete(nsPath);
    else cur.add(nsPath);
    widget.state.expanded = [...cur];
    render();
    widget.emit("change", { expanded: widget.state.expanded });
  };
  widget.select = (id) => {
    widget.state.selected = widget.state.selected === id ? null : id;
    render();
    widget.emit("select", { id, selected: widget.state.selected });
  };
  widget.filter = (text) => {
    widget.state.filter = text ?? "";
    render();
    widget.emit("change", { filter: widget.state.filter });
  };

  host.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-ns-toggle]");
    if (toggle) return widget.toggle(toggle.dataset.nsToggle);
    const leaf = e.target.closest("[data-wt-leaf]");
    if (leaf) return widget.select(leaf.dataset.wtLeaf);
  });
  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-wt-filter]")) widget.filter(e.target.value);
  });

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

/* W-tree 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.7;W5.3 新形态:自渲染;
   W6.3 视觉按 docs/WIDGET-DESIGN.md §3.7)。

   **不重写 components/ns-tree.js**(既有稳定组件,§7"不重写"):树构建/过滤/
   默认展开委托 ns-tree.js 纯函数;渲染在 w-tree.render.js(W6.3:过滤 =
   命中高亮 + 祖先链自动展开 + 非命中 50% 调光,不剪枝;父链 data-chain;
   键盘焦点 data-focus)。本层只负责:state{nodes, expanded, selected, filter,
   focusKey}、toggle/select/filter actions、键盘 ←→ 折叠展开 / ↑↓ 移动 /
   Enter 选中(§3.7 验收)、事件上行(与 W 协议对齐)。
   铁律:本文件不拼 HTML;零 fetch;监听委托在 host。 */

import { allNamespaces, buildNsTree, defaultExpanded } from "../components/ns-tree.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderTreeWidget } from "./w-tree.render.js";

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
  events: ["select", "change", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "tree", keys: ["ArrowLeft", "ArrowRight"] },
  surfaces: ["card", "tab"],
  render: renderTreeWidget, // W5.3:render 进 def(registry 校验形态)
  mount: mountNsTreeWidget,
});

const _nameOf = (item) => String(typeof item === "string" ? item : item?.name ?? "");

/* 可见行序列(与 nsTreeHtml 同 walk 序:本层叶子先于子命名空间;
   折叠的命名空间不下钻)。rows = [{kind:"ns"|"leaf", key}] */
function _visibleRows(tree, expanded) {
  const rows = [];
  const walk = (node) => {
    if (node.leaf) rows.push({ kind: "leaf", key: _nameOf(node.leaf) });
    for (const child of node.children) {
      if (child.leaf && child.children.length === 0) {
        rows.push({ kind: "leaf", key: _nameOf(child.leaf) });
        continue;
      }
      rows.push({ kind: "ns", key: child.full });
      if (expanded.has(child.full)) walk(child);
    }
  };
  walk(tree);
  return rows;
}

/* 挂进宿主:items(ns-tree 的扁平条目:[{name, ...leaf}]) + leafHtml(叶子行,
   缺省 = 名字行带 data-wt-leaf)+ title(面板头,可选);
   expanded 缺省 = defaultExpanded(ns-tree 语义) */
export function mountNsTreeWidget(
  host,
  { items = [], leafHtml = null, expanded = null, title = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}
) {
  const widget = createWidget(TREE_EDITOR_DEF, {
    path,
    state: {
      nodes: items,
      expanded: [...(expanded ?? defaultExpanded(buildNsTree(items)))],
      selected: null,
      filter: "",
      title,
      focusKey: "", // 键盘焦点行("ns:<full>" / "leaf:<name>";§3.7)
    },
    onRegister,
    onUnregister,
  });
  const render = () => {
    host.innerHTML = renderTreeWidget(widget.state, { leafHtml, surface });
  };
  const _rows = () => {
    const tree = buildNsTree(widget.state.nodes);
    // 与渲染面同律:过滤态全部命名空间视为展开(§3.7 调光不剪枝)
    const open = widget.state.filter.trim() ? allNamespaces(tree) : new Set(widget.state.expanded);
    return _visibleRows(tree, open);
  };
  const _focus = (key) => {
    widget.state.focusKey = key;
    render();
  };

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

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-ns-toggle]");
    if (toggle) return widget.toggle(toggle.dataset.nsToggle);
    const leaf = e.target.closest("[data-wt-leaf]");
    if (leaf) return widget.select(leaf.dataset.wtLeaf);
  });
  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-wt-filter]")) widget.filter(e.target.value);
  });
  // 键盘(§3.7):↑↓ 移动焦点行;→ 展开/进子层;← 折叠/回父层;Enter 选中叶子
  host.addEventListener("keydown", (e) => {
    const rows = _rows();
    if (!rows.length) return;
    const cur = rows.findIndex((r) => `${r.kind}:${r.key}` === widget.state.focusKey);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const step = e.key === "ArrowDown" ? 1 : -1;
      const next = cur < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, cur + step));
      return _focus(`${rows[next].kind}:${rows[next].key}`);
    }
    const row = rows[cur];
    if (!row) return;
    if (e.key === "ArrowRight" && row.kind === "ns") {
      if (!widget.state.expanded.includes(row.key)) return widget.toggle(row.key);
      const child = rows[cur + 1]; // 已展开:进第一个子行
      if (child) return _focus(`${child.kind}:${child.key}`);
    }
    if (e.key === "ArrowLeft") {
      if (row.kind === "ns" && widget.state.expanded.includes(row.key)) return widget.toggle(row.key);
      // 叶子/已折叠 → 回父命名空间行
      const parentKey = row.kind === "leaf" ? row.key.split(".").slice(0, -1).join(".") : row.key.split(".").slice(0, -1).join(".");
      const pi = rows.findIndex((r) => r.kind === "ns" && r.key === parentKey);
      if (pi >= 0) return _focus(`ns:${parentKey}`);
    }
    if (e.key === "Enter" && row.kind === "leaf") return widget.select(row.key);
  });
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

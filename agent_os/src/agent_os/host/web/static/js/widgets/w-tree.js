/* W-tree 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.7;W5.3 新形态:自渲染)。

   **不重写 components/ns-tree.js**(既有稳定组件,§7"不重写"):树构建/过滤/
   默认展开委托 ns-tree.js 纯函数;渲染在 w-tree.render.js(过滤命中自动
   展开祖先链、当前项 data-current 浅底)。本层只负责:state{nodes, expanded,
   selected, filter}、toggle/select/filter actions、事件上行(与 W 协议对齐)。
   铁律:本文件不拼 HTML;零 fetch;监听委托在 host。 */

import { buildNsTree, defaultExpanded } from "../components/ns-tree.js";
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
  render: renderTreeWidget, // W5.3:render 面进 def(registry 校验形态)
});

/* 挂进宿主:items(ns-tree 的扁平条目:[{name, ...leaf}]) + leafHtml(叶子行,
   缺省 = 名字行带 data-wt-leaf);expanded 缺省 = defaultExpanded(ns-tree 语义) */
export function mountNsTreeWidget(
  host,
  { items = [], leafHtml = null, expanded = null, path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}
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
  const render = () => {
    host.innerHTML = renderTreeWidget(widget.state, { leafHtml, surface });
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
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

/* W-list 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.6;W5.2 新形态:自渲染)。

   state{items: [{id, label, hint?, icon?}], selected: id|[ids], filter, focus, multi};
   actions 全 local:select/filter/activate(Enter);
   细节:搜索过滤(平列表,子串命中)、单/多选(multi)、键盘导航(↑↓ 移动焦点,
   Enter = activate)、空态;过滤框重渲走选区保留(输入焦点不丢)。
   铁律:本文件不拼 HTML(渲染全在 w-list.render.js);零 fetch;事件上行;
   监听一律委托在 host(重渲会换掉子元素)。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget, preserveSelection } from "./widget.js";
import { renderSelectList, visibleItems } from "./w-list.render.js";

export const LIST_EDITOR_DEF = registerWidgetDef({
  kind: "select-list",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { items: [], selected: null, filter: "", focus: 0, multi: false },
  actions: [
    { id: "select", exec: "local", args_input: { id: { type: "string" } } },
    { id: "filter", exec: "local", args_input: { text: { type: "string" } } },
    { id: "activate", exec: "local" },
  ],
  events: ["select", "activate", "change", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "listbox", keys: ["ArrowUp", "ArrowDown", "Enter"] },
  surfaces: ["card", "tab"],
  render: renderSelectList, // W5.2:render 面进 def(registry 校验形态)
});

/* 挂进宿主:items + multi(多选)+ selected(初始);filter 事件上行,
   select/activate 事件上行(父组件映射到自己的动作,§3) */
export function mountSelectList(
  host,
  { items = [], multi = false, selected = null, path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}
) {
  const widget = createWidget(LIST_EDITOR_DEF, {
    path,
    state: { items: [...items], selected: multi ? (selected ?? []) : selected, filter: "", focus: 0, multi },
    onRegister,
    onUnregister,
  });

  const render = () => {
    host.innerHTML = renderSelectList(widget.state, { surface });
  };
  // 过滤框是文本输入:重渲走选区/焦点保留(§1.3;单 input,selector 定位)
  const renderPreserving = () => preserveSelection(host, render, { selector: "input" });
  const _clampFocus = () => {
    widget.state.focus = Math.min(widget.state.focus, Math.max(0, visibleItems(widget.state).length - 1));
  };
  const _changed = () => widget.emit("change", { selected: widget.state.selected, filter: widget.state.filter });

  widget.select = (id) => {
    if (multi) {
      const cur = Array.isArray(widget.state.selected) ? [...widget.state.selected] : [];
      widget.state.selected = cur.includes(id) ? cur.filter((s) => s !== id) : [...cur, id];
    } else {
      widget.state.selected = widget.state.selected === id ? null : id;
    }
    render();
    widget.emit("select", { id, selected: widget.state.selected });
    _changed();
  };
  widget.filter = (text) => {
    widget.state.filter = text ?? "";
    _clampFocus();
    renderPreserving();
    _changed();
  };
  widget.activate = (id = null) => {
    const target = id ?? visibleItems(widget.state)[widget.state.focus]?.id ?? null;
    if (target !== null) widget.emit("activate", { id: target, selected: widget.state.selected });
  };
  widget.setItems = (items) => {
    widget.state.items = [...items];
    _clampFocus();
    render();
  };

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("click", (e) => {
    const item = e.target.closest("[data-wl-item]");
    if (item) {
      widget.state.focus = Math.max(
        0,
        visibleItems(widget.state).findIndex((it) => it.id === item.dataset.wlItem)
      );
      widget.select(item.dataset.wlItem);
    }
  });
  host.addEventListener("dblclick", (e) => {
    const item = e.target.closest("[data-wl-item]");
    if (item) widget.activate(item.dataset.wlItem);
  });
  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-wl-filter]")) widget.filter(e.target.value);
  });
  host.addEventListener("keydown", (e) => {
    const vis = visibleItems(widget.state);
    if (e.key === "ArrowDown" && vis.length) {
      widget.state.focus = Math.min(widget.state.focus + 1, vis.length - 1);
      renderPreserving();
    } else if (e.key === "ArrowUp" && vis.length) {
      widget.state.focus = Math.max(widget.state.focus - 1, 0);
      renderPreserving();
    } else if (e.key === "Enter") {
      widget.activate(); // Enter = 激活焦点项(焦点行/过滤框内同语义,§2 键盘路径)
    }
  });
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

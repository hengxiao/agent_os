/* W-list — 可选列表(docs/WIDGETS.md §2;草稿列表、技能选择、会话列表)。

   state{items: [{id, label, hint?, icon?}], selected: id|[ids], filter};
   actions 全 local:select/filter/activate(Enter);
   细节:搜索过滤(平列表,子串命中)、单/多选(multi)、键盘导航(↑↓ 移动焦点,
   Enter = activate)、空态。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const LIST_EDITOR_DEF = registerWidgetDef({
  kind: "select-list",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { items: [], selected: null, filter: "" },
  actions: [
    { id: "select", exec: "local", args_input: { id: { type: "string" } } },
    { id: "filter", exec: "local", args_input: { text: { type: "string" } } },
    { id: "activate", exec: "local" },
  ],
  events: ["select", "activate", "change"],
  aria: { role: "listbox", keys: ["ArrowUp", "ArrowDown", "Enter"] },
  surfaces: ["card", "tab"],
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* 挂进宿主:items + multi(多选)+ selected(初始);filter 事件上行,
   select/activate 事件上行(父组件映射到自己的动作,§3) */
export function mountSelectList(
  host,
  { items = [], multi = false, selected = null, path = "", onRegister = null, onUnregister = null } = {}
) {
  const widget = createWidget(LIST_EDITOR_DEF, {
    path,
    state: { items: [...items], selected: multi ? (selected ?? []) : selected, filter: "" },
    onRegister,
    onUnregister,
  });
  let focusIdx = 0;

  const visible = () =>
    widget.state.items.filter((it) =>
      !widget.state.filter || (it.label ?? it.id).toLowerCase().includes(widget.state.filter.toLowerCase())
    );

  const _changed = () => widget.emit("change", { selected: widget.state.selected, filter: widget.state.filter });

  function render() {
    const vis = visible();
    focusIdx = Math.min(focusIdx, Math.max(0, vis.length - 1));
    const isSel = (id) =>
      Array.isArray(widget.state.selected) ? widget.state.selected.includes(id) : widget.state.selected === id;
    host.innerHTML =
      `<div class="wd-list">` +
      `<input class="input wd-list-filter" data-wl-filter placeholder="${esc(copy("w.list.filter"))}"` +
      ` aria-label="${esc(copy("w.list.filter"))}" value="${esc(widget.state.filter)}">` +
      (vis.length
        ? `<div role="listbox" aria-multiselectable="${multi}">` +
          vis
            .map((it, i) => {
              const sel = isSel(it.id);
              return (
                `<div class="wd-list-item" data-wl-item="${esc(it.id)}" role="option" tabindex="0"` +
                ` aria-selected="${sel}"${i === focusIdx ? ' data-focus="1"' : ""}>` +
                (it.icon ? `<span aria-hidden="true">${esc(it.icon)}</span>` : "") +
                `<span class="wd-list-label">${esc(it.label ?? it.id)}</span>` +
                (it.hint ? `<span class="wd-list-hint">${esc(it.hint)}</span>` : "") +
                `</div>`
              );
            })
            .join("") +
          `</div>`
        : `<div class="wd-empty">${esc(copy("w.list.empty"))}</div>`) +
      `</div>`;
  }

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
    render();
    _changed();
  };
  widget.activate = (id = null) => {
    const target = id ?? visible()[focusIdx]?.id ?? null;
    if (target !== null) widget.emit("activate", { id: target, selected: widget.state.selected });
  };
  widget.setItems = (items) => {
    widget.state.items = [...items];
    render();
  };

  host.addEventListener("click", (e) => {
    const item = e.target.closest("[data-wl-item]");
    if (item) {
      focusIdx = visible().findIndex((it) => it.id === item.dataset.wlItem);
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
    const vis = visible();
    if (e.key === "ArrowDown" && vis.length) {
      focusIdx = Math.min(focusIdx + 1, vis.length - 1);
      render();
    } else if (e.key === "ArrowUp" && vis.length) {
      focusIdx = Math.max(focusIdx - 1, 0);
      render();
    } else if (e.key === "Enter") {
      widget.activate(); // Enter = 激活焦点项(焦点行/过滤框内同语义,§2 键盘路径)
    }
  });

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

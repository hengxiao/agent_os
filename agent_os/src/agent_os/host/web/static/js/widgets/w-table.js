/* W-table 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.3;W5.2 新形态:自渲染)。

   state{rows: [{id, cells: {...}}], selected: [ids], schema: {columns: [...]}};
   列定义驱动({key, type: text|number|boolean|enum, label, required?, options?});
   actions 全 local:add_row/remove_row/move_row/set_cell/remove_selected;
   细节:行 DnD 排序走 §15 标准 envelope({source, source_kind:"table-row",
   position:{before}})、新增行骨架、空态、role=grid + Alt+↑/↓ 键盘移行;
   change 事件上行(数据下行,事件上行,§3)。
   铁律:本文件不拼 HTML(渲染全在 w-table.render.js);零 fetch;事件上行;
   监听一律委托在 host(重渲会换掉子元素)。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderTableEditor } from "./w-table.render.js";

const _DND_MIME = "application/x-agent-os-widget";

export const TABLE_EDITOR_DEF = registerWidgetDef({
  kind: "table-editor",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { rows: [], selected: [], schema: { columns: [] } },
  actions: [
    { id: "add_row", exec: "local" },
    { id: "remove_row", exec: "local", args_input: { id: { type: "string" } } },
    { id: "move_row", exec: "local", args_input: { id: { type: "string" }, before: { type: "string" } } },
    { id: "set_cell", exec: "local", args_input: { id: { type: "string" }, key: { type: "string" } } },
    { id: "remove_selected", exec: "local" },
  ],
  events: ["change", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "grid", keys: ["ArrowUp", "ArrowDown"] },
  surfaces: ["card", "tab"],
  render: renderTableEditor, // W5.2:render 面进 def(registry 校验形态)
});

let _rowSeq = 0;
const _newRow = (columns, cells = {}) => ({
  id: `r${++_rowSeq}`,
  cells: Object.fromEntries(columns.map((c) => [c.key, cells[c.key] ?? _skeleton(c)])),
});

/* 新增行骨架(按列型给默认空值) */
function _skeleton(col) {
  if (col.type === "number") return 0;
  if (col.type === "boolean") return false;
  if (col.type === "enum") return (col.options ?? [])[0] ?? "";
  return "";
}

/* 挂进宿主:columns(列定义)+ rows(初始行;下行数据)+ path(§14 前缀)。
   返回 widget;父组件 on("change", ...) 收全部 mutation。
   双形态(§1.4):surface="card" 时渲染摘要卡,宿主委托只挂 open。 */
export function mountTableEditor(host, { columns, rows = [], path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(TABLE_EDITOR_DEF, {
    path,
    state: {
      schema: { columns },
      rows: rows.map((r) => _newRow(columns, r.cells ?? r)),
      selected: [],
    },
    onRegister,
    onUnregister,
  });

  const render = () => {
    host.innerHTML = renderTableEditor(widget.state, { surface });
  };
  const _changed = () => widget.emit("change", { rows: widget.state.rows });

  widget.add_row = (cells = {}) => {
    widget.state.rows = [...widget.state.rows, _newRow(columns, cells)];
    render();
    _changed();
  };
  widget.remove_row = (id) => {
    widget.state.rows = widget.state.rows.filter((r) => r.id !== id);
    widget.state.selected = widget.state.selected.filter((s) => s !== id);
    render();
    _changed();
  };
  widget.move_row = (id, before = "") => {
    const rest = widget.state.rows.filter((r) => r.id !== id);
    const moving = widget.state.rows.find((r) => r.id === id);
    if (!moving) return;
    const idx = before ? rest.findIndex((r) => r.id === before) : rest.length;
    rest.splice(idx < 0 ? rest.length : idx, 0, moving);
    widget.state.rows = rest;
    render();
    _changed();
  };
  widget.set_cell = (id, key, value) => {
    const row = widget.state.rows.find((r) => r.id === id);
    if (row) row.cells[key] = value;
    _changed(); // set_cell 不重渲(输入中,选区/焦点不丢)
  };
  widget.serialize = () => widget.state.rows.map((r) => ({ ...r.cells }));

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-wd-add]")) return widget.add_row();
    const x = e.target.closest("[data-row-x]");
    if (x) return widget.remove_row(x.dataset.rowX);
    const tr = e.target.closest("[data-row]");
    if (tr && !e.target.closest("[data-cell]")) {
      widget.state.selected = [tr.dataset.row];
      render();
    }
  });
  host.addEventListener("input", (e) => {
    const cell = e.target.closest("[data-cell]")?.dataset.cell;
    if (!cell) return;
    const [id, key] = cell.split(":");
    widget.set_cell(id, key, e.target.type === "checkbox" ? e.target.checked : e.target.value);
  });
  host.addEventListener("change", (e) => {
    const cell = e.target.closest("select[data-cell]")?.dataset.cell;
    if (!cell) return;
    const [id, key] = cell.split(":");
    widget.set_cell(id, key, e.target.value);
  });
  // Alt+↑/↓ 键盘移行(§2 a11y)
  host.addEventListener("keydown", (e) => {
    if (!e.altKey) return;
    const tr = e.target.closest("[data-row]");
    if (!tr) return;
    const rs = widget.state.rows;
    const i = rs.findIndex((r) => r.id === tr.dataset.row);
    if (e.key === "ArrowUp" && i > 0) widget.move_row(tr.dataset.row, rs[i - 1].id);
    if (e.key === "ArrowDown" && i >= 0 && i < rs.length - 1) {
      widget.move_row(tr.dataset.row, rs[i + 2]?.id ?? "");
    }
  });
  // 行 DnD(§15):envelope 产出 + accept 校验 + drop 重排
  host.addEventListener("dragstart", (e) => {
    const tr = e.target.closest("[data-row]");
    if (!tr) return;
    e.dataTransfer?.setData(_DND_MIME, JSON.stringify({
      source: `${path}/row/${tr.dataset.row}`, source_kind: "table-row", position: {},
    }));
  });
  host.addEventListener("dragover", (e) => {
    if ([...(e.dataTransfer?.types ?? [])].includes(_DND_MIME)) {
      e.preventDefault();
      host.classList.add("pf-drop-ok");
    }
  });
  host.addEventListener("dragleave", () => host.classList.remove("pf-drop-ok"));
  host.addEventListener("drop", (e) => {
    host.classList.remove("pf-drop-ok");
    let env = null;
    try {
      env = JSON.parse(e.dataTransfer?.getData(_DND_MIME) ?? "null");
    } catch {
      env = null;
    }
    if (!env || env.source_kind !== "table-row") return; // accept 外源不高亮不接收(§15.2)
    e.preventDefault();
    const sourceId = String(env.source ?? "").split("/").pop();
    const before = e.target.closest("[data-row]")?.dataset.row ?? "";
    if (sourceId && sourceId !== before) widget.move_row(sourceId, before);
  });
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

/* W-table 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.3;W5.2 新形态:自渲染;
   W6.6 视觉按 docs/WIDGET-DESIGN.md §3.3 v2 简化)。

   state{rows: [{id, cells: {...}}], selected: [ids], schema: {columns: [...]}, title};
   列定义驱动({key, type: text|number|boolean|enum, label, required?, options?});
   actions 全 local:add_row/remove_row/move_row/set_cell/remove_selected;
   v2(用户验收反馈):**KV 式常驻可编辑**(隐形 input,无编辑态切换)、
   **Excel 键盘逻辑**(Tab/Shift+Tab 横向走格、Enter 下移、左右方向键在
   光标位于端点时跨格)、**行 DnD 与 envelope 一并退役**(Alt+↑/↓ 键盘
   移行保留)、行 DnD 排序走 §15 的标准 envelope 随之移除;
   change 事件上行(数据下行,事件上行,§3)。
   铁律:本文件不拼 HTML(渲染全在 w-table.render.js);零 fetch;事件上行;
   监听一律委托在 host(重渲会换掉子元素)。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderTableEditor } from "./w-table.render.js";

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
  mount: mountTableEditor,
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

/* 挂进宿主:columns(列定义)+ rows(初始行;下行数据)+ path(§14 前缀)
   + title(面板头,可选)。
   返回 widget;父组件 on("change", ...) 收全部 mutation。
   双形态(§1.4):surface="card" 时渲染摘要卡,宿主委托只挂 open。
   v2(§3.3,用户验收反馈):KV 式常驻可编辑(无编辑态);Excel 键盘走格;
   行 DnD/envelope 退役(Alt+↑/↓ 键盘移行保留)。 */
export function mountTableEditor(host, { columns, rows = [], title = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(TABLE_EDITOR_DEF, {
    path,
    state: {
      schema: { columns },
      rows: rows.map((r) => _newRow(columns, r.cells ?? r)),
      selected: [],
      title,
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
    _changed(); // set_cell 不重渲(常驻编辑器自身即显示,输入零干扰)
  };
  widget.serialize = () => widget.state.rows.map((r) => ({ ...r.cells }));

  /* Excel 走格(§3.3 v2):把焦点移到 (ri, ci) 的单元格,返回是否命中 */
  const _focusCell = (ri, ci) => {
    const rs = widget.state.rows;
    const cols = widget.state.schema?.columns ?? [];
    if (ri < 0 || ri >= rs.length || ci < 0 || ci >= cols.length) return false;
    host.querySelector(`[data-cell="${rs[ri].id}:${cols[ci].key}"]`)?.focus?.();
    return true;
  };

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
  // 键盘(v2):Tab/Shift+Tab 横向走格、Enter 下移、←/→ 光标位于端点时跨格
  // (checkbox/select 无文本光标,方向键直接跨格);Alt+↑/↓ 键盘移行(§2 a11y)
  host.addEventListener("keydown", (e) => {
    const cellEl = e.target.closest?.("[data-cell]");
    if (cellEl) {
      const cols = widget.state.schema?.columns ?? [];
      const [id, key] = cellEl.dataset.cell.split(":");
      const ri = widget.state.rows.findIndex((r) => r.id === id);
      const ci = cols.findIndex((c) => c.key === key);
      if (ri < 0 || ci < 0) return;
      const noCaret = cellEl.type === "checkbox" || cellEl.tagName === "SELECT";
      if (e.key === "Tab") {
        e.preventDefault?.();
        if (e.shiftKey) _focusCell(ci > 0 ? ri : ri - 1, ci > 0 ? ci - 1 : cols.length - 1);
        else _focusCell(ci + 1 < cols.length ? ri : ri + 1, ci + 1 < cols.length ? ci + 1 : 0);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault?.();
        _focusCell(ri + 1, ci); // Enter 下移(Excel 逻辑;末行不动)
        return;
      }
      const atStart = noCaret || ((cellEl.selectionStart ?? 0) === 0 && (cellEl.selectionEnd ?? 0) === 0);
      const atEnd =
        noCaret ||
        ((cellEl.selectionStart ?? 0) === String(cellEl.value ?? "").length &&
          (cellEl.selectionEnd ?? 0) === String(cellEl.value ?? "").length);
      if (e.key === "ArrowLeft" && atStart) _focusCell(ri, ci - 1);
      if (e.key === "ArrowRight" && atEnd) _focusCell(ri, ci + 1);
      return; // 其余键归编辑(不拦)
    }
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
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

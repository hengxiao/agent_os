/* W-table — table editor(docs/WIDGETS.md §2;tests 用例表/members 表/键值参数表)。

   state{rows: [{id, cells: {...}}], selected: [ids], schema: {columns: [...]}};
   列定义驱动({key, type: text|number|boolean|enum, label, required?, options?});
   actions 全 local:add_row/remove_row/move_row/set_cell/remove_selected;
   细节:行 DnD 排序走 §15 标准 envelope({source, source_kind:"table-row",
   position:{before}})、新增行骨架、空态、role=grid + Alt+↑/↓ 键盘移行;
   change 事件上行(数据下行,事件上行,§3)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

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
  events: ["change"],
  aria: { role: "grid", keys: ["ArrowUp", "ArrowDown"] },
  surfaces: ["card", "tab"],
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

function _cellEditor(doc, col, row) {
  const value = row.cells[col.key];
  if (col.type === "boolean") {
    return (
      `<input type="checkbox" data-cell="${row.id}:${col.key}"${value ? " checked" : ""}` +
      ` aria-label="${esc(col.label ?? col.key)}">`
    );
  }
  if (col.type === "enum") {
    return (
      `<select data-cell="${row.id}:${col.key}" aria-label="${esc(col.label ?? col.key)}">` +
      (col.options ?? [])
        .map((o) => `<option value="${esc(o)}"${o === value ? " selected" : ""}>${esc(o)}</option>`)
        .join("") +
      `</select>`
    );
  }
  const type = col.type === "number" ? "number" : "text";
  return (
    `<input class="input" type="${type}" data-cell="${row.id}:${col.key}" value="${esc(String(value ?? ""))}"` +
    ` aria-label="${esc(col.label ?? col.key)}">`
  );
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* 挂进宿主:columns(列定义)+ rows(初始行;下行数据)+ path(§14 前缀)。
   返回 widget;父组件 on("change", ...) 收全部 mutation。 */
export function mountTableEditor(host, { columns, rows = [], path = "", onRegister = null, onUnregister = null } = {}) {
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
  const doc = host.ownerDocument;
  const columns_ = columns;

  const _changed = () => widget.emit("change", { rows: widget.state.rows });

  function render() {
    const { rows: rs, selected } = widget.state;
    host.innerHTML =
      (rs.length
        ? `<table class="wd-table" role="grid"><thead><tr>${columns_
            .map((c) => `<th>${esc(c.label ?? c.key)}${c.required ? " *" : ""}</th>`)
            .join("")}<th></th></tr></thead><tbody>` +
          rs
            .map(
              (r) =>
                `<tr data-row="${r.id}"${selected.includes(r.id) ? ' data-selected="1"' : ""}` +
                ` draggable="true" tabindex="0">` +
                columns_.map((c) => `<td>${_cellEditor(doc, c, r)}</td>`).join("") +
                `<td><button class="wd-row-x" data-row-x="${r.id}" aria-label="${esc(copy("w.table.del"))}">✕</button></td></tr>`
            )
            .join("") +
          `</tbody></table>`
        : `<div class="wd-empty">${esc(copy("w.table.empty"))}</div>`) +
      `<button class="wd-add" data-wd-add>${esc(copy("w.table.add"))}</button>`;
  }

  widget.add_row = (cells = {}) => {
    widget.state.rows = [...widget.state.rows, _newRow(columns_, cells)];
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

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

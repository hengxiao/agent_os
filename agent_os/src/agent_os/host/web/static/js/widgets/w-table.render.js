/* W-table 渲染面(docs/WIDGET-ARCH.md §1.1/§2.3;W5.2):
   ``renderTableEditor(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.3):列头(名称 + 类型徽标 + 必填*);行首拖柄(⠿);
   行尾删除钮;空态;底部"添加行"。 */

import { copy } from "../themes.js";

/* 单元格编辑器(列型驱动:text/number/boolean/enum) */
function _cellHtml(col, row) {
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

/* state → html(纯);state 面:{rows:[{id,cells}], selected:[ids], schema:{columns}} */
export function renderTableEditor(state) {
  const columns = state.schema?.columns ?? [];
  const rs = state.rows ?? [];
  const selected = state.selected ?? [];
  return (
    (rs.length
      ? `<table class="wd-table" role="grid"><thead><tr><th class="wd-drag" aria-hidden="true"></th>${columns
          .map(
            (c) =>
              `<th>${esc(c.label ?? c.key)}${c.required ? " *" : ""}` +
              `<span class="wd-type mono">${esc(c.type ?? "text")}</span></th>`
          )
          .join("")}<th></th></tr></thead><tbody>` +
        rs
          .map(
            (r) =>
              `<tr data-row="${r.id}"${selected.includes(r.id) ? ' data-selected="1"' : ""}` +
              ` draggable="true" tabindex="0">` +
              `<td class="wd-drag" aria-hidden="true">⠿</td>` +
              columns.map((c) => `<td>${_cellHtml(c, r)}</td>`).join("") +
              `<td><button class="wd-row-x" data-row-x="${r.id}" aria-label="${esc(copy("w.table.del"))}">✕</button></td></tr>`
          )
          .join("") +
        `</tbody></table>`
      : `<div class="wd-empty">${esc(copy("w.table.empty"))}</div>`) +
    `<button class="wd-add" data-wd-add>${esc(copy("w.table.add"))}</button>`
  );
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

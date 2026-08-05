/* W-table 渲染面(docs/WIDGET-ARCH.md §1.1/§2.3;W5.2 自渲染;W6.2 按
   docs/WIDGET-DESIGN.md §3.3 重做视觉):
   ``renderTableEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。

   tab(§3.3):面板头(title · N 行 × M 列)+ sticky 列头(类型语义图标 +
   名称 500 + 必填红 * + hover 显排序/⋯ 槽);行 40px,hover=--bg-2、选中=
   --live 左条 + 8% 浅底(反馈只改底色);拖柄 ⠿ 平时 30% 透明行 hover 全显;
   单元格展示态点击进内联编辑(32px 框;boolean 始终 checkbox、enum 编辑态
   出 select——展示态为 chip);行尾 ✕ 仅 hover 显;「+ 添加行」整宽虚线
   hover 变实 + --live 字色;空态 = 表头 + 骨架行 + 「添加第一行」主操作。
   card(§3.3):标题行(表名 + 「N 行」徽标)+ 迷你列头(≤3 列,溢出 +N)+
   前 2 行只读(网格对齐)+「查看全部 →」;整卡 = open 入口。 */

import { copy } from "../themes.js";

/* 类型语义图标(§3.3:12px 弱色;图标 + 文字双编码) */
const _TYPE_ICONS = { text: "Aa", number: "#", enum: "≡", date: "📅", boolean: "☑" };

/* state → html(纯);state 面:{rows:[{id,cells}], selected:[ids], schema:{columns},
   title?, editing?("id:key" 内联编辑槽)} */
export function renderTableEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _tableCardHtml(state);
  const columns = state.schema?.columns ?? [];
  const rs = state.rows ?? [];
  const selected = state.selected ?? [];
  const editing = state.editing ?? "";
  const count = copy("w.table.count").replace("{r}", String(rs.length)).replace("{c}", String(columns.length));
  const thead =
    `<thead><tr><th class="wd-drag" aria-hidden="true"></th>` +
    columns
      .map(
        (c) =>
          `<th><span class="wd-th-ico" aria-hidden="true">${_TYPE_ICONS[c.type] ?? "Aa"}</span>` +
          `<span class="wd-th-name">${esc(c.label ?? c.key)}</span>` +
          (c.required ? `<span class="wd-req" aria-hidden="true">*</span>` : "") +
          `<span class="wd-th-act" aria-hidden="true">↑</span><span class="wd-th-act" aria-hidden="true">⋯</span></th>`
      )
      .join("") +
    `<th></th></tr></thead>`;
  const body = rs.length
    ? rs
        .map(
          (r) =>
            `<tr data-row="${r.id}"${selected.includes(r.id) ? ' data-selected="1"' : ""}` +
            ` draggable="true" tabindex="0">` +
            `<td class="wd-drag" aria-hidden="true">⠿</td>` +
            columns.map((c) => `<td>${_cellHtml(c, r, editing)}</td>`).join("") +
            `<td><button class="wd-row-x" data-row-x="${r.id}" aria-label="${esc(copy("w.table.del"))}">✕</button></td></tr>`
        )
        .join("")
    : `<tr class="wd-skel-row" aria-hidden="true"><td></td>` +
      columns
        .map((c, i) => `<td><span class="wd-skeleton" style="width:${i === 0 ? 90 : 60}px;height:10px"></span></td>`)
        .join("") +
      `<td></td></tr>`;
  return (
    `<div class="wd-tbl">` +
    `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title ? `${state.title} · ${count}` : count)}</span></div>` +
    `<div class="wd-table-scroll"><table class="wd-table" role="grid">${thead}<tbody>${body}</tbody></table></div>` +
    (rs.length
      ? `<button class="wd-add" data-wd-add>+ ${esc(copy("w.table.add"))}</button>`
      : `<div class="wd-empty-box"><button class="wd-btn-primary" data-wd-add>+ ${esc(copy("w.table.addfirst"))}</button></div>`) +
    `</div>`
  );
}

/* 单元格(列型驱动):boolean 始终 checkbox;编辑态 = 32px input / enum select;
   展示态 = 文本(enum 为 chip),点击进编辑(§3.3;逻辑面管 state.editing) */
function _cellHtml(col, row, editing) {
  const ref = `${row.id}:${col.key}`;
  const value = row.cells[col.key];
  const label = esc(col.label ?? col.key);
  if (col.type === "boolean") {
    return (
      `<input type="checkbox" data-cell="${ref}"${value ? " checked" : ""}` +
      ` aria-label="${label}">`
    );
  }
  if (editing === ref) {
    if (col.type === "enum") {
      return (
        `<select data-cell="${ref}" aria-label="${label}">` +
        (col.options ?? [])
          .map((o) => `<option value="${esc(o)}"${o === value ? " selected" : ""}>${esc(o)}</option>`)
          .join("") +
        `</select>`
      );
    }
    const type = col.type === "number" ? "number" : "text";
    return (
      `<input class="wd-cell-in" type="${type}" data-cell="${ref}" value="${esc(String(value ?? ""))}"` +
      ` aria-label="${label}">`
    );
  }
  const text = String(value ?? "");
  if (col.type === "enum") {
    return `<span class="wd-chip wd-cell" data-cell="${ref}">${esc(text)}</span>`;
  }
  return `<span class="wd-cell" data-cell="${ref}">${esc(text)}</span>`;
}

/* card 面(§3.3):标题行 + 迷你列头(网格对齐,≤3 列溢出 +N)+ 前 2 行只读 +
   「查看全部 →」;无拖柄/✕/添加行/编辑器 */
function _tableCardHtml(state) {
  const columns = state.schema?.columns ?? [];
  const rs = state.rows ?? [];
  const showCols = columns.slice(0, 3);
  const grid = ` style="--cols:${Math.max(showCols.length, 1)}"`;
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    (state.title ? `<span class="wd-card-name">${esc(state.title)}</span>` : "") +
    `<span class="wd-badge">${esc(copy("w.card.lines").replace("{n}", String(rs.length)))}</span>` +
    `</span>` +
    (showCols.length
      ? `<span class="wd-card-cols"${grid}>` +
        showCols.map((c) => `<span class="wd-card-col">${esc(c.label ?? c.key)}</span>`).join("") +
        (columns.length > 3 ? `<span class="wd-badge">+${columns.length - 3}</span>` : "") +
        `</span>` +
        rs
          .slice(0, 2)
          .map(
            (r) =>
              `<span class="wd-card-row"${grid}>` +
              showCols
                .map((c) => `<span class="wd-card-cell">${esc(String(r.cells?.[c.key] ?? ""))}</span>`)
                .join("") +
              `</span>`
          )
          .join("")
      : "") +
    `<span class="wd-card-meta"><span class="wd-card-all">${esc(copy("w.card.viewall"))}</span></span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* W-list 渲染面(docs/WIDGET-ARCH.md §1.1/§2.6;W5.2):
   ``renderSelectList(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.6):顶部搜索框;条目行(图标+主标+副标);选中行左色条(CSS);
   空态;键盘焦点行(data-focus)。 */

import { copy } from "../themes.js";

/* 过滤后的可见条目(平列表,子串命中;纯函数,逻辑/渲染共用) */
export function visibleItems(state) {
  const filter = state.filter ?? "";
  return (state.items ?? []).filter(
    (it) => !filter || (it.label ?? it.id).toLowerCase().includes(filter.toLowerCase())
  );
}

/* state → html(纯);state 面:{items, selected, filter, focus, multi} */
export function renderSelectList(state) {
  const vis = visibleItems(state);
  const focus = Math.min(state.focus ?? 0, Math.max(0, vis.length - 1));
  const isSel = (id) =>
    Array.isArray(state.selected) ? state.selected.includes(id) : state.selected === id;
  return (
    `<div class="wd-list">` +
    `<input class="input wd-list-filter" data-wl-filter placeholder="${esc(copy("w.list.filter"))}"` +
    ` aria-label="${esc(copy("w.list.filter"))}" value="${esc(state.filter ?? "")}">` +
    (vis.length
      ? `<div role="listbox" aria-multiselectable="${Boolean(state.multi)}">` +
        vis
          .map((it, i) => {
            const sel = isSel(it.id);
            return (
              `<div class="wd-list-item" data-wl-item="${esc(it.id)}" role="option" tabindex="0"` +
              ` aria-selected="${sel}"${i === focus ? ' data-focus="1"' : ""}>` +
              (it.icon ? `<span aria-hidden="true">${esc(it.icon)}</span>` : "") +
              `<span class="wd-list-label">${esc(it.label ?? it.id)}</span>` +
              (it.hint ? `<span class="wd-list-hint">${esc(it.hint)}</span>` : "") +
              `</div>`
            );
          })
          .join("") +
        `</div>`
      : `<div class="wd-empty">${esc(copy("w.list.empty"))}</div>`) +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

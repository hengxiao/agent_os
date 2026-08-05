/* W-list 渲染面(docs/WIDGET-ARCH.md §1.1/§2.6;W5.2 自渲染;W6.3 按
   docs/WIDGET-DESIGN.md §3.6 重做视觉):
   ``renderSelectList(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。

   tab(§3.6):顶部过滤框(🔍 图标 + Esc 清空提示;命中子串 --live **文字色**
   高亮,不是底色);条目行 = 主名 mono 500 + 右侧 meta 11px 弱;hover/键焦
   --bg-2(两态视觉一致);选中 = --live 2px 左条 + 8% 浅底 + 右侧 ✓;
   多选时底部浮条「已选 N · 清除」;过滤空态 =「没有匹配『xx』的项」+
   清除搜索钮(无过滤词的空态仍走 w.list.empty)。
   card(§3.6):「当前选中」标签 + 选中项(名称 mono 500)+ meta(元信息 ·
   共 N 项)+ 右下「更换 →」;整卡 = open 入口。 */

import { copy } from "../themes.js";

/* 过滤后的可见条目(平列表,子串命中;纯函数,逻辑/渲染共用) */
export function visibleItems(state) {
  const filter = state.filter ?? "";
  return (state.items ?? []).filter(
    (it) => !filter || (it.label ?? it.id).toLowerCase().includes(filter.toLowerCase())
  );
}

/* 命中子串高亮(§3.6:--live 文字色,不是底色;先转义再包 mark) */
function _hitHtml(text, filter) {
  const raw = String(text ?? "");
  const q = String(filter ?? "").trim();
  if (!q) return esc(raw);
  const idx = raw.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return esc(raw);
  return (
    esc(raw.slice(0, idx)) +
    `<mark class="wd-hit">${esc(raw.slice(idx, idx + q.length))}</mark>` +
    esc(raw.slice(idx + q.length))
  );
}

/* state → html(纯);state 面:{items, selected, filter, focus, multi, title?} */
export function renderSelectList(state, { surface = "tab" } = {}) {
  if (surface === "card") return _listCardHtml(state);
  const vis = visibleItems(state);
  const focus = Math.min(state.focus ?? 0, Math.max(0, vis.length - 1));
  const isSel = (id) =>
    Array.isArray(state.selected) ? state.selected.includes(id) : state.selected === id;
  const filter = state.filter ?? "";
  const selCount = Array.isArray(state.selected) ? state.selected.length : state.selected ? 1 : 0;
  return (
    `<div class="wd-list">` +
    (state.title ? `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title)}</span></div>` : "") +
    `<div class="wd-list-search">` +
    `<span class="wd-list-ico" aria-hidden="true">🔍</span>` +
    `<input class="wd-list-filter" data-wl-filter placeholder="${esc(copy("w.list.filter"))}"` +
    ` aria-label="${esc(copy("w.list.filter"))}" value="${esc(filter)}">` +
    `<span class="wd-list-esc" aria-hidden="true">${esc(copy("w.list.esc"))}</span>` +
    `</div>` +
    (vis.length
      ? `<div role="listbox" aria-multiselectable="${Boolean(state.multi)}">` +
        vis
          .map((it, i) => {
            const sel = isSel(it.id);
            return (
              `<div class="wd-list-item" data-wl-item="${esc(it.id)}" role="option" tabindex="0"` +
              ` aria-selected="${sel}"${i === focus ? ' data-focus="1"' : ""}>` +
              (it.icon ? `<span aria-hidden="true">${esc(it.icon)}</span>` : "") +
              `<span class="wd-list-label mono">${_hitHtml(it.label ?? it.id, filter)}</span>` +
              (it.hint ? `<span class="wd-list-hint">${esc(it.hint)}</span>` : "") +
              (sel ? `<span class="wd-list-check" aria-hidden="true">✓</span>` : "") +
              `</div>`
            );
          })
          .join("") +
        `</div>`
      : filter
        ? `<div class="wd-empty-box"><span class="wd-empty-guide">${esc(copy("w.list.nomatch").replace("{q}", filter))}</span>` +
          `<button class="wd-btn-ghost" data-wl-clear="1">${esc(copy("w.list.clear"))}</button></div>`
        : `<div class="wd-empty">${esc(copy("w.list.empty"))}</div>`) +
    // 多选浮条(§3.6):已选 N · 清除
    (state.multi && selCount
      ? `<div class="wd-list-bar"><span>${esc(copy("w.list.selcount").replace("{n}", String(selCount)))}</span>` +
        `<span class="wd-dim"> · </span>` +
        `<button class="wd-list-clear" data-wl-clear-sel="1">${esc(copy("w.list.clearall"))}</button></div>`
      : "") +
    `</div>`
  );
}

/* card 面(§3.6):当前选中(名称 mono 500)+ meta(元信息 · 共 N 项)+ 更换 → */
function _listCardHtml(state) {
  const items = state.items ?? [];
  const sel = state.selected;
  const picked = Array.isArray(sel) ? sel : sel ? [sel] : [];
  const first = items.find((it) => it.id === picked[0]);
  const meta = [...(first?.hint ? [first.hint] : []), copy("w.list.total").replace("{n}", String(items.length))];
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-dim">${esc(copy("w.list.current"))}</span>` +
    (first
      ? `<span class="wd-card-line mono wd-card-name">${esc(first.label ?? first.id)}</span>` +
        (picked.length > 1 ? ` <span class="wd-badge">+${picked.length - 1}</span>` : "")
      : `<span class="wd-card-line">${esc(copy("w.card.empty"))}</span>`) +
    `<span class="wd-card-meta"><span class="wd-card-meta-txt">${esc(meta.join(" · "))}</span>` +
    `<span class="wd-card-all">${esc(copy("w.list.change"))}</span></span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

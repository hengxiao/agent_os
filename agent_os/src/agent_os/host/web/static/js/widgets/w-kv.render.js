/* W-kv 渲染面(docs/WIDGET-ARCH.md §1.1/§2.4;W5.2):
   ``renderKvEditor(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.4):两列(key/value)+ 行尾删除;重复 key 行黄底警示(.wd-kv-warn)。 */

import { copy } from "../themes.js";

/* 重复 key 清单(警示面;重复 key 不硬拦,警告即可) */
export function dupKeys(entries) {
  const seen = new Set();
  const dups = new Set();
  for (const e of entries ?? []) {
    if (!e.key) continue;
    if (seen.has(e.key)) dups.add(e.key);
    seen.add(e.key);
  }
  return [...dups];
}

/* state → html(纯);state 面:{entries:[{key,value}]} */
export function renderKvEditor(state) {
  const entries = state.entries ?? [];
  const dups = new Set(dupKeys(entries));
  return (
    entries
      .map(
        (e, i) =>
          `<div class="wd-kv-row${dups.has(e.key) ? " wd-kv-warn" : ""}" data-kv="${i}">` +
          `<input class="input wd-kv-k" data-kv-key="${i}" value="${esc(e.key)}" aria-label="key"` +
          `${dups.has(e.key) ? ' data-warn="1"' : ""}>` +
          `<input class="input wd-kv-v" data-kv-value="${i}" value="${esc(e.value)}" aria-label="value">` +
          `<button class="wd-row-x" data-kv-x="${i}" aria-label="✕">✕</button>` +
          (dups.has(e.key) ? `<span class="wd-dup">${esc(copy("w.kv.dup"))}</span>` : "") +
          `</div>`
      )
      .join("") +
    `<button class="wd-add" data-kv-add>${esc(copy("w.kv.add"))}</button>`
  );
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

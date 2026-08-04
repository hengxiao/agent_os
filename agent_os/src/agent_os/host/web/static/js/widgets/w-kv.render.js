/* W-kv 渲染面(docs/WIDGET-ARCH.md §1.1/§2.4;W5.2):
   ``renderKvEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
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

/* state → html(纯);state 面:{entries:[{key,value}]}
   双形态(§1.4):surface="card" → 键值计数徽标 + 重复 key 警示计数(有则黄)+
   前 3 条只读;无输入框/✕/添加钮 */
export function renderKvEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _kvCardHtml(state);
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

/* card 面(§1.4):计数 + 警示徽标一行,前 3 条 key = value 只读 */
function _kvCardHtml(state) {
  const entries = state.entries ?? [];
  const dups = dupKeys(entries);
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-badge">${esc(copy("w.card.entries").replace("{n}", String(entries.length)))}</span>` +
    (dups.length
      ? `<span class="wd-badge" data-tone="warn">${esc(copy("w.card.dup").replace("{n}", String(dups.length)))}</span>`
      : "") +
    `</span>` +
    entries
      .slice(0, 3)
      .map(
        (e) =>
          `<span class="wd-card-line"><span class="mono">${esc(e.key)}</span> = ${esc(e.value)}</span>`
      )
      .join("") +
    `</div>`
  );
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

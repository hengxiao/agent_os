/* W-kv 渲染面(docs/WIDGET-ARCH.md §1.1/§2.4;W5.2 自渲染;W6.2 按
   docs/WIDGET-DESIGN.md §3.4 重做视觉):
   ``renderKvEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。

   tab(§3.4):KEY/VALUE 列头(11px 600 弱)+ 40/60 两列(key mono);输入框
   平时隐形(border/底透明,聚焦显形);重复 key:两行都 --warn 8% 浅底 +
   key 旁 ⚠(hover 出 tooltip「后者覆盖前者」,不硬拦);行 hover --bg-2 显 ✕;
   「+ 添加」整宽虚线;空态 = ∷ 图标 + 引导 + 「+ 添加第一条」ghost。
   card(§3.4):「N 键值」徽标 +(有重复时)黄「⚠ 重复 ×N」警示徽标 +
   前 3 条 `key = value`(mono,截断省略)+ meta(共 N 条 · M 处重复)。 */

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

/* state → html(纯);state 面:{entries:[{key,value}], title?} */
export function renderKvEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _kvCardHtml(state);
  const entries = state.entries ?? [];
  const dups = new Set(dupKeys(entries));
  const dupTip = copy("w.kv.dup");
  return (
    `<div class="wd-kv">` +
    (state.title
      ? `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title)} · ${esc(copy("w.kv.count").replace("{n}", String(entries.length)))}</span></div>`
      : "") +
    `<div class="wd-kv-cols" aria-hidden="true"><span>KEY</span><span>VALUE</span><span></span></div>` +
    entries
      .map(
        (e, i) =>
          `<div class="wd-kv-row${dups.has(e.key) ? " wd-kv-warn" : ""}" data-kv="${i}">` +
          `<span class="wd-kv-kcell">` +
          `<input class="input wd-kv-k" data-kv-key="${i}" value="${esc(e.key)}" aria-label="key"` +
          `${dups.has(e.key) ? ' data-warn="1"' : ""}>` +
          (dups.has(e.key)
            ? `<span class="wd-kv-warn-ico" data-tip="${esc(dupTip)}" aria-label="${esc(dupTip)}">⚠</span>`
            : "") +
          `</span>` +
          `<input class="input wd-kv-v" data-kv-value="${i}" value="${esc(e.value)}" aria-label="value">` +
          `<button class="wd-row-x" data-kv-x="${i}" aria-label="✕">✕</button>` +
          `</div>`
      )
      .join("") +
    (entries.length
      ? `<button class="wd-add" data-kv-add>+ ${esc(copy("w.kv.add"))}</button>`
      : `<div class="wd-empty-box"><span class="wd-empty-ico" aria-hidden="true">∷</span>` +
        `<span class="wd-empty-guide">${esc(copy("w.kv.empty"))}</span>` +
        `<button class="wd-btn-ghost" data-kv-add>+ ${esc(copy("w.kv.addfirst"))}</button></div>`) +
    `</div>`
  );
}

/* card 面(§3.4):计数 + 警示徽标一行,前 3 条 mono 只读,meta 合计行 */
function _kvCardHtml(state) {
  const entries = state.entries ?? [];
  const dups = dupKeys(entries);
  const meta = [copy("w.kv.total").replace("{n}", String(entries.length))];
  if (dups.length) meta.push(copy("w.kv.dupn").replace("{n}", String(dups.length)));
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-badge">${esc(copy("w.card.entries").replace("{n}", String(entries.length)))}</span>` +
    (dups.length
      ? `<span class="wd-badge" data-tone="warn">⚠ ${esc(copy("w.card.dup").replace("{n}", String(dups.length)))}</span>`
      : "") +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    entries
      .slice(0, 3)
      .map((e) => `<span class="wd-card-line mono">${esc(e.key)} = ${esc(e.value)}</span>`)
      .join("") +
    `<span class="wd-card-meta">${esc(meta.join(" · "))}</span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

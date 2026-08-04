/* W-diff 渲染面(docs/WIDGET-ARCH.md §1.1/§2.11;W5.3):
   ``renderDiffViewer(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class,视觉全走契约 token。
   效果(§2.11):split 双列(与 cards.js diffCard 逐字节同构)/ unified 单列
   (+绿 -红,折叠上下文 [+n] 钮);双编码(+/- 符号必在;add=--ok 浅底、
   del=--danger 浅底,CSS 面);成员头(tier 徽标)。双形态(§1.4,W5.6):
   card = 计数徽标 + 首个 hunk 预览,tab 完整。 */

import { copy } from "../themes.js";

/* split 两列(与 cards.js diffCard 原输出同构:pf-dmember/pf-twocol/pf-dline) */
function _splitMemberHtml(m) {
  const fields = (m.fields ?? [])
    .map(
      (f) =>
        `<div class="pf-twocol" data-kind="${esc(f.kind)}">` +
        `<div>${esc(JSON.stringify(f.old) ?? "—")}</div><div>${esc(JSON.stringify(f.new) ?? "—")}</div></div>`
    )
    .join("");
  const lines = (m.prompt_diff ?? [])
    .filter((l) => l.kind !== "same")
    .map(
      (l) =>
        `<div class="pf-dline" data-kind="${esc(l.kind)}">${l.kind === "add" ? "+" : "-"} ${esc(l.text)}</div>`
    )
    .join("");
  return (
    `<div class="pf-dmember"><span class="mono">${esc(m.member)}</span>` +
    `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
    fields + lines + `</div>`
  );
}

/* unified 单列(旧上新下;same 行折叠上下文,默认收起,[+n] 展开) */
function _unifiedMemberHtml(m, expanded) {
  const fields = (m.fields ?? [])
    .map(
      (f) =>
        `<div class="wd-diff-field" data-kind="${esc(f.kind)}">` +
        `<div class="wd-diff-old">${esc(JSON.stringify(f.old) ?? "—")}</div>` +
        `<div class="wd-diff-new">${esc(JSON.stringify(f.new) ?? "—")}</div></div>`
    )
    .join("");
  const key = m.member ?? "";
  const open = (expanded ?? []).includes(key);
  const parts = [];
  let sameRun = 0;
  const flush = () => {
    if (!sameRun) return;
    parts.push(
      open
        ? `<div class="wd-diff-same" data-kind="same">${sameRun} 行未变(上下文中)</div>`
        : `<button class="wd-diff-fold" data-fold="${esc(key)}">+${sameRun} 行未变</button>`
    );
    sameRun = 0;
  };
  for (const l of m.prompt_diff ?? []) {
    if (l.kind === "same") {
      if (open) {
        flush();
        parts.push(`<div class="pf-dline" data-kind="same">  ${esc(l.text)}</div>`);
      } else {
        sameRun += 1;
      }
      continue;
    }
    flush();
    parts.push(
      `<div class="pf-dline" data-kind="${esc(l.kind)}">${l.kind === "add" ? "+" : "-"} ${esc(l.text)}</div>`
    );
  }
  flush();
  return (
    `<div class="pf-dmember"><span class="mono">${esc(key)}</span>` +
    `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
    fields + parts.join("") + `</div>`
  );
}

/* diff 本体渲染(mode 分发;split = 既有呈现,unified = 折叠上下文单列) */
export function diffBodyHtml(diff, { mode = "split", expanded = null } = {}) {
  const members = (diff?.members ?? [])
    .map((m) => (mode === "unified" ? _unifiedMemberHtml(m, expanded) : _splitMemberHtml(m)))
    .join("");
  return members || "";
}

/* state → html(纯);state 面:{left(diff 对象), mode, expanded:[member 名]}
   双形态(§1.4):surface="card" → +add/-del 计数徽标 + 首个 hunk 的 2 行
   预览(固定紧凑 unified);无 split/unified 切换钮 */
export function renderDiffViewer(state, { surface = "tab" } = {}) {
  if (surface === "card") return _diffCardHtml(state);
  const mode = state.mode ?? "split";
  return (
    `<div class="wd-diff" data-mode="${esc(mode)}">` +
    `<div class="wd-diff-modes">` +
    ["split", "unified"]
      .map((m) => `<button class="wd-mode" data-mode="${m}"${mode === m ? ' data-on="1"' : ""}>${m}</button>`)
      .join("") +
    `</div>` +
    diffBodyHtml(state.left, { mode, expanded: state.expanded ?? [] }) +
    `</div>`
  );
}

/* card 面(§1.4):+add/-del 计数徽标(全成员 prompt_diff 合计,双编码:
   符号必在 + data-kind 色)+ 首个含变更成员的 2 行预览(紧凑 unified,
   复用 .pf-dline 语义类);无模式切换 */
function _diffCardHtml(state) {
  const members = state.left?.members ?? [];
  let adds = 0;
  let dels = 0;
  let preview = "";
  for (const m of members) {
    const changed = (m.prompt_diff ?? []).filter((l) => l.kind !== "same");
    for (const l of m.prompt_diff ?? []) {
      if (l.kind === "add") adds += 1;
      else if (l.kind === "del") dels += 1;
    }
    if (!preview && changed.length) {
      preview = changed
        .slice(0, 2)
        .map(
          (l) =>
            `<div class="pf-dline" data-kind="${esc(l.kind)}">${l.kind === "add" ? "+" : "-"} ${esc(l.text)}</div>`
        )
        .join("");
    }
  }
  return (
    `<div class="wd-diff wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-badge" data-kind="add">+${adds}</span>` +
    `<span class="wd-badge" data-kind="del">-${dels}</span>` +
    `</span>` +
    preview +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

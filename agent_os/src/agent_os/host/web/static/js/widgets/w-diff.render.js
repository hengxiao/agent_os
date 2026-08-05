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
        ? `<div class="wd-diff-same" data-kind="same">${esc(copy("w.diff.ctx").replace("{n}", String(sameRun)))}</div>`
        : `<button class="wd-diff-fold" data-fold="${esc(key)}">${esc(copy("w.diff.fold").replace("{n}", String(sameRun)))}</button>`
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
    _tierBadge(m) +
    `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
    fields + parts.join("") + `</div>`
  );
}

/* tier 徽标(§3.11:●reversible 绿 / ▲escalate 黄;双编码:符号 + 色 + 文案) */
function _tierBadge(m) {
  if (!m?.tier) return "";
  const tone = m.tier === "reversible" ? "ok" : m.tier === "escalate" ? "warn" : "";
  const glyph = m.tier === "reversible" ? "●" : m.tier === "escalate" ? "▲" : "·";
  return `<span class="wd-badge"${tone ? ` data-tone="${tone}"` : ""}>${glyph} ${esc(m.tier)}</span>`;
}

/* 增删计数 + hunk 数(非 same 连续段为 1 hunk;全成员合计;纯) */
function _diffStats(diff) {
  let adds = 0;
  let dels = 0;
  let hunks = 0;
  for (const m of diff?.members ?? []) {
    let inRun = false;
    for (const l of m.prompt_diff ?? []) {
      if (l.kind === "same") {
        if (inRun) hunks += 1;
        inRun = false;
        continue;
      }
      inRun = true;
      if (l.kind === "add") adds += 1;
      else if (l.kind === "del") dels += 1;
    }
    if (inRun) hunks += 1;
  }
  return { adds, dels, hunks };
}

/* diff 本体渲染(mode 分发;split = 既有呈现,unified = 折叠上下文单列) */
export function diffBodyHtml(diff, { mode = "split", expanded = null } = {}) {
  const members = (diff?.members ?? [])
    .map((m) => (mode === "unified" ? _unifiedMemberHtml(m, expanded) : _splitMemberHtml(m)))
    .join("");
  return members || "";
}

/* state → html(纯);state 面:{left(diff 对象), mode, expanded:[member 名], title?}
   双形态(§1.4):surface="card" → +add/−del 计数徽标 + 首个 hunk 的 2 行
   预览(固定紧凑 unified);无 split/unified 切换钮 */
export function renderDiffViewer(state, { surface = "tab" } = {}) {
  if (surface === "card") return _diffCardHtml(state);
  const mode = state.mode ?? "split";
  const members = state.left?.members ?? [];
  const stats = _diffStats(state.left);
  return (
    `<div class="wd-diff" data-mode="${esc(mode)}">` +
    `<div class="wd-pane-head">` +
    `<span class="wd-pane-title mono">${esc(state.title ? `${state.title} · ${mode}` : mode)}</span>` +
    `<span class="wd-diff-counts">` +
    `<span class="wd-badge" data-tone="ok">+${stats.adds}</span>` +
    `<span class="wd-badge" data-tone="danger">−${stats.dels}</span>` +
    `</span>` +
    `<span class="wd-seg">` +
    ["split", "unified"]
      .map(
        (m) =>
          `<button class="wd-seg-btn" data-mode="${m}"${mode === m ? ' data-on="1"' : ""}>${esc(copy(m === "split" ? "w.diff.split" : "w.diff.unified"))}</button>`
      )
      .join("") +
    `</span>` +
    `</div>` +
    (members.length
      ? diffBodyHtml(state.left, { mode, expanded: state.expanded ?? [] })
      : `<div class="wd-empty-box"><span class="wd-empty-ico" aria-hidden="true">✓</span>` +
        `<span class="wd-empty-guide">${esc(copy("w.diff.nochange"))}</span></div>`) +
    `</div>`
  );
}

/* card 面(§3.11):+add/−del 计数徽标(全成员 prompt_diff 合计,双编码:
   符号必在 + data-kind 色)+ 首个含变更成员的 2 行预览(紧凑 unified,
   复用 .pf-dline 语义类)+ meta(hunk 数 · mode)+「查看全部 →」;无模式切换 */
function _diffCardHtml(state) {
  const members = state.left?.members ?? [];
  const stats = _diffStats(state.left);
  let preview = "";
  for (const m of members) {
    const changed = (m.prompt_diff ?? []).filter((l) => l.kind !== "same");
    if (changed.length) {
      preview = changed
        .slice(0, 2)
        .map(
          (l) =>
            `<div class="pf-dline" data-kind="${esc(l.kind)}">${l.kind === "add" ? "+" : "-"} ${esc(l.text)}</div>`
        )
        .join("");
      break;
    }
  }
  return (
    `<div class="wd-diff wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    (state.title ? `<span class="wd-card-name mono">${esc(state.title)}</span>` : "") +
    `<span class="wd-badge" data-kind="add">+${stats.adds}</span>` +
    `<span class="wd-badge" data-kind="del">−${stats.dels}</span>` +
    `</span>` +
    preview +
    `<span class="wd-card-meta"><span class="wd-card-meta-txt">${stats.hunks} hunk · ${esc(state.mode ?? "split")}</span>` +
    `<span class="wd-card-all">${esc(copy("w.card.viewall"))}</span></span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

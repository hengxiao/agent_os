/* W-log 渲染面(docs/WIDGET-ARCH.md §1.1/§2.10;W5.3 自渲染;W6.4 按
   docs/WIDGET-DESIGN.md §3.10 重做视觉):
   ``renderLogViewer(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。

   tab(§3.10):深色面板(--log-bg);mono 12/行高 1.5;行 = 时间戳(弱,有
   ts 才出)+ 2px kind 色条(--sig-* 系,data-kind 驱动 CSS)+ kind 色字 +
   内容;工具行 = 过滤框 + 级别 chips(all + 现有 kind)+ ⧉ 复制 + ⏸ 暂停跟随;
   **过滤 = 未命中 40% 弱化(wd-dim),不是命中加底**;截断保尾时面板顶部
   弱提示;暂停时右下浮囊(无新行 = ↓ 回到底部,有 = 已暂停 · N 行新日志 ↓);
   长行横滚不 wrap(CSS)。
   card(§3.10):「N 行」徽标 +(有 err 时)红「err ×N」+ 最近 3 行(色条
   保留,省略时间戳)+ meta(跟随中 · 上限 N 行)。 */

import { copy } from "../themes.js";

/* 过滤后的可见行(纯;逻辑面/消费方共用——渲染面 W6.4 起走调光不过滤,
   本函数保留给"只要命中集"的调用方) */
export function visibleLogLines(state) {
  const filter = state.filter ?? "";
  return (state.lines ?? []).filter(
    (l) => !filter || l.text.toLowerCase().includes(filter.toLowerCase()) || l.kind.includes(filter)
  );
}

/* 行命中(文本子串 + kind 包含 + 级别 chip;纯) */
function _hit(l, q, level) {
  if (level && l.kind !== level) return false;
  if (!q) return true;
  return l.text.toLowerCase().includes(q) || l.kind.includes(q);
}

const _fmtTs = (ts) => {
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return String(ts);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

/* state → html(纯);state 面:{lines:[{kind,text,ts?}], follow, filter,
   level, truncated, pausedNew, maxLines, title?} */
export function renderLogViewer(state, { surface = "tab" } = {}) {
  if (surface === "card") return _logCardHtml(state);
  const lines = state.lines ?? [];
  const q = (state.filter ?? "").toLowerCase();
  const level = state.level ?? "";
  const filtering = Boolean(q || level);
  const kinds = [...new Set(lines.map((l) => l.kind))];
  const rows = lines
    .map((l) => {
      const dim = filtering && !_hit(l, q, level);
      return (
        `<div class="wd-log-line${dim ? " wd-dim" : ""}" data-kind="${esc(l.kind)}">` +
        (l.ts ? `<span class="wd-log-ts">${esc(_fmtTs(l.ts))}</span>` : "") +
        `<span class="wd-log-kind mono">${esc(l.kind)}</span> ${esc(l.text)}</div>`
      );
    })
    .join("");
  const pausedN = Number(state.pausedNew ?? 0);
  return (
    `<div class="wd-log">` +
    (state.title ? `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title)}</span></div>` : "") +
    `<div class="wd-log-bar">` +
    `<span class="wd-list-search wd-log-search"><span class="wd-list-ico" aria-hidden="true">🔍</span>` +
    `<input class="wd-list-filter" data-wlog-filter="1" placeholder="${esc(copy("w.log.filter"))}"` +
    ` aria-label="${esc(copy("w.log.filter"))}" value="${esc(state.filter ?? "")}"></span>` +
    `<span class="wd-chips">` +
    [`<button class="wd-chip" data-wlog-level="" data-on="${level ? "0" : "1"}">all</button>`,
      ...kinds.map(
        (k) => `<button class="wd-chip" data-wlog-level="${esc(k)}" data-on="${level === k ? "1" : "0"}">${esc(k)}</button>`
      )].join("") +
    `</span>` +
    `<button class="wd-mini" data-wlog-copy="1">${esc(copy("w.log.copy"))}</button>` +
    `<button class="wd-mini" data-wlog-follow="1">${esc(state.follow ? copy("w.log.pause") : copy("w.log.follow2"))}</button>` +
    `</div>` +
    `<div class="wd-log-box" role="log" data-wlog-box="1">` +
    (state.truncated
      ? `<div class="wd-log-note">${esc(copy("w.log.truncated").replace("{n}", String(state.maxLines ?? 500)))}</div>`
      : "") +
    (lines.length
      ? rows
      : `<div class="wd-empty-box"><span class="wd-empty-guide">${esc(copy("w.log.empty"))}</span>` +
        `<span class="wd-empty-sub">${esc(copy("w.log.emptyhint"))}</span></div>`) +
    `</div>` +
    (state.follow
      ? ""
      : `<button class="wd-log-bottom" data-wlog-bottom="1">${esc(
          pausedN > 0 ? copy("w.log.paused").replace("{n}", String(pausedN)) : `↓ ${copy("w.log.bottom")}`
        )}</button>`) +
    `</div>`
  );
}

/* card 面(§3.10):总数徽标 + err 计数徽标(有则红)+ 最近 3 行(色条保留,
   省略时间戳)+ meta;无过滤框/工具行 */
function _logCardHtml(state) {
  const lines = state.lines ?? [];
  const errs = lines.filter((l) => /err/i.test(l.kind)).length;
  const meta = [
    state.follow === false ? copy("w.log.paused").replace("{n}", String(state.pausedNew ?? 0)).replace(" ↓", "") : copy("w.log.following"),
    copy("w.log.capped").replace("{n}", String(state.maxLines ?? 500)),
  ];
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-badge">${esc(copy("w.card.lines").replace("{n}", String(lines.length)))}</span>` +
    (errs ? `<span class="wd-badge" data-tone="danger">${esc(copy("w.log.errn").replace("{n}", String(errs)))}</span>` : "") +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    (lines.length
      ? lines
          .slice(-3)
          .map(
            (l) =>
              `<div class="wd-log-line" data-kind="${esc(l.kind)}">` +
              `<span class="wd-log-kind mono">${esc(l.kind)}</span> ${esc(l.text)}</div>`
          )
          .join("")
      : `<div class="wd-empty">${esc(copy("w.log.empty"))}</div>`) +
    `<span class="wd-card-meta"><span class="wd-card-meta-txt">${esc(meta.join(" · "))}</span></span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

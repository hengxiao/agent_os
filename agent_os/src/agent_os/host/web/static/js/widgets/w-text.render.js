/* W-text 渲染面(docs/WIDGET-ARCH.md §1.1/§2.1;W5.1 新形态):
   ``renderTextEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token
   (css/widgets.css),文案只经 copy()。
   效果(§2.1):mono/plain 两变体;mono 带行号槽;右下微标(行数/字数);
   dirty 左边条(.is-dirty);readonly 灰底(.is-readonly)。
   双形态(§1.4,W5.6):surface="card" 出只读摘要卡(预览+微标),tab 完整交互。 */

import { copy } from "../themes.js";

/* 行数/字数微标(copy 六主题;统计是本地计算) */
export function textEditorMicro(value) {
  const lines = value ? value.split("\n").length : 0;
  return copy("w.text.count").replace("{lines}", String(lines)).replace("{chars}", String(value.length));
}

/* state → html(纯);state 面:{value, dirty, readonly, mono, rows, field, label}
   双形态(§1.4):surface="card" → 只读摘要(前 3 行预览 + 行/字微标 +
   dirty 左边条;无 textarea,整卡即 open 入口);缺省 tab = 完整编辑器。 */
export function renderTextEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _textCardHtml(state);
  const mono = Boolean(state.mono);
  const value = String(state.value ?? "");
  const lines = value ? value.split("\n").length : 0;
  return (
    `<div class="wd-text${state.dirty ? " is-dirty" : ""}${state.readonly ? " is-readonly" : ""}"` +
    ` data-variant="${mono ? "mono" : "plain"}">` +
    (mono
      ? `<span class="wd-gutter" aria-hidden="true">${Array.from({ length: Math.max(lines, 1) }, (_, i) => i + 1).join("\n")}</span>`
      : "") +
    `<textarea class="input${mono ? " mono" : ""}" rows="${Number(state.rows) || 6}"` +
    ` data-field="${esc(state.field ?? "")}" spellcheck="false"` +
    ` aria-label="${esc(state.label ?? state.field ?? "")}"${state.readonly ? " readonly" : ""}>` +
    `${esc(value)}</textarea>` +
    `<span class="wd-micro">${esc(textEditorMicro(value))}</span>` +
    `</div>`
  );
}

/* card 面(§1.4):前 3 行只读预览(mono 保留行号槽)+ 行/字微标 + dirty 左边条;
   摘要视图重设计——截断省略走 CSS(.wd-card-line),空值给占位 */
function _textCardHtml(state) {
  const mono = Boolean(state.mono);
  const value = String(state.value ?? "");
  const lines = value ? value.split("\n") : [];
  const preview = lines.slice(0, 3);
  return (
    `<div class="wd-text wd-card${state.dirty ? " is-dirty" : ""}${state.readonly ? " is-readonly" : ""}"` +
    ` data-surface="card" data-variant="${mono ? "mono" : "plain"}" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    (mono
      ? `<span class="wd-gutter" aria-hidden="true">${preview.map((_, i) => i + 1).join("\n") || "1"}</span>`
      : "") +
    (preview.length
      ? preview.map((l) => `<span class="wd-card-line${mono ? " mono" : ""}">${esc(l)}</span>`).join("")
      : `<span class="wd-card-line">${esc(copy("w.card.empty"))}</span>`) +
    `<span class="wd-micro">${esc(textEditorMicro(value))}</span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

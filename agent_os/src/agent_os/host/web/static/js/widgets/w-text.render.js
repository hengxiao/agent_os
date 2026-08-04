/* W-text 渲染面(docs/WIDGET-ARCH.md §1.1/§2.1;W5.1 新形态):
   ``renderTextEditor(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token
   (css/widgets.css),文案只经 copy()。
   效果(§2.1):mono/plain 两变体;mono 带行号槽;右下微标(行数/字数);
   dirty 左边条(.is-dirty);readonly 灰底(.is-readonly)。 */

import { copy } from "../themes.js";

/* 行数/字数微标(copy 六主题;统计是本地计算) */
export function textEditorMicro(value) {
  const lines = value ? value.split("\n").length : 0;
  return copy("w.text.count").replace("{lines}", String(lines)).replace("{chars}", String(value.length));
}

/* state → html(纯);state 面:{value, dirty, readonly, mono, rows, field, label} */
export function renderTextEditor(state) {
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

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

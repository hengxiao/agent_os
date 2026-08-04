/* W-json 渲染面(docs/WIDGET-ARCH.md §1.1/§2.2;W5.1 新形态):
   ``renderJsonEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.2):继承 W-text 结构;底部错误条(行号 + 摘录,--danger 边条);
   合法时绿勾;右上 format 钮。
   双形态(§1.4,W5.6):card = 状态行(✓ 合法 / 第 N 行错误红)+ 首行预览,
   无 format 钮;tab 完整交互。 */

import { copy } from "../themes.js";
import { renderTextEditor } from "./w-text.render.js";

/* state → html(纯);state 面 = W-text + {error:{line, message}|null} */
export function renderJsonEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _jsonCardHtml(state);
  const value = String(state.value ?? "");
  const err = state.error ?? null;
  return (
    renderTextEditor(state).replace('class="wd-text', 'class="wd-text wd-json') +
    `<button type="button" class="wd-format" data-wd-format="1">${esc(copy("w.json.format"))}</button>` +
    `<span class="wd-json-ok" aria-hidden="true"${err || !value.trim() ? " hidden" : ""}>✓</span>` +
    `<div class="wd-errbar" data-wd-errbar="1"${err ? "" : " hidden"}>` +
    (err ? `${esc(copy("w.json.errline").replace("{line}", String(err.line)))}: ${esc(err.message)}` : "") +
    `</div>`
  );
}

/* card 面(§1.4):状态行(✓ 合法绿 / ✕ 第 N 行错误红,显隐由逻辑面 check()
   局部刷新——与 tab 同一对 .wd-json-ok/.wd-errbar 钩子)+ 首行预览;无 format 钮 */
function _jsonCardHtml(state) {
  const value = String(state.value ?? "");
  const err = state.error ?? null;
  const firstLine = value ? value.split("\n")[0] : "";
  return (
    `<div class="wd-text wd-json wd-card${state.dirty ? " is-dirty" : ""}${state.readonly ? " is-readonly" : ""}"` +
    ` data-surface="card" role="button" tabindex="0" aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-json-ok"${err || !value.trim() ? " hidden" : ""}>✓ ${esc(copy("w.json.ok"))}</span>` +
    `<span class="wd-errbar" data-wd-errbar="1"${err ? "" : " hidden"}>` +
    (err ? `${esc(copy("w.json.errline").replace("{line}", String(err.line)))}: ${esc(err.message)}` : "") +
    `</span></span>` +
    `<span class="wd-card-line mono">${esc(value.trim() ? firstLine : copy("w.card.empty"))}</span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* W-json 渲染面(docs/WIDGET-ARCH.md §1.1/§2.2;W5.1 新形态):
   ``renderJsonEditor(state) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.2):继承 W-text 结构;底部错误条(行号 + 摘录,--danger 边条);
   合法时绿勾;右上 format 钮。 */

import { copy } from "../themes.js";
import { renderTextEditor } from "./w-text.render.js";

/* state → html(纯);state 面 = W-text + {error:{line, message}|null} */
export function renderJsonEditor(state) {
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

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

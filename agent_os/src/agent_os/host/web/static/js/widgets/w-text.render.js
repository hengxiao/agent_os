/* W-text 渲染面(docs/WIDGET-ARCH.md §1.1/§2.1;W5.1 自渲染;W6.1 按
   docs/WIDGET-DESIGN.md §3.1 重做视觉):
   ``renderTextEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token
   (css/widgets.css),文案只经 copy()。

   tab(§3.1):头部(field · lang + readonly 锁)+ 编辑区(mono 逐行行号槽
   [当前行/错误行是 class 槽,逻辑面局部刷新] + 当前行高亮槽 .wd-curline +
   textarea;光标 --live/选区 18%/占位弱色/焦点环全部 CSS 面)+ 右下微标胶囊
   (dirty 圆点 + hover 行列 tip)。
   card(W5.6 双形态 + §3.1):标题行(文档图标位 + 名称 500 + dirty 圆点 +
   「打开 →」hover 渐显)+ 前三行预览(末行渐隐)+ meta 行(行数·字数·相对时间)。

   结构槽位(textEditorTabHtml 的 slots):W-json 复用本结构——overlay = 语法
   着色层(pre.wd-hl 叠在透明文字 textarea 下),headSide = ✓ 徽标 + format 钮,
   foot = 错误条行;slots 不进 state,渲染仍纯。 */

import { copy } from "../themes.js";

/* 行数/字数微标(copy 六主题;统计是本地计算) */
export function textEditorMicro(value) {
  const lines = value ? value.split("\n").length : 0;
  return copy("w.text.count").replace("{lines}", String(lines)).replace("{chars}", String(value.length));
}

/* 相对时间(§3.1 card meta:行数 · 字数 · 3 分钟前;state.updated_at 缺省 →
   调用方省略该段——widget state 不编造时间戳) */
export function relTime(ts, now = Date.now() / 1000) {
  const d = Math.max(0, now - Number(ts));
  if (d < 60) return copy("w.time.now");
  if (d < 3600) return copy("w.time.min").replace("{n}", String(Math.floor(d / 60)));
  if (d < 86400) return copy("w.time.hour").replace("{n}", String(Math.floor(d / 3600)));
  return copy("w.time.day").replace("{n}", String(Math.floor(d / 86400)));
}

/* state → html(纯);state 面:{value, dirty, readonly, mono, rows, field, label,
   lang, updated_at?};surface="card" → 摘要卡,缺省 tab = 完整编辑器 */
export function renderTextEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _textCardHtml(state);
  return textEditorTabHtml(state);
}

/* tab 结构装配(纯;slots 见文件头:W-json 的着色层/徽标/错误条经此注入) */
export function textEditorTabHtml(state, slots = {}) {
  const mono = Boolean(state.mono);
  const readonly = Boolean(state.readonly);
  const value = String(state.value ?? "");
  const lines = value ? value.split("\n").length : 0;
  const headSide =
    (slots.headSide ?? "") + (readonly ? `<span class="wd-lock" aria-hidden="true">🔒</span>` : "");
  return (
    `<div class="wd-text${state.dirty ? " is-dirty" : ""}${readonly ? " is-readonly" : ""}"` +
    ` data-variant="${mono ? "mono" : "plain"}">` +
    (state.field || headSide
      ? `<div class="wd-text-head"><span class="wd-text-name">${esc(state.field ?? "")} · ${esc(state.lang ?? "plain")}</span>` +
        (headSide ? `<span class="wd-head-side">${headSide}</span>` : "") +
        `</div>`
      : "") +
    `<div class="wd-editor">` +
    (mono
      ? `<div class="wd-gutter" aria-hidden="true"><div class="wd-gutter-in">` +
        Array.from({ length: Math.max(lines, 1) }, (_, i) => `<span class="wd-gl" data-line="${i + 1}">${i + 1}</span>`).join("") +
        `</div></div>`
      : "") +
    `<div class="wd-code">` +
    (slots.overlay ?? "") +
    (mono ? `<div class="wd-curline" aria-hidden="true" hidden></div>` : "") +
    `<textarea class="input${mono ? " mono" : ""}${slots.textareaClass ?? ""}" rows="${Number(state.rows) || 6}"` +
    ` data-field="${esc(state.field ?? "")}" spellcheck="false"` +
    ` aria-label="${esc(state.label ?? state.field ?? "")}"${readonly ? " readonly" : ""}` +
    // mono 变体不软换行(行号槽/着色层对齐的前提,VS Code 同律;§3.1 行号基线对齐)
    (mono ? ` wrap="off"` : "") +
    ` placeholder="${esc(copy("w.text.ph"))}">` +
    `${esc(value)}</textarea>` +
    `<span class="wd-micro-box">` +
    `<span class="wd-micro-dot" aria-hidden="true"${state.dirty ? "" : " hidden"}></span>` +
    `<span class="wd-micro">${esc(textEditorMicro(value))}</span>` +
    `<span class="wd-micro-tip" aria-hidden="true"></span>` +
    `</span>` +
    `</div></div>` +
    (slots.foot ?? "") +
    `</div>`
  );
}

/* card 面(§3.1):标题行(图标 + 名称 + dirty 点 + 「打开 →」)+ 前三行预览
   (末行渐隐 .wd-fade)+ meta 行(行数·字数·相对时间);整卡 = open 入口 */
function _textCardHtml(state) {
  const mono = Boolean(state.mono);
  const value = String(state.value ?? "");
  const lines = value ? value.split("\n") : [];
  const preview = lines.slice(0, 3);
  const meta = [textEditorMicro(value)];
  if (state.updated_at) meta.push(relTime(state.updated_at));
  return (
    `<div class="wd-text wd-card${state.dirty ? " is-dirty" : ""}${state.readonly ? " is-readonly" : ""}"` +
    ` data-surface="card" data-variant="${mono ? "mono" : "plain"}" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-doc-ico" aria-hidden="true"></span>` +
    `<span class="wd-card-name">${esc(state.label ?? state.field ?? "")}</span>` +
    `<span class="wd-card-dot" aria-hidden="true"${state.dirty ? "" : " hidden"}></span>` +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    (preview.length
      ? preview
          .map((l, i) => `<span class="wd-card-line${mono ? " mono" : ""}${i === 2 ? " wd-fade" : ""}">${esc(l)}</span>`)
          .join("")
      : `<span class="wd-card-line">${esc(copy("w.card.empty"))}</span>`) +
    `<span class="wd-card-meta">${esc(meta.join(" · "))}</span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

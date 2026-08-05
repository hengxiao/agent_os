/* W-json 渲染面(docs/WIDGET-ARCH.md §1.1/§2.2;W5.1 自渲染;W6.1 按
   docs/WIDGET-DESIGN.md §3.2 重做视觉):
   ``renderJsonEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。

   tab(§3.2):复用 W-text 结构槽位——**语法着色**经 overlay(pre.wd-hl 叠在
   透明文字的 textarea 之下,着色纯渲染层、不进 state):key=--live /
   string=--ok / number·bool=--warn / 标点弱色;括号匹配 <mark> 浅底由逻辑面
   经 jsonHighlightHtml 的 matches 注入;错误三件套 = 行号槽红点(.wd-gl.is-err,
   逻辑面 check() 局部刷新)+ 行内红波浪(.wd-hl-line.is-err)+ 底部错误条
   (✕ + 行级文案 + 「点击跳转 →」提示,点击跳转折旧在逻辑面);合法 ✓ 绿徽标
   (胶囊,带键数)+ format 钮(ghost,平时 40% 透明)进头部右侧。
   card(§3.2):状态行(✓ 合法 · N 键 / ✕ 第 N 行)+ 首行 mono 预览 +
   meta(大小 · 键数 · 相对时间);错误态整卡左边条 --danger(.is-err)。 */

import { copy } from "../themes.js";
import { relTime, textEditorTabHtml } from "./w-text.render.js";

/* state → html(纯);state 面 = W-text + {error:{line, message}|null} */
export function renderJsonEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _jsonCardHtml(state);
  const value = String(state.value ?? "");
  const err = state.error ?? null;
  const valid = !err && value.trim();
  return textEditorTabHtml({ lang: "json", ...state }, {
    textareaClass: " wd-json-input",
    overlay:
      `<pre class="wd-hl" aria-hidden="true">` +
      jsonHighlightHtml(value, { errorLine: err?.line ?? 0 }) +
      `\n</pre>`,
    headSide:
      `<span class="wd-json-ok"${valid ? "" : " hidden"}>✓ ${esc(jsonOkText(value))}</span>` +
      `<button type="button" class="wd-format" data-wd-format="1"${err ? " disabled" : ""}` +
      `${err ? ` title="${esc(copy("w.json.fmt_dis"))}"` : ""}>${esc(copy("w.json.format"))}</button>`,
    foot:
      `<div class="wd-foot">` +
      `<div class="wd-errbar" data-wd-errbar="1"${err ? "" : " hidden"}>` +
      (err ? `${esc(copy("w.json.errline").replace("{line}", String(err.line)))}: ${esc(err.message)}` : "") +
      `</div>` +
      `<span class="wd-err-hint"${err ? "" : " hidden"}>${esc(copy("w.json.jump"))}</span>` +
      `</div>`,
  }).replace('class="wd-text', 'class="wd-text wd-json');
}

/* 合法徽标文案:「JSON 合法 · {n} 键」(键数递归统计;非法/空 → 调用方藏徽标) */
export function jsonOkText(text) {
  return copy("w.json.okn").replace("{n}", String(jsonKeyCount(text) ?? 0));
}

/* 键数统计(递归 object 键;非法 JSON → null) */
export function jsonKeyCount(text) {
  let doc;
  try {
    doc = JSON.parse(String(text ?? ""));
  } catch {
    return null;
  }
  let n = 0;
  const walk = (v) => {
    if (Array.isArray(v)) return v.forEach(walk);
    if (v && typeof v === "object") {
      const ks = Object.keys(v);
      n += ks.length;
      ks.forEach((k) => walk(v[k]));
    }
  };
  walk(doc);
  return n;
}

/* JSON 语法着色(纯;逐行出 .wd-hl-line,token 跨度不破行——JSON 字符串
   不含字面换行,行切分安全)。matches = 括号匹配的绝对字符下标(逻辑面
   matchBrace 给出),该字符包 <mark class="wd-brace"> 浅底。 */
export function jsonHighlightHtml(text, { errorLine = 0, matches = [] } = {}) {
  const mset = new Set(matches);
  const html = _hl(String(text ?? ""), mset);
  return html
    .split("\n")
    .map(
      (line, i) =>
        `<div class="wd-hl-line${i + 1 === errorLine ? " is-err" : ""}" data-line="${i + 1}">${line}</div>`
    )
    .join("");
}

/* 逐字符扫描着色:tolerant(未闭合串/半截 token 照样出,不抛) */
function _hl(text, mset) {
  let out = "";
  let i = 0;
  const pushSpan = (cls, s) => {
    out += `<span class="${cls}">${s}</span>`;
  };
  while (i < text.length) {
    const ch = text[i];
    if (mset.has(i)) {
      out += `<mark class="wd-brace">${esc(ch)}</mark>`;
      i += 1;
      continue;
    }
    if (ch === '"') {
      const m = /^"(?:\\.|[^"\\])*"?/.exec(text.slice(i));
      const tok = m[0];
      const isKey = /^\s*:/.test(text.slice(i + tok.length));
      pushSpan(isKey ? "wd-tk-key" : "wd-tk-str", esc(tok));
      i += tok.length;
      continue;
    }
    if (ch === "-" || (ch >= "0" && ch <= "9")) {
      const m = /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(i));
      if (m) {
        pushSpan("wd-tk-num", esc(m[0]));
        i += m[0].length;
        continue;
      }
    }
    const lit = /^(true|false|null)\b/.exec(text.slice(i));
    if (lit) {
      pushSpan("wd-tk-num", lit[0]);
      i += lit[0].length;
      continue;
    }
    if ("{}[],".includes(ch) || ch === ":") {
      pushSpan("wd-tk-pn", esc(ch));
      i += 1;
      continue;
    }
    out += esc(ch); // 空白/其他:原样(弱色由 pre 底色承)
    i += 1;
  }
  return out;
}

/* card 面(§3.2):状态行(✓ 合法胶囊 / ✕ 错误行)+ 首行 mono 预览 +
   meta(大小 · 键数 · 相对时间);错误态整卡左边条 --danger(.is-err);
   .wd-json-ok/.wd-errbar 钩子与逻辑面 check() 的局部刷新同构(不重渲) */
function _jsonCardHtml(state) {
  const value = String(state.value ?? "");
  const err = state.error ?? null;
  const valid = !err && value.trim();
  const firstLine = value ? value.split("\n")[0] : "";
  const keys = jsonKeyCount(value);
  const meta = [_fmtSize(value)];
  if (keys !== null) meta.push(copy("w.json.keys").replace("{n}", String(keys)));
  if (state.updated_at) meta.push(relTime(state.updated_at));
  return (
    `<div class="wd-text wd-json wd-card${err ? " is-err" : ""}${state.dirty ? " is-dirty" : ""}${state.readonly ? " is-readonly" : ""}"` +
    ` data-surface="card" role="button" tabindex="0" aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-json-ok"${valid ? "" : " hidden"}>✓ ${esc(jsonOkText(value))}</span>` +
    `<span class="wd-errbar" data-wd-errbar="1"${err ? "" : " hidden"}>` +
    (err ? `${esc(copy("w.json.errline").replace("{line}", String(err.line)))}: ${esc(err.message)}` : "") +
    `</span>` +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    `<span class="wd-card-line mono">${esc(value.trim() ? firstLine : copy("w.card.empty"))}</span>` +
    `<span class="wd-card-meta">${esc(meta.join(" · "))}</span>` +
    `</div>`
  );
}

/* 字节大小(UTF-8;B/KB 是单位符号,非文案) */
function _fmtSize(text) {
  const n = new TextEncoder().encode(String(text ?? "")).length;
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

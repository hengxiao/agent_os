/* W-form 渲染面(docs/WIDGET-ARCH.md §1.1/§2.5;W5.2):
   ``renderFormEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。
   效果(§2.5):label + required 星标(--danger);错误字段红边(.wd-field-err)
   + 行内错误语;嵌套 object 缩进分组(fieldset);数组项编辑器(逐项 + ✕ + 添加)。 */

import { copy } from "../themes.js";

function _get(values, path) {
  return path.split(".").reduce((o, k) => o?.[k], values);
}

function _join(prefix, key) {
  return prefix ? `${prefix}.${key}` : key;
}

/* 单字段渲染(递归;spec._required 由调用方按父级 required 注入) */
function _fieldHtml(state, spec, fPath, label) {
  const value = _get(state.values, fPath);
  const err = state.errors?.[fPath];
  const reqMark = spec._required ? `<span class="wd-req" aria-hidden="true"> *</span>` : "";
  const errHtml = err ? `<span class="wd-errbar">${esc(err)}</span>` : "";
  const labelHtml = `<span class="lab-label">${esc(label ?? fPath)}${reqMark}</span>`;
  const errCls = err ? " wd-field-err" : "";
  if (spec?.type === "boolean") {
    return (
      `<label class="lab-field${errCls}">${labelHtml}` +
      `<input type="checkbox" data-f="${esc(fPath)}"${value ? " checked" : ""}></label>` + errHtml
    );
  }
  if (spec?.type === "enum" || spec?.enum) {
    const opts = spec.enum ?? spec.options ?? [];
    return (
      `<label class="lab-field${errCls}">${labelHtml}` +
      `<select data-f="${esc(fPath)}">` +
      opts.map((o) => `<option value="${esc(o)}"${o === value ? " selected" : ""}>${esc(o)}</option>`).join("") +
      `</select></label>` + errHtml
    );
  }
  if (spec?.type === "object") {
    const inner = Object.entries(spec.properties ?? {})
      .map(([k, sub]) => {
        const subSpec = { ...sub, _required: (spec.required ?? []).includes(k) };
        return _fieldHtml(state, subSpec, _join(fPath, k), k);
      })
      .join("");
    return `<fieldset class="wd-nest${errCls}"><legend>${esc(label ?? fPath)}</legend>${inner}</fieldset>` + errHtml;
  }
  if (spec?.type === "array") {
    const items = Array.isArray(value) ? value : [];
    const rows = items
      .map((item, i) => {
        const itemErr = state.errors?.[`${fPath}/${i}`];
        return (
          `<div class="wd-arr-row">` +
          `<input class="input" data-f="${esc(fPath)}/${i}" value="${esc(String(item ?? ""))}">` +
          `<button class="wd-row-x" data-f-del="${esc(fPath)}/${i}" aria-label="✕">✕</button>` +
          (itemErr ? `<span class="wd-errbar">${esc(itemErr)}</span>` : "") +
          `</div>`
        );
      })
      .join("");
    return (
      `<div class="wd-arr${errCls}">${labelHtml}${rows}` +
      `<button class="wd-add" data-f-add="${esc(fPath)}">${esc(copy("w.form.additem"))}</button></div>` + errHtml
    );
  }
  const type = spec?.type === "integer" || spec?.type === "number" ? "number" : "text";
  return (
    `<label class="lab-field${errCls}">${labelHtml}` +
    `<input class="input" type="${type}" data-f="${esc(fPath)}" value="${esc(String(value ?? ""))}"></label>` + errHtml
  );
}

/* state → html(纯);state 面:{values, errors, schema}
   双形态(§1.4):surface="card" → 必填完成度徽标(已填 x/y)+ 缺失必填名
   (≤2 个,溢出 +N;无必填时给字段计数);无任何表单控件 */
export function renderFormEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _formCardHtml(state);
  const schema = state.schema ?? {};
  return (
    `<div class="wd-form" role="form">` +
    Object.entries(schema.properties ?? {})
      .map(([k, spec]) =>
        _fieldHtml(state, { ...spec, _required: (schema.required ?? []).includes(k) }, k, k)
      )
      .join("") +
    `</div>`
  );
}

/* card 面(§1.4):必填完成度 + 缺失警示(摘要只看顶层 required;嵌套校验仍归 tab) */
function _formCardHtml(state) {
  const schema = state.schema ?? {};
  const values = state.values ?? {};
  const required = schema.required ?? [];
  const missing = required.filter((k) => values[k] === undefined || values[k] === null || values[k] === "");
  const head = required.length
    ? `<span class="wd-badge">${esc(
        copy("w.card.required")
          .replace("{x}", String(required.length - missing.length))
          .replace("{y}", String(required.length))
      )}</span>` +
      (missing.length
        ? `<span class="wd-badge" data-tone="warn">${esc(
            copy("w.card.missing").replace(
              "{names}",
              missing.slice(0, 2).join("、") + (missing.length > 2 ? ` +${missing.length - 2}` : "")
            )
          )}</span>`
        : "")
    : `<span class="wd-badge">${esc(
        copy("w.card.items").replace("{n}", String(Object.keys(schema.properties ?? {}).length))
      )}</span>`;
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">${head}</span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

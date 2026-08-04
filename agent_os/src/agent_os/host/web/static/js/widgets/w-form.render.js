/* W-form 渲染面(docs/WIDGET-ARCH.md §1.1/§2.5;W5.2):
   ``renderFormEditor(state) -> html`` **纯函数**——无副作用、不写 state、
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

/* state → html(纯);state 面:{values, errors, schema} */
export function renderFormEditor(state) {
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

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

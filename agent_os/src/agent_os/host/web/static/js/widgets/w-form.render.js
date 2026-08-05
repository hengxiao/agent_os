/* W-form 渲染面(docs/WIDGET-ARCH.md §1.1/§2.5;W5.2 自渲染;W6.2 按
   docs/WIDGET-DESIGN.md §3.5 重做视觉):
   ``renderFormEditor(state, {surface}) -> html`` **纯函数**——无副作用、不写 state、
   不发事件、不调后端;只产出语义 class(``wd-*``),视觉全走契约 token。

   tab(§3.5):label 上置(12px/500)+ 必填红 *;帮助文字 11px 弱色贴控件下
   (spec.description);六型控件同族(32px/同圆角/同焦点环):text input /
   number stepper(− 值 +,钳 min/max 在逻辑面)/ boolean iOS switch(role=
   switch,120ms 滑动)/ enum chips 单选组(radiogroup)/ 嵌套 object 卡片化
   分组(标题 500 + 描述弱色)/ 数组项小卡(拖序柄 + 输入 + ✕)+ 虚线添加;
   错误 = 控件红边 + 行内 ⚠ 人话;操作行:reset(ghost,dirty 才可用)+
   live 圆点「有未保存改动」+ 主操作实心居右(emit submit 在逻辑面)。
   card(§3.5):必填完成度进度条(4px --live)+「必填 x/y」+ 缺失字段名
   chips(≤2,溢出 +N)+ meta(schema · N 字段 · M 嵌套组);无任何控件。 */

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
  const help = spec?.description ? `<span class="wd-help">${esc(spec.description)}</span>` : "";
  const errCls = err ? " wd-field-err" : "";
  if (spec?.type === "boolean") {
    // iOS 式 switch(§3.5):开 = --live 居右,关 = --bg-3 居左,120ms 滑动
    return (
      `<label class="lab-field${errCls}">${labelHtml}` +
      `<button type="button" class="wd-switch" role="switch" aria-checked="${value ? "true" : "false"}"` +
      ` data-f="${esc(fPath)}" aria-label="${esc(label ?? fPath)}">` +
      `<span class="wd-switch-knob" aria-hidden="true"></span></button>` +
      help +
      `</label>` + errHtml
    );
  }
  if (spec?.type === "enum" || spec?.enum) {
    // chips 单选组(§3.5;选中 = --live 浅底 + --live 字)
    const opts = spec.enum ?? spec.options ?? [];
    return (
      `<div class="lab-field${errCls}">${labelHtml}` +
      `<span class="wd-chips" role="radiogroup" aria-label="${esc(label ?? fPath)}">` +
      opts
        .map(
          (o) =>
            `<button type="button" class="wd-chip" role="radio" aria-checked="${o === value}"` +
            ` data-f-chip="${esc(fPath)}:${esc(o)}" data-on="${o === value ? "1" : "0"}">${esc(o)}</button>`
        )
        .join("") +
      `</span>` +
      help +
      `</div>` + errHtml
    );
  }
  if (spec?.type === "object") {
    // 嵌套 object = 卡片化分组(§3.5:标题 500 + 描述弱色 + 内边距)
    const inner = Object.entries(spec.properties ?? {})
      .map(([k, sub]) => {
        const subSpec = { ...sub, _required: (spec.required ?? []).includes(k) };
        return _fieldHtml(state, subSpec, _join(fPath, k), k);
      })
      .join("");
    return (
      `<fieldset class="wd-nest${errCls}"><legend>${esc(label ?? fPath)}</legend>` +
      help +
      inner +
      `</fieldset>` + errHtml
    );
  }
  if (spec?.type === "array") {
    // 数组项 = 小卡(拖序柄 + 输入 + ✕)+ 虚线添加(§3.5)
    const items = Array.isArray(value) ? value : [];
    const rows = items
      .map((item, i) => {
        const itemErr = state.errors?.[`${fPath}/${i}`];
        return (
          `<div class="wd-arr-row" draggable="true" data-f-arr="${esc(fPath)}" data-idx="${i}">` +
          `<span class="wd-drag" aria-hidden="true">⠿</span>` +
          `<input class="input" data-f="${esc(fPath)}/${i}" value="${esc(String(item ?? ""))}"` +
          ` aria-label="${esc(fPath)} ${i + 1}">` +
          `<button class="wd-row-x" data-f-del="${esc(fPath)}/${i}" aria-label="✕">✕</button>` +
          (itemErr ? `<span class="wd-errbar">${esc(itemErr)}</span>` : "") +
          `</div>`
        );
      })
      .join("");
    return (
      `<div class="wd-arr${errCls}">${labelHtml}${rows}` +
      `<button class="wd-add" data-f-add="${esc(fPath)}">+ ${esc(copy("w.form.additem"))}</button>` +
      help +
      `</div>` + errHtml
    );
  }
  if (spec?.type === "integer" || spec?.type === "number") {
    // number stepper(§3.5):− 值 + 同族 32px
    return (
      `<label class="lab-field${errCls}">${labelHtml}` +
      `<span class="wd-stepwrap">` +
      `<button type="button" class="wd-step" data-f-step="${esc(fPath)}:-1" aria-label="−">−</button>` +
      `<input class="input" type="number" data-f="${esc(fPath)}" value="${esc(String(value ?? ""))}">` +
      `<button type="button" class="wd-step" data-f-step="${esc(fPath)}:1" aria-label="+">+</button>` +
      `</span>` +
      help +
      `</label>` + errHtml
    );
  }
  return (
    `<label class="lab-field${errCls}">${labelHtml}` +
    `<input class="input" type="text" data-f="${esc(fPath)}" value="${esc(String(value ?? ""))}"></label>` +
    help + errHtml
  );
}

/* state → html(纯);state 面:{values, errors, schema, dirty?, title?} */
export function renderFormEditor(state, { surface = "tab" } = {}) {
  if (surface === "card") return _formCardHtml(state);
  const schema = state.schema ?? {};
  const props = Object.entries(schema.properties ?? {});
  const dirty = Boolean(state.dirty);
  if (!props.length) {
    // 空 schema(§1.5 空态通例;主操作不落——widget 不出海,见 §3.5 实现注)
    return (
      `<div class="wd-form" role="form">` +
      (state.title ? `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title)} · form</span></div>` : "") +
      `<div class="wd-empty-box"><span class="wd-empty-ico" aria-hidden="true">✎</span>` +
      `<span class="wd-empty-guide">${esc(copy("w.form.empty"))}</span></div>` +
      `</div>`
    );
  }
  return (
    `<div class="wd-form" role="form">` +
    (state.title ? `<div class="wd-pane-head"><span class="wd-pane-title">${esc(state.title)} · form</span></div>` : "") +
    `<div class="wd-form-grid">` +
    props
      .map(([k, spec]) =>
        _fieldHtml(state, { ...spec, _required: (schema.required ?? []).includes(k) }, k, k)
      )
      .join("") +
    `</div>` +
    `<div class="wd-form-foot">` +
    `<button type="button" class="wd-btn-ghost" data-f-reset="1"${dirty ? "" : " disabled"}>${esc(copy("w.form.reset"))}</button>` +
    `<span class="wd-form-dirty"${dirty ? "" : " hidden"}><span class="wd-card-dot" aria-hidden="true"></span>${esc(copy("w.form.dirty"))}</span>` +
    `<button type="button" class="wd-btn-primary" data-f-submit="1">${esc(copy("w.form.save"))}</button>` +
    `</div>` +
    `</div>`
  );
}

/* card 面(§3.5):完成度进度条 + 必填 x/y + 缺失 chips(≤2 溢出 +N)+
   schema meta;摘要只看顶层 required(嵌套校验仍归 tab) */
function _formCardHtml(state) {
  const schema = state.schema ?? {};
  const values = state.values ?? {};
  const required = schema.required ?? [];
  const missing = required.filter((k) => values[k] === undefined || values[k] === null || values[k] === "");
  const props = Object.keys(schema.properties ?? {});
  const nested = Object.values(schema.properties ?? {}).filter((s) => s?.type === "object").length;
  const filled = required.length - missing.length;
  const pct = required.length ? Math.round((filled / required.length) * 100) : 0;
  return (
    `<div class="wd-card" data-surface="card" role="button" tabindex="0"` +
    ` aria-label="${esc(copy("w.card.open"))}">` +
    `<span class="wd-card-head">` +
    `<span class="wd-card-name">${esc(state.title ? `${state.title} · form` : "form")}</span>` +
    `<span class="wd-card-go">${esc(copy("w.card.go"))}</span>` +
    `</span>` +
    (required.length
      ? `<span class="wd-progress" aria-hidden="true"><span class="wd-progress-in" style="width:${pct}%"></span></span>` +
        `<span class="wd-card-line">${esc(copy("w.card.required").replace("{x}", String(filled)).replace("{y}", String(required.length)))}</span>` +
        (missing.length
          ? `<span class="wd-card-head">` +
            missing.slice(0, 2).map((m) => `<span class="wd-chip">${esc(m)}</span>`).join("") +
            (missing.length > 2 ? `<span class="wd-chip">+${missing.length - 2}</span>` : "") +
            `</span>`
          : "")
      : `<span class="wd-card-line">${esc(copy("w.card.items").replace("{n}", String(props.length)))}</span>`) +
    `<span class="wd-card-meta">${esc(copy("w.form.meta").replace("{n}", String(props.length)).replace("{m}", String(nested)))}</span>` +
    `</div>`
  );
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

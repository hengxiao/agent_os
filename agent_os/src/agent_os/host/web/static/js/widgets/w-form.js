/* W-form 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.5;W5.2 新形态:自渲染;
   W6.2 视觉按 docs/WIDGET-DESIGN.md §3.5)。

   state{values, errors, schema, dirty, title?};
   actions 全 local:set_field/validate/reset;
   字段类型:string/number·integer(stepper,钳 min/max)/boolean(switch)/
   enum(chips 单选组)/嵌套 object(卡片分组)/array(小卡:拖序柄+✕+虚线添加);
   required 星标;默认值与 skeletonFromSchema 一致(复用 launch-dialog 纯函数);
   errors 按字段路径即时提示(输入即清该字段错);dirty 跟踪(values ↔ 骨架
   深比较,驱动操作行);submit 钮 = validate → emit submit / 聚焦首个错误。
   铁律:本文件不拼 HTML(渲染全在 w-form.render.js);零 fetch;事件上行;
   监听一律委托在 host(重渲会换掉子元素)。 */

import { copy } from "../themes.js";
import { skeletonFromSchema } from "../components/launch-dialog.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderFormEditor } from "./w-form.render.js";

export const FORM_EDITOR_DEF = registerWidgetDef({
  kind: "schema-form",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { values: {}, errors: {}, schema: {}, dirty: false },
  actions: [
    { id: "set_field", exec: "local", args_input: { path: { type: "string" } } },
    { id: "validate", exec: "local" },
    { id: "reset", exec: "local" },
  ],
  events: ["change", "submit", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "form", keys: ["Enter"] },
  surfaces: ["card", "tab"],
  render: renderFormEditor, // W5.2:render 面进 def(registry 校验形态)
});

const _DND_MIME = "application/x-agent-os-widget";

const _typeOf = (v) =>
  v === null ? "null" : Array.isArray(v) ? "array" : Number.isInteger(v) ? "integer" : typeof v;

/* 字段级校验(values ↔ schema;errors {path: message}。
   轻量面:type/required/integer min-max;硬校验永远在服务端,§2 同哲学) */
export function validateValues(values, schema, prefix = "") {
  const errors = {};
  const obj = values ?? {};
  for (const key of schema?.required ?? []) {
    const v = obj[key];
    if (v === undefined || v === null || v === "") errors[joinPath(prefix, key)] = copy("w.form.required");
  }
  for (const [key, spec] of Object.entries(schema?.properties ?? {})) {
    const path = joinPath(prefix, key);
    const v = obj[key];
    if (v === undefined || v === "") continue;
    if (spec?.type === "object") {
      Object.assign(errors, validateValues(v ?? {}, spec, path));
      continue;
    }
    if (spec?.type && v !== null && _typeOf(v) !== spec.type && !(spec.type === "number" && _typeOf(v) === "integer")) {
      errors[path] = `应为 ${spec.type}(当前 ${_typeOf(v)})`;
      continue;
    }
    if ((spec?.type === "integer" || spec?.type === "number") && typeof v === "number") {
      if (typeof spec.minimum === "number" && v < spec.minimum) errors[path] = `不能小于 ${spec.minimum}`;
      if (typeof spec.maximum === "number" && v > spec.maximum) errors[path] = `不能大于 ${spec.maximum}`;
    }
    if (spec?.type === "array" && Array.isArray(v)) {
      v.forEach((item, i) => {
        if (spec.items?.type && _typeOf(item) !== spec.items.type) {
          errors[`${path}/${i}`] = `应为 ${spec.items.type}`;
        }
      });
    }
  }
  return errors;
}

function joinPath(prefix, key) {
  return prefix ? `${prefix}.${key}` : key;
}

function _get(values, path) {
  return path.split(".").reduce((o, k) => o?.[k], values);
}
function _set(values, path, value) {
  const segs = path.split(".");
  let node = values;
  for (const k of segs.slice(0, -1)) node = node[k] ??= {};
  node[segs.at(-1)] = value;
}

/* 挂进宿主:schema + 初始 values(缺省 = skeletonFromSchema 骨架,
   与 launch-dialog 默认值一致)+ title(面板头,可选);返回 widget
   (values() 取提交载荷)。
   W6.2(§3.5):dirty 跟踪(values ↔ 初始骨架深比较,驱动 reset 可用态与
   「有未保存改动」圆点,局部刷新不重渲);submit = validate → 通过 emit
   submit / 不通过滚动聚焦首个错误;数组项拖序(§15 envelope)。 */
export function mountFormEditor(host, { schema, values = null, title = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(FORM_EDITOR_DEF, {
    path,
    state: {
      schema: schema ?? {},
      values: values ?? skeletonFromSchema(schema),
      errors: {},
      title,
      dirty: false,
    },
    onRegister,
    onUnregister,
  });
  const defaults = JSON.parse(JSON.stringify(widget.state.values));

  const render = () => {
    host.innerHTML = renderFormEditor(widget.state, { surface });
  };
  const _changed = () => widget.emit("change", { values: widget.state.values });
  /* dirty 跟踪(§3.5):values ↔ 初始骨架深比较;操作行局部刷新(不重渲) */
  const _syncDirty = () => {
    widget.state.dirty = JSON.stringify(widget.state.values) !== JSON.stringify(defaults);
    const rst = host.querySelector("[data-f-reset]");
    if (rst) rst.disabled = !widget.state.dirty;
    const d = host.querySelector(".wd-form-dirty");
    if (d) d.hidden = !widget.state.dirty;
  };

  widget.set_field = (fPath, value) => {
    _set(widget.state.values, fPath, value);
    delete widget.state.errors[fPath]; // 输入即清该字段错(即时反馈)
    _syncDirty();
    _changed();
  };
  widget.validate = () => {
    widget.state.errors = validateValues(widget.state.values, widget.state.schema);
    render();
    return Object.keys(widget.state.errors).length === 0;
  };
  widget.reset = () => {
    widget.state.values = JSON.parse(JSON.stringify(defaults));
    widget.state.errors = {};
    widget.state.dirty = false;
    render();
    _changed();
  };
  widget.values = () => widget.state.values;

  /* submit(§3.5):validate → 通过 emit submit;不通过滚动聚焦首个错误(行内,不弹窗) */
  const _submit = () => {
    if (!widget.validate()) {
      const first = host.querySelector(".wd-field-err");
      first?.scrollIntoView?.();
      first?.querySelector?.("input")?.focus?.();
      return;
    }
    widget.emit("submit", { values: widget.state.values });
  };

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("input", (e) => {
    const f = e.target.closest("[data-f]")?.dataset.f;
    if (!f) return;
    const spec = _specOf(widget.state.schema, f);
    let value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    if ((spec?.type === "integer" || spec?.type === "number") && value !== "") value = Number(value);
    widget.set_field(f, value);
  });
  host.addEventListener("change", (e) => {
    const f = e.target.closest("select[data-f]")?.dataset.f;
    if (f) widget.set_field(f, e.target.value);
  });
  host.addEventListener("click", (e) => {
    // 操作行(§3.5):reset(dirty 才可用)/ submit
    if (e.target.closest("[data-f-reset]")) return widget.reset();
    if (e.target.closest("[data-f-submit]")) return _submit();
    // boolean switch(§3.5):点击翻转,局部刷 aria(不重渲,焦点不丢)
    const sw = e.target.closest(".wd-switch");
    if (sw) {
      const f = sw.dataset.f;
      const next = !Boolean(_get(widget.state.values, f));
      widget.set_field(f, next);
      sw.setAttribute("aria-checked", String(next));
      return;
    }
    // enum chips 单选组(§3.5):组内局部翻转
    const chip = e.target.closest("[data-f-chip]");
    if (chip) {
      const raw = chip.dataset.fChip;
      const sep = raw.indexOf(":");
      widget.set_field(raw.slice(0, sep), raw.slice(sep + 1));
      for (const c of [...(chip.closest(".wd-chips")?.querySelectorAll?.("[data-f-chip]") ?? [])]) {
        c.dataset.on = c === chip ? "1" : "0";
        c.setAttribute?.("aria-checked", String(c === chip));
      }
      return;
    }
    // number stepper(§3.5):步进 + 钳 min/max,局部写回 input(不重渲)
    const step = e.target.closest("[data-f-step]");
    if (step) {
      const raw = step.dataset.fStep;
      const sep = raw.lastIndexOf(":");
      const f = raw.slice(0, sep);
      const dir = Number(raw.slice(sep + 1));
      const spec = _specOf(widget.state.schema, f);
      let next = (Number(_get(widget.state.values, f)) || 0) + dir;
      if (typeof spec?.minimum === "number") next = Math.max(spec.minimum, next);
      if (typeof spec?.maximum === "number") next = Math.min(spec.maximum, next);
      widget.set_field(f, next);
      const input = host.querySelector(`[data-f="${f}"]`);
      if (input) input.value = String(next);
      return;
    }
    const add = e.target.closest("[data-f-add]");
    if (add) {
      const arrPath = add.dataset.fAdd;
      const arr = _get(widget.state.values, arrPath) ?? [];
      const itemSpec = _specOf(widget.state.schema, arrPath)?.items;
      _set(widget.state.values, arrPath, [...arr, itemSpec?.type === "number" || itemSpec?.type === "integer" ? 0 : ""]);
      _syncDirty();
      render();
      return _changed();
    }
    const del = e.target.closest("[data-f-del]");
    if (del) {
      const [arrPath, i] = del.dataset.fDel.split("/");
      const arr = (_get(widget.state.values, arrPath) ?? []).filter((_, idx) => idx !== Number(i));
      _set(widget.state.values, arrPath, arr);
      _syncDirty();
      render();
      return _changed();
    }
  });
  // 数组项拖序(§3.5;§15 envelope 同构,只收本控件行)
  host.addEventListener("dragstart", (e) => {
    const row = e.target.closest("[data-f-arr]");
    if (!row) return;
    e.dataTransfer?.setData(_DND_MIME, JSON.stringify({
      source: `${path}/arr/${row.dataset.fArr}/${row.dataset.idx}`,
      source_kind: "form-arr-item", position: {},
    }));
  });
  host.addEventListener("dragover", (e) => {
    if ([...(e.dataTransfer?.types ?? [])].includes(_DND_MIME)) e.preventDefault();
  });
  host.addEventListener("drop", (e) => {
    let env = null;
    try {
      env = JSON.parse(e.dataTransfer?.getData(_DND_MIME) ?? "null");
    } catch {
      env = null;
    }
    if (!env || env.source_kind !== "form-arr-item") return;
    e.preventDefault();
    const segs = String(env.source ?? "").split("/");
    const from = Number(segs.pop());
    const arrPath = segs.pop();
    const target = e.target.closest("[data-f-arr]");
    if (!target || target.dataset.fArr !== arrPath) return; // 只收同数组行
    const to = Number(target.dataset.idx);
    const arr = [...(_get(widget.state.values, arrPath) ?? [])];
    if (from < 0 || from >= arr.length || to === from) return;
    const [moved] = arr.splice(from, 1);
    arr.splice(to, 0, moved);
    _set(widget.state.values, arrPath, arr);
    _syncDirty();
    render();
    _changed();
  });
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

function _specOf(schema, fPath) {
  const segs = fPath.replaceAll("/", ".0.").split(".");
  let node = schema;
  for (const s of segs) {
    if (s === "0") node = node?.items;
    else node = node?.properties?.[s];
  }
  return node;
}

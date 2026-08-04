/* W-form 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.5;W5.2 新形态:自渲染)。

   state{values, errors, schema};
   actions 全 local:set_field/validate/reset;
   字段类型:string/number/integer(minimum/maximum 约束)/boolean/enum/
   嵌套 object(缩进组)/array(数组项编辑器:逐项 input + ✕ + 添加);
   required 星标;默认值与 skeletonFromSchema 一致(复用 launch-dialog 纯函数);
   errors 按字段路径即时提示(输入即清该字段错)。
   铁律:本文件不拼 HTML(渲染全在 w-form.render.js);零 fetch;事件上行;
   监听一律委托在 host(重渲会换掉子元素)。 */

import { copy } from "../themes.js";
import { skeletonFromSchema } from "../components/launch-dialog.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";
import { renderFormEditor } from "./w-form.render.js";

export const FORM_EDITOR_DEF = registerWidgetDef({
  kind: "schema-form",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { values: {}, errors: {}, schema: {} },
  actions: [
    { id: "set_field", exec: "local", args_input: { path: { type: "string" } } },
    { id: "validate", exec: "local" },
    { id: "reset", exec: "local" },
  ],
  events: ["change", "submit"],
  aria: { role: "form", keys: ["Enter"] },
  surfaces: ["card", "tab"],
  render: renderFormEditor, // W5.2:render 面进 def(registry 校验形态)
});

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
   与 launch-dialog 默认值一致);返回 widget(values() 取提交载荷) */
export function mountFormEditor(host, { schema, values = null, path = "", onRegister = null, onUnregister = null } = {}) {
  const widget = createWidget(FORM_EDITOR_DEF, {
    path,
    state: {
      schema: schema ?? {},
      values: values ?? skeletonFromSchema(schema),
      errors: {},
    },
    onRegister,
    onUnregister,
  });
  const defaults = JSON.parse(JSON.stringify(widget.state.values));

  const render = () => {
    host.innerHTML = renderFormEditor(widget.state);
  };
  const _changed = () => widget.emit("change", { values: widget.state.values });

  widget.set_field = (fPath, value) => {
    _set(widget.state.values, fPath, value);
    delete widget.state.errors[fPath]; // 输入即清该字段错(即时反馈)
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
    render();
    _changed();
  };
  widget.values = () => widget.state.values;

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
    const add = e.target.closest("[data-f-add]");
    if (add) {
      const arrPath = add.dataset.fAdd;
      const arr = _get(widget.state.values, arrPath) ?? [];
      const itemSpec = _specOf(widget.state.schema, arrPath)?.items;
      _set(widget.state.values, arrPath, [...arr, itemSpec?.type === "number" || itemSpec?.type === "integer" ? 0 : ""]);
      render();
      return _changed();
    }
    const del = e.target.closest("[data-f-del]");
    if (del) {
      const [arrPath, i] = del.dataset.fDel.split("/");
      const arr = (_get(widget.state.values, arrPath) ?? []).filter((_, idx) => idx !== Number(i));
      _set(widget.state.values, arrPath, arr);
      render();
      return _changed();
    }
  });

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

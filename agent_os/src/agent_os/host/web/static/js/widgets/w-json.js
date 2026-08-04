/* W-json — JSON editor(docs/WIDGETS.md §2;带校验的文本编辑器)。

   state{value, dirty, error:{line, message}|null, schema};
   actions 全 local:set_value(即时 JSON 合法性校验)/format(一键美化,幂等)/
   validate(按 schema,字段级→行级提示);失焦校验;
   细节:**行级错误定位**(parse 错误的 position → 行号;schema 不合按字段名
   搜行——错在哪一行显示,不是只报 message)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { mountTextEditor } from "./w-text.js";

export const JSON_EDITOR_DEF_KIND = "json-editor";

/* parse 错误 → {line, message}(V8 "position N" 换算行号;
   SpiderMonkey "line N column M" 直读;新版 V8 无 position——
   提取 "Unexpected token 'x'" 的 token 在文本里搜行;
   end 类错误兜底报全文行数) */
export function locateJsonError(text, message) {
  const pos = /position (\d+)/.exec(message);
  if (pos) return { line: text.slice(0, Number(pos[1])).split("\n").length, message };
  const lc = /line (\d+) column \d+/.exec(message);
  if (lc) return { line: Number(lc[1]), message };
  const tok = /Unexpected token '((?:\\.|[^'])*)'/.exec(message);
  if (tok) {
    const idx = text.indexOf(tok[1]);
    if (idx >= 0) return { line: text.slice(0, idx).split("\n").length, message };
  }
  return { line: Math.max(1, text.split("\n").length), message };
}

/* JSON 合法性即时校验:合法 → null;非法 → {line, message} */
export function jsonErrorAt(text) {
  if (!text.trim()) return null;
  try {
    JSON.parse(text);
    return null;
  } catch (e) {
    return locateJsonError(text, e.message ?? String(e));
  }
}

/* schema 校验(轻量:required/properties.type 两级,与 launch-dialog 同哲学):
   不合 → {line, message}(按字段名搜行);合 → null。不是全量 jsonschema——
   控件本地提示面,硬校验永远在服务端的闸门。 */
export function schemaErrorAt(text, schema) {
  if (!schema || typeof schema !== "object") return null;
  let doc;
  try {
    doc = JSON.parse(text || "{}");
  } catch {
    return null; // JSON 合法性优先(jsonErrorAt 已报),schema 校验不重复报
  }
  const _typeOf = (v) =>
    v === null ? "null" : Array.isArray(v) ? "array" : Number.isInteger(v) ? "integer" : typeof v;
  const _lineOf = (key) => {
    const idx = text.indexOf(`"${key}"`);
    return idx < 0 ? 1 : text.slice(0, idx).split("\n").length;
  };
  for (const key of schema.required ?? []) {
    if (!(key in (doc ?? {}))) {
      return { line: _lineOf(schema.required[0] ?? key), message: `缺必填字段 ${key}` };
    }
  }
  for (const [key, spec] of Object.entries(schema.properties ?? {})) {
    if (key in (doc ?? {}) && spec?.type && _typeOf(doc[key]) !== spec.type) {
      return { line: _lineOf(key), message: `字段 ${key} 应为 ${spec.type}(当前 ${_typeOf(doc[key])})` };
    }
  }
  return null;
}

/* format 一键美化(幂等:对已美化文本是恒等操作) */
export function formatJson(text) {
  return JSON.stringify(JSON.parse(text), null, 2);
}

/* 挂进宿主(与 W-text 同族:textarea + 错误条 + format 钮;
   errorSlot = 既有提示槽(lab 的 data-json-hint span)——写行级消息进去,
   没有就自建 .wd-errbar;宿主监听挂在 host 上,后注册者后触发——
   lab 的旧 hint 先写,本控件以行级信息收尾(§2 行级定位增强) */
export function mountJsonEditor(host, { schema = null, path = "", onRegister = null, onUnregister = null } = {}) {
  const textarea = host.querySelector("textarea");
  if (!textarea) throw new Error("json-editor: host 里没有 textarea");
  const widget = mountTextEditor(host, { path, onRegister, onUnregister });
  widget.kind = JSON_EDITOR_DEF_KIND;
  widget.state.schema = schema;
  widget.state.error = null;

  const slot =
    host.querySelector(".lab-hint") ??
    (() => {
      const bar = host.ownerDocument.createElement("div");
      bar.className = "wd-errbar";
      bar.hidden = true;
      host.appendChild(bar);
      return bar;
    })();
  const fmtBtn = host.ownerDocument.createElement("button");
  fmtBtn.type = "button";
  fmtBtn.className = "wd-format";
  fmtBtn.textContent = copy("w.json.format");
  host.appendChild(fmtBtn);

  const show = (err) => {
    widget.state.error = err;
    if (slot.classList?.contains("wd-errbar")) slot.hidden = !err;
    slot.textContent = err ? `${copy("w.json.errline").replace("{line}", String(err.line))}: ${err.message}` : "";
  };

  const check = () => show(jsonErrorAt(textarea.value) ?? schemaErrorAt(textarea.value, widget.state.schema));

  host.addEventListener("input", (e) => {
    if (e.target === textarea) check(); // 即时校验(后于宿主旧 hint 触发,行级收尾)
  });
  host.addEventListener("focusout", (e) => {
    if (e.target === textarea) check(); // 失焦校验(§2)
  });
  fmtBtn.addEventListener("click", () => {
    const err = jsonErrorAt(textarea.value);
    if (err) return check(); // 不合法不美化(先修错,§2)
    const [s, e] = [textarea.selectionStart, textarea.selectionEnd];
    textarea.value = formatJson(textarea.value);
    textarea.selectionStart = s;
    textarea.selectionEnd = e;
    textarea.dispatchEvent(new Event("input", { bubbles: true })); // 同步宿主表单模型(lab 的 data-field 委托)
    show(null);
  });

  widget.validate = (schemaArg = widget.state.schema) => {
    widget.state.schema = schemaArg;
    check();
    return widget.state.error === null;
  };
  const _destroy = widget.destroy.bind(widget);
  widget.destroy = () => {
    fmtBtn.remove();
    if (slot.classList?.contains("wd-errbar")) slot.remove();
    _destroy();
  };
  return widget;
}

/* W-json 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.2;W5.1 新形态:自渲染)。

   state = W-text 面 + {error:{line, message}|null, schema};
   actions 全 local:set_value(即时 JSON 合法性校验)/format(一键美化,幂等)/
   validate(按 schema,字段级→行级提示);失焦校验;
   细节:**行级错误定位**(parse 错误的 position → 行号;schema 不合按字段名
   搜行——错在哪一行显示,不是只报 message)。
   铁律:本文件不拼 HTML(渲染全在 w-json.render.js);零 fetch;事件上行。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { _mountText } from "./w-text.js";
import { renderJsonEditor } from "./w-json.render.js";

export const JSON_EDITOR_DEF = registerWidgetDef({
  kind: "json-editor",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { value: "", dirty: false, readonly: false, lang: "json", wrap: true, mono: true },
  actions: [
    { id: "set_value", exec: "local", args_input: { value: { type: "string" } } },
    { id: "format", exec: "local" },
    { id: "validate", exec: "local" },
  ],
  events: ["change", "commit", "revert"],
  aria: { role: "textbox-multiline", keys: ["Escape"] },
  surfaces: ["card", "tab"],
  render: renderJsonEditor, // W5.1:render 面进 def(registry 校验形态)
});

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

/* 自渲染装配(§1.3;宿主给空挂点 + data-field 或显式 options)。
   错误条/绿勾 = render 面;校验结果进出走局部 hidden/文本刷新(不重渲,
   不打断输入);format 经 update 面(全量重渲 + 选区保留) */
export function mountJsonEditor(host, { schema = null, ...opts } = {}) {
  const widget = _mountText(host, JSON_EDITOR_DEF, {
    ...opts,
    mono: opts.mono ?? true, // JSON 编辑默认 mono 变体(行号槽)
    extraState: { schema, error: null },
  });
  const textarea = () => host.querySelector("textarea");

  const check = () => {
    const live = textarea()?.value; // 活元素优先(失焦校验时 input 可能没来过)
    if (typeof live === "string") widget.state.value = live;
    widget.state.error =
      jsonErrorAt(widget.state.value) ?? schemaErrorAt(widget.state.value, widget.state.schema);
    // 局部刷新(不重渲):错误条显隐 + 行级文案;绿勾随错误进出
    const bar = host.querySelector(".wd-errbar");
    if (bar) {
      bar.hidden = !widget.state.error;
      bar.textContent = widget.state.error
        ? `${copy("w.json.errline").replace("{line}", String(widget.state.error.line))}: ${widget.state.error.message}`
        : "";
    }
    const okMark = host.querySelector(".wd-json-ok");
    if (okMark) okMark.hidden = Boolean(widget.state.error) || !widget.state.value.trim();
  };

  host.addEventListener("input", (e) => {
    if (e.target === textarea()) check(); // 即时校验(行级定位)
  });
  host.addEventListener("focusout", (e) => {
    if (e.target === textarea()) check(); // 失焦校验(§2)
  });
  // 委托在 host(重渲后子元素换新,直接挂子元素监听会死——W5.1 自渲染纪律)
  host.addEventListener("click", (e) => {
    if (e.target.closest?.("[data-wd-format]")) return _format();
    if (e.target.closest?.("[data-wd-errbar]") && widget.state.error) {
      _jumpToLine(widget.state.error.line); // 错误条点击跳到错误行(§2.2)
    }
  });

  const _format = () => {
    const ta = textarea();
    if (!ta) return;
    if (jsonErrorAt(ta.value)) return check(); // 不合法不美化(先修错,§2)
    widget.update({ value: formatJson(ta.value) }); // 重渲 + 选区保留(W5.1 update 面)
    textarea()?.dispatchEvent?.(new Event("input", { bubbles: true })); // 同步宿主表单模型(lab 的 data-field 委托)
    check();
  };

  /* 错误条点击 → 光标跳到错误行行首(§2.2 交互) */
  const _jumpToLine = (line) => {
    const ta = textarea();
    if (!ta) return;
    const lines = String(ta.value).split("\n");
    const pos = lines.slice(0, Math.max(0, line - 1)).reduce((n, l) => n + l.length + 1, 0);
    ta.focus?.();
    ta.selectionStart = pos;
    ta.selectionEnd = pos;
  };

  widget.validate = (schemaArg = widget.state.schema) => {
    widget.state.schema = schemaArg;
    check();
    return widget.state.error === null;
  };
  check(); // 初值显隐同步(stub/虚拟面 region 不解析 hidden 属性;真实 DOM 同值重写)
  return widget;
}

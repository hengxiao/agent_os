/* W-json 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.2;W5.1 新形态:自渲染;
   W6.1 视觉按 docs/WIDGET-DESIGN.md §3.2)。

   state = W-text 面 + {error:{line, message}|null, schema};
   actions 全 local:set_value/format(一键美化,幂等;非法禁用)/validate
   (按 schema,字段级→行级提示);**失焦才校验**(§3.2:输入中不闪红——
   输入只刷着色层,校验在 focusout/validate/format 路径);
   细节:**行级错误定位**(parse 错误的 position → 行号;schema 不合按字段名
   搜行——错在哪一行显示,不是只报 message)、括号匹配(matchBrace,
   字符串掩码跳过串内括号)、错误三件套(行号槽红点 + 红波浪 + 底部错误条
   点击跳转并闪行 1.5s)。
   铁律:本文件不拼 HTML(渲染全在 w-json.render.js);零 fetch;事件上行。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { _mountText } from "./w-text.js";
import { jsonHighlightHtml, jsonOkText, renderJsonEditor } from "./w-json.render.js";

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
  events: ["change", "commit", "revert", "open"], // open = card 形态整卡点击(§1.4)
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

/* 字符串掩码(纯):mask[i]=1 表示第 i 字符在串内(含引号),括号匹配跳过 */
function _stringMask(s) {
  const mask = new Uint8Array(s.length);
  let inStr = false;
  let esc = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inStr) {
      mask[i] = 1;
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
    } else if (c === '"') {
      mask[i] = 1;
      inStr = true;
    }
  }
  return mask;
}

/* 括号匹配(§3.2;纯):光标邻位(pos-1 或 pos)是 {}[] 时找配对,
   返回 [i, j] 绝对下标;串内括号不算;找不到/不在括号旁 → null */
export function matchBrace(text, pos) {
  const s = String(text ?? "");
  const mask = _stringMask(s);
  let idx = -1;
  if ("{}[]".includes(s[pos - 1] ?? "")) idx = pos - 1;
  else if ("{}[]".includes(s[pos] ?? "")) idx = pos;
  if (idx < 0 || mask[idx]) return null;
  const open = "{[".includes(s[idx]);
  const want = { "{": "}", "[": "]", "}": "{", "]": "[" }[s[idx]];
  let depth = 0;
  for (let i = idx; i >= 0 && i < s.length; i += open ? 1 : -1) {
    if (mask[i]) continue;
    if (s[i] === s[idx]) depth += 1;
    else if (s[i] === want) {
      depth -= 1;
      if (depth === 0) return [idx, i];
    }
  }
  return null;
}

/* 自渲染装配(§1.3;宿主给空挂点 + data-field 或显式 options)。
   校验面(§3.2):**失焦才校验**(输入只刷着色层,不闪红);check() 局部
   刷新(不重渲):错误条/✓ 徽标/format 禁用/行号槽红点/红波浪;
   format 经 update 面(全量重渲 + 选区保留) */
export function mountJsonEditor(host, { schema = null, surface = "tab", ...opts } = {}) {
  const widget = _mountText(host, JSON_EDITOR_DEF, {
    ...opts,
    surface,
    mono: opts.mono ?? true, // JSON 编辑默认 mono 变体(行号槽)
    extraState: { schema, error: null },
  });
  const textarea = () => host.querySelector("textarea");

  /* 着色层局部刷新(不重渲):内容着色 + 错误行红波浪 + 括号匹配浅底;
     滚动对齐(textarea 驱动) */
  const syncHl = () => {
    const pre = host.querySelector(".wd-hl");
    const ta = textarea();
    if (!pre || !ta) return;
    const text = typeof ta.value === "string" ? ta.value : widget.state.value;
    pre.innerHTML =
      jsonHighlightHtml(text, {
        errorLine: widget.state.error?.line ?? 0,
        matches: matchBrace(text, ta.selectionStart ?? 0) ?? [],
      }) + "\n";
    pre.scrollTop = ta.scrollTop ?? 0;
    pre.scrollLeft = ta.scrollLeft ?? 0;
  };

  const check = () => {
    const live = textarea()?.value; // 活元素优先(失焦校验时 input 可能没来过)
    if (typeof live === "string") widget.state.value = live;
    widget.state.error =
      jsonErrorAt(widget.state.value) ?? schemaErrorAt(widget.state.value, widget.state.schema);
    const err = widget.state.error;
    // 局部刷新(不重渲):错误条显隐 + 行级文案 + 跳转提示;绿勾随错误进出
    const bar = host.querySelector(".wd-errbar");
    if (bar) {
      bar.hidden = !err;
      bar.textContent = err
        ? `${copy("w.json.errline").replace("{line}", String(err.line))}: ${err.message}`
        : "";
    }
    const hint = host.querySelector(".wd-err-hint");
    if (hint) hint.hidden = !err;
    const okMark = host.querySelector(".wd-json-ok");
    if (okMark) {
      const valid = !err && widget.state.value.trim();
      okMark.hidden = !valid;
      if (valid) okMark.textContent = `✓ ${jsonOkText(widget.state.value)}`; // 键数随内容刷新
    }
    const fmt = host.querySelector(".wd-format");
    if (fmt) {
      fmt.disabled = Boolean(err); // 非法禁用(§3.2),title 给原因
      fmt.title = err ? copy("w.json.fmt_dis") : "";
    }
    host.querySelector(".wd-json")?.classList?.toggle("is-err", Boolean(err)); // card 左边条
    for (const gl of [...(host.querySelectorAll?.(".wd-gl") ?? [])]) {
      gl.classList?.toggle("is-err", gl.dataset?.line === String(err?.line ?? 0)); // 行号槽红点
    }
    syncHl(); // 红波浪/括号/着色(局部,不重渲全文)
  };

  if (surface !== "card") {
    // card 形态:open 委托已由 _mountText 挂好,交互监听一律不挂(§1.4)
    host.addEventListener("input", (e) => {
      if (e.target === textarea()) syncHl(); // 输入只刷着色层;不校验(§3.2 输入中不闪红)
    });
    host.addEventListener("focusout", (e) => {
      if (e.target === textarea()) check(); // 失焦才校验(§3.2)
    });
    host.addEventListener("keyup", (e) => {
      if (e.target === textarea()) syncHl(); // 括号匹配跟手
    });
    // 委托在 host(重渲后子元素换新,直接挂子元素监听会死——W5.1 自渲染纪律)
    host.addEventListener("click", (e) => {
      if (e.target.closest?.("[data-wd-format]")) return _format();
      if (e.target.closest?.("[data-wd-errbar]") && widget.state.error) {
        return _jumpToLine(widget.state.error.line); // 错误条点击跳错误行 + 闪行(§2.2/§3.2)
      }
      if (e.target === textarea()) syncHl();
    });
  }

  const _format = () => {
    const ta = textarea();
    if (!ta) return;
    if (jsonErrorAt(ta.value)) return check(); // 不合法不美化(先修错,§2;钮同时禁用)
    widget.update({ value: formatJson(ta.value) }); // 重渲 + 选区保留(W5.1 update 面)
    textarea()?.dispatchEvent?.(new Event("input", { bubbles: true })); // 同步宿主表单模型(lab 的 data-field 委托)
    check();
  };

  /* 错误条点击 → 光标跳到错误行行首 + 该行闪烁 1.5s(§3.2) */
  const _jumpToLine = (line) => {
    const ta = textarea();
    if (!ta) return;
    const lines = String(ta.value).split("\n");
    const pos = lines.slice(0, Math.max(0, line - 1)).reduce((n, l) => n + l.length + 1, 0);
    ta.focus?.();
    ta.selectionStart = pos;
    ta.selectionEnd = pos;
    ta.scrollTop = Math.max(0, (line - 2) * 20); // 行高 20px(= --text-sm × 1.6)
    // 闪行(着色层该行一次性脉冲;stub 面区域不解析 [data-line=N],守卫 null)
    const row = host.querySelector(".wd-hl")?.querySelector?.(`[data-line="${line}"]`);
    if (row?.classList) {
      row.classList.add("wd-flash");
      setTimeout(() => row.classList.remove("wd-flash"), 1500);
    }
  };

  widget.validate = (schemaArg = widget.state.schema) => {
    widget.state.schema = schemaArg;
    check();
    return widget.state.error === null;
  };
  check(); // 初值显隐同步(stub/虚拟面 region 不解析 hidden 属性;真实 DOM 同值重写)
  return widget;
}

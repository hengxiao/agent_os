/* W-text — text editor(docs/WIDGETS.md §2;多行文本,mono/plain 两变体)。

   state{value, dirty, readonly, lang, wrap, mono};
   actions 全 local:set_value/commit(发 commit 事件)/revert(回 baseline);
   细节:**选区保留**(commit/revert 写 value 前后存取 selectionStart/End,
   重渲染不丢选区)、占位符(宿主 textarea 自带)、行数/字数微标、Esc=blur;
   aria(role=textbox-multiline 经 textarea 天然角色 + aria-label 必填——
   缺省从 data-field 推,不给就拒装,§2 a11y)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const TEXT_EDITOR_DEF = registerWidgetDef({
  kind: "text-editor",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { value: "", dirty: false, readonly: false, lang: "plain", wrap: true, mono: false },
  actions: [
    { id: "set_value", exec: "local", args_input: { value: { type: "string" } } },
    { id: "commit", exec: "local" },
    { id: "revert", exec: "local" },
  ],
  events: ["change", "commit", "revert"],
  aria: { role: "textbox-multiline", keys: ["Escape"] },
  surfaces: ["card", "tab"],
});

/* 行数/字数微标(copy 六主题;技术内容直读,统计是本地计算) */
function _micro(value) {
  const lines = value ? value.split("\n").length : 0;
  return copy("w.text.count").replace("{lines}", String(lines)).replace("{chars}", String(value.length));
}

/* 挂进宿主:host 内含一个 textarea(data-field;lab 表单模型零动——
   控件只加微标/选区保留/commit-revert 语义,不接管值的所有权) */
export function mountTextEditor(host, { path = "", onRegister = null, onUnregister = null } = {}) {
  const textarea = host.querySelector("textarea");
  if (!textarea) throw new Error("text-editor: host 里没有 textarea");
  if (!textarea.getAttribute("aria-label")) {
    const field = textarea.dataset?.field;
    if (!field) throw new Error("text-editor: aria-label 必填(或给 data-field 推导)");
    textarea.setAttribute("aria-label", field);
  }
  const widget = createWidget(TEXT_EDITOR_DEF, {
    path,
    state: { value: textarea.value, readonly: Boolean(textarea.readOnly), mono: host.dataset.variant === "mono" },
    onRegister,
    onUnregister,
  });
  let baseline = textarea.value; // commit 锚点(revert 回这里)

  const micro = host.ownerDocument.createElement("span");
  micro.className = "wd-micro";
  micro.textContent = _micro(textarea.value);
  host.appendChild(micro);

  const refresh = () => {
    micro.textContent = _micro(textarea.value);
  };

  host.addEventListener("input", (e) => {
    if (e.target !== textarea) return;
    widget.state.value = textarea.value;
    widget.state.dirty = textarea.value !== baseline;
    refresh();
    widget.emit("change", { value: textarea.value, dirty: widget.state.dirty });
  });
  host.addEventListener("keydown", (e) => {
    if (e.target === textarea && e.key === "Escape") textarea.blur(); // Esc=blur(§2 a11y)
  });

  widget.commit = () => {
    baseline = textarea.value;
    widget.state.dirty = false;
    widget.emit("commit", { value: textarea.value });
  };
  widget.revert = () => {
    // 选区保留(§2):写回前后存取选区
    const [s, e] = [textarea.selectionStart, textarea.selectionEnd];
    textarea.value = baseline;
    textarea.selectionStart = s;
    textarea.selectionEnd = e;
    widget.state.value = baseline;
    widget.state.dirty = false;
    refresh();
    widget.emit("revert", { value: baseline });
  };
  const _destroy = widget.destroy.bind(widget);
  widget.destroy = () => {
    micro.remove();
    _destroy();
  };
  widget.el = textarea;
  widget.register(host.dataset.summary ?? "");
  return widget;
}

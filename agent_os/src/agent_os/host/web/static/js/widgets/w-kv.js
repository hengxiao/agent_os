/* W-kv 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.4;W5.2 新形态:自渲染;
   W6.2 视觉按 docs/WIDGET-DESIGN.md §3.4)。

   state{entries: [{key, value}], allow_dup: bool, title?};
   actions 全 local:add/remove/set;**重复 key 即时警示**(警告态非硬拦,
   ⚠ + tooltip,随输入重渲即时更新);**末行 value 回车自动加行**(§3.4);
   序列化往返(entries ↔ object)。
   铁律:本文件不拼 HTML(渲染全在 w-kv.render.js);零 fetch;事件上行;
   监听一律委托在 host(重渲会换掉子元素)。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { dupKeys, renderKvEditor } from "./w-kv.render.js";

export { dupKeys }; // 兼容面(原从本文件导出;渲染面是纯函数唯一事实源)

export const KV_EDITOR_DEF = registerWidgetDef({
  kind: "kv-editor",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { entries: [], allow_dup: false },
  actions: [
    { id: "add", exec: "local" },
    { id: "remove", exec: "local", args_input: { index: { type: "integer" } } },
    { id: "set", exec: "local", args_input: { index: { type: "integer" } } },
  ],
  events: ["change", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "group", keys: [] },
  surfaces: ["card", "tab"],
  render: renderKvEditor, // W5.2:render 面进 def(registry 校验形态)
});

/* 序列化往返(重复 key 时后者覆盖——与 JSON object 语义一致,警示已提前给) */
export function entriesToObject(entries) {
  const out = {};
  for (const e of entries ?? []) if (e.key) out[e.key] = e.value;
  return out;
}

export function objectToEntries(obj) {
  return Object.entries(obj ?? {}).map(([key, value]) => ({ key, value }));
}

export function mountKvEditor(host, { entries = [], allow_dup = false, title = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(KV_EDITOR_DEF, {
    path,
    state: { entries: entries.map((e) => ({ ...e })), allow_dup, title },
    onRegister,
    onUnregister,
  });
  const render = () => {
    host.innerHTML = renderKvEditor(widget.state, { surface });
  };
  const _changed = () => widget.emit("change", { entries: widget.state.entries });

  widget.add = (entry = { key: "", value: "" }) => {
    widget.state.entries = [...widget.state.entries, { ...entry }];
    render();
    _changed();
  };
  widget.remove = (index) => {
    widget.state.entries = widget.state.entries.filter((_, i) => i !== index);
    render();
    _changed();
  };
  widget.set = (index, patch) => {
    Object.assign(widget.state.entries[index] ?? {}, patch);
    render(); // key 变化影响警示态,重渲(输入焦点由 set 调用方自持)
    _changed();
  };
  widget.serialize = () => entriesToObject(widget.state.entries);

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-kv-add]")) return widget.add();
    const x = e.target.closest("[data-kv-x]");
    if (x) return widget.remove(Number(x.dataset.kvX));
  });
  host.addEventListener("input", (e) => {
    const k = e.target.closest("[data-kv-key]");
    const v = e.target.closest("[data-kv-value]");
    if (k) widget.set(Number(k.dataset.kvKey), { key: e.target.value });
    if (v) {
      Object.assign(widget.state.entries[Number(v.dataset.kvValue)] ?? {}, { value: e.target.value });
      _changed(); // value 变更不影响警示态,不重渲(焦点不丢)
    }
  });
  // 末行 value 回车自动加行(§3.4;焦点落到新行 key)
  host.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const v = e.target.closest?.("[data-kv-value]");
    if (!v) return;
    if (Number(v.dataset.kvValue) === widget.state.entries.length - 1) {
      widget.add();
      host.querySelector(`[data-kv-key="${widget.state.entries.length - 1}"]`)?.focus?.();
    }
  });
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

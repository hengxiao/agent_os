/* W-kv — key-value editor(docs/WIDGETS.md §2;table 的两列特化:
   attrs/metadata/配置映射)。

   state{entries: [{key, value}], allow_dup: bool};
   actions 全 local:add/remove/set;**重复 key 即时警示**(警告态非硬拦);
   序列化往返(entries ↔ object)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

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
  events: ["change"],
  aria: { role: "group", keys: [] },
  surfaces: ["card", "tab"],
});

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* 序列化往返(重复 key 时后者覆盖——与 JSON object 语义一致,警示已提前给) */
export function entriesToObject(entries) {
  const out = {};
  for (const e of entries ?? []) if (e.key) out[e.key] = e.value;
  return out;
}

export function objectToEntries(obj) {
  return Object.entries(obj ?? {}).map(([key, value]) => ({ key, value }));
}

/* 重复 key 清单(警示面) */
export function dupKeys(entries) {
  const seen = new Set();
  const dups = new Set();
  for (const e of entries ?? []) {
    if (!e.key) continue;
    if (seen.has(e.key)) dups.add(e.key);
    seen.add(e.key);
  }
  return [...dups];
}

export function mountKvEditor(host, { entries = [], allow_dup = false, path = "", onRegister = null, onUnregister = null } = {}) {
  const widget = createWidget(KV_EDITOR_DEF, {
    path,
    state: { entries: entries.map((e) => ({ ...e })), allow_dup },
    onRegister,
    onUnregister,
  });
  const _changed = () => widget.emit("change", { entries: widget.state.entries });

  function render() {
    const dups = new Set(dupKeys(widget.state.entries));
    host.innerHTML =
      widget.state.entries
        .map(
          (e, i) =>
            `<div class="wd-kv-row" data-kv="${i}">` +
            `<input class="input wd-kv-k" data-kv-key="${i}" value="${esc(e.key)}" aria-label="key"` +
            `${dups.has(e.key) ? ' data-warn="1"' : ""}>` +
            `<input class="input wd-kv-v" data-kv-value="${i}" value="${esc(e.value)}" aria-label="value">` +
            `<button class="wd-row-x" data-kv-x="${i}" aria-label="✕">✕</button>` +
            (dups.has(e.key) ? `<span class="wd-dup">${esc(copy("w.kv.dup"))}</span>` : "") +
            `</div>`
        )
        .join("") +
      `<button class="wd-add" data-kv-add>${esc(copy("w.kv.add"))}</button>`;
  }

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

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

/* W-text 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.1;W5.1 新形态:自渲染;
   W6.1 视觉按 docs/WIDGET-DESIGN.md §3.1)。

   state{value, dirty, readonly, mono, rows, field, label, lang, updated_at?};
   actions 全 local:set_value/commit(发 commit 事件)/revert(回 baseline);
   细节:**选区保留**(update 全量重渲经 preserveSelection 存取选区/焦点)、
   Esc=blur;aria-label 必填(从 field/label 推,不给就拒装,§2 a11y)。
   W6.1:光标活动局部刷新(当前行槽/行列 tip/行号槽当前行,不重渲)、
   行号槽与着色层滚动同步(capture 委托——scroll 不冒泡)。
   铁律:本文件不拼 HTML(渲染全在 w-text.render.js);零 fetch;事件上行。

   生命周期(§1.3):mount = render(state) → host.innerHTML(控件自己产出,
   宿主不预置元素)→ 委托绑在 host;输入只局部刷新微标/dirty 边条(不重渲,
   输入态零干扰);update(patch) = state 合并 → 全量重渲(选区保留)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget, preserveSelection } from "./widget.js";
import { renderTextEditor, textEditorMicro } from "./w-text.render.js";

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
  events: ["change", "commit", "revert", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "textbox-multiline", keys: ["Escape"] },
  surfaces: ["card", "tab"],
  render: renderTextEditor, // W5.1:render 面进 def(registry 校验形态)
  mount: mountTextEditor, // C1:mount 面进 def(compound 视图装配;函数声明提升,def 字面量期可用)
});

/* 自渲染装配(§1.3;宿主给空挂点 + data-field/data-variant/data-rows 或显式
   options,控件自己产出全部元素)。``_extraState``/``_def`` 是 W-json 的
   复用面(同族控件共用一套挂载逻辑)。
   双形态(§1.4):surface="card" 时渲染走 card 面,宿主委托只挂 open
   (bindCardOpen);update() 不变(state 合并 → 按当前 surface 全量重渲)。 */
export function mountTextEditor(host, { value = "", field = "", mono = null, rows = null, readonly = false, label = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  return _mountText(host, TEXT_EDITOR_DEF, { value, field, mono, rows, readonly, label, path, onRegister, onUnregister, surface });
}

export function _mountText(host, def, { value = "", field = "", mono = null, rows = null, readonly = false, label = "", path = "", onRegister = null, onUnregister = null, extraState = {}, surface = "tab" } = {}) {
  const fieldName = field || host.dataset?.field || "";
  const ariaLabel = label || fieldName;
  if (!ariaLabel) throw new Error(`${def.kind}: aria-label 必填(或给 data-field 推导)`);
  const widget = createWidget(def, {
    path,
    state: {
      value: String(value ?? ""),
      dirty: false,
      readonly: Boolean(readonly),
      mono: mono ?? host.dataset?.variant === "mono",
      rows: rows ?? (Number(host.dataset?.rows) || 6),
      field: fieldName,
      label: ariaLabel,
      ...extraState,
    },
    onRegister,
    onUnregister,
  });
  let baseline = widget.state.value; // commit 锚点(revert 回这里)

  const textarea = () => host.querySelector("textarea");
  const render = () => {
    host.innerHTML = def.render(widget.state, { surface });
    const ta = textarea();
    // 元素值 ↔ state 对齐(真实 DOM 由文本内容自带;stub/异常面兜底同步)
    if (ta && ta.value !== widget.state.value) ta.value = widget.state.value;
    widget.el = ta;
  };
  // §1.3 update:state 合并 → 全量重渲(选区/焦点保留,W5.1 必答题;
  // 定位面 = textarea——单输入控件每宿主一个)
  widget.update = (patch) => {
    Object.assign(widget.state, patch);
    preserveSelection(host, render, { selector: "textarea" });
  };

  /* 输入局部刷新(不重渲):微标 + dirty 边条 + dirty 圆点(tab 胶囊 / card 标题行) */
  const syncDirty = () => {
    host.querySelector(".wd-text")?.classList?.toggle("is-dirty", widget.state.dirty);
    const micro = host.querySelector(".wd-micro");
    if (micro) micro.textContent = textEditorMicro(widget.state.value);
    for (const sel of [".wd-micro-dot", ".wd-card-dot"]) {
      const dot = host.querySelector(sel);
      if (dot) dot.hidden = !widget.state.dirty;
    }
  };

  /* 光标面(W6.1,§3.1):当前行高亮槽定位 + 行号槽当前行 + 行列 tip——
     全部局部 DOM 写(不重渲;选区/焦点不动)。行高 20px = --text-sm × 1.6
     (widgets.css 槽/pre/textarea 三面同算式)。 */
  const syncCursor = () => {
    const ta = textarea();
    if (!ta) return;
    const pos = ta.selectionStart ?? 0;
    const before = String(ta.value ?? "").slice(0, pos);
    const line = before.split("\n").length;
    const col = pos - before.lastIndexOf("\n"); // 1 基列
    const cur = host.querySelector(".wd-curline");
    if (cur) {
      cur.hidden = Boolean(widget.state.readonly); // readonly 无光标(§3.1)
      cur.style.top = `${(line - 1) * 20 - (ta.scrollTop ?? 0)}px`;
    }
    for (const gl of [...(host.querySelectorAll?.(".wd-gl") ?? [])]) {
      gl.classList?.toggle("is-current", gl.dataset?.line === String(line));
    }
    const tip = host.querySelector(".wd-micro-tip");
    if (tip) {
      tip.textContent = copy("w.text.pos").replace("{line}", String(line)).replace("{col}", String(col));
    }
  };

  /* 滚动同步(W6.1):行号槽平移 + W-json 着色层对齐(渲染层滚动联动) */
  const syncScroll = () => {
    const ta = textarea();
    if (!ta) return;
    const gi = host.querySelector(".wd-gutter-in");
    if (gi) gi.style.transform = `translateY(${-(ta.scrollTop ?? 0)}px)`;
    const hl = host.querySelector(".wd-hl");
    if (hl) {
      hl.scrollTop = ta.scrollTop ?? 0;
      hl.scrollLeft = ta.scrollLeft ?? 0;
    }
  };
  render();
  syncDirty(); // 初值同步(stub/虚拟面上 region 不带串内文本;真实 DOM 为同值重写)

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
    host.addEventListener("input", (e) => {
      const ta = textarea();
      if (!ta || e.target !== ta) return;
      widget.state.value = ta.value;
      widget.state.dirty = ta.value !== baseline;
      syncDirty();
      syncCursor();
      widget.emit("change", { value: ta.value, dirty: widget.state.dirty });
    });
    host.addEventListener("keydown", (e) => {
      if (e.target === textarea() && e.key === "Escape") e.target.blur?.(); // Esc=blur(§2 a11y)
    });
    // 光标活动(W6.1):当前行/行列 tip;scroll 不冒泡,capture 委托
    host.addEventListener("keyup", (e) => {
      if (e.target === textarea()) syncCursor();
    });
    host.addEventListener("click", (e) => {
      if (e.target === textarea()) syncCursor();
    });
    host.addEventListener("focusin", (e) => {
      if (e.target === textarea()) syncCursor();
    });
    host.addEventListener(
      "scroll",
      (e) => {
        if (e.target === textarea()) syncScroll();
      },
      true
    );
  }

  widget.commit = () => {
    baseline = widget.state.value;
    widget.state.dirty = false;
    syncDirty();
    widget.emit("commit", { value: widget.state.value });
  };
  widget.revert = () => {
    widget.update({ value: baseline, dirty: false }); // 全量重渲,选区保留
    widget.emit("revert", { value: baseline });
  };
  const _destroy = widget.destroy.bind(widget);
  widget.destroy = () => {
    host.innerHTML = ""; // 自渲染件:拆 = 清空自己产出的全部元素
    _destroy();
  };
  widget.register(host.dataset?.summary ?? "");
  return widget;
}

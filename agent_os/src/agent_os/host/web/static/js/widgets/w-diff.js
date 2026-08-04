/* W-diff 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.11;W5.3 新形态:自渲染)。

   state{left, right, mode: "split"|"unified", expanded: [member 名]};
   actions 全 local:set_mode;细节:字段两列 + prompt 红绿行(**split 与既有
   diff 呈现逐字节一致**——提取不改语义,cards.js 的 diffCard 委托本渲染面的
   diffBodyHtml);unified = 单列新旧堆叠 + same 行折叠上下文([+n] 展开钮)。
   铁律:本文件不拼 HTML(渲染全在 w-diff.render.js);零 fetch;监听委托在 host。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderDiffViewer } from "./w-diff.render.js";

// 兼容面:diffBodyHtml 迁至渲染面(W5.3),此处原样 re-export(cards.js 等在用)
export { diffBodyHtml } from "./w-diff.render.js";

export const DIFF_VIEWER_DEF = registerWidgetDef({
  kind: "diff-viewer",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { mode: "split", expanded: [] },
  actions: [{ id: "set_mode", exec: "local", args_input: { mode: { type: "string" } } }],
  events: ["change", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "group", keys: [] },
  surfaces: ["card", "tab"],
  render: renderDiffViewer, // W5.3:render 面进 def(registry 校验形态)
});

/* 挂进宿主:diff(与 cards/lab-iterate 同构的 diff 对象);set_mode 切换重渲 */
export function mountDiffViewer(host, { diff, mode = "split", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(DIFF_VIEWER_DEF, {
    path,
    state: { left: diff, right: null, mode, expanded: [] },
    onRegister,
    onUnregister,
  });
  const render = () => {
    host.innerHTML = renderDiffViewer(widget.state, { surface });
  };

  widget.set_mode = (mode) => {
    if (mode !== "split" && mode !== "unified") return;
    widget.state.mode = mode;
    render();
    widget.emit("change", { mode });
  };

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  host.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (btn) return widget.set_mode(btn.dataset.mode);
    const fold = e.target.closest("[data-fold]");
    if (fold) {
      if (!widget.state.expanded.includes(fold.dataset.fold)) {
        widget.state.expanded = [...widget.state.expanded, fold.dataset.fold];
      }
      render(); // 折叠上下文展开(§2 长文本折叠)
    }
  });
  }

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

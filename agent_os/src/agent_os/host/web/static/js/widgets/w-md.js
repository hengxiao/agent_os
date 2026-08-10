/* W-md 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.12;W5.3 新形态:自渲染)。

   state{source};纯查看器(无 actions);代码块复制钮(data-md-copy 序号 →
   按序取源代码块文本,写剪贴板,emit copy;缺席环境降级为仅事件)。
   铁律:本文件不拼 HTML(mdToHtml/render 全在 w-md.render.js);零 fetch。 */

import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderMarkdownViewer } from "./w-md.render.js";

// 兼容面:mdToHtml/looksMarkdown 迁至渲染面(W5.3),此处原样 re-export
export { looksMarkdown, mdToHtml } from "./w-md.render.js";

export const MD_VIEWER_DEF = registerWidgetDef({
  kind: "md-viewer",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { source: "", view: "preview" },
  actions: [],
  events: ["copy", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "document", keys: [] },
  surfaces: ["card", "tab"],
  render: renderMarkdownViewer, // W5.3:render 面进 def(registry 校验形态)
  mount: mountMarkdownViewer,
});

/* 取第 i 个代码块文本(与 mdToHtml 的围栏解析同律:行首 ``` 开/合) */
function _codeBlockAt(source, idx) {
  const blocks = [];
  let inCode = false;
  let buf = [];
  for (const line of String(source ?? "").split("\n")) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        blocks.push(buf.join("\n"));
        buf = [];
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) buf.push(line);
  }
  if (inCode) blocks.push(buf.join("\n")); // 未闭合兜底(与 mdToHtml 同)
  return blocks[idx] ?? "";
}

/* 挂进宿主:source(markdown 原文;state 可序列化)+ title(面板头,可选)。
   双形态(§1.4):surface="card" 时渲染摘要卡,宿主委托只挂 open(复制钮不进卡)。
   W6.4(§3.12):复制钮点击后 1s ✓ 成功反馈(局部,不重渲)。 */
export function mountMarkdownViewer(host, { source = "", title = "", bar = true, path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const widget = createWidget(MD_VIEWER_DEF, { path, state: { source, title }, onRegister, onUnregister });
  const render = () => {
    host.innerHTML = renderMarkdownViewer(widget.state, { surface, bar });
  };
  widget.set_source = (source) => {
    widget.state.source = source;
    render();
  };
  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4)
  } else {
  // 委托在 host(重渲会换掉子元素,W5.1 纪律)
  host.addEventListener("click", (e) => {
    // 预览 | 源码(§3.12 v2;切换不重取数据,同一份 state.source)
    const v = e.target.closest?.("[data-md-view]");
    if (v) {
      widget.state.view = v.dataset.mdView === "source" ? "source" : "preview";
      return render();
    }
    // 全文复制(两态都在,§3.12 v2)
    const all = e.target.closest?.("[data-md-copyall]");
    if (all) {
      const text = widget.state.source;
      globalThis.navigator?.clipboard?.writeText?.(text);
      widget.emit("copy", { text });
      all.textContent = "✓";
      setTimeout(() => {
        all.textContent = "⧉";
      }, 1000);
      return;
    }
    // 代码块复制钮(§2.12)
    const btn = e.target.closest?.("[data-md-copy]");
    if (!btn) return;
    const text = _codeBlockAt(widget.state.source, Number(btn.dataset.mdCopy));
    globalThis.navigator?.clipboard?.writeText?.(text); // 剪贴板(缺席环境降级为事件)
    widget.emit("copy", { text });
    // 成功反馈(§3.12):⧉ → ✓,1s 后复原
    btn.textContent = "✓";
    setTimeout(() => {
      btn.textContent = "⧉";
    }, 1000);
  });
  }
  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

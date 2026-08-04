/* W-bubble 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.13;W5.3 新形态:自渲染)。

   state{anchor, messages: [{role, text, ts}], open, busy, draft};
   职责分界(铁律):**cascade 组装本地做(纯函数),send 出海在父级**——
   submit 事件携带 {anchor, text, cascade},父组件把它 POST 给评论技能,
   回复经 receiveReply 回填;apply_reply 只发事件(气泡不越权改任何数据)。
   铁律:本文件不拼 HTML(渲染全在 w-bubble.render.js);零 fetch;事件上行。

   a11y:role=log(消息区)、role=dialog(气泡卡,Esc 关闭、Enter 发送)。 */

import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";
import { contextCascade } from "./cascade.js";
import { renderBubble } from "./w-bubble.render.js";

export const BUBBLE_DEF = registerWidgetDef({
  kind: "chat-bubble",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { anchor: null, messages: [], open: false, busy: false, draft: "" },
  actions: [
    { id: "open", exec: "local" },
    { id: "close", exec: "local" },
    { id: "apply_reply", exec: "local", args_input: { index: { type: "integer" } },
      context: ["widget"] }, // 轻动作不背大信封(§16.1-3)
  ],
  events: ["submit", "apply", "open", "close"],
  aria: { role: "dialog", keys: ["Enter", "Escape"] },
  surfaces: ["card", "tab"],
  render: renderBubble, // W5.3:render 面进 def(registry 校验形态)
});

/* 挂进宿主:anchor(§14 语义锚:成员/字段/可选 span)+ cascadeProviders(各级 fragment
   提供者,§16;缺省走全局注册表)。``triggerPath`` = cascade 的触发路径(缺省
   取 anchor.path)。seedMessages = 既有边注(数据兼容)。 */
export function mountBubble(
  host,
  { anchor, triggerPath = null, seedMessages = [], cascadeProviders = null, cascadeLevels = null, path = "", onRegister = null, onUnregister = null } = {}
) {
  const widget = createWidget(BUBBLE_DEF, {
    path,
    state: {
      anchor,
      messages: seedMessages.map((m) => ({ role: m.role ?? "user", text: m.text ?? "", ts: m.ts ?? m.at ?? 0 })),
      open: true,
      busy: false,
      draft: "",
    },
    onRegister,
    onUnregister,
  });
  const anchorTo = [anchor?.member, anchor?.path].filter(Boolean).join(" · ");

  const render = () => {
    host.innerHTML = renderBubble(widget.state);
  };

  widget.open = () => {
    widget.state.open = true;
    render();
    widget.emit("open", { anchor });
  };
  widget.close = () => {
    widget.state.open = false;
    widget.emit("close", { anchor });
    widget.destroy();
  };
  /* 父级回填回复(send 出海的结果;busy 骨架撤下) */
  widget.receiveReply = (text) => {
    widget.state.busy = false;
    widget.state.messages = [...widget.state.messages, { role: "assistant", text, ts: Date.now() / 1000 }];
    render();
  };
  widget.apply_reply = (index) => {
    const m = widget.state.messages[index];
    if (m?.role === "assistant") widget.emit("apply", { anchor, text: m.text });
  };

  function _submit() {
    const text = widget.state.draft.trim();
    if (!text || widget.state.busy) return;
    widget.state.draft = "";
    widget.state.messages = [...widget.state.messages, { role: "user", text, ts: Date.now() / 1000 }];
    widget.state.busy = true;
    render();
    // §16:cascade 本地组装(纯函数);出海 = 父组件订阅 submit 后的事
    const cascade = contextCascade(triggerPath ?? anchor?.path ?? "", {
      levels: cascadeLevels,
      providers: cascadeProviders ?? undefined,
    });
    widget.emit("submit", { anchor, text, cascade });
  }

  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-bubble-draft]")) widget.state.draft = e.target.value;
  });
  host.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.closest("[data-bubble-draft]")) _submit();
    if (e.key === "Escape") widget.close(); // Esc 关(不产生提交)
  });
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-bubble-send]")) return _submit();
    const apply = e.target.closest("[data-apply]");
    if (apply) widget.apply_reply(Number(apply.dataset.apply));
  });

  widget.focus = () => {
    const input = host.querySelector("[data-bubble-draft]");
    input?.focus?.();
  };

  render();
  widget.register(anchorTo);
  return widget;
}

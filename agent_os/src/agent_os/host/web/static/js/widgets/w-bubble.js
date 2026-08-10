/* W-bubble 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.13;W5.3 新形态:自渲染)。

   state{anchor, messages: [{role, text, ts}], open, busy, draft};
   职责分界(铁律):**cascade 组装本地做(纯函数),send 出海在父级**——
   submit 事件携带 {anchor, text, cascade},父组件把它 POST 给评论技能,
   回复经 receiveReply 回填;apply_reply 只发事件(气泡不越权改任何数据)。
   铁律:本文件不拼 HTML(渲染全在 w-bubble.render.js);零 fetch;事件上行。

   a11y:role=log(消息区)、role=dialog(气泡卡,Esc 关闭、Enter 发送)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { contextCascade } from "./cascade.js";
import { renderBubble } from "./w-bubble.render.js";

export const BUBBLE_DEF = registerWidgetDef({
  kind: "chat-bubble",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { anchor: null, messages: [], open: false, busy: false, draft: "", unread: 0 },
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
  mount: mountBubble,
});

/* 挂进宿主:anchor(§14 语义锚:成员/字段/可选 span)+ cascadeProviders(各级 fragment
   提供者,§16;缺省走全局注册表)。``triggerPath`` = cascade 的触发路径(缺省
   取 anchor.path)。seedMessages = 既有边注(数据兼容)。 */
export function mountBubble(
  host,
  { anchor, triggerPath = null, seedMessages = [], cascadeProviders = null, cascadeLevels = null, path = "", onRegister = null, onUnregister = null, surface = "tab", unread = 0 } = {}
) {
  const widget = createWidget(BUBBLE_DEF, {
    path,
    state: {
      anchor,
      messages: seedMessages.map((m) => ({ role: m.role ?? "user", text: m.text ?? "", ts: m.ts ?? m.at ?? 0 })),
      open: true,
      busy: false,
      draft: "",
      unread: Number(unread) || 0, // card 面未读徽标(§1.4;tab 不读此项)
    },
    onRegister,
    onUnregister,
  });
  const anchorTo = [anchor?.member, anchor?.path].filter(Boolean).join(" · ");

  const render = () => {
    host.innerHTML = renderBubble(widget.state, { surface });
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
  widget.apply_reply = (index) => {
    const m = widget.state.messages[index];
    if (m?.role === "assistant") widget.emit("apply", { anchor, text: m.text });
  };

  function _submit(retry = false) {
    const text = retry ? widget.state.lastText : widget.state.draft.trim();
    if (!text || widget.state.busy) return;
    if (!retry) {
      widget.state.draft = "";
      widget.state.messages = [...widget.state.messages, { role: "user", text, ts: Date.now() / 1000 }];
    }
    widget.state.lastText = text; // 重试锚(§3.13 失败行内红条 + 重试)
    widget.state.error = null;
    widget.state.busy = true;
    render();
    // §16:cascade 本地组装(纯函数);出海 = 父组件订阅 submit 后的事
    const cascade = contextCascade(triggerPath ?? anchor?.path ?? "", {
      levels: cascadeLevels,
      providers: cascadeProviders ?? undefined,
    });
    widget.emit("submit", { anchor, text, cascade });
  }

  /* 父级回填回复(send 出海的结果;busy 骨架撤下,错误清) */
  widget.receiveReply = (text) => {
    widget.state.busy = false;
    widget.state.error = null;
    widget.state.messages = [...widget.state.messages, { role: "assistant", text, ts: Date.now() / 1000 }];
    render();
  };
  /* 发送失败(§3.13):行内红条 + 重试(父级出海失败后回填) */
  widget.notifyError = (msg = "") => {
    widget.state.busy = false;
    widget.state.error = msg || copy("w.bubble.fail");
    render();
  };

  if (surface === "card") {
    // card:宿主委托只挂 open(§1.4)——负载沿用本控件 open 事件语义({anchor})
    bindCardOpen(host, widget, { anchor });
  } else {
  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-bubble-draft]")) {
      widget.state.draft = e.target.value;
      // 发送钮空输入禁用(§3.13):局部刷新,不重渲
      const send = host.querySelector("[data-bubble-send]");
      if (send) send.disabled = !e.target.value.trim() || widget.state.busy;
    }
  });
  host.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && e.target.closest("[data-bubble-draft]")) _submit(); // Enter 发送,Shift+Enter 换行(§3.13)
    if (e.key === "Escape") widget.close(); // Esc 关(不产生提交)
  });
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-bubble-x]")) return widget.close(); // ✕ 收起(§3.13)
    if (e.target.closest("[data-bubble-retry]")) return _submit(true); // 失败重试
    if (e.target.closest("[data-bubble-send]")) return _submit();
    const apply = e.target.closest("[data-apply]");
    if (apply) widget.apply_reply(Number(apply.dataset.apply));
  });
  }

  widget.focus = () => {
    const input = host.querySelector("[data-bubble-draft]");
    input?.focus?.();
  };

  render();
  widget.register(anchorTo);
  return widget;
}

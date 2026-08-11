/* W-bubble 逻辑面(docs/WIDGET-ARCH.md §1.1/§2.13;W5.3 新形态:自渲染;
   v2 · 用户验收反馈 2026-08-11,docs/WIDGET-DESIGN.md §3.13 v2)。

   state{anchor, messages: [{role, text, ts, expanded?}], open, busy, draft,
   unread, newFrom, newpill};
   职责分界(铁律):**cascade 组装本地做(纯函数),send 出海在父级**——
   submit 事件携带 {anchor, text, cascade},父组件把它 POST 给评论技能,
   回复经 receiveReply 回填;apply_reply 只发事件(气泡不越权改任何数据)。
   铁律:本文件不拼 HTML(渲染全在 w-bubble.render.js);零 fetch;事件上行。

   v2 UX(§3.13 v2):
   - busy 不吞消息:composer 发送中可用;连发进本地队列——用户消息先入流
     (不等回包),在飞一条(emit 串行 pump),回复按序回填;失败不堵队
     (重试补发 lastText);至少保证「输入不丢」;
   - 滚底语义:新消息到达时 log 在底 → 自动滚底;不在底 → 浮「↓ 新消息」
     pill(点击滚底),不硬拽;回底 pill 自收;
   - 长单条消息折叠:data-more 切 expanded(>6 行/-clamp:6);
   - composer autosize(1 行起 4 行止,超出内滚);Esc/✕ 收起,点外不收
     (防误丢草稿)。

   a11y:role=log(消息区)、role=dialog(气泡卡,Esc 关闭、Enter 发送)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { contextCascade } from "./cascade.js";
import { renderBubble, bubbleNewFrom } from "./w-bubble.render.js";

export const BUBBLE_DEF = registerWidgetDef({
  kind: "chat-bubble",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { anchor: null, messages: [], open: false, busy: false, draft: "", unread: 0 },
  actions: [
    { id: "open", exec: "local" },
    { id: "close", exec: "local" },
    { id: "delete", exec: "local" }, // v3:批注删除(协议原则:action 必是 skill,local 也是)
    { id: "apply_reply", exec: "local", args_input: { index: { type: "integer" } },
      context: ["widget"] }, // 轻动作不背大信封(§16.1-3)
  ],
  events: ["submit", "apply", "open", "close", "delete"],
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
      newFrom: 0, // 下方按 unread 重算(v2 未读分隔线游标)
      newpill: false, // 「↓ 新消息」浮囊(滚底语义;非底部新消息才出)
    },
    onRegister,
    onUnregister,
  });
  const anchorTo = [anchor?.member, anchor?.path].filter(Boolean).join(" · ");
  widget.state.newFrom = bubbleNewFrom(widget.state.messages, Number(unread) || 0);

  /* 发送队列(v2):在飞一条(state.busy),连发排 queue——用户消息先入流,
     emit 串行;输入永不丢 */
  const queue = [];
  const logEl = () => host.querySelector(".w-bubble-log");
  const pillEl = () => host.querySelector("[data-bubble-pill]");
  let _shown = widget.state.messages.length; // 已渲消息数(判「新消息到达」)

  const render = () => {
    // 滚底语义:仅「新消息到达」一拍定 pill(在底 → 滚底不出;不在底 → 出,
    // 不硬拽);非增长重渲(busy/typing 等)不动 pill——否则队列续渲会误清
    const log = logEl();
    const wasBottom = log
      ? log.scrollTop + log.clientHeight >= log.scrollHeight - 4
      : true;
    const grew = widget.state.messages.length > _shown;
    if (grew) widget.state.newpill = !wasBottom;
    host.innerHTML = renderBubble(widget.state, { surface });
    _shown = widget.state.messages.length;
    const nl = logEl();
    if (nl && wasBottom) nl.scrollTop = nl.scrollHeight;
  };

  /* composer autosize(1 行起 4 行止;stub 无布局面时静默) */
  const autosize = (ta) => {
    if (!ta || typeof ta.scrollHeight !== "number" || !ta.style) return;
    const line = Number.parseFloat(globalThis.getComputedStyle?.(ta)?.lineHeight) || 18;
    const max = line * 4 + 12;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, max)}px`;
    ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden";
  };

  function _pump() {
    if (widget.state.busy || !queue.length) return;
    const text = queue.shift();
    widget.state.lastText = text; // 重试锚(失败行内红条 + 重试)
    widget.state.busy = true;
    widget.state.error = null;
    render();
    // §16:cascade 本地组装(纯函数);出海 = 父组件订阅 submit 后的事
    const cascade = contextCascade(triggerPath ?? anchor?.path ?? "", {
      levels: cascadeLevels,
      providers: cascadeProviders ?? undefined,
    });
    widget.emit("submit", { anchor, text, cascade });
  }

  function _submit(retry = false) {
    const text = retry ? widget.state.lastText : widget.state.draft.trim();
    if (!text) return;
    if (retry) {
      queue.unshift(text); // 失败重试:补发同一文本(用户消息已在流,不重复追加)
      widget.state.error = null;
      _pump();
      return;
    }
    widget.state.draft = "";
    widget.state.messages = [...widget.state.messages, { role: "user", text, ts: Date.now() / 1000 }];
    queue.push(text);
    const ta = host.querySelector("[data-bubble-draft]");
    if (ta) autosize(ta); // 清空后回 1 行
    render(); // 用户消息先入流(不等回包)
    _pump();
  }

  /* 父级回填回复(send 出海的结果;busy 骨架撤下,错误清;按序 pump 下一条) */
  widget.receiveReply = (text) => {
    widget.state.busy = false;
    widget.state.error = null;
    widget.state.messages = [...widget.state.messages, { role: "assistant", text, ts: Date.now() / 1000 }];
    render();
    _pump();
  };
  /* 发送失败(§3.13):行内红条 + 重试(父级出海失败后回填);不堵队 */
  widget.notifyError = (msg = "") => {
    widget.state.busy = false;
    widget.state.error = msg || copy("w.bubble.fail");
    render();
    _pump();
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
  /* v3:删除(action local skill;事件上行 → 父级摘除 + 后端删持久化) */
  widget.delete = () => {
    widget.emit("delete", { anchor });
  };
  widget.apply_reply = (index) => {
    const m = widget.state.messages[index];
    if (m?.role === "assistant") widget.emit("apply", { anchor, text: m.text });
  };

  if (surface === "card") {
    // card:宿主委托只挂 open(§1.4)——负载沿用本控件 open 事件语义({anchor})
    bindCardOpen(host, widget, { anchor });
  } else {
  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-bubble-draft]")) {
      widget.state.draft = e.target.value;
      // 发送钮空输入禁用(§3.13;busy 不禁用——发送中可续写,v2):局部刷新,不重渲
      const send = host.querySelector("[data-bubble-send]");
      if (send) send.disabled = !e.target.value.trim();
      autosize(e.target);
    }
  });
  host.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && e.target.closest("[data-bubble-draft]")) {
      e.preventDefault?.(); // Enter 发送(Shift+Enter 换行,v2 textarea)
      _submit();
    }
    if (e.key === "Escape") widget.close(); // Esc 收(点外不收,防误丢草稿)
  });
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-bubble-x]")) return widget.close(); // ✕ 收起(§3.13)
    // 🗑 删除(v3):两击确认(第一击武装,第二击上行 delete)
    const del = e.target.closest("[data-bubble-del]");
    if (del) {
      if (del.dataset.armed === "1") return widget.delete();
      del.dataset.armed = "1";
      del.classList.add("is-armed");
      del.title = copy("w.bubble.del.confirm");
      return;
    }
    if (e.target.closest("[data-bubble-retry]")) return _submit(true); // 失败重试
    if (e.target.closest("[data-bubble-send]")) return _submit();
    // 「↓ 新消息」pill:点击滚底自收
    if (e.target.closest("[data-bubble-pill]")) {
      const log = logEl();
      if (log) log.scrollTop = log.scrollHeight;
      widget.state.newpill = false;
      const p = pillEl();
      if (p) p.hidden = true;
      return;
    }
    // 长消息展开/折叠(v2)
    const more = e.target.closest("[data-more]");
    if (more) {
      const m = widget.state.messages[Number(more.dataset.more)];
      if (m) {
        m.expanded = !m.expanded;
        render();
      }
      return;
    }
    const apply = e.target.closest("[data-apply]");
    if (apply) widget.apply_reply(Number(apply.dataset.apply));
  });
  // 滚底判定监听(capture:scroll 不冒泡):回底自收 pill
  host.addEventListener("scroll", (e) => {
    const log = e.target.closest?.(".w-bubble-log") ?? null;
    if (!log) return;
    if (log.scrollTop + log.clientHeight >= log.scrollHeight - 4) {
      widget.state.newpill = false;
      const p = pillEl();
      if (p) p.hidden = true;
    }
  }, true);
  }

  widget.focus = () => {
    const input = host.querySelector("[data-bubble-draft]");
    input?.focus?.();
  };

  render();
  widget.register(anchorTo);
  return widget;
}

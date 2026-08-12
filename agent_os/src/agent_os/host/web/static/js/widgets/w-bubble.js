/* W-bubble 逻辑面(docs/WIDGET-DESIGN.md §3.13 **v4** · 批注批处理工作流 v2.1):
   单条批注卡——消息流/发送队列/typing/pill/未读分隔线/折叠全部退役(代码删除)。

   state{anchor, quote, content, status(pending|applied|ignored|outdated),
   createdAt, view(composing|expanded), draft, severity, generation};
   职责分界(铁律):cascade 组装本地做(纯函数),**出海在父级**——submit 事件
   携带 {anchor, content, quote, cascade},父组件 POST 给 annotations 端点;
   本控件零 fetch;事件上行;本文件不拼 HTML(渲染全在 w-bubble.render.js)。

   v4 交互(v2.1 §1.2/§3):
   - composing(输入态):Enter 提交 / Shift+Enter 换行 / Esc 取消;
     空输入提交 = 抖动提示;>500 字截断 + hint;autosize 1→3 行超出内滚;
     **点外 = 有内容提交 / 空取消**(裁决 C1,覆盖 v3.2 保草稿——宿主调
     submitOrCancel);
   - expanded(展示态):编辑 → 回 composing(draft 预填 content),提交后
     状态回 pending;✕ = 收起(壳显隐归宿主);
   - 乐观更新:提交即写 state(content/status=pending)+ 转展示态;父级出海
     失败 → notifyError 回输入态(草稿恢复)。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { contextCascade } from "./cascade.js";
import { renderBubble } from "./w-bubble.render.js";

const MAX_LEN = 500; // v2.1 §3.4:批注上限(截断 + 提示)

export const BUBBLE_DEF = registerWidgetDef({
  kind: "chat-bubble",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { anchor: null, quote: "", content: "", status: "pending", view: "composing", draft: "" },
  actions: [
    { id: "open", exec: "local" },
    { id: "close", exec: "local" },
    { id: "delete", exec: "local" }, // 批注删除(协议原则:action 必是 skill,local 也是)
  ],
  events: ["submit", "open", "close", "delete"],
  aria: { role: "dialog", keys: ["Enter", "Escape"] },
  surfaces: ["card", "tab"],
  render: renderBubble, // W5.3:render 面进 def(registry 校验形态)
  mount: mountBubble,
});

/* 挂进宿主:anchor(§14 语义锚)+ cascadeProviders(各级 fragment 提供者,§16;
   缺省走全局注册表)。``triggerPath`` = cascade 的触发路径(缺省取 anchor.path)。
   种子 = 单条批注记录(quote/content/status/createdAt/severity/generation)。 */
export function mountBubble(
  host,
  { anchor, quote = "", content = "", status = "pending", createdAt = "", severity = "", generation = null,
    view = null, triggerPath = null, cascadeProviders = null, cascadeLevels = null,
    path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}
) {
  const widget = createWidget(BUBBLE_DEF, {
    path,
    state: {
      anchor,
      quote: String(quote ?? anchor?.quote ?? ""),
      content: String(content ?? ""),
      status: status || "pending",
      createdAt: createdAt || "",
      severity: severity || "",
      generation: generation ?? null,
      view: view ?? (content ? "expanded" : "composing"),
      draft: "",
    },
    onRegister,
    onUnregister,
  });
  const anchorTo = [anchor?.member, anchor?.path].filter(Boolean).join(" · ");

  const render = () => {
    host.innerHTML = renderBubble(widget.state, { surface });
  };

  /* autosize(v2.1 §3:1 行起 3 行止,超出内滚;stub 无布局面时静默) */
  const autosize = (ta) => {
    if (!ta || typeof ta.scrollHeight !== "number" || !ta.style) return;
    const line = Number.parseFloat(globalThis.getComputedStyle?.(ta)?.lineHeight) || 18;
    const max = line * 3 + 12;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, max)}px`;
    ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden";
  };

  const _draftEl = () => host.querySelector("[data-bubble-draft]");
  /* 读 composer DOM 当前值(F4 先例:直写 value 不经 input 事件时 state 滞后;
     只采信真输入控件——stub region 面 div 也带空 value,不能当真) */
  const _domDraft = () => {
    const el = _draftEl();
    return el && /^(TEXTAREA|INPUT)$/.test(el.tagName ?? "") && typeof el.value === "string"
      ? el.value : null;
  };
  const _hint = (msg) => {
    const h = host.querySelector("[data-bubble-hint]");
    if (!h) return;
    h.textContent = msg || "";
    h.hidden = !msg;
  };
  const _shake = () => {
    const el = _draftEl();
    if (!el?.classList) return;
    el.classList.add("is-shake");
    setTimeout(() => el.classList?.remove("is-shake"), 350);
  };

  function _submit() {
    const text = String(_domDraft() ?? widget.state.draft ?? "").trim();
    if (!text) {
      _shake(); // v2.1 §3.4:空输入不提交,抖动提示
      return false;
    }
    widget.state.content = text;
    widget.state.status = "pending"; // 提交/重新编辑 → 回 pending(参与下一轮生成)
    widget.state.draft = "";
    widget.state.view = "expanded"; // 乐观更新;父级出海失败 → notifyError 回输入态
    render();
    // §16:cascade 本地组装(纯函数);出海 = 父组件订阅 submit 后的事
    const cascade = contextCascade(triggerPath ?? anchor?.path ?? "", {
      levels: cascadeLevels,
      providers: cascadeProviders ?? undefined,
    });
    widget.emit("submit", { anchor, content: text, quote: widget.state.quote, cascade });
    return true;
  }

  function _cancel() {
    if (widget.state.content) {
      widget.state.view = "expanded"; // 编辑取消 → 回展示态(内容不动)
      widget.state.draft = "";
      render();
      return;
    }
    widget.close(); // 新建取消 → 关闭(宿主:无内容即摘除)
  }

  /* 点外(裁决 C1;宿主点外委托调):composing 有内容提交/空取消;expanded 收起 */
  widget.submitOrCancel = () => {
    if (widget.state.view === "composing") {
      const text = String(_domDraft() ?? widget.state.draft ?? "").trim();
      if (text) _submit();
      else _cancel();
      return;
    }
    widget.close();
  };

  /* 父级出海失败回填:回输入态,草稿恢复(乐观更新回退) */
  widget.notifyError = (msg = "") => {
    widget.state.draft = widget.state.content;
    widget.state.view = "composing";
    render();
    _hint(msg || copy("w.bubble.fail"));
  };

  widget.open = () => {
    widget.emit("open", { anchor });
  };
  widget.close = () => {
    widget.emit("close", { anchor });
    widget.destroy();
  };
  /* 删除(action local skill;事件上行 → 父级摘除 + 后端删持久化) */
  widget.delete = () => {
    widget.emit("delete", { anchor });
  };

  if (surface === "card") {
    // card:宿主委托只挂 open(§1.4)——负载沿用本控件 open 事件语义({anchor})
    bindCardOpen(host, widget, { anchor });
  } else {
    host.addEventListener("input", (e) => {
      if (e.target.closest("[data-bubble-draft]")) {
        let v = String(e.target.value ?? "");
        if (v.length > MAX_LEN) {
          v = v.slice(0, MAX_LEN); // v2.1 §3.4:500 字截断 + 提示
          e.target.value = v;
          _hint(copy("w.bubble.toolong"));
        } else {
          _hint("");
        }
        widget.state.draft = v;
        // 添加钮空输入禁用:局部刷新,不重渲
        const send = host.querySelector("[data-bubble-send]");
        if (send) send.disabled = !v.trim();
        autosize(e.target);
      }
    });
    host.addEventListener("keydown", (e) => {
      if (!e.target.closest("[data-bubble-draft]")) {
        if (e.key === "Escape") widget.close();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault?.(); // Enter 提交(Shift+Enter 换行)
        _submit();
      }
      if (e.key === "Escape") {
        e.preventDefault?.();
        _cancel(); // Esc 取消(编辑 → 回展示态;新建 → 关闭)
      }
    });
    host.addEventListener("click", (e) => {
      if (e.target.closest("[data-bubble-x]")) return widget.close(); // ✕ 收起
      // 🗑 删除:两击确认(裁决 C3:第一击武装,第二击上行 delete)
      const del = e.target.closest("[data-bubble-del]");
      if (del) {
        if (del.dataset.armed === "1") return widget.delete();
        del.dataset.armed = "1";
        del.classList.add("is-armed");
        del.title = copy("w.bubble.del.confirm");
        return;
      }
      if (e.target.closest("[data-bubble-cancel]")) return _cancel();
      if (e.target.closest("[data-bubble-send]")) return _submit();
      if (e.target.closest("[data-bubble-edit]")) {
        widget.state.view = "composing"; // 编辑 → 输入态(草稿预填现有内容)
        widget.state.draft = widget.state.content;
        render();
        widget.focus();
      }
    });
  }

  widget.focus = () => {
    const input = _draftEl();
    input?.focus?.();
  };

  render();
  widget.register(anchorTo);
  return widget;
}

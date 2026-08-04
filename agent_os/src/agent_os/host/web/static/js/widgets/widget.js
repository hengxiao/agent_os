/* widget 实例工厂(docs/WIDGETS.md §1.2/§3 组合与装配规则)。

   实例 = {kind, state(可序列化), emit(event, payload), on(event, fn), destroy()}:
   - **事件上行**:emit 只发 def.events 声明过的事件(协议面),父组件用 on 订阅
     并把自己的 handler 映射成 app action——控件不知道 app 存在(数据流单向);
   - **不调后端**:本目录禁止出现 fetch((代码评审纪律,静态扫描进 widgets 测试);
   - **寻址**(APP-MODEL §14):path = 父级前缀 + 叶子段,由 mount 方给;
     登记/注销经 onRegister/onUnregister 回调——注册动作本身是宿主职责
     (widget 不调注册端点,那也算出海);
   - **context**(§17.7-3):register() 时把 def.context_provider 按 path 注册进
     cascade 运行时(scope "widget"),destroy 注销——"所有 widget 都有 context"
     的运行时面;无 path 的实例不可寻址,不注册。 */

import { registerContextProvider } from "./cascade.js";

/* 选区/焦点保留(docs/WIDGET-ARCH.md §1.3;W5.1 必答题):全量重渲前后存取
   聚焦元素的 selectionStart/End 与 activeElement,重渲后找回新元素并恢复。
   ``selector``:重渲后找回元素的定位面(真实 DOM 缺省用聚焦元素 tag;
   调用方可显式给——W-text/W-json 每宿主一个 textarea,直接传 "textarea";
   多输入控件迁移时按 data-field 细化)。 */
export function preserveSelection(host, fn, { selector = null } = {}) {
  const doc = host.ownerDocument ?? globalThis.document;
  const active = doc?.activeElement;
  const inside = Boolean(active) && (active === host || Boolean(host.contains?.(active)));
  const sel = selector ?? (inside ? String(active.tagName).toLowerCase() : null);
  const [s, e] = inside ? [active.selectionStart, active.selectionEnd] : [0, 0];
  fn();
  if (!inside || !sel) return;
  const next = host.querySelector(sel);
  if (!next) return;
  next.selectionStart = s;
  next.selectionEnd = e;
  next.focus?.();
}

export function createWidget(def, { state = {}, path = "", onRegister = null, onUnregister = null } = {}) {
  const listeners = {};
  let destroyed = false;
  let unregisterProvider = null;
  const widget = {
    kind: def.kind,
    def,
    path,
    state: { ...(def.state_defaults ?? {}), ...state },
    on(event, fn) {
      (listeners[event] ??= []).push(fn);
      return widget;
    },
    emit(event, payload) {
      if (destroyed || !(def.events ?? []).includes(event)) return; // 未声明事件不发
      for (const fn of listeners[event] ?? []) fn(payload);
    },
    /* 登记叶子路径(渲染时由 mount 方调用一次;摘要由宿主补,控件不编) */
    register(summaryHint = "") {
      if (destroyed || !path) return;
      if (onRegister) onRegister(path, { kind: def.kind, summary_hint: summaryHint });
      // §17.7-3:provider 随 register 进 cascade 运行时(读实时 state)
      unregisterProvider = registerContextProvider(path, "widget", (c) =>
        def.context_provider(widget.state, c)
      );
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      if (unregisterProvider) unregisterProvider(); // 注销随 destroy(§17.7-3)
      if (path && onUnregister) onUnregister(path);
      for (const key of Object.keys(listeners)) delete listeners[key];
    },
  };
  return widget;
}

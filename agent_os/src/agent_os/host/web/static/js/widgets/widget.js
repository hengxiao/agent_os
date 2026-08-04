/* widget 实例工厂(docs/WIDGETS.md §1.2/§3 组合与装配规则)。

   实例 = {kind, state(可序列化), emit(event, payload), on(event, fn), destroy()}:
   - **事件上行**:emit 只发 def.events 声明过的事件(协议面),父组件用 on 订阅
     并把自己的 handler 映射成 app action——控件不知道 app 存在(数据流单向);
   - **不调后端**:本目录禁止出现 fetch((代码评审纪律,静态扫描进 widgets 测试);
   - **寻址**(APP-MODEL §14):path = 父级前缀 + 叶子段,由 mount 方给;
     登记/注销经 onRegister/onUnregister 回调——注册动作本身是宿主职责
     (widget 不调注册端点,那也算出海)。 */

export function createWidget(def, { state = {}, path = "", onRegister = null, onUnregister = null } = {}) {
  const listeners = {};
  let destroyed = false;
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
      if (!destroyed && path && onRegister) onRegister(path, { kind: def.kind, summary_hint: summaryHint });
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      if (path && onUnregister) onUnregister(path);
      for (const key of Object.keys(listeners)) delete listeners[key];
    },
  };
  return widget;
}

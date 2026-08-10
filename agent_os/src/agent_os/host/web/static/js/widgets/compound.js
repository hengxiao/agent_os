/* Compound Widget 基座(docs/COMPOUND-WIDGET.md;C1)。

   复合 widget = 本身是 widget,又拥有子 widget:
   - ownership 唯一(§1):一个 instance 同一时刻只有一个 owner(attach 前查
     既有 owner 与祖先链防环,§10);
   - 渲染协议(§3):父 layout 纯函数只放 <div data-slot="id"> 占位,子 HTML
     一律不内联;父 innerHTML 后子 view 进占位;父重渲 → 子 view 重挂
     (detach→attach,canonical instance 与 state 不动);
   - 多视图/hard link(§5):view = {host, surface, live}——live 是该 kind
     mount 产出的**视图实例**(DOM 绑定),接线实现 = state 引用同一化
     (live.state = canonical.state)与 emit 转发(live.emit = canonical.emit),
     事件因此统一走 canonical → 事件闸门(§7-1)与 listeners 只有一份;
   - 管控三通道(§7):on_child_event 闸门(false 吞/true 上行/{payload} 改写)
     统一包成 compound 的 child_event 事件上行;child_context 在 cascade
     provider 注册面改写;surface/可见性由 slotRefs 与 mount_view 的 surface 定;
   - reparent(§6):path 重算 + onUnregister/onRegister 链 + cascade provider
     按新 path 重注册(随 child_context 一起迁)+ 全树 reparent 事件
     ({child, from, to})+ 同帧 detach→attach;
   - destroy(§4):递归销毁子树与全部 view;view.detach() 只摘一个视图,
     instance 不销毁(hidden 语义,state/context 照旧)。

   协议事件(自动声明,见下):child_event(闸门上行业面)、reparent(§6-3)。 */

import { registerContextProvider } from "./cascade.js";
import { getWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

/* 防环遍历(§10):target 是否在 rootInst 的子树内(ownership 是树) */
function _subtreeContains(rootInst, target) {
  const kids = rootInst._compound?.children;
  if (!kids) return false;
  for (const rec of kids.values()) {
    if (rec.inst === target || _subtreeContains(rec.inst, target)) return true;
  }
  return false;
}

export function createCompound(def, { state = {}, path = "", onRegister = null, onUnregister = null } = {}) {
  const cx = def.compound;
  if (typeof cx?.layout !== "function") {
    throw new Error(`compound ${def.kind}: compound.layout 必须是纯函数(§2/§3-1)`);
  }
  // 协议事件自动声明(child_event/reparent;def.events 未列时补齐——
  // createWidget 的 emit 过滤未声明事件,协议通道不能哑)
  const events = [...new Set([...(def.events ?? []), "child_event", "reparent"])];
  const inst = createWidget({ ...def, events }, { state, path, onRegister, onUnregister });
  const allow = new Set(cx.dynamic?.allow ?? []);
  const max = Number.isFinite(cx.dynamic?.max) ? cx.dynamic.max : Infinity;

  const children = new Map(); // id → rec{ id, kind, slot, surface, path, inst, views[], unregisterCtx, mountOpts }
  let seq = 0;

  /* ── 子实例装配 ─────────────────────────────────────────── */

  /* path 注册(onRegister 回调链)+ cascade provider(§7-2 child_context 改写面) */
  const _registerChild = (rec) => {
    onRegister?.(rec.path, { kind: rec.kind, summary_hint: "" });
    rec.unregisterCtx = registerContextProvider(rec.path, "widget", (c) => {
      const frag = rec.inst.def.context_provider(rec.inst.state, c);
      return cx.child_context ? cx.child_context(rec.inst, frag) : frag;
    });
  };
  const _unregisterChild = (rec) => {
    rec.unregisterCtx?.();
    rec.unregisterCtx = null;
    onUnregister?.(rec.path);
  };

  /* 事件闸门(§7-1):子 emit 先入闸门,再决定吞/上行/改写上行;
     通过后扇出该子全部 view(同 state 两面孔,W5.6 的实例化) */
  const _wireGate = (rec) => {
    for (const ev of rec.inst.def.events ?? []) {
      rec.inst.on(ev, (payload) => {
        const decision = cx.on_child_event ? cx.on_child_event(rec.inst, ev, payload) : true;
        if (decision === false) return; // 吞掉
        const out =
          decision && typeof decision === "object" && "payload" in decision ? decision.payload : payload;
        inst.emit("child_event", { child: rec.id, event: ev, payload: out });
        _fanout(rec); // update 扇出:各 view 按自己 surface 重渲(§5)
      });
    }
  };

  /* view 装配(§5):分两类——
     compound 子件:用实例自己的「父视图挂载」inst.mount_view(渲染它的
       layout+子树;此名与 hard link 入口 **link_view** 严格分离——
       早期版本把 link 入口也覆写成 mount_view,compound 子的 def.mount
       桥一旦回call实例 mount_view 就无限递归,真实浏览器 RangeError);
     leaf 子件:childDef.mount 全装 → 接线(state 引用同一化 + emit 转发)。
     view 挂载不给 path(挂成 ""):cascade provider 生命周期归 compound 统一
     (mount 内 widget.register 的注册/注销会把 view detach 误伤成 provider
     摘除——§5 hidden 语义:最后一个 view detach 时 instance 的 state/context 照旧) */
  const _linkView = (rec, host, { surface = rec.surface } = {}) => {
    let live;
    if (rec.inst._compound) {
      const pv = rec.inst.mount_view(host, { surface });
      live = {
        state: rec.inst.state,
        emit: rec.inst.emit.bind(rec.inst),
        update: () => rec.inst.relayout?.(),
        destroy: () => pv.detach(),
      };
    } else {
      const childDef = getWidgetDef(rec.kind);
      if (typeof childDef?.mount !== "function") {
        throw new Error(`compound: kind ${rec.kind} 无 mount 面(C1 def.mount)`);
      }
      live = childDef.mount(host, { ...rec.mountOpts, surface, path: "" });
      live.state = rec.inst.state; // 同 instance(§5):state 引用同一化
      live.emit = rec.inst.emit.bind(rec.inst); // 事件统一走 canonical(闸门/listeners 一份)
      // 状态同一化后首渲对齐(mount 用自己的 options 先渲过一遍,canonical state 才是事实源)
      if (typeof live.update === "function") live.update({});
      else host.innerHTML = childDef.render(rec.inst.state, { surface });
    }
    const view = { host, surface, live };
    view.detach = () => {
      live.destroy?.();
      host.innerHTML = ""; // detach 只摘视图:instance/state/context 不动(§4/§5)
      rec.views = rec.views.filter((v) => v !== view);
    };
    rec.views.push(view);
    return view;
  };

  const _fanout = (rec, { exceptView = null } = {}) => {
    for (const view of rec.views) {
      if (view === exceptView) continue;
      if (typeof view.live.update === "function") view.live.update({}); // text/json:选区保留重渲
      else view.host.innerHTML = rec.inst.def.render(rec.inst.state, { surface: view.surface });
    }
  };

  /* canonical 子实例创建(register 由 compound 统一做);kind 带 compound
     字段时递归 createCompound——否则预定义的 compound 子件会是「僵尸」
     (有 def 无 compound API,真实浏览器首崩的第二个根因) */
  const _spawn = (id, kind, { state: childState = {}, surface = "tab", slot = null, options = {} } = {}) => {
    const childDef = getWidgetDef(kind);
    if (!childDef) throw new Error(`compound: 未知 kind ${kind}(注册表惰性校验,§2)`);
    if (children.has(id)) throw new Error(`compound: 子件 id 重复 ${id}`);
    const childInst = childDef.compound
      ? createCompound(childDef, { state: childState })
      : createWidget(childDef, { state: childState });
    const rec = {
      id,
      kind,
      slot: slot ?? id,
      surface,
      path: inst.path ? `${inst.path}/${id}` : id,
      inst: childInst,
      views: [],
      unregisterCtx: null,
      mountOpts: options,
    };
    childInst.path = rec.path; // §1:寻址 = owner.path + 子段(身份即路径)
    childInst._compoundOwner = inst; // ownership 唯一(§1)
    childInst._compoundId = id;
    // §5 视图计数/取 view.live 的公开面(C3 需要:宿主调 mount 期方法)——
    // 仅 leaf 子件:compound 子有自身父视图账(inst.views,mount_view 记),
    // 覆盖会两账混一(fanout 撞上无 live 的父视图记录,真实浏览器 TypeError)
    if (!childInst._compound) childInst.views = rec.views;
    childInst.link_view = (host, opts = {}) => _linkView(rec, host, opts); // hard link 入口(§5;与 compound 自身 mount_view 严格分离)
    children.set(id, rec);
    _registerChild(rec);
    _wireGate(rec);
    _relayout(); // 结构变化 → 父 layout 重渲(§3-3 重挂)
    return childInst;
  };

  /* slot 元素查找:真实 DOM 恒走精确选择器;`[data-slot]`(无值)退化
     仅供无属性区域提取的环境(单 slot 场景;协议面不受影响)——退化命中
     的元素必须本身不带值(stub 虚拟区域无属性),真实 DOM 里带值占位是
     别的 slot,不能错挂(C3:动态子件 slot id 无占位时经退化撞上 doc 占位) */
  const _slotEl = (view, id) => {
    const exact = view.host.querySelector(`[data-slot="${id}"]`);
    if (exact) return exact;
    const bare = view.host.querySelector("[data-slot]");
    return bare && !bare.getAttribute?.("data-slot") ? bare : null;
  };

  /* 父视图装配(§3):layout 落 chrome,子 view 进 data-slot 占位;
     重渲 = 旧 view detach + 新 view attach(canonical/state 不动) */
  const _relayout = () => {
    for (const view of inst.views) {
      const slotRefs = Object.fromEntries(
        [...children.values()].map((r) => [r.slot, { path: r.path, kind: r.kind, surface: r.surface }])
      );
      view.host.innerHTML = cx.layout(inst.state, slotRefs); // §3-1:子 HTML 不内联
      for (const rec of children.values()) {
        const slotEl = _slotEl(view, rec.slot);
        if (!slotEl) {
          // 占位消失的 slot:摘下 owner 视图(藏起,不销毁 instance)
          for (const v of rec.views.filter((x) => x.owned)) v.detach();
          continue;
        }
        const owned = rec.views.filter((x) => x.owned);
        if (owned.length && owned[0].host === slotEl) continue; // 已在位(无重挂必要)
        for (const v of owned) v.detach(); // §3-3:重挂 = detach → attach 同帧
        const nv = _linkView(rec, slotEl, { surface: rec.surface });
        nv.owned = true; // owner slot 内视图(迁移随 layout;hard link 外加视图不受 layout 管)
      }
    }
  };

  /* ── 实例 API(§4)────────────────────────────────────────── */

  inst.child = (id) => children.get(id)?.inst ?? null;
  inst.children = () => [...children.values()].map((r) => r.inst);
  inst.children_snapshot = () =>
    [...children.values()].map((r) => ({ id: r.id, kind: r.kind, slot: r.slot, path: r.path }));

  inst.add_child = (kind, { state: childState = {}, surface = "tab", slot = null, options = {} } = {}) => {
    if (!allow.has(kind)) throw new Error(`compound ${def.kind}: kind ${kind} 不在 dynamic.allow 白名单(§4)`);
    if (children.size >= max) throw new Error(`compound ${def.kind}: 子件数达 max=${max}(§4)`);
    return _spawn(slot ?? `${kind}-${++seq}`, kind, { state: childState, surface, slot, options });
  };

  inst.attach_existing = (childInst, { slot = null, surface = "tab", options = {} } = {}) => {
    if (!childInst?.kind) throw new Error("compound: attach_existing 需要 widget 实例");
    if (childInst === inst) throw new Error("compound: 不能收养自己(§10 防环)");
    if (childInst._compoundOwner) throw new Error(`compound: ${childInst.kind} 已有 owner(§1 ownership 唯一)`);
    if (_subtreeContains(childInst, inst)) {
      throw new Error(`compound: ${childInst.kind} 的子树已含本节点(§10 防环)`);
    }
    const id = childInst._compoundId ?? `${childInst.kind}-${++seq}`;
    const rec = {
      id,
      kind: childInst.kind,
      slot: slot ?? id,
      surface,
      path: inst.path ? `${inst.path}/${id}` : id,
      inst: childInst,
      views: [],
      unregisterCtx: null,
      mountOpts: options,
    };
    childInst._compoundOwner = inst;
    childInst._compoundId = id;
    childInst.path = rec.path; // §6:path 重算(身份不变)
    // §5 公开面同 _spawn:仅 leaf 子件;重挂(move)时重指新 rec.views,防陈旧别名
    if (!childInst._compound) childInst.views = rec.views;
    childInst.link_view = (host, opts = {}) => _linkView(rec, host, opts); // hard link 入口(§5;同 _spawn 的分离纪律)
    children.set(id, rec);
    _registerChild(rec); // §6-2:新 path 注册 + provider 按新 path 重注册
    _wireGate(rec);
    _relayout();
    return childInst;
  };

  inst.remove_child = (id, { destroy = true } = {}) => {
    const rec = children.get(id);
    if (!rec) throw new Error(`compound: 无子件 ${id}`);
    for (const v of [...rec.views]) v.detach(); // view 先迁出(§6-1)
    _unregisterChild(rec); // 旧 path 注销(provider + onUnregister)
    children.delete(id);
    rec.inst._compoundOwner = null;
    if (destroy) {
      rec.inst.destroy?.(); // 递归销毁(§4;compound 子的 destroy 已覆写为递归)
    }
    _relayout();
    return rec;
  };

  inst.move_child = (id, newOwner, { slot = null, surface = null } = {}) => {
    const rec = children.get(id);
    if (!rec) throw new Error(`compound: 无子件 ${id}(move_child)`);
    const from = rec.path;
    const detached = inst.remove_child(id, { destroy: false }); // §6-1:detach(state 不动)
    try {
      newOwner.attach_existing(detached.inst, {
        slot: slot ?? rec.slot,
        surface: surface ?? rec.surface, // surface 可显式改挂(如 card→tab;文档 {slot} 的扩展项)
        options: rec.mountOpts,
      });
    } catch (err) {
      // 事务回滚(§4):attach 失败 → 挂回原 owner
      inst.attach_existing(detached.inst, { slot: rec.slot, surface: rec.surface, options: rec.mountOpts });
      throw err;
    }
    // §6-3:全树广播(新旧 owner 各自发;寻址依赖方据此改指)
    const payload = { child: id, from, to: detached.inst.path };
    inst.emit("reparent", payload);
    newOwner.emit?.("reparent", payload);
    return detached.inst;
  };

  /* 父自身的视图(compound 也是 widget;§3 渲染协议入口) */
  inst.views = [];
  inst.relayout = () => _relayout(); // compound 子件的 update 面(_linkView 用)
  inst.mount_view = (host, { surface = "tab" } = {}) => {
    const view = { host, surface };
    view.detach = () => {
      for (const rec of children.values()) {
        for (const v of rec.views.filter((x) => x.owned)) v.detach();
      }
      host.innerHTML = "";
      inst.views = inst.views.filter((v) => v !== view);
    };
    inst.views.push(view);
    _relayout();
    return view;
  };

  /* destroy(§4):递归销毁全部 view 与子树 */
  const _destroy = inst.destroy.bind(inst);
  inst.destroy = () => {
    for (const id of [...children.keys()]) {
      const rec = children.get(id);
      for (const v of [...rec.views]) v.detach();
      _unregisterChild(rec);
      children.delete(id);
      rec.inst.destroy?.();
    }
    for (const v of [...inst.views]) v.host.innerHTML = "";
    inst.views = [];
    _destroy();
  };

  inst._compound = { children }; // 防环遍历面(§10)

  // 预定义 slots(§4:随实例创建)
  for (const s of cx.slots ?? []) {
    _spawn(s.id ?? `${s.kind}-${++seq}`, s.kind, {
      state: s.state ?? {},
      surface: s.surface ?? "tab",
      slot: s.id ?? null,
      options: s.options ?? {},
    });
  }

  return inst;
}

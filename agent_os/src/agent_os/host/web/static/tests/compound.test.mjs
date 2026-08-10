/* Compound Widget 协议测试(docs/COMPOUND-WIDGET.md;C1):
   四能力逐块(预定义 slots 渲染 / 动态生灭(白名单+max 闸)/ move 后
   path·context 重注册且 state 不动 / hard link 双 view 同 instance 不同
   surface 且 update 扇出)+ 事件闸门三态 + context 改写 + 防环 +
   layout 纯函数不内联子 HTML。
   运行:node static/tests/compound.test.mjs */

import assert from "node:assert/strict";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url);

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const {
  registerWidgetDef, createCompound, contextCascade, mountTextEditor, mountLogViewer,
} = await import("../js/widgets/index.js");

/* 测试 compound:单 slot 布局(dom-stub 区域提取按 [data-slot] 无值命中;
   子内容断言走 slot 区域串)。_mkDef 给预定义 ed;_mkDyn 无预定义(动态面)。 */
function _layout() {
  return (state, slotRefs) =>
    `<div class="tc"><b>${state.title ?? ""}</b>` +
    Object.keys(slotRefs)
      .map((id) => `<div data-slot="${id}"></div>`)
      .join("") +
    `</div>`;
}

function _mkDef({ gate = null, childContext = null, dynamic = null, slots = null } = {}) {
  return registerWidgetDef({
    kind: `t-compound-${Math.random().toString(36).slice(2, 8)}`,
    v: 1,
    state_schema: { type: "object" },
    state_defaults: { title: "T" },
    actions: [],
    events: ["change"],
    aria: { role: "group" },
    surfaces: ["card", "tab"],
    compound: {
      slots: slots ?? [
        { id: "ed", kind: "text-editor", surface: "card",
          state: { value: "种子文本", field: "f", label: "f" }, options: { label: "f", field: "f" } },
      ],
      dynamic: dynamic ?? { allow: ["log-viewer"], max: 2 },
      layout: _layout(),
      ...(gate ? { on_child_event: gate } : {}),
      ...(childContext ? { child_context: childContext } : {}),
    },
  });
}
const _rec = (c, id) => c._compound.children.get(id);
const _slotHost = (c, id) => _rec(c, id)?.views?.[0]?.host ?? null;

{
  // ① 预定义 slots 渲染 + layout 纯函数不内联子 HTML(§3-1)
  const doc = makeDocument();
  globalThis.document = doc;
  const def = _mkDef();
  const c = createCompound(def, { path: "/root/pg" });
  const layoutOnly = def.compound.layout(c.state, { ed: { path: "/root/pg/ed", kind: "text-editor", surface: "card" } });
  assert.ok(!layoutOnly.includes("种子文本"), "layout 产出无子内容(不内联,§3-1)");
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const view = c.mount_view(host);
  assert.ok(host.innerHTML.includes('data-slot="ed"'), "父 chrome 带 data-slot 占位");
  assert.ok(_slotHost(c, "ed")?.innerHTML.includes("种子文本"), "子 view 进占位(card 面渲染)");
  assert.ok(_slotHost(c, "ed")?.innerHTML.includes('data-surface="card"'), "预定义 surface=card 生效");
  assert.ok(view, "mount_view 返回 view");
  // 父重渲:子 view 重挂,canonical instance 与 state 不动(§3-3)
  const edInst = c.child("ed");
  const stateRef = edInst.state;
  c.state.title = "T2";
  c.mount_view(host);
  assert.ok(host.innerHTML.includes("T2"), "父 layout 重渲");
  assert.ok(_slotHost(c, "ed")?.innerHTML.includes("种子文本"), "重挂后子内容在");
  assert.equal(c.child("ed"), edInst, "重挂不换 instance(§3-3)");
  assert.equal(edInst.state, stateRef, "state 引用不动(§3-3)");
}

{
  // ② 动态 add/remove:白名单 + max 闸 + destroy/detach 语义(§4)
  const doc = makeDocument();
  globalThis.document = doc;
  const c = createCompound(_mkDef({ slots: [] }), { path: "/root/dyn" });
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  c.mount_view(host);
  const log = c.add_child("log-viewer", { state: { lines: [{ kind: "info", text: "第一行" }] }, surface: "tab" });
  assert.ok(_rec(c, "log-viewer-1").views.length === 1, "动态子件挂进占位");
  assert.ok(_rec(c, "log-viewer-1").views[0].host.innerHTML.includes("第一行"), "动态子件渲染内容在");
  assert.equal(c.children_snapshot().length, 1, "动态 1 件");
  assert.throws(() => c.add_child("kv-editor"), /白名单/, "白名单外 kind 拒(§4)");
  const log2 = c.add_child("log-viewer", { state: { lines: [{ kind: "info", text: "第二行" }] }, surface: "tab" });
  assert.throws(() => c.add_child("log-viewer"), /max=2/, "max 闸(§4)");
  // remove destroy:false = detach(instance 活着可被别家 attach)
  let fired = 0;
  log.on("change", () => fired++);
  const rec = c.remove_child(log._compoundId, { destroy: false });
  assert.ok(!c.child(log._compoundId), "remove 后不在 children");
  log.emit("change", {});
  assert.equal(fired, 1, "detach 后实例仍活(emit 可达,§4)");
  assert.equal(rec.inst._compoundOwner, null, "detach 后无 owner(可被 attach)");
  // destroy:true = 递归销毁(emit 静默 + 视图摘尽)
  const rec2 = c._compound.children.get(log2._compoundId);
  c.remove_child(log2._compoundId, { destroy: true });
  log2.emit("change", {});
  assert.equal(fired, 1, "destroy:true 后 emit 静默(递归销毁,§4)");
  assert.equal(rec2.views.length, 0, "视图摘尽");
  assert.equal(c.children_snapshot().length, 0, "全部移除");
}

{
  // ③ move_child:path/context 重注册 + state 不动 + reparent 广播 + 回滚(§4/§6)
  const doc = makeDocument();
  globalThis.document = doc;
  const a = createCompound(_mkDef(), { path: "/root/a" });
  const b = createCompound(_mkDef({ slots: [] }), { path: "/root/b" });
  const hostA = doc.createElement("div");
  const hostB = doc.createElement("div");
  doc.body.appendChild(hostA);
  doc.body.appendChild(hostB);
  a.mount_view(hostA);
  b.mount_view(hostB);
  const ed = a.child("ed");
  const events = [];
  b.on("reparent", (p) => events.push(["b", p]));
  a.on("reparent", (p) => events.push(["a", p]));
  assert.equal(ed.path, "/root/a/ed", "初始 path = owner.path + 子段");
  assert.ok(contextCascade("/root/a/ed").cascade.length >= 1, "旧 path 有 provider");
  const stateRef = ed.state;
  a.move_child("ed", b);
  assert.equal(ed.path, "/root/b/ed", "§6:path 重算(身份不变)");
  assert.equal(ed.state, stateRef, "§6:state 不动(引用同一)");
  assert.equal(ed._compoundOwner, b, "ownership 移交");
  assert.ok(!(_slotHost(a, "ed")?.innerHTML ?? "").includes("种子文本"), "旧 owner 视图摘下");
  assert.ok(_slotHost(b, "ed")?.innerHTML.includes("种子文本"), "新 owner slot 挂入(同帧 detach→attach)");
  assert.equal(contextCascade("/root/a/ed").cascade.length, 0, "旧 path provider 注销");
  assert.ok(contextCascade("/root/b/ed").cascade.length >= 1, "新 path provider 重注册(§6-2)");
  assert.deepEqual(events.map(([w]) => w).sort(), ["a", "b"], "reparent 全树广播(新旧 owner 各发)");
  assert.ok(events.some(([, p]) => p.child === "ed" && p.from === "/root/a/ed" && p.to === "/root/b/ed"),
    "广播负载 {child, from, to}");
  // 事务回滚:目标 attach 失败 → 挂回原 owner(§4 两步任一失败回滚)
  const c2 = createCompound(_mkDef(), { path: "/root/c2" });
  const stow = c2.child("ed");
  const badOwner = { attach_existing: () => { throw new Error("attach boom"); } };
  assert.throws(() => c2.move_child("ed", badOwner), /boom/);
  assert.ok(c2.child("ed") === stow, "attach 失败回滚:子件挂回原 owner");
  assert.equal(stow.path, "/root/c2/ed", "回滚后 path 归位");
  assert.ok(contextCascade("/root/c2/ed").cascade.length >= 1, "回滚后 provider 复原");
}

{
  // ④ hard link:双 view 同 instance 不同 surface,update 扇出(§5)
  const doc = makeDocument();
  globalThis.document = doc;
  const c = createCompound(_mkDef(), { path: "/root/hl" });
  const slotHost = doc.createElement("div");
  const tabHost = doc.createElement("div");
  doc.body.appendChild(slotHost);
  doc.body.appendChild(tabHost);
  c.mount_view(slotHost); // slot 内 = card 面
  const ed = c.child("ed");
  const tabView = ed.link_view(tabHost, { surface: "tab" }); // hard link:同 instance 的 tab 视图
  assert.ok(_slotHost(c, "ed")?.innerHTML.includes('data-surface="card"'), "左 = card 面");
  assert.ok(tabHost.innerHTML.includes("<textarea"), "右 = tab 面(完整编辑器)");
  assert.equal(ed.state, tabView.live.state, "state 引用同一化(同 instance,§5)");
  assert.equal(typeof ed.link_view, "function", "hard link 入口在(§5,与 mount_view 分离)");
  // 右 tab 输入 → canonical emit → 扇出:左 card 同步变
  const ta = tabHost.querySelector("textarea");
  ta.value = "右侧改过的文本";
  tabHost.trigger("input", { target: ta });
  assert.equal(ed.state.value, "右侧改过的文本", "右 tab 编辑进 canonical state");
  assert.ok(_slotHost(c, "ed")?.innerHTML.includes("右侧改过的文本"), "update 扇出:左 card 同步变(§5)");
  // 事件不随 view 复制:change 只在 canonical 上一层(闸门也一份)
  const changes = [];
  c.on("child_event", (p) => changes.push(p));
  const ta2 = tabHost.querySelector("textarea"); // 扇出重渲后元素换新,重取
  ta2.value = "右侧改过的文本";
  tabHost.trigger("input", { target: ta2 });
  assert.equal(changes.filter((p) => p.event === "change").length, 1, "change 事件一份(不经 view 复制)");
  // 最后 view detach:instance 不销毁(hidden 语义,state/context 照旧)
  tabView.detach();
  assert.ok(!ed.destroyed, "view detach 不销毁 instance(§5)");
  assert.ok(contextCascade("/root/hl/ed").cascade.length >= 1, "detach 后 context 照旧");
}

{
  // ⑤ 事件闸门三态(§7-1):吞 / 上行 / 改写上行
  const doc = makeDocument();
  globalThis.document = doc;
  const log = [];
  // 吞
  let c = createCompound(_mkDef({ gate: () => false }), { path: "/g1" });
  c.on("child_event", (p) => log.push(p));
  c.child("ed").state.value = "x";
  const h1 = doc.createElement("div");
  doc.body.appendChild(h1);
  const v1 = c.child("ed").link_view(h1, { surface: "tab" });
  const ta1 = h1.querySelector("textarea");
  ta1.value = "y";
  h1.trigger("input", { target: ta1 });
  assert.equal(log.length, 0, "闸门 false = 吞掉,不上行");
  v1.detach();
  // 上行
  log.length = 0;
  c = createCompound(_mkDef({ gate: () => true }), { path: "/g2" });
  c.on("child_event", (p) => log.push(p));
  const h2 = doc.createElement("div");
  doc.body.appendChild(h2);
  c.child("ed").link_view(h2, { surface: "tab" });
  const ta2 = h2.querySelector("textarea");
  ta2.value = "z";
  h2.trigger("input", { target: ta2 });
  assert.equal(log.length, 1, "闸门 true = 上行");
  assert.equal(log[0].event, "change", "child_event 包事件名");
  assert.equal(log[0].payload.value, "z", "原 payload 透传");
  // 改写上行
  log.length = 0;
  c = createCompound(_mkDef({ gate: (child, ev, payload) => ({ payload: { ...payload, value: "[改写]" } }) }), { path: "/g3" });
  c.on("child_event", (p) => log.push(p));
  const h3 = doc.createElement("div");
  doc.body.appendChild(h3);
  c.child("ed").link_view(h3, { surface: "tab" });
  const ta3 = h3.querySelector("textarea");
  ta3.value = "raw";
  h3.trigger("input", { target: ta3 });
  assert.equal(log[0].payload.value, "[改写]", "闸门 {payload} 改写上行(§7-1)");
}

{
  // ⑥ context 改写(§7-2):cascade 收集子 fragment 时经父改写
  const doc = makeDocument();
  globalThis.document = doc;
  const c = createCompound(
    _mkDef({ childContext: (child, frag) => ({ ...frag, masked: true, by: "parent" }) }),
    { path: "/root/ctx" }
  );
  const env = contextCascade("/root/ctx/ed");
  assert.equal(env.cascade.length, 1, "子 provider 注册在子 path");
  assert.equal(env.cascade[0].data.masked, true, "child_context 改写生效(§7-2)");
  assert.equal(env.cascade[0].data.by, "parent", "改写字段在上行 fragment");
}

{
  // ⑦ 防环(§10)+ ownership 唯一(§1)
  const doc = makeDocument();
  globalThis.document = doc;
  const a = createCompound(_mkDef(), { path: "/r1" });
  const b = createCompound(_mkDef(), { path: "/r2" });
  a.attach_existing(b); // b 是 a 的子
  assert.throws(() => b.attach_existing(a), /防环/, "祖孙互挂 = 环,拒");
  assert.throws(() => a.attach_existing(a), /自己/, "自挂拒");
  const stray = createCompound(_mkDef(), { path: "/r3" });
  assert.throws(() => a.attach_existing(b), /已有 owner/, "ownership 唯一:有主实例拒挂");
  assert.ok(stray, "无主干线不受影响");
}

console.log("compound.test.mjs: all assertions passed");

{
  // C2 playground 冒烟:模块可载 + BUILD 三方一致(compound.html/widget.html/常量)
  const { bootPlayground } = await import("../js/compound-playground.js");
  assert.equal(typeof bootPlayground, "function", "playground 模块可载(bootPlayground 出口)");
  const { readFileSync } = await import("node:fs");
  const { BUILD } = await import("../js/widget-sandbox.js");
  const chtml = readFileSync(new URL("../compound.html", import.meta.url), "utf8");
  const whtml = readFileSync(new URL("../widget.html", import.meta.url), "utf8");
  for (const [name, html] of [["compound.html", chtml], ["widget.html", whtml]]) {
    const links = [...html.matchAll(/(?:href|src|from)="([^"]+)"/g)].map((m) => m[1]);
    const stale = links.filter((l) => l.includes("/static/") && !l.includes(`?v=${BUILD}`));
    assert.deepEqual(stale, [], `${name}:全部 /static 链接与 BUILD=${BUILD} 同步`);
  }
  assert.ok(chtml.includes("bootPlayground") && chtml.includes("pg-info"), "compound.html 入口与说明文案在");
}

console.log("compound.test.mjs: C2 playground smoke assertions passed");

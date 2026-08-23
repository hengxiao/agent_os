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
  registerWidgetDef, getWidgetDef, createCompound, contextCascade, mountTextEditor, mountLogViewer,
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

{
  // C3:doc-editor = 产品级 compound(docs/COMPOUND-WIDGET.md §9;协议在真实场景)
  const { mountDocEditor } = await import("../../../web_platform/static/doc-editor.js");
  const doc = makeDocument();
  globalThis.document = doc;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    if (String(url).endsWith("/annotations")) {
      const sent = JSON.parse(options.body ?? "{}");
      return { ok: true, json: async () => ({ ...sent, createdAt: "2026-08-13T00:00:00+00:00" }) };
    }
    return { ok: true, json: async () => ({}) };
  };
  const host = doc.createElement("div");
  host.innerHTML =
    `<div class="doc-toolbar"></div><div data-doc-preview="1"></div><div data-doc-chat-log="1"></div>` +
    `<input data-doc-chat-input="1"><span data-doc-chars="1"></span><span data-doc-dirty="1"></span><div data-doc-bubblebar="1"></div>`;
  doc.body.appendChild(host);
  const ed = mountDocEditor(host, { name: "demo.test", text: "# 标题\n首段内容\n\n次段内容", chat: [] },
    { seedFlows: [{ anchor: "doc.md#L2-L2", quote: "首段内容", content: "旧批注", status: "pending" }] });

  // 结构:预定义 doc 子件(md-viewer)+ 种子批注首挂出标(v4:_ensureEntry 幂等建)
  const snap = ed.compound.children_snapshot();
  assert.deepEqual(snap.map((s) => s.id), ["doc", "doc.md#L2-L2"], "预定义 doc + 种子批注出标(v4)");
  assert.equal(snap[0].path, "/doc/demo.test/doc", "子件 path = owner.path + 子段(§1)");
  assert.ok(getWidgetDef("doc-editor")?.compound, "doc-editor 注册进 registry(compound 形态)");

  // 段落锚点钮开泡:同锚点已有 entry(种子出标)→ 聚焦展开,不重复建;
  // view 经 link_view 挂进宿主壳(§5)
  const anchorBtn = new StubEl("button");
  anchorBtn.dataset.anchorBtn = "1";
  const paraBlock = new StubEl("div");
  paraBlock.dataset.anchor = "doc.md#L2-L2";
  anchorBtn.closest = (sel) =>
    sel === "[data-anchor-btn]" ? anchorBtn : sel === "[data-anchor]" ? paraBlock : null;
  paraBlock.parentNode = host.querySelector("[data-doc-preview]");
  host.querySelector("[data-doc-preview]").trigger("click", { target: anchorBtn });
  const snap2 = ed.compound.children_snapshot();
  assert.equal(snap2.length, 2, "同锚点重开 = 聚焦不新建(子件数不变)");
  assert.equal(snap2[1].kind, "chat-bubble", "批注子件 kind");
  assert.equal(snap2[1].path, "/doc/demo.test/doc.md#L2-L2", "批注 path 与信封寻址一致");
  const entry = ed.bubbles.get("doc.md#L2-L2");
  assert.equal(entry.el.hidden, false, "锚点钮点开 = 展开(出标时是藏着的)");
  assert.ok(entry.body.innerHTML.includes("w-bubble"), "气泡卡挂进壳(link_view)");
  assert.ok(entry.body.innerHTML.includes("旧批注"), "种子记录进卡(展示态 expanded)");
  assert.equal(entry.inst.state.status, "pending", "种子状态 pending");

  // child_context(§7-2):submit 负载 cascade 三级(§16 仍组装;annotations 端点
  // 不消费全文——落库只要 anchor/quote/content)
  let submitPayload = null;
  entry.inst.on("submit", (p) => (submitPayload = p));
  const input3 = new StubEl("input");
  input3.dataset.bubbleDraft = "";
  input3.parentNode = entry.body;
  input3.value = "这段太绕";
  entry.body.trigger("input", { target: input3 });
  entry.body.trigger("keydown", { target: input3, key: "Enter" });
  await new Promise((r) => setTimeout(r, 30));
  const annPost = calls.find((c) => String(c.url).endsWith("/annotations") && c.options?.method === "POST");
  assert.ok(annPost, "submit → child_event → annotations 出海(v4:save_annotation 路径)");
  const env = JSON.parse(annPost.options.body);
  assert.equal(env.anchor, "doc.md#L2-L2", "负载带锚点");
  assert.equal(env.content, "这段太绕", "负载带批注内容(无即时回复)");
  assert.equal(env.status, "pending", "提交回 pending");
  assert.equal(submitPayload.cascade.cascade[0].scope, "widget", "widget 级 fragment 在近端");
  assert.ok(submitPayload.cascade.cascade[0].data.paragraph.includes("首段内容"), "child_context 注入锚段原文");
  assert.ok(submitPayload.cascade.cascade[0].data.full_text.includes("次段内容"), "child_context 注入全文");
  assert.equal(submitPayload.cascade.cascade[1].data.name, "demo.test", "app 级文档态(宿主注册)");
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(entry.el.hidden, true, "提交成功 → 收起成标记(v2.1 §2.1 帧 3)");
  assert.equal(entry.marker.hidden, false, "段旁标记显出");

  // 可见性管控(§7-3):控件内 ✕ → close → 壳藏起 + 段旁标记;seen 前进
  const xBtn = new StubEl("button");
  xBtn.dataset.bubbleX = "1";
  xBtn.closest = (sel) => (sel === "[data-bubble-x]" ? xBtn : null);
  xBtn.parentNode = entry.body;
  entry.body.trigger("click", { target: xBtn });
  assert.equal(entry.el.hidden, true, "close 后壳藏起(可见性管控)");
  assert.equal(entry.marker.hidden, false, "段旁标记显出");

  // view source:state.view 驱动 layout;doc slot 进出(§3-3)
  assert.ok(!host.querySelector("[data-doc-preview]").innerHTML.includes('data-slot="doc"'),
    "preview 态无 doc slot(块 chrome)");
  const srcBtn = new StubEl("button");
  srcBtn.dataset.vm = "source";
  srcBtn.closest = (sel) => (sel === "[data-vm]" ? srcBtn : null);
  const seg = [...(host.querySelector(".doc-toolbar")?.children ?? [])].find((c) =>
    c.classList?.contains("doc-viewseg")
  );
  seg.trigger("click", { target: srcBtn });
  const pv = host.querySelector("[data-doc-preview]").innerHTML;
  assert.ok(pv.includes('data-slot="doc"'), "source 态 doc slot 出现(layout 按 state.view 切换)");
  assert.equal(ed.compound.state.view, "source", "模式进 compound state(可序列化)");
}

console.log("compound.test.mjs: C3 doc-editor compound assertions passed");

{
  // C4.1 badge 协议补丁(docs/COMPOUND-WIDGET §7 增补;DESKTOP-WIDGET §4):
  // 闸门放行的负载带 badge 字段 → 父记 state.badges[childId];slotRefs 附
  // badge;值变 → 父 relayout;0/null 摘徽;吞掉的事件不记账;state 可序列化。
  const doc = makeDocument();
  globalThis.document = doc;
  let lastRefs = null;
  let layoutCalls = 0;
  const def = registerWidgetDef({
    kind: `t-badge-compound-${Math.random().toString(36).slice(2, 8)}`,
    v: 1,
    state_schema: { type: "object" },
    state_defaults: {},
    actions: [],
    events: ["change"],
    aria: { role: "group" },
    surfaces: ["tab"],
    compound: {
      dynamic: { allow: ["log-viewer"], max: 3 },
      layout: (state, slotRefs) => {
        layoutCalls += 1;
        lastRefs = slotRefs;
        return (
          `<div class="tb">` +
          Object.entries(slotRefs)
            .map(([id, r]) => `<span data-tb="${id}">${r.badge ? `<i class="bdg">${r.badge}</i>` : ""}</span>`)
            .join("") +
          `</div>`
        );
      },
    },
  });
  const c = createCompound(def, { path: "/root/tb" });
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  c.mount_view(host);
  const kid = c.add_child("log-viewer", { state: { lines: [] } });
  const kidId = kid._compoundId;
  const callsAfterAdd = layoutCalls;

  // ① 放行负载带 badge:number → 记账 + slotRefs.badge + 父 relayout
  kid.emit("change", { lines: [], badge: 3 });
  assert.equal(c.state.badges[kidId], 3, "badge 记账进父 state.badges(闸门信息面)");
  assert.equal(lastRefs[kidId].badge, 3, "slotRefs 附 badge 元信息(layout 合规读面)");
  assert.ok(layoutCalls > callsAfterAdd, "badge 变 → 父 relayout(任务栏重渲)");
  assert.ok(host.innerHTML.includes("<i class=\"bdg\">3</i>"), "chrome 徽标随 relayout 上屏");

  // ② 同值不重复 relayout;变值再记
  const calls2 = layoutCalls;
  kid.emit("change", { lines: [], badge: 3 });
  assert.equal(layoutCalls, calls2, "badge 同值不 relayout(记账幂等)");
  kid.emit("change", { lines: [], badge: 5 });
  assert.equal(c.state.badges[kidId], 5, "badge 变值覆盖");

  // ③ 无 badge 字段的负载不记账;0 摘徽
  kid.emit("change", { lines: [] });
  assert.equal(c.state.badges[kidId], 5, "无 badge 字段不动账");
  kid.emit("change", { lines: [], badge: 0 });
  assert.ok(!(kidId in c.state.badges), "badge:0 = 清零摘徽");
  assert.equal(lastRefs[kidId].badge, null, "摘徽后 slotRefs.badge 回落 null");

  // ④ 闸门吞掉的事件不记账(false 决策优先于记账)
  const def2 = registerWidgetDef({
    kind: `t-badge-gate-${Math.random().toString(36).slice(2, 8)}`,
    v: 1,
    state_schema: { type: "object" },
    state_defaults: {},
    actions: [],
    events: ["change"],
    aria: { role: "group" },
    surfaces: ["tab"],
    compound: {
      dynamic: { allow: ["log-viewer"], max: 3 },
      layout: (s, r) => `<div></div>`,
      on_child_event: (child, event, payload) => (payload?.keep ? true : false),
    },
  });
  const c2 = createCompound(def2, { path: "/root/tg" });
  const kid2 = c2.add_child("log-viewer", { state: { lines: [] } });
  kid2.emit("change", { badge: 9 });
  assert.ok(!("badge" in (c2.state.badges ?? {})), "吞掉的事件不记账(§7-1 决策优先)");
  kid2.emit("change", { badge: 9, keep: true });
  assert.equal(c2.state.badges[kid2._compoundId], 9, "放行后正常记账");

  // ⑤ state.badges 可序列化(JSON 往返;协议铁律)
  const round = JSON.parse(JSON.stringify(c.state));
  assert.equal(round.badges[kidId] ?? null, null, "state JSON 往返(摘徽后无残留)");
  c.state.badges[kidId] = 7;
  assert.equal(JSON.parse(JSON.stringify(c.state)).badges[kidId], 7, "state.badges JSON 往返");
}

console.log("compound.test.mjs: C4.1 badge patch assertions passed");

{
  // C4.4:reparent 级联改址(_repathSubtree)——attach 后全后代 path 换新前缀,
  // provider 按新 path 重注(全树寻址唯一,DESKTOP-WIDGET §7「/root 通到叶」)
  const doc = makeDocument();
  globalThis.document = doc;
  const innerDef = registerWidgetDef({
    kind: `t-inner-${Math.random().toString(36).slice(2, 8)}`,
    v: 1, state_schema: { type: "object" }, state_defaults: {}, actions: [], events: [],
    aria: { role: "group" }, surfaces: ["tab"],
    compound: {
      slots: [{ id: "leaf", kind: "log-viewer", surface: "tab",
        state: { lines: [{ kind: "info", text: "叶" }] } }],
      layout: () => `<div data-slot="leaf"></div>`,
    },
  });
  const inner = createCompound(innerDef, { path: "/tmp/inner" });
  const leafBefore = inner.children_snapshot().map((s) => s.path);
  assert.deepEqual(leafBefore, ["/tmp/inner/leaf"], "attach 前后代按原前缀");
  const outer = createCompound(
    registerWidgetDef({
      kind: `t-outer-${Math.random().toString(36).slice(2, 8)}`,
      v: 1, state_schema: { type: "object" }, state_defaults: {}, actions: [], events: [],
      aria: { role: "group" }, surfaces: ["tab"],
      compound: { dynamic: { allow: [innerDef.kind], max: 2 }, layout: () => `<div></div>` },
    }),
    { path: "/root" }
  );
  inner._compoundId = "inner"; // 身份指定(attach 后 id = slot;与 desktop 驱动同手法)
  outer.attach_existing(inner, { slot: "inner" });
  assert.equal(inner.path, "/root/inner", "reparent 后本实例 path 重算(§6)");
  assert.deepEqual(
    inner.children_snapshot().map((s) => s.path),
    ["/root/inner/leaf"],
    "后代 path 级联改址(全树寻址唯一)"
  );
  const { contextCascade } = await import("../js/widgets/index.js");
  const cas = contextCascade("/root/inner/leaf");
  assert.ok((cas.cascade ?? []).some((f) => f.scope === "widget"), "后代 provider 按新 path 重注可取");
  const stale = contextCascade("/tmp/inner/leaf");
  assert.ok(!(stale.cascade ?? []).some((f) => f.scope === "widget"), "旧 path provider 已注销(无幽灵注册)");
}


/* ── P3 生成工作流(v2.1 §4/§5):生成钮四态/状态栏/生成链 → Diff 视图 → 采纳 ── */
{
  const { mountDocEditor } = await import("../../../web_platform/static/doc-editor.js");
  const doc = makeDocument();
  globalThis.document = doc;
  const genCalls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    if (u.endsWith("/generate")) {
      genCalls.push(JSON.parse(options.body ?? "{}"));
      return { ok: true, json: async () => ({
        newVersion: 4, versionId: "v004",
        annotationResults: [{ annotationId: "doc.md#L2-L2", status: "applied", aiNote: "已改写" }],
        diff: "--- a@v003\n+++ b@v004\n@@ -1,2 +1,2 @@\n-段落一\n+段落一改过\n 段落二",
      }) };
    }
    if (u.endsWith("/annotations")) {
      return { ok: true, json: async () => ([
        { anchor: "doc.md#L2-L2", quote: "段落一", content: "改这段", status: "applied" },
      ]) };
    }
    return { ok: true, json: async () => ({}) };
  };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const ed = mountDocEditor(host, { name: "demo.p3", text: "# 标题\n段落一\n\n段落二", chat: [], versions: ["v003"] },
    { seedFlows: [{ anchor: "doc.md#L2-L2", quote: "段落一", content: "改这段", status: "pending" }] });

  // 四态:有 pending → 可点 + 计数徽标;状态栏 = 字数 · v003 · 1 条待处理
  const genBtn = host.querySelector("[data-doc-generate]");
  assert.ok(genBtn, "生成钮在工具条");
  assert.equal(genBtn.disabled, false, "有 pending → 可点");
  assert.equal(host.querySelector("[data-doc-gen-n]").textContent, "1", "计数徽标 = pending 数");
  assert.equal(host.querySelector("[data-doc-ver]").textContent, "v003", "状态栏版本位");
  assert.ok(host.querySelector("[data-doc-pending]").textContent.includes("1"), "状态栏 pending 计数");

  // 生成链:点击 → POST generate(baseVersion=3)→ Diff 自动切(摘要/来源卡/行)
  const genClick = new StubEl("button"); // region 元素无 dataset,合成驱动(dom-stub 面)
  genClick.dataset.docGenerate = "1";
  genClick.closest = (sel) => (sel === "[data-doc-generate]" ? genClick : null);
  genClick.parentNode = host;
  host.trigger("click", { target: genClick });
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(genCalls.length, 1, "generate 出海一次");
  assert.equal(genCalls[0].baseVersion, 3, "baseVersion = 当前版本号");
  const dv = host.querySelector("[data-doc-diffview]");
  assert.equal(dv.hidden, false, "生成后自动切 Diff 视图");
  assert.ok(host.querySelector("[data-doc-preview]").hidden, "预览藏起");
  assert.ok(dv.innerHTML.includes("已应用"), "摘要卡(统计文案)");
  assert.ok(dv.innerHTML.includes("doc-diff-src"), "来源批注卡");
  assert.ok(dv.innerHTML.includes('data-kind="add"'), "diff 新增行");
  assert.ok(dv.innerHTML.includes("已改写"), "aiNote 进来源卡");
  assert.ok(host.querySelector("[data-doc-chatpending]").textContent.includes("已应用"), "状态栏生成摘要");

  // 采纳 → 回预览,diff 钮藏
  const accept = new StubEl("button");
  accept.dataset.diffAccept = "1";
  accept.closest = (sel) => (sel === "[data-diff-accept]" ? accept : null);
  accept.parentNode = dv;
  host.trigger("click", { target: accept });
  assert.equal(host.querySelector("[data-doc-preview]").hidden, false, "采纳 → 回预览");
  assert.equal(dv.hidden, true, "diffview 藏");
  console.log("compound.test.mjs: P3 generate workflow assertions passed");
}
console.log("compound.test.mjs: C4.4 reparent cascade assertions passed");

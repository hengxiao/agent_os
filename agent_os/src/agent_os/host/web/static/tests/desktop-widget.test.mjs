/* Desktop widget 结构测试(docs/DESKTOP-WIDGET.md;C4.1):
   def 注册面(DESKTOP_DEF/SUPERVISOR_INBOX_DEF)+ layout 三分支(纯函数直调:
   桌面/单窗/任务栏恒在 + badge/顺序)+ 集成(createCompound:预定义 inbox、
   add_child、activate/minimize hidden 语义、remove_child、badge 记账、
   taskbar_order 持久、state 可序列化)。
   说明:stub 的区域提取按 [data-slot] 无值命中,desktop 多 slot(inbox 恒在)
   在 stub 不可区分——view 级 detach/重挂断言走 tests-ui(test_desktop.py);
   此处断 chrome 产出(layout 纯函数)与 canonical state 语义。
   (旧壳 app.js 的 M5 桌面化测试在同目录 desktop.test.mjs,两文件并存;
   旧壳 C4.4 退役时旧文件随退。)
   运行:node static/tests/desktop-widget.test.mjs */

import assert from "node:assert/strict";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url);

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const {
  getWidgetDef, registerWidgetDef, createCompound, createWidget,
  DESKTOP_DEF, SUPERVISOR_INBOX_DEF, renderDesktopLayout, renderSupervisorInbox, orderedIds,
} = await import("../js/widgets/index.js");

/* 演示 app 占位 def(与 desktop-page.js 同 kind;registry 进程级) */
const _appDef = (kind, label) =>
  registerWidgetDef({
    kind,
    v: 1,
    state_schema: { type: "object" },
    state_defaults: { log: [], draft: "", filter: "" },
    actions: [],
    events: ["change"],
    aria: { role: "application", label },
    surfaces: ["tab"],
    mount: (host) => {
      const w = createWidget(getWidgetDef(kind));
      const render = () => {
        host.innerHTML =
          `<div class="w-app" data-app="${kind}">` +
          (w.state.log ?? []).map((m) => `<div class="w-app-msg">${m.text}</div>`).join("") +
          `<input data-draft value="${w.state.draft ?? ""}"></div>`;
      };
      w.update = () => render();
      render();
      return w;
    },
  });
_appDef("conversation", "对话");
_appDef("runs-explorer", "运行");

{
  // ① def 结构(§2):注册面 + state_defaults + events + dynamic.allow
  assert.equal(getWidgetDef("desktop"), DESKTOP_DEF, "desktop 注册进 registry");
  assert.equal(getWidgetDef("supervisor-inbox"), SUPERVISOR_INBOX_DEF, "supervisor-inbox 注册进 registry");
  const d = DESKTOP_DEF.state_defaults;
  for (const k of ["wallpaper", "active", "icon_order", "taskbar_order"]) {
    assert.ok(k in d, `state_defaults.${k} 在(§2)`);
  }
  assert.equal(d.active, null, "active 缺省 null(桌面分支)");
  assert.deepEqual(DESKTOP_DEF.surfaces, ["tab"], "desktop 只有完整面(§2)");
  for (const ev of ["activate", "close", "child_event", "reparent"]) {
    assert.ok(
      DESKTOP_DEF.events.includes(ev) || ["child_event", "reparent"].includes(ev),
      `events 含 ${ev}(child_event/reparent 基座自动补)`
    );
  }
  assert.deepEqual(
    DESKTOP_DEF.compound.dynamic.allow,
    ["conversation", "doc-editor", "skills-explorer", "runs-explorer",
      "tools-explorer", "lab", "debug-console"],
    "dynamic.allow = §2 白名单"
  );
  assert.deepEqual(
    DESKTOP_DEF.compound.slots.map((s) => [s.id, s.kind, s.surface]),
    [["inbox", "supervisor-inbox", "card"]],
    "预定义系统件 inbox(托盘 card 面)"
  );
  assert.equal(typeof DESKTOP_DEF.compound.layout, "function", "layout 在 def(§3)");
}

{
  // ② layout 三分支(纯函数直调;slotRefs 伪造)
  const refs = {
    inbox: { path: "/root/inbox", kind: "supervisor-inbox", surface: "card", badge: 2 },
    conversation: { path: "/root/conversation", kind: "conversation", surface: "tab", badge: null,
      title: "对话 · abc123" },
    "runs-explorer": { path: "/root/runs-explorer", kind: "runs-explorer", surface: "tab", badge: 4 },
  };
  const before = JSON.stringify(refs);

  // 分支一:桌面(active null)——图标栅格 + 无窗口;任务栏恒在 + badge
  const desk = renderDesktopLayout({ wallpaper: "default", active: null, icon_order: [], taskbar_order: [] }, refs);
  assert.ok(desk.includes("dt-desk"), "分支一:桌面区在");
  assert.ok(!desk.includes("dt-win"), "分支一:无窗口 chrome");
  assert.equal(desk.match(/data-desk-open=/g).length, 2, "图标栅格 = 每个动态子件一图标");
  assert.ok(desk.includes("dt-taskbar"), "任务栏恒在(分支三)");
  assert.ok(desk.includes('data-slot="inbox"'), "inbox 系统件 slot 在托盘");
  assert.ok(desk.includes('data-desk-inbox-badge="1">2<'), "inbox badge 上屏(闸门记账读面)");
  assert.ok(desk.includes('data-desk-task-badge="runs-explorer">4<'), "任务行 badge 按 slotRefs 上屏");
  assert.ok(!desk.includes('data-desk-task-badge="conversation"'), "无 badge 的子件无徽标");
  assert.ok(!desk.includes('data-desk-open="inbox"'), "inbox 系统件不进图标/任务行");
  assert.ok(desk.includes("对话 · abc123"), "per-instance 题名:slotRefs.title 优先(C4.4)");
  assert.ok(desk.includes(">运行<"), "无 title 回落 aria.label(题名优先级)");

  // 分支二:单窗(active=conversation)——标题栏 + 激活 slot;无图标栅格
  const win = renderDesktopLayout({ wallpaper: "default", active: "conversation", icon_order: [], taskbar_order: [] }, refs);
  assert.ok(win.includes("dt-titlebar"), "分支二:窗口标题栏在");
  assert.ok(win.includes("data-desk-min"), "标题栏最小化钮在");
  assert.ok(win.includes('data-slot="conversation"'), "激活子件 slot 在");
  assert.ok(!win.includes("data-desk-open"), "分支二:无图标栅格");
  assert.ok(win.includes('data-desk-task="conversation" data-active="1"'), "任务行激活态");
  assert.ok(win.includes('data-desk-task="runs-explorer" data-active="0"'), "他行非激活");
  assert.ok(win.includes('data-slot="inbox"'), "单窗态 inbox 恒在(托盘)");

  // 顺序:taskbar_order 驱动任务行序;icon_order 驱动图标序
  const ordered = renderDesktopLayout(
    { wallpaper: "default", active: null, icon_order: ["runs-explorer", "conversation"], taskbar_order: ["runs-explorer", "conversation"] },
    refs
  );
  assert.ok(
    ordered.indexOf('data-desk-task="runs-explorer"') < ordered.indexOf('data-desk-task="conversation"'),
    "taskbar_order 驱动任务行序"
  );
  assert.ok(
    ordered.indexOf('data-desk-open="runs-explorer"') < ordered.indexOf('data-desk-open="conversation"'),
    "icon_order 驱动图标序"
  );

  // 防御:active 指向已删子件 → 回落桌面分支
  const stray = renderDesktopLayout({ wallpaper: "default", active: "ghost", icon_order: [], taskbar_order: [] }, refs);
  assert.ok(stray.includes("dt-desk") && !stray.includes("dt-win"), "active 失效 → 桌面分支");

  // 纯函数:同入同出 + 不改 slotRefs/state
  const again = renderDesktopLayout({ wallpaper: "default", active: null, icon_order: [], taskbar_order: [] }, refs);
  assert.equal(again, desk, "layout 纯函数:同入同出");
  assert.equal(JSON.stringify(refs), before, "layout 不改 slotRefs");
}

{
  // ③ orderedIds:order 先(滤已删),新增补尾
  assert.deepEqual(orderedIds(["a", "b", "c"], ["c", "a"]), ["c", "a", "b"], "order 先");
  assert.deepEqual(orderedIds(["a", "b"], ["ghost", "b"]), ["b", "a"], "已删 id 滤除");
  assert.deepEqual(orderedIds(["a", "b"], null), ["a", "b"], "无 order 按注册序");
}

{
  // ④ inbox 薄壳渲染(纯):card 计数 + 最近 3 条;空态只有头部
  const html = renderSupervisorInbox(
    { pending: [{ id: "d1", text: "批准发布" }, { id: "d2", text: "确认删除" }, { id: "d3", text: "第三条" }, { id: "d4", text: "第四条" }] },
    { surface: "card" }
  );
  assert.ok(html.includes('data-inbox-n="1">4<'), "inbox 计数 = pending 数(badge 数据源)");
  assert.ok(html.includes("第三条") && !html.includes("第四条"), "card 面只出最近 3 条(+留痕)");
  assert.ok(html.includes("+1"), "截断留痕");
  const empty = renderSupervisorInbox({ pending: [] }, { surface: "card" });
  assert.ok(empty.includes("w-inbox-head") && !empty.includes("w-inbox-item"), "空态只有头部(C4.1 薄壳)");
}

{
  // ⑤ 集成:createCompound(desktop)——预定义 inbox / add_child / activate /
  // minimize hidden(canonical state 不动)/ remove_child / badge 记账 / 序列化
  const doc = makeDocument();
  globalThis.document = doc;
  const inst = createCompound(DESKTOP_DEF, { path: "/root" });
  const snap0 = inst.children_snapshot();
  assert.deepEqual(snap0.map((s) => s.id), ["inbox"], "预定义 inbox 随实例创建(§4)");
  assert.equal(snap0[0].path, "/root/inbox", "寻址 = /root/<子段>(§2)");

  const host = doc.createElement("div");
  doc.body.appendChild(host);
  inst.mount_view(host);
  assert.ok(host.innerHTML.includes("dt-root"), "mount_view 落 desktop chrome");
  assert.ok(host.innerHTML.includes("dt-desk"), "初始 = 桌面分支(active null)");

  inst.add_child("conversation", { slot: "conversation" });
  inst.add_child("runs-explorer", { slot: "runs-explorer" });
  assert.deepEqual(
    inst.children_snapshot().map((s) => s.id),
    ["inbox", "conversation", "runs-explorer"],
    "add_child 动态生灭(白名单内)"
  );
  assert.throws(() => inst.add_child("chat-bubble"), /白名单/, "白名单外 kind 拒(§4)");

  // activate → 单窗分支;子 state 写入(canonical)后最小化 → 重开逐字在
  const conv = inst.child("conversation");
  inst.state.active = "conversation";
  inst.relayout();
  assert.ok(host.innerHTML.includes("dt-win"), "activate → 单窗分支");
  assert.ok(host.innerHTML.includes('data-slot="conversation"'), "激活 slot 上屏");
  conv.state.draft = "逐字验证";
  conv.state.log = [{ role: "user", text: "第一句话" }];
  inst.state.active = null; // 最小化 = hidden 语义(§5:instance 活着)
  inst.relayout();
  assert.ok(!host.innerHTML.includes("dt-win"), "最小化 → 回桌面分支");
  inst.state.active = "conversation";
  inst.relayout();
  assert.equal(conv.state.draft, "逐字验证", "hidden 语义:重开 draft 逐字在(canonical 不动)");
  assert.equal(conv.state.log[0].text, "第一句话", "hidden 语义:log 在");

  // badge 记账(§7 补丁):子 emit 带 badge → 父 state.badges + chrome 徽标
  conv.emit("change", { badge: 3 });
  assert.equal(inst.state.badges.conversation, 3, "desktop 闸门 badge 记账");
  assert.ok(host.innerHTML.includes('data-desk-task-badge="conversation">3<'), "任务行徽标随 relayout 上屏");

  // 重排:taskbar_order state 变更 → relayout(html 序随 state)
  inst.state.active = null;
  inst.state.taskbar_order = ["runs-explorer", "conversation"];
  inst.relayout();
  const html2 = host.innerHTML;
  assert.ok(
    html2.indexOf('data-desk-task="runs-explorer"') < html2.indexOf('data-desk-task="conversation"'),
    "taskbar_order 持久于 state 且驱动重渲"
  );

  // 关闭 = remove_child(destroy):snapshot/html 同步;重开 = 全新 instance
  inst.remove_child("conversation", { destroy: true });
  assert.ok(!inst.child("conversation"), "remove_child 后 instance 不在");
  assert.ok(!host.innerHTML.includes('data-desk-task="conversation"'), "任务行随 remove 消失");
  const conv2 = inst.add_child("conversation", { slot: "conversation" });
  assert.notEqual(conv2, conv, "重开 = 全新 instance(§7 验收)");
  assert.equal(conv2.state.draft, "", "新 instance state 干净");

  // state 可序列化(JSON 往返)
  const round = JSON.parse(JSON.stringify(inst.state));
  assert.deepEqual(round.taskbar_order, ["runs-explorer", "conversation"], "state JSON 往返");
  assert.equal(round.badges.conversation ?? null, null, "badges 随 state 序列化(离树清讫)");

  // activate/close 事件面(§2;宿主适配层代发语义)
  const seen = [];
  inst.on("activate", (p) => seen.push(["activate", p.id]));
  inst.on("close", (p) => seen.push(["close", p.id]));
  inst.emit("activate", { id: "runs-explorer" });
  inst.emit("close", { id: "runs-explorer" });
  assert.deepEqual(seen, [["activate", "runs-explorer"], ["close", "runs-explorer"]], "activate/close 事件可发");
}

{
  // ⑥ C4.4:slotRefs.title 元信息(owner 提供)+ child_context 全局注入
  const doc = makeDocument();
  globalThis.document = doc;
  const inst = createCompound(DESKTOP_DEF, { path: "/root" });
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  inst.mount_view(host);
  inst.add_child("runs-explorer", { slot: "runs-explorer", title: "运行·自题" });
  assert.ok(host.innerHTML.includes("运行·自题"), "add_child title 上屏(slotRefs.title,盖过 aria.label)");
  const { contextCascade } = await import("../js/widgets/index.js");
  const cascade = contextCascade("/root/runs-explorer");
  const wfrag = (cascade.cascade ?? []).find((f) => f.scope === "widget");
  assert.ok(wfrag?.data?.desktop, "child_context 注入 desktop 级上下文(§7-2,C4.4)");
  assert.equal(wfrag.data.desktop.user, "local", "desktop 上下文 user");
  assert.ok(wfrag.data.desktop.theme && wfrag.data.desktop.at, "desktop 上下文 theme/at(真实值)");
  JSON.stringify(wfrag.data.desktop); // 可序列化(协议铁律)
}

console.log("desktop-widget.test.mjs: all assertions passed");

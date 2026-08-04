/* 桌面化 root widget 单测(docs/APP-MODEL.md M5 增补;Windows 桌面式):
   桌面主区(无激活 tab = 桌面;图标网格/最近关闭/壁纸开关)、
   图标点击 = tab 条同一 action(同源断言:legacy/最近关闭 = shell.tab.open,
   对话 = shell.tab.focus)、开始菜单同数据源、窗口标题栏(最小化回桌面 tab 保留,
   ✕ 走 M5 关闭≠销毁)、系统托盘(live/主题/收件箱,role=button + aria-label)、
   M5 布局持久化(icon_mode)不回归。
   运行:node static/tests/desktop.test.mjs */

import assert from "node:assert/strict";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url); // "/static/js/" → 旧 web 共享模块

const { makeDocument, StubEl } = await import("./dom-stub.mjs");

/* ── boot(fetch + DOM stub;与 platform.test.mjs 同哲学,只留本测试用的面)── */

const doc = makeDocument();
globalThis.document = doc;
doc.querySelector = (sel) => doc.body.querySelector(sel);
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

// index.html 骨架元素(与 web_platform/static/index.html 同 id)
const mk = (tag, id) => {
  const el = doc.createElement(tag);
  el.setAttribute("id", id);
  doc.body.appendChild(el);
  return el;
};
mk("div", "themes");
mk("button", "startBtn");
mk("div", "startMenu");
mk("div", "tabs");
mk("div", "launcher");
mk("select", "sessionSel");
mk("button", "newSession");
mk("div", "tray");
mk("div", "titlebar");
mk("div", "desktop");
mk("div", "log");
mk("div", "detailHost");
mk("div", "inputBar");
mk("textarea", "intent");
mk("button", "send");

const calls = [];
// shell 唯一事实源(JS 镜像,语义与后端 mutator 对齐;含 desktop 扩展键)
const shellState = {
  tabs: [{ id: "conv", instance_id: "", kind: "conversation", ref: "conv", title: "对话" }],
  active_tab: "conv", theme: "", sessions: [], layout: { order: [], icon_mode: false },
  widgets: {}, desktop: { pinned: [], wallpaper: true },
};
const shellMutate = (action, args) => {
  const s = shellState;
  if (action === "shell.tab.open") {
    const ex = s.tabs.find((t) => t.kind !== "conversation" && t.kind === args.kind && t.ref === args.ref);
    if (ex) s.active_tab = ex.id;
    else {
      s.tabs.push({ id: args.id, instance_id: args.instance_id ?? "", kind: args.kind, ref: args.ref, title: args.title });
      s.active_tab = args.id;
    }
  } else if (action === "shell.tab.focus") s.active_tab = args.tab;
  else if (action === "shell.tab.close") {
    s.tabs = s.tabs.filter((t) => t.id !== args.tab);
    if (s.active_tab === args.tab) s.active_tab = "conv";
  } else if (action === "shell.tab.minimize") s.active_tab = ""; // 最小化 = 无激活 tab(桌面)
  else if (action === "shell.desktop.set") {
    if ("wallpaper" in args) s.desktop.wallpaper = Boolean(args.wallpaper);
  } else if (action === "shell.layout.set") s.layout.icon_mode = Boolean(args.icon_mode);
  else if (action === "shell.theme.set") s.theme = args.theme ?? "";
  return { ok: true, instance: { id: "app-shell", kind: "shell", state: s } };
};
globalThis.fetch = async (path, options = {}) => {
  const url = String(path);
  calls.push({ url, method: options.method ?? "GET", body: options.body });
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (url === "/platform/api/shell") {
    return reply({ id: "app-shell", kind: "shell", ref: "shell", title: "shell",
      state: shellState, created_by: "", created_at: 1 });
  }
  if (url.startsWith("/platform/api/apps/app-shell/actions/")) {
    const action = url.split("/actions/")[1];
    return reply(shellMutate(action, JSON.parse(options.body ?? "{}").args ?? {}));
  }
  if (url === "/platform/api/widgets/register" || url === "/platform/api/widgets/unregister") {
    return reply({ ok: true });
  }
  if (url === "/platform/api/sessions" && !options.method) {
    return reply([{ id: "s1", title: "晚餐技能", messages: 0, cards: 0, created_at: 1, last_at: 2 }]);
  }
  if (url === "/platform/api/sessions/s1") {
    return reply({ id: "s1", title: "晚餐技能", messages: [] });
  }
  if (url === "/platform/api/sessions/s1/decisions/present" || url === "/platform/api/sessions/s1/runs/present") {
    return reply({ presented: [] });
  }
  if (url === "/platform/api/apps/spawn") {
    const body = JSON.parse(options.body ?? "{}");
    return reply({ instance: { id: `app-spawn-${body.ref}`, kind: body.kind, ref: body.ref,
      title: body.title, state: body.state ?? {}, created_by: body.created_by ?? "", created_at: 1 },
      opened: true });
  }
  throw new Error(`未 stub 的请求: ${options.method ?? "GET"} ${url}`);
};

// legacy 挂载注入(node 测试的替代装配口;skills/tools 两个桌面图标用到)
const legacyMounted = [];
globalThis.__legacyMounts = {
  skills: (host) => { legacyMounted.push("skills"); host.innerHTML = `<div>技能页</div>`; return () => {}; },
  tools: (host) => { legacyMounted.push("tools"); host.innerHTML = `<div>工具页</div>`; return () => {}; },
};

await import("../../../web_platform/static/app.js");
const probe = globalThis.__platform;
assert.ok(probe, "测试探针在");
const tick = async (n = 4) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
};
await tick();

const deskHtml = () => doc.querySelector("#desktop").innerHTML;
const trayHtml = () => doc.querySelector("#tray").innerHTML;
const tabsHtml = () => doc.querySelector("#tabs").innerHTML;
const titleHtml = () => doc.querySelector("#titlebar").innerHTML;
const shellCalls = (action) => calls.filter((c) => c.url.includes(`/actions/${action}`));

/* 点击驱动(事件委托在 document;StubEl 挂 body 以支持 closest 上溯) */
const clickOn = (dataset) => {
  const el = new StubEl("button");
  Object.assign(el.dataset, dataset);
  el.parentNode = doc.body;
  doc.trigger("click", { target: el });
  return el;
};

/* ── 基态:对话激活(窗口最大化),桌面不显示 ───────────────────── */

assert.equal(probe.state.active, "conv", "boot 回落对话(M5 语义不动)");
assert.ok(doc.querySelector("#desktop").hidden, "有激活 tab 时桌面隐藏");
assert.ok(!doc.querySelector("#titlebar").hidden, "激活态标题栏在");
assert.ok(titleHtml().includes("对话"), "标题栏 = 激活 tab 标题");
assert.ok(!titleHtml().includes("data-win-close"), "对话不可关闭(与 tab 条一致:无 ✕)");
assert.ok(titleHtml().includes("data-win-min"), "标题栏有最小化");
assert.equal(doc.body.dataset.desktop, "0", "非桌面态样式钩子");

/* ── 桌面渲染:无激活 tab = 桌面主屏(网格/壁纸/透明底钩子)────── */

probe.state.active = ""; // 最小化后的状态(此处直接置位,后文走 minimize 路径再验)
probe.renderMain();
assert.ok(!doc.querySelector("#desktop").hidden, "无激活 tab → 桌面可见");
assert.ok(doc.querySelector("#log").hidden && doc.querySelector("#inputBar").hidden, "桌面态对话流/输入区隐藏");
assert.ok(doc.querySelector("#detailHost").hidden, "桌面态详情宿主隐藏");
assert.ok(doc.querySelector("#titlebar").hidden, "桌面态无窗口 → 无标题栏");
assert.equal(doc.body.dataset.desktop, "1", "桌面态样式钩子(壳透明透出主题壁纸)");
assert.equal(doc.body.dataset.wallpaper, "1", "壁纸开关镜像(shell.state.desktop.wallpaper 缺省开)");
// 图标网格:对话恒首 + legacy 五应用(数据驱动)
for (const id of ["conv", "skills", "runs", "tools", "lab", "debugold"]) {
  assert.ok(deskHtml().includes(`data-desk-open="${id}"`), `桌面有图标 ${id}`);
}
assert.ok(
  deskHtml().indexOf('data-desk-open="conv"') < deskHtml().indexOf('data-desk-open="skills"'),
  "对话恒首");
assert.deepEqual(probe.desktopIcons().map((i) => i.id),
  ["conv", "skills", "runs", "tools", "lab", "debugold"], "图标数据源:对话 + 五应用(无最近关闭时)");
assert.ok(deskHtml().includes("data-desk-wallpaper"), "壁纸开关在桌面");
assert.ok(deskHtml().includes('aria-pressed="true"'), "壁纸开关双编码(aria-pressed)");

/* ── 图标点击 = tab 条同一 action(同源断言)─────────────────── */

// legacy 应用图标 → openDetail(与 launcher 同一入口)= shell.tab.open
clickOn({ deskOpen: "skills" });
await tick();
assert.ok(shellCalls("shell.tab.open").length >= 1, "桌面图标点击 = shell.tab.open(与 tab 条同一 action)");
const openBody = JSON.parse(shellCalls("shell.tab.open").at(-1).body);
assert.equal(openBody.args.kind, "skills", "图标 → 对应 app(同源 openDetail 载荷)");
assert.equal(probe.state.active, "d:skills:skills", "点击后窗口激活(最大化)");
assert.ok(doc.querySelector("#desktop").hidden, "激活后桌面让位");
assert.ok(legacyMounted.includes("skills"), "legacy 视图挂载(与 launcher 路径一致)");
assert.ok(titleHtml().includes("技能"), "标题栏 = app 标题");
assert.ok(titleHtml().includes("data-win-close"), "app 窗口有 ✕");

// 开始菜单:与桌面图标同数据源;菜单项点击同源(shell.tab.open)
clickOn({}); // 先点空处(此时菜单未开,无影响)
const startBtn = doc.querySelector("#startBtn");
doc.trigger("click", { target: startBtn });
await tick();
assert.ok(!doc.querySelector("#startMenu").hidden, "开始按钮点开应用菜单");
assert.equal(startBtn.getAttribute("aria-expanded"), "true", "aria-expanded 同步");
for (const id of ["conv", "skills", "runs", "tools", "lab", "debugold"]) {
  assert.ok(doc.querySelector("#startMenu").innerHTML.includes(`data-desk-open="${id}"`),
    `开始菜单与桌面同数据源(${id})`);
}
const openBefore = shellCalls("shell.tab.open").length;
clickOn({ deskOpen: "tools" }); // 菜单项(与桌面图标同 data-desk-open)
await tick();
assert.ok(shellCalls("shell.tab.open").length > openBefore, "开始菜单项 = shell.tab.open(同源)");
assert.equal(probe.state.active, "d:tools:tools", "菜单项打开对应 app");
assert.ok(doc.querySelector("#startMenu").hidden, "点完即收");
assert.equal(startBtn.getAttribute("aria-expanded"), "false");

/* ── 最小化:回桌面,tab 保留在任务栏 ───────────────────────── */

clickOn({ winMin: "" });
await tick();
assert.ok(shellCalls("shell.tab.minimize").length >= 1, "最小化 = shell.tab.minimize(local)");
assert.equal(shellState.active_tab, "", "服务端 active_tab 置空(桌面态持久化)");
assert.equal(probe.state.active, "", "最小化回桌面");
assert.ok(shellState.tabs.some((t) => t.id === "d:tools:tools"), "最小化≠关闭(tab 保留)");
assert.ok(tabsHtml().includes('data-tab="d:tools:tools"'), "tab 仍在任务栏");
assert.ok(!doc.querySelector("#desktop").hidden, "桌面重现");
assert.ok(doc.querySelector("#titlebar").hidden, "无窗口 → 无标题栏");

// 对话图标 → activateTab(与 tab 条点击同一 action = shell.tab.focus)
const focusBefore = shellCalls("shell.tab.focus").length;
clickOn({ deskOpen: "conv" });
await tick();
assert.ok(shellCalls("shell.tab.focus").length > focusBefore, "对话图标 = shell.tab.focus(与 tab 条点击同一 action)");
assert.equal(probe.state.active, "conv", "回对话窗口");

/* ── 最近关闭:桌面图标(前 3)+ 重开同源 ─────────────────────── */

// 开 skills → 标题栏 ✕ 关闭(M5 关闭≠销毁,进最近关闭;回落 conv 不动)
clickOn({ deskOpen: "skills" });
await tick();
clickOn({ winClose: "" });
await tick();
assert.equal(probe.state.closedTabs.length, 1, "✕ 关闭≠销毁(进最近关闭)");
assert.equal(probe.state.active, "conv", "关闭回落 conversation(M5 语义不动)");
clickOn({ winMin: "" }); // 回桌面看最近关闭图标
await tick();
assert.ok(deskHtml().includes('data-desk-open="d:skills:skills"'), "最近关闭上图标(前 3)");
assert.deepEqual(probe.desktopIcons().at(-1).id, "d:skills:skills", "最近关闭在图标数据源末尾");
// 点最近关闭图标 = reopenTab(与"最近关闭"列表同一入口 = shell.tab.open)
const reopenBefore = shellCalls("shell.tab.open").length;
clickOn({ deskOpen: "d:skills:skills" });
await tick();
assert.ok(shellCalls("shell.tab.open").length > reopenBefore, "最近关闭图标 = shell.tab.open(同源)");
assert.equal(probe.state.active, "d:skills:skills", "重开聚焦(同一 tab id → 同 instance)");
assert.equal(probe.state.closedTabs.length, 0, "重开后出最近关闭列表");
assert.ok(tabsHtml().includes('data-tab="d:skills:skills"'), "重开回任务栏");

/* ── 壁纸开关:shell.desktop.set 持久化 ─────────────────────── */

clickOn({ winMin: "" }); // 回桌面(壁纸开关在桌面上)
await tick();
clickOn({ deskWallpaper: "" });
await tick();
assert.ok(shellCalls("shell.desktop.set").length >= 1, "壁纸开关 = shell.desktop.set(local)");
assert.equal(shellState.desktop.wallpaper, false, "壁纸写进 shell.state.desktop(持久化)");
assert.equal(doc.body.dataset.wallpaper, "0", "镜像驱动样式钩子(壳回纯色)");
clickOn({ deskWallpaper: "" });
await tick();
assert.equal(shellState.desktop.wallpaper, true, "再点还原");
assert.equal(doc.body.dataset.wallpaper, "1");

/* ── 系统托盘:live / 主题 / 收件箱(role=button + aria-label)── */

for (const item of ["data-tray-live", "data-tray-theme", "data-tray-inbox"]) {
  assert.ok(trayHtml().includes(item), `托盘有 ${item}`);
}
assert.equal((trayHtml().match(/role="button"/g) ?? []).length >= 3, true, "托盘项 role=button");
assert.ok(trayHtml().includes("aria-label"), "托盘项 aria-label");
assert.ok(trayHtml().includes('data-live="poll"'), "live 指示(node 无 EventSource → 轮询降级,色点+原文双编码)");
// 收件箱计数 = 未决 escalation 数;点击 → 回对话处理
probe.state.messages.push({ role: "agent", text: "「ops.janitor」请求批准",
  cards: [{ type: "escalation", data: { question_id: "esc-9" } }] });
probe.renderTray();
assert.ok(trayHtml().includes("收件箱: 1"), "收件箱 decisions 计数(aria-label)");
clickOn({ trayInbox: "" });
await tick();
assert.equal(probe.state.active, "conv", "收件箱 → 回对话(决策在对话里处理)");
// 主题切换:循环下一主题(与顶栏同一通道 applyTheme + shell.theme.set)
clickOn({ trayTheme: "" });
await tick();
assert.equal(shellState.theme, "moe", "托盘主题 = shell.theme.set(注册表顺序循环)");
assert.ok(trayHtml().includes("moe"), "托盘显示当前主题");
clickOn({ trayLive: "" });
await tick();
assert.ok(trayHtml().includes("data-tray-live"), "live 项点击重连不炸(降级态保持)");

/* ── M5 不回归:图标列开关持久化(shell.layout.set)───────────── */

clickOn({ iconToggle: "" });
await tick();
assert.equal(shellState.layout.icon_mode, true, "图标列写进 shell.state.layout(持久化不回归)");
assert.equal(doc.body.dataset.iconMode, "1", "镜像驱动样式");

console.log("desktop.test.mjs: all assertions passed");

/* 主题系统 T1 冒烟测试(DEBUG-UI-THEMES.md §2.5 / §5;DEBUG-UI-MOE.md §7 M1):
   1) 注册表:classic + moe 声明完整({id,name,css,copy,motion,mascot,scope}),
      主题 css 已加载且契约变量完整才注册(swatch 三色取自主题 css);
   2) 启动解析:URL(?theme=)> localStorage > classic;URL 命中同时持久化;
   3) applyTheme:<html data-theme> + localStorage + hash ?theme= 同步
      (classic 为默认省略参数);未知主题 → warn + 回落 classic;
   4) scope 回落(§5):合成 scoped 主题验证未验收页面强制回落 classic
      (moe 自 T1.1 起 app-wide 全站);
   5) 切换器:渲染(两项 + swatch 色板)、点击切换联动 data-theme/localStorage;
   6) MascotLayer:moe 下出现(控制条右侧,表情随会话状态),classic 下不出现;
   7) 文案表:状态短语/空断点列表随主题切换(classic = 现状文案)。
   运行:node static/tests/smoke-theme.test.mjs(DOM 用 dom-stub.mjs,无浏览器)。 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { StubEl, makeDocument } from "./dom-stub.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub:document(含 documentElement + 真实主题 css 的 styleSheets)/
      location / history / localStorage / fetch / EventSource ── */
const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.resolve(here, "..");

/* 从真实主题 css 提取 [data-theme] 声明块,构造 styleSheets stub——
   注册校验与 swatch 取色走真实 css 内容(契约缺失会真实告警) */
function sheetStub(themeId, cssRel) {
  const css = readFileSync(path.join(staticDir, cssRel), "utf8");
  const re = new RegExp(`\\[data-theme="${themeId}"\\]\\s*\\{([^}]*)\\}`, "g");
  const rules = [...css.matchAll(re)].map((m) => ({
    selectorText: `[data-theme="${themeId}"]`,
    style: { cssText: m[1] },
  }));
  return { href: `/static/${cssRel}`, cssRules: rules };
}

const doc = makeDocument();
doc.documentElement = new StubEl("html");
doc.styleSheets = [
  sheetStub("classic", "css/themes/classic.css"),
  sheetStub("moe", "css/themes/moe.css"),
  sheetStub("terminal", "css/themes/terminal.css"),
  sheetStub("blueprint", "css/themes/blueprint.css"),
  sheetStub("ink", "css/themes/ink.css"),
  sheetStub("pixel", "css/themes/pixel.css"),
];
doc.querySelector = (sel) => doc.body.querySelector(sel);
const toastStack = new StubEl("div");
toastStack.setAttribute("id", "toastStack");
doc.body.appendChild(toastStack);
doc.body.dataset.route = "runs";

const locationStub = { hash: "#/debug/dbg-s1?theme=moe" };
const historyCalls = [];
const storageMap = new Map();
const warns = [];
const realWarn = console.warn;
console.warn = (...a) => warns.push(a.join(" "));

class ESStub {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.handlers = {};
    ESStub.instances.push(this);
  }
  addEventListener(type, fn) {
    (this.handlers[type] ??= []).push(fn);
  }
  close() {}
}

const SNAPSHOT = {
  session_id: "dbg-s1",
  run_id: "r1",
  state: "paused",
  pause_point: {
    signal: "pre:tool.call", run_id: "r1", frame_id: "f1", depth: 1, step: 1,
    tool: "system.python.exec", skill: null,
    payload: { frame_id: "f1", depth: 1, tool: "system.python.exec", args: {} },
    breakpoint_ids: [], reason: "breakpoint",
  },
  breakpoints: [],
  frame_stack: [{ frame_id: "f1", skill: "local:fib@1.0.0", depth: 1 }],
};

globalThis.document = doc;
globalThis.location = locationStub;
globalThis.history = {
  replaceState: (_a, _b, url) => {
    historyCalls.push(String(url));
    locationStub.hash = String(url); // 照浏览器:replaceState 更新地址栏
  },
};
globalThis.localStorage = {
  getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
  setItem: (k, v) => storageMap.set(k, String(v)),
  removeItem: (k) => storageMap.delete(k),
};
globalThis.EventSource = ESStub;
globalThis.fetch = async (url) => {
  const u = String(url);
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (u === "/api/debug/sessions/dbg-s1") return reply(SNAPSHOT);
  if (u === "/api/runs/r1/signals") return reply([]);
  if (u === "/api/debug/sessions/dbg-s1/frames/f1") {
    return reply({ frame_id: "f1", skill: "local:fib@1.0.0", messages: [] });
  }
  throw new Error(`未 stub 的请求: ${u}`);
};

const themes = await import("../js/themes.js");
const { mascotHtml, mascotStateFor } = await import("../js/components/mascot.js");
const { store } = await import("../js/store.js");
const { closeDebugView, openDebugView } = await import("../js/components/debug-view.js");

const {
  DEFAULT_THEME,
  THEME_STORAGE_KEY,
  applyTheme,
  copy,
  currentThemeId,
  initTheme,
  listThemes,
  mountThemePicker,
  registerTheme,
  syncTheme,
  themeSwatch,
} = themes;

/* ══ 1. 注册表:声明完整 + css 已加载 + 契约完整才注册 ═══════════ */
{
  const list = listThemes();
  assert.equal(list.length, 6, "六主题注册(classic/moe + T2/T3 四主题)");
  const byId = Object.fromEntries(list.map((t) => [t.id, t]));
  for (const id of ["classic", "moe", "terminal", "blueprint", "ink", "pixel"]) {
    const t = byId[id];
    assert.ok(t, `主题 ${id} 已注册`);
    for (const f of ["id", "name", "css", "copy", "motion", "mascot", "scope"]) {
      assert.ok(f in t, `主题 ${id} 声明含 ${f}`);
    }
  }
  assert.equal(byId.classic.scope, "app-wide", "classic 全站");
  assert.equal(byId.moe.scope, "app-wide", "moe 全站(T1.1)");
  assert.equal(byId.classic.mascot, null, "classic 无 mascot");
  assert.equal(byId.moe.mascot, "mochi", "moe mascot = mochi");
  assert.deepEqual(
    themeSwatch("moe"), ["#fff5f7", "#5c3d47", "#c2245c"],
    "swatch 三色取自主题 css(基底/文本/主色)");
  assert.deepEqual(
    themeSwatch("classic"), ["#0b0e14", "#e6ebf2", "#3b9eff"], "classic swatch");

  /* 负例:css 未加载 → 不注册 + warn */
  warns.length = 0;
  const ok = registerTheme({
    id: "ghost", name: "Ghost", css: "css/themes/ghost.css",
    copy: {}, motion: {}, mascot: null, scope: "app-wide",
  });
  assert.equal(ok, false, "css 未加载的主题不注册");
  assert.ok(warns.some((w) => w.includes("ghost") && w.includes("未加载")), "warn:css 未加载");
  assert.equal(listThemes().length, 6, "注册表未被污染");
}

/* ══ 2. 启动解析:URL > localStorage;URL 命中持久化 ═══════════════ */
{
  storageMap.set(THEME_STORAGE_KEY, "classic"); // localStorage 与 URL 冲突
  locationStub.hash = "#/debug/dbg-s1?theme=moe";
  const eff = initTheme();
  assert.equal(eff, "moe", "URL=moe 优先于 localStorage(moe 全站,T1.1)");
  assert.equal(doc.documentElement.dataset.theme, "moe", "data-theme 跟随有效主题");
  assert.equal(
    storageMap.get(THEME_STORAGE_KEY), "moe", "URL 命中的主题写入持久化(深链接同款气质)");

  doc.body.dataset.route = "debug";
  assert.equal(syncTheme(), "moe", "debug 页:moe 生效");
  doc.body.dataset.route = "runs";
  assert.equal(syncTheme(), "moe", "runs 页:moe 全站同样生效(T1.1)");
}

/* ══ 3. localStorage 回落与默认值 ═══════════════════════════════ */
{
  locationStub.hash = "#/runs";
  storageMap.set(THEME_STORAGE_KEY, "moe");
  initTheme();
  assert.equal(currentThemeId(), "moe", "无 URL:localStorage=moe,runs 页同样生效(T1.1)");
  assert.ok(historyCalls.some((u) => u.includes("theme=moe")), "?theme= 同步进 hash");

  storageMap.clear();
  locationStub.hash = "#/runs";
  initTheme();
  assert.equal(currentThemeId(), "classic", "无 URL 无存储 → classic");
  assert.equal(storageMap.get(THEME_STORAGE_KEY), "classic");
}

/* ══ 4. applyTheme:data-theme + localStorage + URL 同步;未知主题回落 ══ */
{
  doc.body.dataset.route = "debug";
  historyCalls.length = 0;
  applyTheme("moe");
  assert.equal(doc.documentElement.dataset.theme, "moe", "applyTheme 切 data-theme");
  assert.equal(storageMap.get(THEME_STORAGE_KEY), "moe", "applyTheme 持久化");
  assert.ok(historyCalls.at(-1).includes("theme=moe"), "applyTheme 同步 URL");

  applyTheme("classic");
  assert.equal(doc.documentElement.dataset.theme, "classic");
  assert.ok(!historyCalls.at(-1).includes("theme="), "classic 为默认,URL 省略 theme 参数");

  warns.length = 0;
  applyTheme("hologram"); // 不存在的主题 id
  assert.equal(doc.documentElement.dataset.theme, "classic", "未知主题回落 classic");
  assert.ok(warns.some((w) => w.includes("未知主题")), "warn:未知主题");
  doc.body.dataset.route = "runs";
}

/* ══ 5. 切换器:渲染 + 点击切换 ═══════════════════════════════════ */
{
  doc.body.dataset.route = "debug";
  const box = doc.createElement("div");
  doc.body.appendChild(box);
  mountThemePicker(box);

  const btn = box.querySelector(".theme-picker-btn");
  const menu = box.querySelector(".theme-menu");
  assert.ok(btn && menu, "切换器按钮 + 菜单渲染");
  assert.equal(menu.hidden, true, "菜单默认收起");
  const items = menu.querySelectorAll(".theme-item");
  assert.equal(items.length, 6, "六个主题项");
  for (const item of items) {
    const dots = item.querySelector(".swatch").children;
    assert.equal(dots.length, 3, "每项三色 swatch");
    assert.ok(dots.every((d) => d.style.background.startsWith("#")), "swatch 色值就位");
  }

  btn.trigger("click");
  assert.equal(menu.hidden, false, "点击按钮展开菜单");
  const moeItem = items.find((i) => i.dataset.themeId === "moe");
  moeItem.trigger("click");
  assert.equal(doc.documentElement.dataset.theme, "moe", "菜单选择 moe → data-theme 切换");
  assert.equal(storageMap.get(THEME_STORAGE_KEY), "moe", "菜单选择持久化");
  assert.equal(menu.hidden, true, "选择后菜单收起");
  assert.equal(moeItem.getAttribute("aria-selected"), "true", "选中态 aria-selected");
  assert.match(btn.querySelector(".theme-picker-label").textContent, /Moe/, "按钮显示当前主题名");
  box.remove();
  applyTheme("classic");
  doc.body.dataset.route = "runs";
}

/* ══ 6. 文案表:状态短语/空断点列表随主题切换 ═════════════════════ */
{
  const { statusPill } = await import("../js/components/status-pill.js");
  const { renderBreakpoints } = await import("../js/components/breakpoint-list.js");

  applyTheme("classic");
  assert.match(statusPill("paused"), /已暂停/, "classic 状态短语 = 现状文案");
  assert.match(renderBreakpoints([]), /还没有断点/, "classic 空断点 = 现状文案");

  doc.body.dataset.route = "debug";
  applyTheme("moe");
  assert.match(statusPill("paused"), /抓到啦！/, "moe 状态短语");
  assert.match(statusPill("aborted"), /先到这里喵\(aborted\)/, "moe 技术原文并列保留");
  assert.match(renderBreakpoints([]), /还没放捕兽夹哦/, "moe 空断点列表");
  assert.equal(copy("session.waiting"), "Mochi 准备出发…", "moe 等待就绪文案");
  applyTheme("classic");
  doc.body.dataset.route = "runs";
}

/* ══ 7. MascotLayer:主题判层 + 表情映射(组件零分支)═════════════ */
{
  applyTheme("classic");
  assert.equal(mascotHtml("paused"), "", "classic 无 mascot:层不渲染");

  doc.body.dataset.route = "debug";
  applyTheme("moe");
  const html = mascotHtml("paused");
  assert.match(html, /class="mascot-layer" data-mascot="mochi" data-expr="paused"/, "moe mascot 层");
  assert.match(html, /<symbol id="mochi-running"/, "精灵表:running");
  assert.match(html, /<symbol id="mochi-paused"/, "精灵表:paused");
  assert.match(html, /<symbol id="mochi-done"/, "精灵表:done");
  assert.match(html, /href="#mochi-paused"/, "引用 paused 表情");
  assert.match(html, /role="img" aria-label="Mochi:/, "aria 双编码");

  assert.equal(mascotStateFor({ state: "running" }), "running");
  assert.equal(mascotStateFor({ state: "paused" }), "paused");
  assert.equal(mascotStateFor({ state: "detached" }, "done"), "done");
  assert.equal(mascotStateFor({ state: "detached" }, "failed"), "failed", "failed → 哭脸表情");
  assert.equal(mascotStateFor({ state: "detached" }, "aborted"), null, "aborted 不映射");
  assert.equal(mascotHtml(null), "", "未映射状态 → 层消失");

  const failedHtml = mascotHtml("failed");
  assert.match(failedHtml, /<symbol id="mochi-failed"/, "精灵表:failed 哭脸");
  assert.match(failedHtml, /href="#mochi-failed"/, "引用 failed 表情");
  const readyHtml = mascotHtml("ready");
  assert.match(readyHtml, /data-expr="ready"/, "ready 待机表情(调试首页)");
  assert.match(readyHtml, /href="#mochi-paused"/, "ready 复用 paused 精灵");
  applyTheme("classic");
  assert.equal(mascotHtml("ready"), "", "classic:ready 层也不渲染");
  doc.body.dataset.route = "runs";
}

/* ══ 8. 调试台集成:moe 下 MascotLayer 出现,classic 下不出现 ══════ */
{
  store.set({ route: { name: "debug-session", sessionId: "dbg-s1", runId: null } });
  doc.body.dataset.route = "debug";

  applyTheme("moe");
  const main = new StubEl("main");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush();
  let bar = main.querySelector("#dbgBar");
  assert.match(bar.innerHTML, /data-mascot="mochi" data-expr="paused"/, "moe:控制条右侧 Mochi(paused)");
  assert.match(bar.innerHTML, /抓到啦！/, "moe:控制条状态徽标萌系文案");
  assert.match(bar.innerHTML, /paused @ pre:tool\.call/, "moe:暂停点技术原文并列保留");
  closeDebugView();

  applyTheme("classic");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush();
  bar = main.querySelector("#dbgBar");
  assert.doesNotMatch(bar.innerHTML, /mascot-layer/, "classic:MascotLayer 不出现");
  assert.match(bar.innerHTML, /已暂停/, "classic:状态徽标现状文案");
  closeDebugView();
  doc.body.dataset.route = "runs";
}

/* ══ 9. scope 回落机制(§5;合成 scoped 主题——moe 已全站,机制本身仍须覆盖)══ */
{
  /* 合成主题 scoped-demo:契约变量复用 classic.css 的声明块,
     但 sheet 用独立文件名 + 自有选择器(sheetTokens 双重匹配) */
  const classicCss = readFileSync(path.join(staticDir, "css/themes/classic.css"), "utf8");
  const decls = classicCss.match(/\[data-theme="classic"\]\s*\{([^}]*)\}/)[1];
  doc.styleSheets.push({
    href: "/static/css/themes/scoped-demo.css",
    cssRules: [{ selectorText: '[data-theme="scoped-demo"]', style: { cssText: decls } }],
  });
  const ok = registerTheme({
    id: "scoped-demo", name: "Scoped Demo", css: "css/themes/scoped-demo.css",
    copy: {}, motion: {}, mascot: null, scope: ["debug"],
  });
  assert.equal(ok, true, "合成 scoped 主题注册(契约变量复用 classic)");
  doc.body.dataset.route = "runs";
  applyTheme("scoped-demo");
  assert.equal(currentThemeId(), "classic", "scoped-demo 未验收 runs → 回落 classic");
  assert.equal(doc.documentElement.dataset.theme, "classic");
  doc.body.dataset.route = "debug";
  assert.equal(syncTheme(), "scoped-demo", "已验收页 → scoped-demo 生效");
  doc.body.dataset.route = "runs";
  assert.equal(syncTheme(), "classic", "离开验收页 → 回落 classic");
  applyTheme("classic"); // 复位持久化/URL
}

/* ══ 10. 状态色点 → 猫咪表情(moe css 源级断言;双编码钩子/文字不受影响)══ */
{
  const moeCss = readFileSync(path.join(staticDir, "css/themes/moe.css"), "utf8");
  const EMOJI = {
    running: "😺", done: "😸", failed: "😿",
    aborted: "🙀", unknown: "😴", paused: "🐾",
  };
  for (const [st, emoji] of Object.entries(EMOJI)) {
    assert.ok(
      moeCss.includes(`.status-pill[data-status="${st}"] .pill-dot::before`) &&
        moeCss.includes(`content: "${emoji}"`),
      `moe pill ${st} → ${emoji}`);
  }
  assert.match(moeCss, /@keyframes moe-spin/, "running 猫咪转圈动画");
  for (const [tone, emoji] of Object.entries({ done: "😸", danger: "😿", aborted: "🙀" })) {
    assert.ok(
      moeCss.includes(`.banner[data-tone="${tone}"] .banner-icon::before`) &&
        moeCss.includes(`content: "${emoji}"`),
      `moe 结束横幅 ${tone} → ${emoji}`);
  }
  /* classic 不受影响:classic.css 不得出现 pill emoji 规则 */
  const classicCss = readFileSync(path.join(staticDir, "css/themes/classic.css"), "utf8");
  assert.ok(!classicCss.includes("pill-dot::before"), "classic 保持色点,无 emoji 替换");

  /* P1 哭脸泪滴 / P2 时间线 emoji / P3 检视面板 / P4 入场动画(moe css 源级断言) */
  assert.ok(moeCss.includes(".mascot-svg .mt"), "failed 泪滴 .mt 规则");
  const KIND_EMOJI = { run: "🚀", call: "📞", ret: "↩️", llm: "🧠", tool: "🔧", exec: "💻" };
  for (const [kind, emoji] of Object.entries(KIND_EMOJI)) {
    assert.ok(
      moeCss.includes(`.dbg-row[data-kind="${kind}"]`) && moeCss.includes(`content: "${emoji} "`),
      `时间线 ${kind} → ${emoji}`);
  }
  assert.ok(moeCss.includes(".dbg-trace-list::before"), "时间线粉色虚线竖轨");
  assert.ok(moeCss.includes(".dbg-sec-title::before"), "检视面板小节标题 🌸");
  assert.match(moeCss, /@keyframes moe-pop-in/, "卡片入场动画");
  assert.ok(moeCss.includes("prefers-reduced-motion"), "reduced-motion 兜底");
  assert.ok(!classicCss.includes("moe-pop-in"), "classic 无入场动画");
}

/* ══ 11. T2/T3 四主题:注册、文案腔调、mascot 抽象第二实例(§3.3-3.6)══ */
{
  assert.ok(themes.listThemes().length >= 6, "注册表六主题齐全");
  const byId = Object.fromEntries(themes.listThemes().map((t) => [t.id, t]));
  for (const id of ["terminal", "blueprint", "ink", "pixel"]) {
    assert.ok(byId[id], `主题 ${id} 已注册`);
    assert.equal(byId[id].scope, "app-wide", `${id} 同 moe 的 T1.1 依据全站开放`);
  }
  assert.equal(byId.terminal.mascot, null, "terminal 无 mascot(气场不需要)");
  assert.equal(byId.blueprint.mascot, null, "blueprint 无 mascot");
  assert.equal(byId.ink.mascot, null, "ink 无 mascot(禅不需要团子)");
  assert.equal(byId.pixel.mascot, "sprite8", "pixel mascot = sprite8");

  /* 各主题文案腔调(技术原文并列保留) */
  applyTheme("terminal");
  assert.equal(themes.copy("status.done"), "[done]", "terminal shell 腔");
  assert.match(themes.copy("status.paused"), /\[halted\]\(paused\)/, "terminal 技术原文并列");
  assert.equal(mascotHtml("running"), "", "terminal:mascot 层不渲染");
  applyTheme("blueprint");
  assert.equal(themes.copy("status.done"), "APPROVED(done)", "blueprint 图签腔");
  assert.equal(themes.copy("debug.end.title"), "run 已归档", "blueprint 归档文案");
  applyTheme("ink");
  assert.equal(themes.copy("status.paused"), "驻(paused)", "ink 文言腔 + 原文并列");
  assert.equal(themes.copy("debug.end.title"), "墨尽", "ink 结束文案");
  applyTheme("pixel");
  assert.equal(themes.copy("status.done"), "CLEAR!", "pixel 游戏腔");
  assert.equal(themes.copy("debug.end.title"), "QUEST END", "pixel 结束文案");

  /* sprite8:mascot 抽象第二实例——同一 MascotLayer 接口,另一套精灵表 */
  const pRun = mascotHtml("running");
  assert.match(pRun, /data-mascot="sprite8" data-expr="running"/, "pixel:sprite8 层渲染");
  assert.match(pRun, /<symbol id="sprite8-running"/, "sprite8 精灵表:running");
  assert.match(pRun, /<symbol id="sprite8-paused"/, "sprite8 精灵表:paused");
  assert.match(pRun, /<symbol id="sprite8-done"/, "sprite8 精灵表:done");
  assert.match(pRun, /<symbol id="sprite8-failed"/, "sprite8 精灵表:failed");
  assert.match(pRun, /href="#sprite8-running"/, "引用 sprite8 running 精灵");
  assert.match(pRun, /role="img" aria-label="Sprite8:/, "sprite8 aria 双编码");
  const pReady = mascotHtml("ready");
  assert.match(pReady, /href="#sprite8-paused"/, "sprite8 ready 复用 paused 精灵");
  /* 表情映射与 mochi 共用同一 mascotStateFor(层接口一致) */
  assert.equal(mascotStateFor({ state: "detached" }, "failed"), "failed", "sprite8 共用表情映射");

  applyTheme("classic");
  doc.body.dataset.route = "runs";
}

console.warn = realWarn;
console.log("smoke-theme.test.mjs: all assertions passed");

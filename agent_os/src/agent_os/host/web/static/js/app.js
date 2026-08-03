/* Agent OS Web UI 入口(docs/WEB-UI.md §4.1 应用壳 / §5 交互与状态规范 / §6.1 结构)。
   职责:hash 路由(runs/skills/tools,§4.1 深链接含 ?frame/?signal;D6 增 ?set=)、
   Runs 侧栏(搜索/筛选/折叠;D6 set 切换器)、API 健康轮询(live 指示 + 列表 5s 刷新)、
   三态、Toast、复制。
   run 详情(§4.2 Run Workbench 三联动)由 workbench.js 承担;本文件只做路由分发与事件转发。 */

import { store } from "./store.js";
import { getJson, postJson } from "./api.js";
import { statusPill } from "./components/status-pill.js";
import { absTime, copyText, emptyBlock, esc, fmtCost, relTime, toast } from "./util.js";
import { openLaunchDialog } from "./components/launch-dialog.js";
import { closeSkillsView, openSkillsView } from "./components/skills-view.js";
import { closeToolsView, openToolsView } from "./components/tools-view.js";
import { closeLab, openLab } from "./components/lab.js";
import { closeIterate, openIterate } from "./components/lab-iterate.js";
import { closeDebugHome, openDebugHome } from "./components/debug-home.js";
import {
  closeDebugView,
  debugChange,
  debugClick,
  openDebugView,
} from "./components/debug-view.js";
import {
  openInbox,
  pollInbox,
  renderInboxBadge,
  toggleInbox,
} from "./components/inbox.js";
import {
  closeWorkbench,
  openWorkbench,
  reconnectLive,
  wbDoResume,
  wbJumpFirstError,
  wbNavEdge,
  wbNavSignal,
  wbRcaAvailable,
  wbRequestStop,
  wbResumable,
  wbRunning,
  workbenchClick,
  workbenchKeydown,
} from "./workbench.js";
import { installGlobalKeys } from "./shortcuts.js";
import { initTheme, mountThemePicker, syncTheme, copy } from "./themes.js";
import { COMMANDS, openCommandPalette } from "./components/command-palette.js";
import { openShortcutsPanel } from "./components/shortcuts-panel.js";

const POLL_INTERVAL = 5000; // §4.1:列表 5s 轮询;兼作 API 健康检查

const $ = (sel, root = document) => root.querySelector(sel);

/* UI 局部状态:筛选/折叠是纯视图状态,不进全局 store。 */
const ui = {
  search: "",
  statusFilter: new Set(), // 空集 = 全部
};

/* ── hash 路由(§4.1 深链接:#/runs、#/runs/<id>?frame=<fid>&signal=<i>、
      #/skills、#/skills/<name>、#/tools、#/tools/<name>;
      P4 调试台:#/debug、#/debug/<session_id>)────────────────────── */

function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "");
  const [path, qs] = raw.split("?");
  const seg = path.split("/").filter(Boolean).map(decodeURIComponent);
  const q = new URLSearchParams(qs ?? "");
  const set = q.get("set"); // D6:?set= 深链接(无参数 → null,保持当前选择)
  if (seg[0] === "runs" && seg[1]) {
    return { name: "run-detail", runId: seg[1], frame: q.get("frame"), signal: q.get("signal"), set };
  }
  if (seg[0] === "skills") return { name: "skills", runId: null, itemName: seg[1] ?? null, set };
  if (seg[0] === "tools") return { name: "tools", runId: null, itemName: seg[1] ?? null, set };
  // Skill Lab(docs/SKILL-DEV.md;L1):#/lab 与 #/lab/<draft> 深链接;
  // 迭代模式(docs/LAB-ITERATION.md;Flow C):#/lab/<draft>/iterate
  if (seg[0] === "lab") {
    if (seg[2] === "iterate") return { name: "lab-iterate", runId: null, draft: seg[1] ?? null, set };
    return { name: "lab", runId: null, draft: seg[1] ?? null, set };
  }
  if (seg[0] === "debug") {
    return seg[1]
      ? { name: "debug-session", sessionId: seg[1], runId: null, set }
      : { name: "debug-home", runId: null, set };
  }
  return { name: "runs", runId: null, set };
}

function applyRoute() {
  const route = parseRoute(location.hash);
  store.set({ route, selectedRunId: route.runId });
  if (route.set !== null) {
    // D6:?set=all/空串 = 全部;否则 set 名(未知名在 skillsets 到达后 sanitize 回落)
    store.set({ skillSet: route.set === "" || route.set === "all" ? null : route.set });
  }
}

/* ── 渲染:TopBar 导航 ─────────────────────────────────────────────── */

function renderNav() {
  const name = store.get("route").name;
  const page =
    name === "run-detail" ? "runs"
    : name.startsWith("debug") ? "debug"
    : name === "lab-iterate" ? "lab" // 迭代模式归 Lab 导航(docs/LAB-ITERATION.md)
    : name;
  document.body.dataset.route = page; // 侧栏仅 Runs 页显示(app.css 按此驱动)
  document.querySelectorAll(".nav-item").forEach((a) => {
    if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

/* ── D6 一站多 skill set:侧栏 set 切换器(≥2 sets 可见,否则与单站一致)──
   选择写 store.skillSet(null = 全部)与 hash ?set=(replaceState,不触发路由);
   runs 列表前端过滤;列表项 skill 名旁显示 set chip(仅多 set)。 */

const multiSets = () => (store.get("skillsets") ?? []).length >= 2;

async function loadSkillsets() {
  try {
    const sets = await getJson("/api/skillsets");
    store.set({ skillsets: Array.isArray(sets) ? sets : [] });
  } catch {
    store.set({ skillsets: [] }); // 取数失败按无 sets 处理(界面与单站一致)
  }
}

function sanitizeSkillSet() {
  const cur = store.get("skillSet");
  if (cur && !(store.get("skillsets") ?? []).some((s) => s.name === cur)) {
    store.set({ skillSet: null }); // hash 恢复/残留了未知 set:回落"全部"
  }
}

function renderSetSelect() {
  const sel = $("#setSelect");
  const sets = store.get("skillsets") ?? [];
  sel.hidden = sets.length < 2;
  if (sel.hidden) return;
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部";
  sel.appendChild(all);
  for (const s of sets) {
    const opt = document.createElement("option");
    opt.value = s.name;
    opt.textContent = `${s.name} (${s.skills ?? 0})`; // 各 set 带技能数
    sel.appendChild(opt);
  }
  sel.value = store.get("skillSet") ?? "";
}

function syncSetHash() {
  const raw = (location.hash || "#/runs").replace(/^#/, "");
  const [path, qs] = raw.split("?");
  const q = new URLSearchParams(qs ?? "");
  const cur = store.get("skillSet");
  if (cur) q.set("set", cur);
  else q.delete("set");
  const s = q.toString();
  history.replaceState(null, "", `#${path}${s ? `?${s}` : ""}`);
}

/* ── 渲染:Runs 侧栏(三态齐全,§5)─────────────────────────────────── */

const skeletonRows = (n) =>
  Array.from({ length: n }, () =>
    `<div class="skeleton-row">` +
    `<span class="skeleton skeleton-line w-70"></span>` +
    `<span class="skeleton skeleton-line w-40"></span>` +
    `</div>`).join("");

function visibleRuns() {
  const q = ui.search.trim().toLowerCase();
  const sel = store.get("skillSet"); // D6:set 过滤(null = 全部;旧 run 归 "default")
  return store.get("runs").filter((r) => {
    if (sel && (r.skill_set ?? "default") !== sel) return false;
    if (ui.statusFilter.size && !ui.statusFilter.has(r.status)) return false;
    if (q && !(r.skill || "").toLowerCase().includes(q)) return false;
    return true;
  });
}

function runItemHtml(r) {
  const sel = r.run_id === store.get("selectedRunId");
  // D6:多 set 时 skill 名旁显示 set 名
  const setChip = multiSets()
    ? `<span class="kind-chip run-set" title="skill set">${esc(r.skill_set ?? "default")}</span>`
    : "";
  return (
    `<div class="run-item" data-id="${esc(r.run_id)}" role="option" tabindex="0"` +
    ` aria-selected="${sel}" title="${esc(r.skill)} · ${esc(r.run_id)}">` +
    `<div class="run-item-row">` +
    `<span class="run-skill">${esc(r.skill)}</span>` +
    setChip +
    statusPill(r.status) +
    `</div>` +
    `<div class="run-item-meta">` +
    `<span class="run-time" title="${esc(absTime(r.started_at))}">${esc(relTime(r.started_at))}</span>` +
    `<span class="run-cost">${esc(fmtCost(r.cost))}</span>` +
    `</div>` +
    `</div>`
  );
}

function renderRunList() {
  const box = $("#runList");
  const status = store.get("runsStatus");
  if (status === "loading") {
    box.innerHTML = skeletonRows(6);
    return;
  }
  if (status === "error" && store.get("runs").length === 0) {
    box.innerHTML =
      `<div class="panel-error">` +
      `<span class="error-msg">加载 run 列表失败</span>` +
      `<button class="btn" data-action="retry-runs">重试</button>` +
      `</div>`;
    return;
  }
  const runs = visibleRuns();
  if (!runs.length) {
    box.innerHTML =
      store.get("runs").length === 0
        ? emptyBlock("还没有 run", "点击右上角 + New Run 发起第一次运行", "run", {
            label: "+ New Run",
            dataAction: "open-launch",
            primary: true,
          })
        : emptyBlock("无匹配的 run", "调整搜索关键词或状态筛选", "search");
    return;
  }
  box.innerHTML = runs.map(runItemHtml).join("");
}

function renderChips() {
  document.querySelectorAll("#statusChips .chip").forEach((c) => {
    const s = c.dataset.status;
    c.classList.toggle(
      "is-active",
      s === "all" ? ui.statusFilter.size === 0 : ui.statusFilter.has(s));
  });
}

/* ── 渲染:主区(Runs 空态引导 / Run Workbench(D2)/ Skills·Tools 浏览器(D4))── */

function renderMain() {
  const main = $("#main");
  const route = store.get("route");
  if (route.name !== "run-detail") closeWorkbench(); // 离开 Workbench:live 会话收尾
  if (route.name !== "skills") closeSkillsView();
  if (route.name !== "tools") closeToolsView();
  if (route.name !== "debug-home") closeDebugHome();
  if (route.name !== "debug-session") closeDebugView(); // 离开调试台:SSE/轮询收尾
  if (route.name !== "lab") closeLab(); // 离开 Lab:丢弃页面状态(草稿在服务端,随时可回)
  if (route.name !== "lab-iterate") closeIterate(); // 离开迭代模式同理(版本/批注在服务端)
  if (route.name === "skills") {
    openSkillsView(main, route.itemName); // §4.6(#/skills 与 #/skills/<name> 深链接恢复)
    return;
  }
  if (route.name === "tools") {
    openToolsView(main, route.itemName); // §4.7
    return;
  }
  if (route.name === "debug-home") {
    openDebugHome(main); // P4 调试首页:活跃会话 + 新会话(启动前断点)
    return;
  }
  if (route.name === "debug-session") {
    openDebugView(main, route.sessionId); // P4 调试台
    return;
  }
  if (route.name === "lab") {
    openLab(main, route.draft); // Skill Lab(docs/SKILL-DEV.md;L1)
    return;
  }
  if (route.name === "lab-iterate") {
    openIterate(main, route.draft); // 迭代模式(docs/LAB-ITERATION.md;Flow C)
    return;
  }
  if (route.name === "run-detail") {
    // §4.2 Workbench 三联动(?frame/?signal 深链接参数一并交给 workbench 恢复)
    openWorkbench(main, route.runId, { frame: route.frame, signal: route.signal });
    return;
  }
  main.innerHTML =
    `<div class="main-home">` +
    emptyBlock("选择一个 run", "从左侧列表选择 run 查看详情,或点击 + New Run 发起新运行", "select", {
      label: "+ New Run",
      dataAction: "open-launch",
      primary: true,
    }) +
    `</div>`;
}

/* ── API 健康轮询(§4.1 列表 5s 轮询)+ TopBar live 指示(§5)────────
   live 指示双态语义:SSE 会话(store.liveConn,workbench 写)优先——
   "ok" 蓝点脉冲 / "connecting" / "down" 黄点 + "已断开,点击重连"(点击重建
   该 run 的 SSE);无 live 会话时按 API 健康(ok 蓝 / fail 黄,点击立即重试)。 */

let lastHealth = null;

function renderLiveIndicator() {
  const el = $("#liveIndicator");
  const conn = store.get("liveConn");
  if (conn === "ok") {
    el.dataset.state = "ok";
    el.title = "实时连接正常";
    return;
  }
  if (conn === "connecting") {
    el.dataset.state = "connecting";
    el.title = "连接中…";
    return;
  }
  if (conn === "down") {
    el.dataset.state = "down"; // §5:黄点 + "已断开,点击重连"
    el.title = "已断开,点击重连";
    return;
  }
  el.dataset.state = lastHealth === null ? "connecting" : lastHealth ? "ok" : "fail";
  el.title = lastHealth === false ? "连接异常(点击立即重试)" : "连接正常";
}

function setHealth(ok) {
  if (lastHealth !== ok) {
    if (!ok) toast("API 连接异常,自动重试中", "error");
    else if (lastHealth === false) toast("API 连接已恢复", "success");
  }
  lastHealth = ok;
  renderLiveIndicator();
}

async function poll() {
  try {
    const runs = await getJson("/api/runs");
    setHealth(true);
    store.set({ runs, runsStatus: "ready" });
  } catch {
    setHealth(false);
    store.set({ runsStatus: "error" }); // 保留旧 runs:有数据时列表仍可见,仅 Toast 提示
  }
}

/* ── store 订阅:按 patch 精准重渲染(Workbench 的 selection/runs
      订阅在 workbench.js,按键拆分、只重绘受影响栏)────────────────── */

store.subscribe((state, patch) => {
  if ("route" in patch) {
    renderNav();
    syncTheme(); // 主题 scope 回落跟随页面(§5:未验收页面强制 classic)
    renderRunList(); // aria-selected 跟随路由
    renderMain();
  }
  if ("runs" in patch || "runsStatus" in patch) renderRunList();
  if ("liveConn" in patch) renderLiveIndicator(); // §5 SSE 连接态 → TopBar live 点
  if ("inboxPending" in patch) renderInboxBadge(); // S3 §5:待答计数徽标(>0 显示,high 变色)
  if ("skillsets" in patch) {
    sanitizeSkillSet(); // hash 恢复的 set 名未知 → 回落全部(可能嵌套发 skillSet patch)
    renderSetSelect();
    renderRunList(); // set chip 显隐跟随 sets 数
  }
  if ("skillSet" in patch) {
    renderSetSelect(); // 双侧(Skills 页下拉经 store 联动)同步选中
    renderRunList();
    syncSetHash();
  }
});

/* ── 事件接线(事件委托,§6.1;Workbench 动作经 workbenchClick 转发)── */

document.addEventListener("click", (e) => {
  const action = e.target.closest("[data-action]");
  if (action) {
    const act = action.dataset.action;
    if (act === "retry-runs") {
      poll();
      return;
    }
    if (act === "open-launch") {
      openLaunchDialog(); // 空态主按钮:同 + New Run
      return;
    }
    if (act === "open-inbox") {
      openInbox(); // S3 §5:run 头"等待上级裁决"Banner 的入口
      return;
    }
    if (act === "copy") {
      const label = action.dataset.copyLabel || "已复制";
      copyText(action.dataset.copy).then((ok) =>
        toast(ok ? label : "复制失败", ok ? "success" : "error"));
      return;
    }
    if (debugClick(e, action)) return; // P4 调试台动作(dbg-cmd/dbg-gutter/dbg-bp-* 等)
    workbenchClick(e, action); // workbench 自有 data-action(ft-toggle/tl-toggle/wb-* 等)
    return;
  }
  if (workbenchClick(e, null)) return; // workbench 行点击(帧树/时间线)
  if (debugClick(e, null)) return; // P4 调试台行点击(调用栈/轨迹行)
  const item = e.target.closest(".run-item");
  if (item) location.hash = `#/runs/${encodeURIComponent(item.dataset.id)}`;
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  if (workbenchKeydown(e)) return; // workbench 行/组头 Enter = 点击
  if (e.target.classList?.contains("run-item")) {
    location.hash = `#/runs/${encodeURIComponent(e.target.dataset.id)}`;
  }
});

$("#searchInput").addEventListener("input", (e) => {
  ui.search = e.target.value;
  renderRunList();
});

// P4 调试台 change 委托(新增断点表单 kind 切换 → match 输入禁用态)
document.addEventListener("change", (e) => debugChange(e));

// D6:set 切换器 → store(订阅者同步 hash/列表;Skills 页下拉经 store 联动)
$("#setSelect").addEventListener("change", (e) => {
  store.set({ skillSet: e.target.value || null });
});

$("#statusChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const s = chip.dataset.status;
  if (s === "all") ui.statusFilter.clear();
  else if (ui.statusFilter.has(s)) ui.statusFilter.delete(s);
  else ui.statusFilter.add(s);
  renderChips();
  renderRunList();
});

$("#collapseBtn").addEventListener("click", () => {
  const bar = $("#sidebar");
  const collapsed = bar.classList.toggle("collapsed");
  $("#collapseBtn").title = collapsed ? "展开侧栏" : "折叠侧栏";
  try { // UX 评审 P1-9:折叠状态跨会话记忆(Lab 三栏页尤其需要宽度)
    localStorage.setItem("agent-os.sidebar.collapsed", collapsed ? "1" : "0");
  } catch { /* 隐私模式等:仅本次会话有效 */ }
});
try { // 启动恢复折叠状态
  if (localStorage.getItem("agent-os.sidebar.collapsed") === "1") {
    $("#sidebar").classList.add("collapsed");
  }
} catch { /* 同上 */ }

$("#liveIndicator").addEventListener("click", () => {
  if (store.get("liveConn") === "down") {
    reconnectLive(); // §5:SSE 已断开 → 点击重建该 run 的 SSE
    return;
  }
  poll();
});
// S3(docs/SUPERVISOR.md §5):TopBar 收件箱图标 → 抽屉开关
$("#inboxBtn").addEventListener("click", () => toggleInbox());
// + New Run 主按钮(§4.3):打开 Launch Modal(不打断当前页;成功后跳 #/runs/<id> 进 live)
$("#newRunBtn").addEventListener("click", () => openLaunchDialog());

/* ── ⌘K 命令条(§5):命令注册表纯数据在 command-palette.js;
      可见性按上下文过滤(Stop 仅 running / Resume 仅 run 详情非 running /
      定位首个错误仅异常 run),执行分发如下 ────────────────────────── */

async function reloadSkills() {
  try {
    const r = await postJson("/api/skills/reload");
    toast(r?.reloaded ? "技能已重载" : "技能无变化(文件未修改)", "success");
  } catch (e) {
    toast(e.message ?? "reload 失败", "error");
  }
}

function availableCommands() {
  return COMMANDS.filter((c) => {
    if (c.id === "stop") return wbRunning();
    if (c.id === "resume") return wbResumable();
    if (c.id === "jump-error") return wbRcaAvailable();
    return true; // New Run / Reload Skills / 跳页:所有页面可用
  });
}

function execCommand(id) {
  if (id === "new-run") openLaunchDialog();
  else if (id === "stop") wbRequestStop(); // 复用 live bar 不可逆确认条(§5)
  else if (id === "resume") wbDoResume();
  else if (id === "reload-skills") reloadSkills();
  else if (id === "goto-runs") location.hash = "#/runs";
  else if (id === "goto-skills") location.hash = "#/skills";
  else if (id === "goto-tools") location.hash = "#/tools";
  else if (id === "jump-error") wbJumpFirstError();
}

let palette = null; // 当前打开的命令条(⌘K toggle 语义)

function togglePalette() {
  if (palette && !palette.isClosed()) {
    palette.close();
    palette = null;
    return;
  }
  if (document.querySelector(".modal-overlay")) return; // 其它浮层(Launch 等)打开时不叠加
  palette = openCommandPalette({
    commands: availableCommands(),
    onPick: (id) => {
      palette = null;
      execCommand(id);
    },
  });
}

/* `/` 聚焦当前页搜索框(§5):Runs 页 = 侧栏搜索;Skills/Tools 页 = 浏览器工具栏搜索 */
function focusPageSearch() {
  const page = document.body.dataset.route;
  const el = page === "runs" ? $("#searchInput") : $("#main .brw-search");
  if (!el) return;
  el.focus();
  el.select?.();
}

/* ── 全局键盘(§5;不劫持输入控件:textarea/input 聚焦时除 Esc 与 ⌘K 外不生效)── */
installGlobalKeys({
  onPalette: togglePalette, // ⌘K / Ctrl+K(输入框聚焦时也可用)
  onHelp: () => openShortcutsPanel(), // ?
  onSearchFocus: focusPageSearch, // /
  onSignalStep: (d) => wbNavSignal(d), // j / k(Workbench)
  onSignalEdge: (w) => wbNavEdge(w), // gg / G(Workbench)
  onJumpError: () => wbJumpFirstError(), // ⌘J / Ctrl+J(异常 run)
});

window.addEventListener("hashchange", applyRoute);

/* ── 页面冻结恢复(WebBridge 排障报告根因):Chrome 后台标签页被冻结(Energy
   Saver / Tab Freeze)时 JS 事件循环整体停摆,按钮点击被静默吞掉且无任何
   提示——表象即"页面不 work"。恢复可见时:提示"刚才是休眠"+ 立即补一轮
   轮询(数据面各自轮询自愈,这里消除恢复期滞后并告知原因)。bfcache 恢复
   (pageshow.persisted)时 DOM 是旧快照,同样补提示与刷新。── */
let _hiddenAt = 0;
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    _hiddenAt = Date.now();
    return;
  }
  if (_hiddenAt && Date.now() - _hiddenAt > 30_000) {
    toast(copy("app.resumed"), "info");
    poll();
    pollInbox();
  }
  _hiddenAt = 0;
});
window.addEventListener("pageshow", (e) => {
  if (e.persisted) {
    toast(copy("app.resumed"), "info");
    poll();
    pollInbox();
  }
});

/* ── 启动:深链接恢复(§4.1)→ 首次轮询 → 5s 周期 ─────────────────── */

if (!location.hash) history.replaceState(null, "", "#/runs");
initTheme(); // 主题 T1:URL > localStorage > classic 解析 + <html data-theme>
mountThemePicker($("#themePicker")); // TopBar 主题切换器(§2.5)
applyRoute();
renderChips();
loadSkillsets(); // D6:到达后渲染 set 下拉,并 sanitize hash 恢复的 set 名
poll();
pollInbox(); // S3:supervisor 收件箱随同一周期轮询
renderInboxBadge();
setInterval(() => {
  poll();
  pollInbox();
}, POLL_INTERVAL);

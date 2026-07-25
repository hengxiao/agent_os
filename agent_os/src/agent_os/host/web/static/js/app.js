/* Agent OS Web UI 入口(WEB-UI.md §4.1 应用壳 / §5 交互与状态规范 / §6.1 结构)。
   职责:hash 路由(runs/skills/tools,§4.1 深链接含 ?frame/?signal)、Runs 侧栏(搜索/筛选/折叠)、
   API 健康轮询(live 指示 + 列表 5s 刷新)、三态、Toast、复制。
   run 详情(§4.2 Run Workbench 三联动)由 workbench.js 承担;本文件只做路由分发与事件转发。 */

import { store } from "./store.js";
import { getJson, postJson } from "./api.js";
import { statusPill } from "./components/status-pill.js";
import { absTime, copyText, emptyBlock, esc, fmtCost, relTime, toast } from "./util.js";
import { openLaunchDialog } from "./components/launch-dialog.js";
import { closeSkillsView, openSkillsView } from "./components/skills-view.js";
import { closeToolsView, openToolsView } from "./components/tools-view.js";
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
      #/skills、#/skills/<name>、#/tools、#/tools/<name>)────────────────── */

function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "");
  const [path, qs] = raw.split("?");
  const seg = path.split("/").filter(Boolean).map(decodeURIComponent);
  const q = new URLSearchParams(qs ?? "");
  if (seg[0] === "runs" && seg[1]) {
    return { name: "run-detail", runId: seg[1], frame: q.get("frame"), signal: q.get("signal") };
  }
  if (seg[0] === "skills") return { name: "skills", runId: null, itemName: seg[1] ?? null };
  if (seg[0] === "tools") return { name: "tools", runId: null, itemName: seg[1] ?? null };
  return { name: "runs", runId: null };
}

function applyRoute() {
  const route = parseRoute(location.hash);
  store.set({ route, selectedRunId: route.runId });
}

/* ── 渲染:TopBar 导航 ─────────────────────────────────────────────── */

function renderNav() {
  const name = store.get("route").name;
  const page = name === "run-detail" ? "runs" : name;
  document.body.dataset.route = page; // 侧栏仅 Runs 页显示(app.css 按此驱动)
  document.querySelectorAll(".nav-item").forEach((a) => {
    if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
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
  return store.get("runs").filter((r) => {
    if (ui.statusFilter.size && !ui.statusFilter.has(r.status)) return false;
    if (q && !(r.skill || "").toLowerCase().includes(q)) return false;
    return true;
  });
}

function runItemHtml(r) {
  const sel = r.run_id === store.get("selectedRunId");
  return (
    `<div class="run-item" data-id="${esc(r.run_id)}" role="option" tabindex="0"` +
    ` aria-selected="${sel}" title="${esc(r.skill)} · ${esc(r.run_id)}">` +
    `<div class="run-item-row">` +
    `<span class="run-skill">${esc(r.skill)}</span>` +
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
  if (route.name === "skills") {
    openSkillsView(main, route.itemName); // §4.6(#/skills 与 #/skills/<name> 深链接恢复)
    return;
  }
  if (route.name === "tools") {
    openToolsView(main, route.itemName); // §4.7
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
    renderRunList(); // aria-selected 跟随路由
    renderMain();
  }
  if ("runs" in patch || "runsStatus" in patch) renderRunList();
  if ("liveConn" in patch) renderLiveIndicator(); // §5 SSE 连接态 → TopBar live 点
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
    if (act === "copy") {
      const label = action.dataset.copyLabel || "已复制";
      copyText(action.dataset.copy).then((ok) =>
        toast(ok ? label : "复制失败", ok ? "success" : "error"));
      return;
    }
    workbenchClick(e, action); // workbench 自有 data-action(ft-toggle/tl-toggle/wb-* 等)
    return;
  }
  if (workbenchClick(e, null)) return; // workbench 行点击(帧树/时间线)
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
});

$("#liveIndicator").addEventListener("click", () => {
  if (store.get("liveConn") === "down") {
    reconnectLive(); // §5:SSE 已断开 → 点击重建该 run 的 SSE
    return;
  }
  poll();
});
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

/* ── 启动:深链接恢复(§4.1)→ 首次轮询 → 5s 周期 ─────────────────── */

if (!location.hash) history.replaceState(null, "", "#/runs");
applyRoute();
renderChips();
poll();
setInterval(poll, POLL_INTERVAL);

/* Agent OS Web UI 入口(WEB-UI.md §4.1 应用壳 / §5 交互与状态规范 / §6.1 结构)。
   职责:hash 路由(runs/skills/tools)、Runs 侧栏(搜索/筛选/折叠)、
   API 健康轮询(live 指示 + 列表 5s 刷新)、三态、Toast、复制。 */

import { store } from "./store.js";
import { getJson, ApiError } from "./api.js";
import { statusPill } from "./components/status-pill.js";

const POLL_INTERVAL = 5000; // §4.1:列表 5s 轮询;兼作 API 健康检查

const $ = (sel, root = document) => root.querySelector(sel);

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* UI 局部状态:筛选/折叠/详情是纯视图状态,不进全局 store(store 只存 §6.1 四键)。 */
const ui = {
  search: "",
  statusFilter: new Set(), // 空集 = 全部
  detail: { status: "idle", runId: null, data: null, error: null, metaKey: "" },
};

/* ── 时间 / cost 格式化(§5:相对时间 + title 绝对时间)──────────────── */

function relTime(iso) {
  const t = Date.parse(iso ?? "");
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 10) return "刚刚";
  if (s < 60) return `${Math.floor(s)} 秒前`;
  const m = s / 60;
  if (m < 60) return `${Math.floor(m)} 分钟前`;
  const h = m / 60;
  if (h < 24) return `${Math.floor(h)} 小时前`;
  const d = h / 24;
  if (d < 30) return `${Math.floor(d)} 天前`;
  return new Date(t).toLocaleDateString();
}

function absTime(iso) {
  const t = Date.parse(iso ?? "");
  return Number.isNaN(t) ? "—" : new Date(t).toLocaleString();
}

const fmtCost = (c) => `$${(Number(c) || 0).toFixed(2)}`;

/* ── hash 路由(§4.1 深链接:#/runs、#/runs/<id>、#/skills、#/tools)── */

function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "").split("?")[0]; // ?frame/?signal 属 D2,此处容忍忽略
  const seg = raw.split("/").filter(Boolean).map(decodeURIComponent);
  if (seg[0] === "runs" && seg[1]) return { name: "run-detail", runId: seg[1] };
  if (seg[0] === "skills") return { name: "skills", runId: null };
  if (seg[0] === "tools") return { name: "tools", runId: null };
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

const emptyBlock = (title, hint) =>
  `<div class="empty">` +
  `<div class="empty-illust" aria-hidden="true">插画位</div>` +
  `<span class="empty-title">${esc(title)}</span>` +
  `<span class="empty-hint">${esc(hint)}</span>` +
  `</div>`;

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
        ? emptyBlock("还没有 run", "点击右上角 + New Run 发起第一次运行")
        : emptyBlock("无匹配的 run", "调整搜索关键词或状态筛选");
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

/* ── 渲染:主区(Runs 空态引导 / run 详情脚手架 / Skills / Tools)────── */

const placeholderPage = (title, hint) =>
  `<h1 class="page-head">${esc(title)}</h1>` + emptyBlock(hint, "该页面在里程碑 D4 交付");

function renderMain() {
  const main = $("#main");
  const route = store.get("route");
  if (route.name === "skills") {
    main.innerHTML = placeholderPage("Skills", "施工中 — Skills 浏览器 D4 交付");
    return;
  }
  if (route.name === "tools") {
    main.innerHTML = placeholderPage("Tools", "施工中 — Tools 浏览器 D4 交付");
    return;
  }
  if (route.name === "run-detail") {
    renderDetail();
    return;
  }
  main.innerHTML =
    `<div class="main-home">` +
    emptyBlock("选择一个 run", "从左侧列表选择 run 查看详情,或点击 + New Run 发起新运行") +
    `</div>`;
}

/* run 详情(D1 脚手架:run 头 + D2 提示;Workbench 三联动属 D2) */

const detailMetaKey = (meta, data) =>
  `${meta.skill}|${data?.status ?? meta.status}|${meta.cost}`;

async function loadDetail(runId) {
  ui.detail = { status: "loading", runId, data: null, error: null, metaKey: "" };
  renderDetail();
  try {
    const data = await getJson(`/api/runs/${encodeURIComponent(runId)}`);
    if (ui.detail.runId !== runId || store.get("route").name !== "run-detail") return; // 路由已切走
    ui.detail = { status: "ready", runId, data, error: null, metaKey: "" };
  } catch (e) {
    if (ui.detail.runId !== runId || store.get("route").name !== "run-detail") return;
    ui.detail = { status: "error", runId, data: null, error: e, metaKey: "" };
  }
  renderDetail();
}

function renderDetail() {
  const main = $("#main");
  const d = ui.detail;
  if (d.status === "idle" || d.status === "loading") {
    main.innerHTML =
      `<div class="run-detail"><div class="card"><div class="skeleton-stack">` +
      `<span class="skeleton skeleton-line w-40"></span>` +
      `<span class="skeleton skeleton-line w-70"></span>` +
      `<span class="skeleton skeleton-line w-60"></span>` +
      `</div></div></div>`;
    return;
  }
  if (d.status === "error") {
    const notFound = d.error instanceof ApiError && d.error.status === 404;
    main.innerHTML =
      `<div class="run-detail"><div class="card panel-error">` +
      `<span class="error-msg">${notFound ? "未找到该 run(可能已被清理)" : "加载 run 详情失败"}</span>` +
      `<span class="mono">${esc(d.runId)}</span>` +
      (notFound
        ? `<a class="btn" href="#/runs">返回 Runs</a>`
        : `<button class="btn" data-action="retry-detail">重试</button>`) +
      `</div></div>`;
    return;
  }
  const meta = store.get("runs").find((r) => r.run_id === d.runId) || {};
  d.metaKey = detailMetaKey(meta, d.data);
  main.innerHTML =
    `<div class="run-detail">` +
    `<div class="card run-header">` +
    `<span class="run-title">${esc(meta.skill ?? "—")}</span>` +
    statusPill(d.data.status ?? meta.status) +
    `<span class="run-id" title="${esc(d.runId)}">` +
    `<span class="run-id-text">${esc(d.runId)}</span>` +
    `<button class="copy-btn" data-action="copy" data-copy="${esc(d.runId)}" data-tip="复制 run_id"` +
    ` aria-label="复制 run_id">${COPY_SVG}</button>` +
    `</span>` +
    `<span class="run-time" title="${esc(absTime(meta.started_at))}">${esc(relTime(meta.started_at))}</span>` +
    `<span class="run-cost mono">${esc(fmtCost(meta.cost))}</span>` +
    `</div>` +
    `<div class="card scaffold-hint">` +
    `<span class="scaffold-title">Workbench 三联动视图 — D2 交付</span>` +
    `<span class="scaffold-sub">帧树 / 信号时间线 / 上下文检视器将在这里渲染。</span>` +
    `</div>` +
    `</div>`;
}

/* ── Toast(§5:右下,3s 自动消失,带关闭钮)────────────────────────── */

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.kind = kind;
  el.setAttribute("role", "status");
  el.innerHTML =
    `<span class="toast-msg"></span>` +
    `<button class="toast-close" aria-label="关闭">✕</button>`;
  el.querySelector(".toast-msg").textContent = msg;
  const remove = () => el.remove();
  el.querySelector(".toast-close").addEventListener("click", remove);
  $("#toastStack").appendChild(el);
  setTimeout(remove, 3000);
}

/* ── 复制(§5:run_id 一键复制)────────────────────────────────────── */

const COPY_SVG =
  `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor"` +
  ` stroke-width="1.5" stroke-linecap="round" aria-hidden="true">` +
  `<rect x="5.5" y="5.5" width="8" height="9" rx="1.5"/>` +
  `<path d="M10.5 5.5V3A1.5 1.5 0 0 0 9 1.5H4A1.5 1.5 0 0 0 2.5 3v7A1.5 1.5 0 0 0 4 11.5h1.5"/>` +
  `</svg>`;

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

/* ── API 健康轮询(§5 live 指示 + §4.1 列表 5s 轮询)───────────────── */

let lastHealth = null;

function setHealth(ok) {
  const el = $("#liveIndicator");
  el.dataset.state = ok ? "ok" : "fail";
  el.title = ok ? "连接正常" : "连接异常(点击立即重试)";
  if (lastHealth !== ok) {
    if (!ok) toast("API 连接异常,自动重试中", "error");
    else if (lastHealth === false) toast("API 连接已恢复", "success");
  }
  lastHealth = ok;
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

/* ── store 订阅:按 patch 精准重渲染 ─────────────────────────────── */

store.subscribe((state, patch) => {
  if ("route" in patch) {
    renderNav();
    renderRunList(); // aria-selected 跟随路由
    if (state.route.name === "run-detail") loadDetail(state.route.runId);
    else renderMain();
  }
  if ("runs" in patch || "runsStatus" in patch) {
    renderRunList();
    // 详情头的 skill/时间来自列表元信息;仅元信息变化时重渲染(避免轮询重置滚动)
    if (state.route.name === "run-detail" && ui.detail.status === "ready") {
      const meta = state.runs.find((r) => r.run_id === ui.detail.runId) || {};
      if (detailMetaKey(meta, ui.detail.data) !== ui.detail.metaKey) renderDetail();
    }
  }
});

/* ── 事件接线(事件委托,§6.1)────────────────────────────────────── */

document.addEventListener("click", (e) => {
  const action = e.target.closest("[data-action]");
  if (action) {
    const act = action.dataset.action;
    if (act === "retry-runs") poll();
    if (act === "retry-detail") loadDetail(ui.detail.runId);
    if (act === "copy") {
      copyText(action.dataset.copy).then((ok) =>
        toast(ok ? "已复制 run_id" : "复制失败", ok ? "success" : "error"));
    }
    return;
  }
  const item = e.target.closest(".run-item");
  if (item) location.hash = `#/runs/${encodeURIComponent(item.dataset.id)}`;
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.classList?.contains("run-item")) {
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

$("#liveIndicator").addEventListener("click", poll);
// + New Run 主按钮:D3 交付,当前仅展示 Tooltip(data-tip),点击 no-op

window.addEventListener("hashchange", applyRoute);

/* ── 启动:深链接恢复(§4.1)→ 首次轮询 → 5s 周期 ─────────────────── */

if (!location.hash) history.replaceState(null, "", "#/runs");
applyRoute();
renderChips();
poll();
setInterval(poll, POLL_INTERVAL);

/* Run Workbench 页面(WEB-UI.md §4.2):run 头 + 异常 Banner + 三栏
   (帧树 260px / 信号时间线 flex / 上下文检视器 420px)+ Usage 折叠占位栏。

   三联动(§4.2 规则 1,共享 selection store,订阅按键拆分、只重绘受影响栏):
     帧树选帧 → selection.source="tree":时间线过滤到该帧(提示条可清除),检视器懒加载该帧;
     时间线选信号 → source="timeline":帧树展开祖先+滚动选中所属帧,检视器滚动到对应消息卡片;
     深链接 #/runs/<id>?frame=<fid>&signal=<i>(§4.1)载入后恢复 selection。

   Live 变体(§4.3,detail.status==="running"):
     顶部进度条区(live 脉冲 / 已用时长秒级走动 / steps+cost 双 ProgressBar /
     右侧常驻 Stop);数据优先 SSE(/api/runs/{id}/stream,回放与 REST 快照重复,
     open 时清空重折叠保幂等),SSE 不可用回退 2s 轮询 detail+signals;帧树随
     frame.push/pop 实时生长(新帧滑入,running 帧旋转指示);时间线自动跟随
     (视窗在底则跟随,上翻暂停并浮现"回到底部");Stop → 不可逆确认条 → POST
     /stop → loading → Toast;结束(SSE event:end 或轮询发现终态)→ 进度条区
     替换为结果 Banner(done 绿 / failed 红 / aborted 紫),live 熄灭,停止追加。

   本模块持有页面私有状态(wb):折叠集、帧上下文缓存(Map,懒加载 + 三态)、live 会话。
   组件纯函数在 components/*;本文件只做取数、DOM 写入与事件接线。 */

import { store } from "./store.js";
import { ApiError, getJson, postJson } from "./api.js";
import {
  COPY_SVG,
  absTime,
  copyText,
  emptyBlock,
  esc,
  fmtCost,
  relTime,
  shortId,
  shortSkill,
  toast,
} from "./util.js";
import { statusPill } from "./components/status-pill.js";
import { banner } from "./components/banner.js";
import {
  buildFrameTree,
  defaultCollapsedIds,
  findPath,
  renderFrameTree,
} from "./components/frame-tree.js";
import { deriveTimelineView, renderTimeline } from "./components/timeline.js";
import { focusMessageIndex, renderMessages } from "./components/message-card.js";
import {
  createLiveState,
  deriveProgress,
  fmtElapsed,
  mergeSignal,
  mountLiveBar,
} from "./components/progress-bar.js";

/* live 轮询回退间隔(§4.3:SSE 不可用时 2s 轮询 detail) */
const LIVE_POLL_MS = 2000;
/* 已用时长走动间隔(§5:live 时长秒级更新) */
const LIVE_TICK_MS = 200;

/* ── 页面私有状态(每次切换 run 整体重置)────────────────────────── */

const wb = {
  main: null,
  runId: null,
  status: "idle", // idle | loading | ready | error
  detail: null, // GET /api/runs/{id}(含 frames 帧摘要)
  signals: [], // GET /api/runs/{id}/signals
  error: null,
  roots: [], // 帧树(buildFrameTree)
  failedFrameIds: [], // 失败帧(时间线异常组判定)
  collapsedFrames: new Set(), // 帧树折叠(defaultCollapsedIds 初始化)
  collapsedGroups: new Set(), // 时间线用户折叠的组 key
  frames: new Map(), // 帧上下文缓存:fid -> { status, data?, error? }
  pendingQuery: null, // 深链接 ?frame&signal,ready 后恢复
  lastView: null, // 最近一次 deriveTimelineView 结果(检视器聚焦查组用)
  metaKey: "", // runs 列表元信息指纹(轮询时仅元信息变化才重绘页头)
  els: null, // { head, live, tree, timeline, inspector } 面板引用
  live: null, // live 会话(§4.3):{ state, es, pollId, tickId, follow, ending, knownFrames, bar, startMs }
};

const wbActive = () =>
  store.get("route").name === "run-detail" &&
  store.get("route").runId === wb.runId &&
  wb.status === "ready";

/* 仅路由匹配(不限 ready):load 竞态守卫用 */
const wbRouteMatches = (runId) =>
  store.get("route").name === "run-detail" && store.get("route").runId === runId;

const pushOrder = (signals) =>
  signals.filter((s) => s?.name === "pre:frame.push").map((s) => s.frame_id);

const findFrame = (fid) =>
  (wb.detail?.frames ?? []).find((f) => f?.frame_id === fid) ?? null;

/* ── 入口:路由到 run 详情时由 app.js 调用(幂等)─────────────────── */

export function openWorkbench(main, runId, query = null) {
  wb.main = main;
  if (wb.runId !== runId) {
    teardownLive(); // 换 run:旧 live 会话(SSE/定时器)先收尾
    wb.runId = runId;
    wb.status = "loading";
    wb.detail = null;
    wb.signals = [];
    wb.error = null;
    wb.roots = [];
    wb.failedFrameIds = [];
    wb.collapsedFrames = new Set();
    wb.collapsedGroups = new Set();
    wb.frames = new Map();
    wb.pendingQuery = query;
    wb.lastView = null;
    wb.metaKey = "";
    wb.els = null;
    store.set({ selection: null }); // 换 run 清空三联动
    load();
    return;
  }
  // 同 run:仅查询参数变化(手动编辑 hash)时恢复 selection
  if (query?.frame && wb.status === "ready") applyPendingQuery(query);
}

/* 离开 run 详情页(app.js 路由分发时调用,幂等):live 会话与页面态整体收尾 */
export function closeWorkbench() {
  teardownLive();
  wb.main = null;
  wb.runId = null;
  wb.status = "idle";
  wb.detail = null;
  wb.signals = [];
  wb.error = null;
  wb.roots = [];
  wb.els = null;
}

/* ── 取数:detail + signals 并行;帧上下文按选中懒加载 ────────────── */

async function load() {
  const runId = wb.runId;
  wb.status = "loading";
  renderShell();
  try {
    const [detail, signals] = await Promise.all([
      getJson(`/api/runs/${encodeURIComponent(runId)}`),
      getJson(`/api/runs/${encodeURIComponent(runId)}/signals`),
    ]);
    if (wb.runId !== runId || !wbRouteMatches(runId)) return; // 加载期间路由已切走
    wb.detail = detail;
    wb.signals = Array.isArray(signals) ? signals : [];
    wb.roots = buildFrameTree(detail?.frames, pushOrder(wb.signals));
    wb.collapsedFrames = defaultCollapsedIds(wb.roots);
    wb.failedFrameIds = (detail?.frames ?? [])
      .filter((f) => f?.status === "failed")
      .map((f) => f.frame_id);
    wb.status = "ready";
    renderShell();
    const q = wb.pendingQuery;
    wb.pendingQuery = null;
    if (q?.frame) applyPendingQuery(q); // 深链接恢复(§4.1)
    if (detail?.status === "running") startLive(); // §4.3:进行中 run 进 live 变体
  } catch (e) {
    if (wb.runId !== runId) return;
    wb.status = "error";
    wb.error = e;
    renderShell();
  }
}

async function loadFrame(fid) {
  if (!fid || wb.frames.get(fid)?.status === "loading") return;
  wb.frames.set(fid, { status: "loading" });
  if (store.get("selection")?.frameId === fid) renderInspectorPanel();
  try {
    const data = await getJson(
      `/api/runs/${encodeURIComponent(wb.runId)}/frames/${encodeURIComponent(fid)}`);
    wb.frames.set(fid, { status: "ready", data });
  } catch (e) {
    wb.frames.set(fid, { status: "error", error: e });
  }
  if (store.get("selection")?.frameId === fid && wbActive()) renderInspectorPanel();
}

/* ── 渲染:页面骨架(loading / error / ready 三态,§5)────────────── */

const skeletonStack = (
  `<div class="skeleton-stack skeleton-pad">` +
  `<span class="skeleton skeleton-line w-40"></span>` +
  `<span class="skeleton skeleton-line w-70"></span>` +
  `<span class="skeleton skeleton-line w-60"></span>` +
  `</div>`
);

function renderShell() {
  const main = wb.main;
  if (!main) return;
  if (wb.status === "loading" || wb.status === "idle") {
    main.innerHTML =
      `<div class="wb">` +
      `<div class="card">${skeletonStack}</div>` +
      `<div class="wb-cols">` +
      `<section class="wb-panel wb-tree">${skeletonStack}</section>` +
      `<section class="wb-panel wb-timeline">${skeletonStack}</section>` +
      `<section class="wb-panel wb-inspector">${skeletonStack}</section>` +
      `</div></div>`;
    return;
  }
  if (wb.status === "error") {
    const notFound = wb.error instanceof ApiError && wb.error.status === 404;
    main.innerHTML =
      `<div class="wb"><div class="card panel-error">` +
      `<span class="error-msg">${notFound ? "未找到该 run(可能已被清理)" : "加载 run 详情失败"}</span>` +
      `<span class="mono">${esc(wb.runId)}</span>` +
      (notFound
        ? `<a class="btn" href="#/runs">返回 Runs</a>`
        : `<button class="btn" data-action="wb-retry">重试</button>`) +
      `</div></div>`;
    return;
  }
  main.innerHTML =
    `<div class="wb">` +
    `<div id="wbHead"></div>` +
    `<div id="wbLive" class="wb-live"></div>` +
    `<div class="wb-cols">` +
    `<section class="wb-panel wb-tree" id="wbTree" aria-label="帧树"></section>` +
    `<section class="wb-panel wb-timeline" id="wbTimeline" aria-label="信号时间线"></section>` +
    `<section class="wb-panel wb-inspector" id="wbInspector" aria-label="上下文检视器"></section>` +
    `</div>` +
    `<details class="wb-usage" id="wbUsage">` +
    `<summary>Usage 按帧 <span class="wb-tag">D5 交付</span></summary>` +
    `<div class="wb-usage-body">${skeletonStack}</div>` +
    `</details>` +
    `</div>`;
  wb.els = {
    head: main.querySelector("#wbHead"),
    live: main.querySelector("#wbLive"),
    tree: main.querySelector("#wbTree"),
    timeline: main.querySelector("#wbTimeline"),
    inspector: main.querySelector("#wbInspector"),
  };
  // live 时间线自动跟随(§4.2 规则 2 live 半):用户上翻暂停跟随,回到底部恢复
  wb.els.timeline.addEventListener("scroll", () => {
    const live = wb.live;
    const el = wb.els?.timeline;
    if (!live || live.ending || !el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
    if (atBottom !== live.follow) {
      live.follow = atBottom;
      syncFollowBtn();
    }
  });
  renderHeader();
  renderTreePanel();
  renderTimelinePanel();
  renderInspectorPanel();
}

/* ── 渲染:run 头(§4.2:skill/StatusPill/run_id/started_at/result 摘要)
      + 异常 run Banner(status + error + RCA D5 注)────────────────── */

function renderHeader() {
  const el = wb.els?.head;
  if (!el || wb.status !== "ready") return;
  const d = wb.detail ?? {};
  const meta = store.get("runs").find((r) => r.run_id === wb.runId) ?? {};
  const skill = meta.skill ?? shortSkill(d.frames?.[0]?.skill);
  const status = d.status ?? meta.status;
  const cost = meta.cost ?? d.usage?.cost;
  wb.metaKey = `${skill}|${status}|${cost}|${meta.started_at}`;
  let resultSummary = "—";
  if (d.result != null) {
    try {
      resultSummary = JSON.stringify(d.result);
    } catch {
      resultSummary = String(d.result);
    }
    if (resultSummary.length > 120) resultSummary = `${resultSummary.slice(0, 120)}…`;
  }
  el.innerHTML =
    `<div class="card run-header wb-head">` +
    `<span class="run-title">${esc(skill)}</span>` +
    statusPill(status) +
    `<span class="run-id" title="${esc(wb.runId)}">` +
    `<span class="run-id-text">${esc(wb.runId)}</span>` +
    `<button class="copy-btn" data-action="copy" data-copy="${esc(wb.runId)}"` +
    ` data-copy-label="已复制 run_id" data-tip="复制 run_id" aria-label="复制 run_id">${COPY_SVG}</button>` +
    `</span>` +
    `<span class="run-time" title="${esc(absTime(meta.started_at))}">${esc(relTime(meta.started_at))}</span>` +
    `<span class="run-cost mono">${esc(fmtCost(cost))}</span>` +
    `<span class="wb-result mono" title="result 摘要">result: ${esc(resultSummary)}</span>` +
    `</div>` +
    (status === "failed"
      ? banner("danger", `run failed — ${esc(d.error ?? "未知错误")}`, "RCA 一键定位 D5 交付")
      : "") +
    (status === "aborted"
      ? banner("aborted", `run aborted — ${esc(d.error ?? "已中止")}`, "RCA 一键定位 D5 交付")
      : "");
}

/* ── 渲染:帧树(左栏)───────────────────────────────────────────── */

function renderTreePanel() {
  const el = wb.els?.tree;
  if (!el) return;
  const html = renderFrameTree(wb.roots, {
    collapsed: wb.collapsedFrames,
    selection: store.get("selection"),
  });
  el.innerHTML = html || emptyBlock("无帧数据", "该 run 尚未产生帧(checkpoint 缺失)");
}

/* 选中态定点更新(不整树重绘);时间线来源时展开祖先并滚动到所属帧(§4.2 规则 1) */
function updateTreeSelection(sel) {
  const el = wb.els?.tree;
  if (!el) return;
  if (sel?.frameId && sel.source === "timeline") {
    const path = findPath(wb.roots, sel.frameId);
    const collapsedAncestors = (path ?? [])
      .slice(0, -1)
      .filter((n) => wb.collapsedFrames.has(n.frame.frame_id));
    if (collapsedAncestors.length) {
      collapsedAncestors.forEach((n) => wb.collapsedFrames.delete(n.frame.frame_id));
      renderTreePanel(); // 祖先由折叠变展开,行集变化,需整树重绘
    }
  }
  el.querySelectorAll(".ft-row").forEach((row) => {
    row.setAttribute("aria-selected", String(row.dataset.frameId === (sel?.frameId ?? null)));
  });
  if (sel?.frameId && sel.source === "timeline") {
    el.querySelector(`.ft-row[data-frame-id="${sel.frameId}"]`)?.scrollIntoView({ block: "nearest" });
  }
}

/* ── 渲染:信号时间线(中栏)────────────────────────────────────── */

function renderTimelinePanel() {
  const el = wb.els?.timeline;
  if (!el) return;
  const sel = store.get("selection");
  const view = deriveTimelineView(wb.signals, sel, {
    collapsed: wb.collapsedGroups,
    failedFrameIds: wb.failedFrameIds,
  });
  wb.lastView = view;
  el.innerHTML = wb.signals.length
    ? renderTimeline(wb.signals, view, {
        selection: sel,
        frameSkill: view.filterFrameId ? shortSkill(findFrame(view.filterFrameId)?.skill) : null,
      })
    : emptyBlock("无信号数据", "trace.jsonl 缺失或该 run 尚未产生信号");
  if (sel?.source === "timeline" && sel.signalIndex != null && view.focused?.visible) {
    el.querySelector(`[data-signal-index="${sel.signalIndex}"]`)?.scrollIntoView({ block: "nearest" });
  }
  applyFollow(); // live:新信号到达时视窗在底则自动跟随(§4.2 规则 2 live 半)
}

/* ── Live(§4.3):进度条区 / SSE 驱动 / 轮询回退 / Stop / 结束态 ──── */

/* 自动跟随:follow 时滚到底;"回到底部"悬浮钮按 follow 态浮现/移除 */
function applyFollow() {
  const live = wb.live;
  const el = wb.els?.timeline;
  if (!el) return;
  if (live && !live.ending && live.follow) el.scrollTop = el.scrollHeight;
  syncFollowBtn();
}

function syncFollowBtn() {
  const live = wb.live;
  const el = wb.els?.timeline;
  if (!el) return;
  const show = Boolean(live) && !live.ending && !live.follow;
  let btn = el.querySelector(".tl-follow");
  if (!show) {
    btn?.remove();
    return;
  }
  if (!btn) {
    btn = document.createElement("button");
    btn.className = "tl-follow";
    btn.dataset.action = "wb-follow";
    btn.textContent = "↓ 回到底部";
    el.appendChild(btn);
  }
}

/* 帧树实时生长(§4.3):live.state.frames 重建树,新帧行加滑入动画类 */
function refreshLiveTree() {
  const live = wb.live;
  if (!live) return;
  wb.roots = buildFrameTree(live.state.frames, pushOrder(wb.signals));
  renderTreePanel();
  const el = wb.els?.tree;
  if (!el) return;
  for (const f of live.state.frames) {
    if (live.knownFrames.has(f.frame_id)) continue;
    live.knownFrames.add(f.frame_id);
    el.querySelector(`.ft-row[data-frame-id="${f.frame_id}"]`)?.classList.add("ft-new");
  }
}

function startLive() {
  const live = (wb.live = {
    state: createLiveState(),
    es: null,
    pollId: null,
    tickId: null,
    follow: true,
    ending: false,
    knownFrames: new Set(),
    bar: null,
    startMs: Date.parse(wb.detail?.started_at ?? "") || Date.now(),
  });
  // REST 快照(初始 signals)先折叠进 live 状态:帧树/进度与时间线同源
  for (const s of wb.signals) mergeSignal(live.state, s);
  refreshLiveTree();
  live.bar = mountLiveBar(wb.els.live, { onStop: doStop });
  live.bar.update(deriveProgress(wb.detail, wb.signals));
  live.bar.setElapsed(fmtElapsed(live.startMs, Date.now()));
  live.tickId = setInterval(() => {
    live.bar?.setElapsed(fmtElapsed(live.startMs, Date.now()));
  }, LIVE_TICK_MS);
  if (typeof EventSource === "function") {
    const es = new EventSource(`/api/runs/${encodeURIComponent(wb.runId)}/stream`);
    live.es = es;
    es.onopen = () => {
      // 服务端 subscribe 原子回放环形缓冲,与 REST 快照重复:清空重折叠,幂等防重
      live.state = createLiveState();
      wb.signals = [];
    };
    es.onmessage = (ev) => {
      let row = null;
      try {
        row = JSON.parse(ev.data);
      } catch {
        return; // keepalive 注释行不到这里;畸形行跳过
      }
      onLiveSignal(row);
    };
    es.addEventListener("end", () => {
      es.close();
      finishLive(); // SSE 终止事件:run 已结束,切结束态
    });
    es.onerror = () => {
      if (live.ending || wb.live !== live) return;
      es.close();
      toast("实时连接中断,回退 2s 轮询", "info");
      startPolling();
    };
  } else {
    startPolling();
  }
}

function onLiveSignal(row) {
  const live = wb.live;
  if (!live || live.ending) return;
  mergeSignal(live.state, row);
  wb.signals = live.state.signals;
  if (live.state.lastEffect === "tree" || live.state.lastEffect === "chip") {
    refreshLiveTree(); // frame.push/pop 或帧 steps/cost 变化
  }
  renderTimelinePanel(); // 含 applyFollow
  live.bar?.update(deriveProgress(wb.detail, wb.signals));
}

/* SSE 不可用(浏览器无 EventSource / 连接失败):回退 2s 轮询 detail+signals */
function startPolling() {
  const live = wb.live;
  if (!live || live.pollId != null) return;
  live.pollId = setInterval(pollTick, LIVE_POLL_MS);
}

async function pollTick() {
  const live = wb.live;
  const runId = wb.runId;
  if (!live || live.ending) return;
  try {
    const [detail, signals] = await Promise.all([
      getJson(`/api/runs/${encodeURIComponent(runId)}`),
      getJson(`/api/runs/${encodeURIComponent(runId)}/signals`),
    ]);
    if (wb.runId !== runId || !wbRouteMatches(runId) || wb.live !== live || live.ending) return;
    wb.detail = detail;
    live.state = createLiveState(); // 轮询拿全量快照:整体重折叠,天然幂等
    for (const s of signals ?? []) mergeSignal(live.state, s);
    wb.signals = live.state.signals;
    if (detail?.status && detail.status !== "running") {
      await finishLive(); // 轮询发现终态:切结束态
      return;
    }
    refreshLiveTree();
    renderTimelinePanel();
    live.bar?.update(deriveProgress(detail, wb.signals));
  } catch {
    /* 单次轮询失败静默:下个周期重试(连接异常 Toast 由 app 健康轮询承担) */
  }
}

/* 结束态切换(§4.3):进度条区 → 结果 Banner;live 脉冲熄灭;时间线停止追加 */
async function finishLive() {
  const live = wb.live;
  const runId = wb.runId;
  if (!live || live.ending) return;
  live.ending = true;
  live.es?.close();
  if (live.pollId != null) clearInterval(live.pollId);
  if (live.tickId != null) clearInterval(live.tickId);
  try {
    const [detail, signals] = await Promise.all([
      getJson(`/api/runs/${encodeURIComponent(runId)}`),
      getJson(`/api/runs/${encodeURIComponent(runId)}/signals`),
    ]);
    if (wb.runId !== runId || !wbRouteMatches(runId)) return;
    wb.detail = detail ?? wb.detail;
    if (Array.isArray(signals) && signals.length) wb.signals = signals;
    // 终态帧树:优先 checkpoint 重建的真实帧;产物半写窗口回退 live 帧(标终态)
    let frames = wb.detail?.frames ?? [];
    if (!frames.length && live.state.frames.length) {
      const st = wb.detail?.status === "aborted" ? "aborted" : wb.detail?.status === "failed" ? "failed" : "done";
      frames = live.state.frames.map((f) => (f.status === "running" ? { ...f, status: st } : f));
    }
    wb.roots = buildFrameTree(frames, pushOrder(wb.signals));
    wb.failedFrameIds = frames.filter((f) => f?.status === "failed").map((f) => f.frame_id);
  } catch {
    /* 终态拉取失败:仍按现有 detail 收尾(Banner/页头显示已知状态) */
  }
  if (wb.runId !== runId) return;
  live.bar?.end(wb.detail?.status ?? "done", wb.detail?.error);
  live.bar?.destroy();
  wb.live = null; // 时间线停止追加(onLiveSignal/pollTick 均守卫 wb.live)
  renderHeader();
  renderTreePanel();
  renderTimelinePanel();
}

function teardownLive() {
  const live = wb.live;
  if (!live) return;
  live.ending = true;
  live.es?.close();
  if (live.pollId != null) clearInterval(live.pollId);
  if (live.tickId != null) clearInterval(live.tickId);
  live.bar?.destroy();
  wb.live = null;
}

/* Stop 流(§4.3/§5):确认条由 live bar 组件承担;成功后等 safe point 生效 */
async function doStop() {
  try {
    await postJson(`/api/runs/${encodeURIComponent(wb.runId)}/stop`);
    toast("已请求中止,run 将在下一个 safe point 停止", "success");
    return true;
  } catch (e) {
    toast(e.message ?? "stop 失败", "error");
    return false;
  }
}

/* ── 渲染:上下文检视器(右栏,三态:Skeleton/空提示/错误重试)─────── */

function renderInspectorPanel() {
  const el = wb.els?.inspector;
  if (!el) return;
  const sel = store.get("selection");
  const fid = sel?.frameId ?? null;
  if (!fid) {
    el.innerHTML = emptyBlock("选择一帧查看上下文", "点击左侧帧树的帧,或点击时间线信号联动定位");
    return;
  }
  const cached = wb.frames.get(fid);
  if (!cached) {
    loadFrame(fid); // 懒加载;内部按 selection 仍未变化时重绘
    return;
  }
  if (cached.status === "loading") {
    el.innerHTML = skeletonStack;
    return;
  }
  if (cached.status === "error") {
    const notFound = cached.error instanceof ApiError && cached.error.status === 404;
    // live 中帧上下文尚未落盘(checkpoint 在 run 结束后才可检视),文案区分
    const running = wb.detail?.status === "running";
    el.innerHTML =
      `<div class="panel-error">` +
      `<span class="error-msg">${
        notFound
          ? running
            ? "该帧仍在进行,上下文待 run 结束后落盘"
            : "未找到该帧(可能已被压缩/清理)"
          : "加载帧上下文失败"
      }</span>` +
      `<span class="mono">f-${esc(shortId(fid))}</span>` +
      `<button class="btn" data-action="wb-retry-frame" data-frame-id="${esc(fid)}">重试</button>` +
      `</div>`;
    return;
  }
  const f = cached.data ?? {};
  const msgs = Array.isArray(f.messages) ? f.messages : [];
  const steps = f.usage?.steps;
  const cost = f.usage?.cost;
  el.innerHTML =
    `<div class="insp-head">` +
    `<span class="insp-title">${esc(shortSkill(f.skill))} · f-${esc(shortId(fid))}</span>` +
    statusPill(f.status) +
    (steps != null ? `<span class="chip chip-static">${esc(steps)} steps</span>` : "") +
    (cost != null ? `<span class="chip chip-static mono">${esc(fmtCost(cost))}</span>` : "") +
    `<button class="btn btn-mini" data-action="wb-copy-frame" data-tip="复制该帧全部消息 JSON">复制全部 JSON</button>` +
    `</div>` +
    (f.error
      ? banner("danger", "帧错误", esc(typeof f.error === "string" ? f.error : JSON.stringify(f.error)))
      : "") +
    (msgs.length ? renderMessages(msgs) : emptyBlock("该帧无上下文消息", "frame.context.messages 为空"));
  // 时间线选中 → 滚动到该信号对应的消息卡片(§4.2 规则 1)
  if (sel?.signalIndex != null) {
    const signal = wb.signals[sel.signalIndex];
    const group = (wb.lastView?.all ?? []).find((g) =>
      g.items.some((it) => it.index === sel.signalIndex));
    const idx = focusMessageIndex(signal, group?.step ?? null, msgs);
    if (idx != null) {
      el.querySelector(`[data-msg-index="${idx}"]`)?.scrollIntoView({ block: "start" });
    }
  }
}

/* ── 深链接恢复(§4.1:?frame=<fid>&signal=<i>)────────────────────── */

function applyPendingQuery(q) {
  const fid = q.frame;
  if (!fid || !findPath(wb.roots, fid)) return;
  const n = Number(q.signal);
  const signalIndex =
    q.signal != null && q.signal !== "" && Number.isInteger(n) && n >= 0 && n < wb.signals.length
      ? n
      : null;
  store.set({
    selection: { frameId: fid, signalIndex, source: signalIndex != null ? "timeline" : "tree" },
  });
}

/* selection → hash(replaceState,不触发 hashchange;深链接可分享) */
function syncHash(sel) {
  const route = store.get("route");
  if (route.name !== "run-detail") return;
  const q = [];
  if (sel?.frameId) q.push(`frame=${encodeURIComponent(sel.frameId)}`);
  if (sel?.signalIndex != null) q.push(`signal=${sel.signalIndex}`);
  history.replaceState(null, "", `#/runs/${encodeURIComponent(route.runId)}${q.length ? `?${q.join("&")}` : ""}`);
}

/* ── 选择动作(点击与 Enter 共用)────────────────────────────────── */

function selectFrame(fid) {
  const cur = store.get("selection");
  if (cur?.frameId === fid && cur?.signalIndex == null && cur?.source === "tree") {
    store.set({ selection: null }); // 再点已选帧 = 清除过滤(开关)
  } else {
    store.set({ selection: { frameId: fid, signalIndex: null, source: "tree" } });
  }
}

function selectSignal(dataset) {
  const idx = Number(dataset.signalIndex);
  if (!Number.isInteger(idx)) return;
  const cur = store.get("selection");
  if (cur?.signalIndex === idx) {
    store.set({ selection: null }); // 再点已选信号 = 取消选中(开关)
  } else {
    store.set({
      selection: { frameId: dataset.frameId || null, signalIndex: idx, source: "timeline" },
    });
  }
}

function copyMessage(msgIndex) {
  const fid = store.get("selection")?.frameId;
  const cached = fid ? wb.frames.get(fid) : null;
  const m = cached?.status === "ready" ? cached.data?.messages?.[msgIndex] : null;
  if (!m) return;
  const text = typeof m.content === "string" && m.content ? m.content : JSON.stringify(m, null, 2);
  copyText(text).then((ok) => toast(ok ? "已复制消息原文" : "复制失败", ok ? "success" : "error"));
}

function copyFrame() {
  const fid = store.get("selection")?.frameId;
  const cached = fid ? wb.frames.get(fid) : null;
  if (cached?.status !== "ready") return;
  copyText(JSON.stringify(cached.data?.messages ?? [], null, 2)).then((ok) =>
    toast(ok ? "已复制帧上下文 JSON" : "复制失败", ok ? "success" : "error"));
}

/* ── 事件接线(app.js 事件委托转发;命中即返回 true)───────────────── */

export function workbenchClick(e, action) {
  if (!wb.main || store.get("route").name !== "run-detail") return false;
  if (action) {
    const act = action.dataset.action;
    if (act === "wb-retry") return load(), true;
    if (act === "wb-retry-frame") return loadFrame(action.dataset.frameId), true;
    if (act === "wb-copy-frame") return copyFrame(), true;
    if (act === "wb-copy-msg") return copyMessage(Number(action.dataset.msgIndex)), true;
    if (act === "ft-toggle") {
      const fid = action.dataset.frameId;
      if (wb.collapsedFrames.has(fid)) wb.collapsedFrames.delete(fid);
      else wb.collapsedFrames.add(fid);
      return renderTreePanel(), true;
    }
    if (act === "tl-toggle") {
      const key = action.dataset.group;
      if (wb.collapsedGroups.has(key)) wb.collapsedGroups.delete(key);
      else wb.collapsedGroups.add(key);
      return renderTimelinePanel(), true;
    }
    if (act === "tl-clear") return store.set({ selection: null }), true;
    if (act === "wb-follow") {
      // "回到底部"悬浮钮(§4.2 规则 2 live 半):恢复自动跟随
      const live = wb.live;
      if (live) {
        live.follow = true;
        applyFollow();
      }
      return true;
    }
    return false;
  }
  const ftRow = e.target.closest(".ft-row");
  if (ftRow) return selectFrame(ftRow.dataset.frameId), true;
  const tlRow = e.target.closest(".tl-row");
  if (tlRow) return selectSignal(tlRow.dataset), true;
  return false;
}

export function workbenchKeydown(e) {
  if (e.key !== "Enter" || store.get("route").name !== "run-detail") return false;
  const t = e.target;
  const ft = t.closest?.(".ft-row");
  if (ft) return selectFrame(ft.dataset.frameId), true;
  const tl = t.closest?.(".tl-row");
  if (tl) return selectSignal(tl.dataset), true;
  const head = t.closest?.(".tl-group-head");
  if (head) {
    const key = head.dataset.group;
    if (wb.collapsedGroups.has(key)) wb.collapsedGroups.delete(key);
    else wb.collapsedGroups.add(key);
    return renderTimelinePanel(), true;
  }
  const filter = t.closest?.(".tl-filter");
  if (filter) return store.set({ selection: null }), true;
  return false;
}

/* ── store 订阅:按键拆分,只重绘受影响栏(§4.2 规则 1)────────────── */

store.subscribe((state, patch) => {
  if ("selection" in patch) {
    if (!wbActive()) return;
    const sel = store.get("selection");
    updateTreeSelection(sel); // 帧树:选中边条 + 时间线来源时滚动定位
    renderTimelinePanel(); // 时间线:过滤 / 聚焦高亮
    renderInspectorPanel(); // 检视器:切帧 / 滚动到消息
    syncHash(sel);
  }
  if (("runs" in patch || "runsStatus" in patch) && wbActive()) {
    // 页头的 skill/started_at 来自列表元信息;仅元信息变化时重绘页头(避免轮询重置滚动)
    const meta = state.runs.find((r) => r.run_id === wb.runId) ?? {};
    const d = wb.detail ?? {};
    const key = `${meta.skill ?? shortSkill(d.frames?.[0]?.skill)}|${d.status ?? meta.status}|` +
      `${meta.cost ?? d.usage?.cost}|${meta.started_at}`;
    if (key !== wb.metaKey) renderHeader();
  }
});

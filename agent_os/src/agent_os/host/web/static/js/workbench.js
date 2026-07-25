/* Run Workbench 页面(WEB-UI.md §4.2):run 头 + 异常 Banner + 三栏
   (帧树 320px / 执行轨迹 flex / 上下文检视器 420px)+ Usage 折叠栏(§4.5)。

   三联动(§4.2 规则 1,共享 selection store,订阅按键拆分、只重绘受影响栏):
     帧树选帧 → selection.source="tree":轨迹过滤到该帧(提示条可清除),检视器懒加载该帧;
     轨迹选行 → source="timeline":帧树展开祖先+滚动选中所属帧,检视器滚动到对应消息卡片;
     RCA 定位 → source="rca"(§4.4):帧树选中展开祖先,轨迹展开所在调用子树滚动到出错行,
     检视器 veto 归因卡 + 出错卡片红色高亮(一次性脉冲);
     深链接 #/runs/<id>?frame=<fid>&signal=<i>(§4.1)载入后恢复 selection。

   Live 变体(§4.3,detail.status==="running"):
     顶部进度条区(live 脉冲 / 已用时长秒级走动 / steps+cost 双 ProgressBar /
     右侧常驻 Stop);数据优先 SSE(/api/runs/{id}/stream,回放与 REST 快照重复,
     open 时清空重折叠保幂等),SSE 不可用回退 2s 轮询 detail+signals;帧树随
     frame.push/pop 实时生长(新帧滑入,running 帧旋转指示);轨迹随信号增量重算
     (buildTraceRows 全量纯函数,O(n) 足够轻)并自动跟随
     (视窗在底则跟随,上翻暂停并浮现"回到底部");Stop → 不可逆确认条 → POST
     /stop → loading → Toast;结束(SSE event:end 或轮询发现终态)→ 进度条区
     替换为结果 Banner(done 绿 / failed 红 / aborted 紫),live 熄灭,停止追加。
     SSE onerror(§5):store.liveConn 转 "down"(TopBar 黄点 + "已断开,点击重连",
     点击经 reconnectLive() 重建该 run 的 SSE),同时回退轮询保数据不断。

   RCA 模式(§4.4,异常 run):顶部 rcaBanner(定位首个错误 ⌘J / Resume ▶);
     wbJumpFirstError:GET /rca(载入时已随 detail 拉取)→ planRcaJump 纯函数规划 →
     帧树展开祖先选中 first_error.frame_id → 轨迹展开包含出错信号的调用子树并滚动定位
     (无对应行回退该帧最后信号)→ 检视器 veto 归因卡 + 出错卡片高亮脉冲。
     Resume → POST /resume(后端阻塞到恢复结束,产物写回原 run)→ Toast → 重新 load。

   性能(§6.3):轨迹渲染行 >500 启用窗口化(windowRange 视窗 ±50 行,上下占位行
   显示"还有 N 行"),滚动时节流增量替换;j/k/gg/G 键盘导航与 RCA 滚动定位经
   findRowPosition 先落窗再 scrollIntoView。call 行参数(帧 input)随载入按帧
   批量预取(frames/{fid},上限 PREFETCH_FRAMES),到位后重绘一次轨迹。

   本模块持有页面私有状态(wb):折叠集、帧上下文缓存(Map,懒加载 + 三态)、live 会话、
   RCA 缓存、Usage 面板句柄、窗口化行集。组件纯函数在 components/*;本文件只做取数、
   DOM 写入与事件接线。 */

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
  frameDurations,
  renderFrameTree,
} from "./components/frame-tree.js";
import {
  ROW_H,
  WINDOW_BUFFER,
  WINDOW_THRESHOLD,
  deriveTraceView,
  findRowPosition,
  flattenTraceRows,
  isCallCollapsed,
  renderTrace,
  rowForSignal,
  windowRange,
} from "./components/trace.js";
import { focusMessageIndex, renderMessages } from "./components/message-card.js";
import {
  createLiveState,
  deriveProgress,
  fmtElapsed,
  mergeSignal,
  mountLiveBar,
} from "./components/progress-bar.js";
import { planRcaJump, rcaBannerHtml, vetoCardHtml } from "./components/rca-panel.js";
import { mountUsagePanel } from "./components/usage-panel.js";

/* live 轮询回退间隔(§4.3:SSE 不可用时 2s 轮询 detail) */
const LIVE_POLL_MS = 2000;
/* 已用时长走动间隔(§5:live 时长秒级更新) */
const LIVE_TICK_MS = 200;
/* 窗口化滚动重渲染节流(ms,§6.3) */
const SCROLL_RENDER_MS = 80;
/* call 行参数(帧 input)批量预取上限(§4.2;超出帧数选中时再懒加载) */
const PREFETCH_FRAMES = 80;

/* ── 页面私有状态(每次切换 run 整体重置)────────────────────────── */

const wb = {
  main: null,
  runId: null,
  status: "idle", // idle | loading | ready | error
  detail: null, // GET /api/runs/{id}(含 frames 帧摘要)
  signals: [], // GET /api/runs/{id}/signals
  error: null,
  roots: [], // 帧树(buildFrameTree)
  collapsedFrames: new Set(), // 帧树折叠(defaultCollapsedIds 初始化)
  traceCollapsed: new Set(), // 轨迹用户显式折叠的 call(按 sigIndex)
  traceExpanded: new Set(), // 轨迹用户显式展开的 call(覆盖深度默认折叠)
  tracePayload: new Set(), // 轨迹 payload 展开的行(按 sigIndex)
  frames: new Map(), // 帧上下文缓存:fid -> { status, data?, error? }
  pendingQuery: null, // 深链接 ?frame&signal,ready 后恢复
  lastTraceView: null, // 最近一次 deriveTraceView 结果(检视器聚焦/RCA 展开用)
  lastRows: [], // 最近一次 flattenTraceRows 结果(窗口化/键盘导航用)
  lastScrollRender: 0, // 窗口化滚动重渲染节流时间戳
  scrollTimer: null, // 窗口化滚动节流 trailing 定时器
  rca: null, // GET /rca 缓存(异常 run 载入时拉取;{ status, first_error })
  usage: null, // Usage 折叠栏挂载句柄(§4.5,mountUsagePanel 返回)
  skills: null, // GET /api/skills 缓存(§4.2 帧块 kind chip;每次载入拉一次)
  kindByName: new Map(), // skill 名 → kind(prompt/code)
  usageByFid: new Map(), // frame_id → /usage 帧行(tokens/cost)
  durations: new Map(), // frame_id → 帧时长 ms(frameDurations 纯函数)
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
    wb.usage?.destroy?.();
    if (wb.scrollTimer) clearTimeout(wb.scrollTimer);
    wb.runId = runId;
    wb.status = "loading";
    wb.detail = null;
    wb.signals = [];
    wb.error = null;
    wb.roots = [];
    wb.collapsedFrames = new Set();
    wb.traceCollapsed = new Set();
    wb.traceExpanded = new Set();
    wb.tracePayload = new Set();
    wb.frames = new Map();
    wb.pendingQuery = query;
    wb.lastTraceView = null;
    wb.lastRows = [];
    wb.lastScrollRender = 0;
    wb.scrollTimer = null;
    wb.rca = null;
    wb.usage = null;
    wb.skills = null;
    wb.kindByName = new Map();
    wb.usageByFid = new Map();
    wb.durations = new Map();
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
  wb.usage?.destroy?.();
  if (wb.scrollTimer) clearTimeout(wb.scrollTimer);
  wb.scrollTimer = null;
  wb.usage = null;
  wb.rca = null;
  wb.main = null;
  wb.runId = null;
  wb.status = "idle";
  wb.detail = null;
  wb.signals = [];
  wb.error = null;
  wb.roots = [];
  wb.lastRows = [];
  wb.els = null;
  wb.skills = null;
  wb.kindByName = new Map();
  wb.usageByFid = new Map();
  wb.durations = new Map();
}

/* ── 取数:detail + signals 并行;帧上下文按选中懒加载 ────────────── */

async function load() {
  const runId = wb.runId;
  wb.status = "loading";
  renderShell();
  try {
    // detail + signals 并行;skills(kind chip)与 usage(tokens/cost)随载入拉取,
    // 失败降级为空(帧块少 chip/metadata,不阻塞页面,§5 降级原则)
    const [detail, signals, skillsGlobal, usage] = await Promise.all([
      getJson(`/api/runs/${encodeURIComponent(runId)}`),
      getJson(`/api/runs/${encodeURIComponent(runId)}/signals`),
      getJson("/api/skills").catch(() => []),
      getJson(`/api/runs/${encodeURIComponent(runId)}/usage`).catch(() => null),
    ]);
    if (wb.runId !== runId || !wbRouteMatches(runId)) return; // 加载期间路由已切走
    let skills = skillsGlobal;
    // D6:set 跑的 run:全局 registry 大概率没有该技能,按 run 所属 set 重拉(失败沿用全局)
    if (detail?.skill_set && detail.skill_set !== "default") {
      const scoped = await getJson(
        `/api/skills?skill_set=${encodeURIComponent(detail.skill_set)}`).catch(() => null);
      if (Array.isArray(scoped)) skills = scoped;
      if (wb.runId !== runId || !wbRouteMatches(runId)) return;
    }
    wb.detail = detail;
    wb.signals = Array.isArray(signals) ? signals : [];
    wb.skills = Array.isArray(skills) ? skills : [];
    wb.kindByName = new Map(
      wb.skills.filter((s) => s?.name).map((s) => [s.name, s.kind ?? null]));
    wb.usageByFid = new Map(
      (usage?.frames ?? []).filter((f) => f?.frame_id).map((f) => [f.frame_id, f]));
    wb.durations = frameDurations(wb.signals);
    wb.roots = buildFrameTree(detail?.frames, pushOrder(wb.signals));
    wb.collapsedFrames = defaultCollapsedIds(wb.roots);
    // §4.4:异常 run 随载入拉取 RCA 缓存(定位动线 / veto 归因卡数据源);失败降级 null
    wb.rca = null;
    if (detail?.status === "failed" || detail?.status === "aborted") {
      try {
        wb.rca = await getJson(`/api/runs/${encodeURIComponent(runId)}/rca`);
      } catch {
        wb.rca = null;
      }
      if (wb.runId !== runId || !wbRouteMatches(runId)) return;
    }
    wb.status = "ready";
    renderShell();
    const q = wb.pendingQuery;
    wb.pendingQuery = null;
    if (q?.frame) {
      applyPendingQuery(q); // 深链接恢复(§4.1)
    } else if (!store.get("selection") && wb.roots.length) {
      // 默认选中根帧(§4.2:进入 run 即有三联动语境,检视器不再是空屏)
      store.set({
        selection: { frameId: wb.roots[0].frame.frame_id, signalIndex: null, source: "tree" },
      });
    }
    if (detail?.status === "running") startLive(); // §4.3:进行中 run 进 live 变体
    else prefetchFrameInputs(); // call 行参数(帧 input)批量预取,到位重绘轨迹
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

/* call 行参数预取(§4.2):帧 input 只在 frames/{fid} 上下文中;按帧摘要批量拉取
   (上限 PREFETCH_FRAMES,同时温Inspect缓存),全部到位后重绘一次轨迹补 call 行 args。
   已缓存(ready/loading)的帧跳过;进行中 run 上下文未落盘,不预取(由 live 结束后补)。 */
async function prefetchFrameInputs() {
  const runId = wb.runId;
  const fids = (wb.detail?.frames ?? [])
    .map((f) => f?.frame_id)
    .filter((fid) => fid && wb.frames.get(fid)?.status == null)
    .slice(0, PREFETCH_FRAMES);
  if (!fids.length) return;
  await Promise.allSettled(
    fids.map((fid) =>
      getJson(`/api/runs/${encodeURIComponent(runId)}/frames/${encodeURIComponent(fid)}`).then(
        (data) => {
          if (wb.frames.get(fid)?.status == null) wb.frames.set(fid, { status: "ready", data });
        })));
  if (wb.runId !== runId || !wbRouteMatches(runId) || wb.status !== "ready") return;
  renderTimelinePanel(); // 参数到位:call 行补 ({...}) 与 payload
  if (store.get("selection")?.frameId) renderInspectorPanel(); // 检视器缓存已温,直接可用
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
    `<details class="wb-usage" id="wbUsage"></details>` + // §4.5 Usage 折叠栏(mountUsagePanel 填充)
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
    if (live && !live.ending && el) {
      const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
      if (atBottom !== live.follow) {
        live.follow = atBottom;
        syncFollowBtn();
      }
    }
    onTimelineScrollWindow(); // §6.3 窗口化:滚动时节流增量替换
  });
  // §4.5 Usage 折叠栏(默认收起,展开懒加载 /api/runs/{id}/usage)
  wb.usage?.destroy?.();
  wb.usage = mountUsagePanel(main.querySelector("#wbUsage"), {
    load: () => getJson(`/api/runs/${encodeURIComponent(wb.runId)}/usage`),
  });
  renderHeader();
  renderTreePanel();
  renderTimelinePanel();
  renderInspectorPanel();
}

/* ── 渲染:执行轨迹(中栏,§6.3 窗口化)────────────────────────── */

const sumHeight = (rows, from, to) => {
  let h = 0;
  for (let i = from; i < to && i < rows.length; i += 1) h += rows[i]?.h ?? ROW_H;
  return h;
};

const windowed = () => (wb.lastRows?.length ?? 0) > WINDOW_THRESHOLD;

/* 滚动 → 窗口变化时节流重渲染(80ms + trailing,占位行高度维持滚动位置) */
function onTimelineScrollWindow() {
  if (!windowed()) return;
  const now = Date.now();
  const since = now - wb.lastScrollRender;
  if (since >= SCROLL_RENDER_MS) {
    wb.lastScrollRender = now;
    renderTimelinePanel({ skipScroll: true });
    return;
  }
  if (!wb.scrollTimer) {
    wb.scrollTimer = setTimeout(() => {
      wb.scrollTimer = null;
      wb.lastScrollRender = Date.now();
      if (wb.els?.timeline) renderTimelinePanel({ skipScroll: true });
    }, SCROLL_RENDER_MS - since);
  }
}

/* 选中信号滚动定位(轨迹/RCA 来源):窗口化时先按行位置落窗(scrollTop 置中)
   再重渲染切片,最后 scrollIntoView 微调;非窗口化直接 scrollIntoView。
   合并行经 findRowPosition 按 [sigIndex, sigEnd] 区间命中。 */
function scrollSignalIntoView(signalIndex) {
  const el = wb.els?.timeline;
  if (!el) return;
  if (windowed()) {
    const pos = findRowPosition(wb.lastRows, signalIndex);
    if (pos) {
      const vh = el.clientHeight || 0;
      if (pos.offset < el.scrollTop || pos.offset + ROW_H > el.scrollTop + vh) {
        el.scrollTop = Math.max(0, pos.offset - vh / 2);
        renderTimelinePanel({ skipScroll: true }); // 目标行进入新窗口切片
      }
    }
  }
  el.querySelector(`[data-signal-index="${signalIndex}"]`)?.scrollIntoView({ block: "nearest" });
}

/* 帧 input 合并进帧摘要(call 行 args 数据源):已加载的帧上下文取 input 字段 */
function framesWithInputs() {
  const frames = wb.live ? wb.live.state.frames : (wb.detail?.frames ?? []);
  return frames.map((f) => {
    const cached = wb.frames.get(f?.frame_id);
    return cached?.status === "ready" && cached.data?.input !== undefined
      ? { ...f, input: cached.data.input }
      : f;
  });
}

function renderTimelinePanel({ skipScroll = false } = {}) {
  const el = wb.els?.timeline;
  if (!el) return;
  const sel = store.get("selection");
  const view = deriveTraceView(wb.signals, sel, {
    frames: framesWithInputs(),
    collapsed: wb.traceCollapsed,
    expanded: wb.traceExpanded,
    payloadOpen: wb.tracePayload,
  });
  wb.lastTraceView = view;
  const rows = flattenTraceRows(view.rows);
  wb.lastRows = rows;
  let win = null;
  if (rows.length > WINDOW_THRESHOLD) {
    // live 跟随时窗口直接按底部计算(渲染后 applyFollow 才落 scrollTop)
    const follow = Boolean(wb.live) && !wb.live.ending && wb.live.follow;
    const st = follow ? Math.max(0, sumHeight(rows, 0, rows.length) - (el.clientHeight || 0)) : el.scrollTop;
    const { start, end } = windowRange(rows.length, st, ROW_H, el.clientHeight || 0, WINDOW_BUFFER);
    win = {
      start,
      end,
      topPad: sumHeight(rows, 0, start),
      bottomPad: sumHeight(rows, end, rows.length),
      topCount: start,
      bottomCount: rows.length - end,
    };
  }
  el.innerHTML = wb.signals.length
    ? renderTrace(view, {
        selection: sel,
        frameSkill: view.filterFrameId ? shortSkill(findFrame(view.filterFrameId)?.skill) : null,
        window: win,
      })
    : emptyBlock("无信号数据", "trace.jsonl 缺失或该 run 尚未产生信号", "activity");
  const guided = sel?.source === "timeline" || sel?.source === "rca"; // §4.2/§4.4 聚焦滚动
  if (!skipScroll && guided && sel.signalIndex != null && view.focused?.visible) {
    scrollSignalIntoView(sel.signalIndex);
  }
  applyFollow(); // live:新信号到达时视窗在底则自动跟随(§4.2 规则 2 live 半)
}

/* ── 渲染:run 头(§4.2:skill/StatusPill/run_id/started_at/result 摘要)
      + 异常 run RCA Banner(§4.4:status + error 摘要 + 定位 ⌘J + Resume)── */

function renderHeader() {
  const el = wb.els?.head;
  if (!el || wb.status !== "ready") return;
  const d = wb.detail ?? {};
  const meta = store.get("runs").find((r) => r.run_id === wb.runId) ?? {};
  const skill = meta.skill ?? shortSkill(d.frames?.[0]?.skill);
  const status = d.status ?? meta.status;
  const cost = meta.cost ?? d.usage?.cost;
  const steps = d.usage?.steps ?? meta.steps;
  wb.metaKey = `${skill}|${status}|${cost}|${meta.started_at}`;
  // result 摘要降级为次级一行(截断);无 result(进行中/异常)整行省略
  let resultLine = "";
  if (d.result != null) {
    let resultSummary;
    try {
      resultSummary = JSON.stringify(d.result);
    } catch {
      resultSummary = String(d.result);
    }
    if (resultSummary.length > 120) resultSummary = `${resultSummary.slice(0, 120)}…`;
    resultLine = `<span class="wb-result mono" title="result 摘要">result: ${esc(resultSummary)}</span>`;
  }
  el.innerHTML =
    `<div class="card run-header wb-head">` +
    `<div class="run-header-main">` +
    `<span class="run-title">${esc(skill)}</span>` +
    statusPill(status) +
    `<span class="run-id" title="${esc(wb.runId)}">` +
    `<span class="run-id-text">${esc(wb.runId)}</span>` +
    `<button class="copy-btn" data-action="copy" data-copy="${esc(wb.runId)}"` +
    ` data-copy-label="已复制 run_id" data-tip="复制 run_id" aria-label="复制 run_id">${COPY_SVG}</button>` +
    `</span>` +
    `<span class="run-time" title="${esc(absTime(meta.started_at))}">${esc(relTime(meta.started_at))}</span>` +
    (steps != null ? `<span class="meta-chip">${esc(steps)} steps</span>` : "") +
    `<span class="meta-chip mono">${esc(fmtCost(cost))}</span>` +
    `</div>` +
    resultLine +
    `</div>` +
    // §4.4 异常 Banner:status + error 摘要 + 定位首个错误 ⌘J + Resume ▶
    (status === "failed" || status === "aborted" ? rcaBannerHtml(status, d.error) : "");
}

/* ── 渲染:帧树(左栏)───────────────────────────────────────────── */

/* 帧块旁挂数据(§4.2 块头 chip/metadata):kind 按 skill 名 join /api/skills(载入一次);
   tokens/cost 按 frame_id join /usage;duration 取该帧首末信号 ts 差(frameDurations)。 */
function frameExtras(frame) {
  const u = wb.usageByFid.get(frame?.frame_id);
  return {
    kind: wb.kindByName.get(shortSkill(frame?.skill)) ?? null,
    tokens: (Number(u?.prompt_tokens) || 0) + (Number(u?.completion_tokens) || 0),
    cost: Number(u?.cost) || 0,
    durationMs: wb.durations.get(frame?.frame_id) ?? null,
  };
}

function renderTreePanel() {
  const el = wb.els?.tree;
  if (!el) return;
  const html = renderFrameTree(wb.roots, {
    collapsed: wb.collapsedFrames,
    selection: store.get("selection"),
    extras: frameExtras,
  });
  el.innerHTML = html || emptyBlock("无帧数据", "该 run 尚未产生帧(checkpoint 缺失)", "layers");
}

/* 选中态定点更新(不整树重绘);时间线/RCA 来源时展开祖先并滚动到所属帧(§4.2 规则 1/§4.4) */
function updateTreeSelection(sel) {
  const el = wb.els?.tree;
  if (!el) return;
  const guided = sel?.source === "timeline" || sel?.source === "rca";
  if (sel?.frameId && guided) {
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
  if (sel?.frameId && guided) {
    el.querySelector(`.ft-row[data-frame-id="${sel.frameId}"]`)?.scrollIntoView({ block: "nearest" });
  }
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

/* 帧树实时生长(§4.3):live.state.frames 重建树,时长随信号流刷新,新帧块加滑入动画类 */
function refreshLiveTree() {
  const live = wb.live;
  if (!live) return;
  wb.roots = buildFrameTree(live.state.frames, pushOrder(wb.signals));
  wb.durations = frameDurations(wb.signals);
  renderTreePanel();
  const el = wb.els?.tree;
  if (!el) return;
  for (const f of live.state.frames) {
    if (live.knownFrames.has(f.frame_id)) continue;
    live.knownFrames.add(f.frame_id);
    el.querySelector(`.ft-block[data-frame-id="${f.frame_id}"]`)?.classList.add("ft-new");
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
    connectSse(live);
  } else {
    startPolling();
  }
}

/* SSE 连接(§4.3/§5):open → liveConn "ok"(TopBar 蓝点)并停轮询回退;
   error → liveConn "down"(TopBar 黄点 + "已断开,点击重连")+ 回退 2s 轮询;
   event:end → 结束态切换。重连(reconnectLive)复用本函数。 */
function connectSse(live) {
  const es = new EventSource(`/api/runs/${encodeURIComponent(wb.runId)}/stream`);
  live.es = es;
  es.onopen = () => {
    // 服务端 subscribe 原子回放环形缓冲,与 REST 快照重复:清空重折叠,幂等防重
    live.state = createLiveState();
    wb.signals = [];
    store.set({ liveConn: "ok" });
    if (live.pollId != null) {
      clearInterval(live.pollId); // SSE 恢复:停轮询回退
      live.pollId = null;
    }
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
    store.set({ liveConn: "down" }); // §5:TopBar 黄点 + "已断开,点击重连"
    toast("实时连接中断,回退 2s 轮询;可点击 TopBar live 点重连", "info");
    startPolling();
  };
}

/* TopBar live 点点击重连(§5):重建该 run 的 SSE;非 live 会话返回 false */
export function reconnectLive() {
  const live = wb.live;
  if (!live || live.ending || wb.detail?.status !== "running") return false;
  live.es?.close();
  store.set({ liveConn: "connecting" });
  connectSse(live);
  return true;
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
    // 终态快照:usage 一并刷新(帧块 tokens/cost 只在 run 结束后齐备);失败降级 null
    const [detail, signals, usage] = await Promise.all([
      getJson(`/api/runs/${encodeURIComponent(runId)}`),
      getJson(`/api/runs/${encodeURIComponent(runId)}/signals`),
      getJson(`/api/runs/${encodeURIComponent(runId)}/usage`).catch(() => null),
    ]);
    if (wb.runId !== runId || !wbRouteMatches(runId)) return;
    wb.detail = detail ?? wb.detail;
    if (Array.isArray(signals) && signals.length) wb.signals = signals;
    if (usage?.frames) {
      wb.usageByFid = new Map(
        usage.frames.filter((f) => f?.frame_id).map((f) => [f.frame_id, f]));
    }
    wb.durations = frameDurations(wb.signals);
    // 终态帧树:优先 checkpoint 重建的真实帧;产物半写窗口回退 live 帧(标终态)
    let frames = wb.detail?.frames ?? [];
    if (!frames.length && live.state.frames.length) {
      const st = wb.detail?.status === "aborted" ? "aborted" : wb.detail?.status === "failed" ? "failed" : "done";
      frames = live.state.frames.map((f) => (f.status === "running" ? { ...f, status: st } : f));
    }
    wb.roots = buildFrameTree(frames, pushOrder(wb.signals));
    // §4.4:终态为异常时刷新 RCA 缓存(刚失败的 run 立即可一键定位)
    if (wb.detail?.status === "failed" || wb.detail?.status === "aborted") {
      try {
        wb.rca = await getJson(`/api/runs/${encodeURIComponent(runId)}/rca`);
      } catch {
        /* RCA 拉取失败保持旧缓存 */
      }
    }
  } catch {
    /* 终态拉取失败:仍按现有 detail 收尾(Banner/页头显示已知状态) */
  }
  if (wb.runId !== runId) return;
  live.bar?.end(wb.detail?.status ?? "done", wb.detail?.error);
  live.bar?.destroy();
  wb.live = null; // 轨迹停止追加(onLiveSignal/pollTick 均守卫 wb.live)
  renderHeader();
  renderTreePanel();
  renderTimelinePanel();
  prefetchFrameInputs(); // 终态上下文已落盘:补 call 行参数(§4.2)
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
  if (store.get("liveConn") != null) store.set({ liveConn: null }); // TopBar 回到 API 健康指示
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

/* ── RCA 模式(§4.4):一键定位首个错误 + Resume ────────────────── */

/* 异常 run 判定(⌘J / 命令条"定位首个错误"可见性) */
export const wbRcaAvailable = () => {
  const s = wb.detail?.status;
  return wbActive() && (s === "failed" || s === "aborted");
};

export const wbRunning = () => wbActive() && wb.detail?.status === "running";

/* 一键定位(§4.4;Banner 按钮 / ⌘J / ⌘K 命令):
   planRcaJump 纯函数规划 → 帧树展开祖先选中 → 时间线展开所在组滚动到出错信号 →
   检视器(source "rca")veto 归因卡 + 出错卡片红色高亮脉冲。三步经一次
   selection 写入联动完成。 */
export async function wbJumpFirstError() {
  if (!wbActive()) return false;
  if (!wbRcaAvailable()) {
    toast("仅异常 run(failed/aborted)支持定位首个错误", "info");
    return false;
  }
  if (!wb.rca) {
    // 缓存缺失(载入时拉取失败):现场补拉一次
    try {
      wb.rca = await getJson(`/api/runs/${encodeURIComponent(wb.runId)}/rca`);
    } catch (e) {
      toast(e.message ?? "RCA 数据拉取失败", "error");
      return false;
    }
    if (!wbActive()) return false;
  }
  const plan = planRcaJump(wb.rca, wb.detail?.frames ?? [], wb.signals);
  if (!plan.frameId) {
    toast(
      plan.reason === "no-error" ? "未检测到可定位的错误信号" : "错误帧不在当前产物中(可能已被压缩/清理)",
      "info");
    return false;
  }
  // 帧树:展开祖先(updateTreeSelection 对 source "rca" 同样处理,此处提前展开
  // 保证整树一次重绘);轨迹:展开包含出错信号的全部调用子树(含深度默认折叠层)
  if (plan.signalIndex != null) revealSignalInTrace(plan.signalIndex);
  store.set({
    selection: { frameId: plan.frameId, signalIndex: plan.signalIndex, source: "rca" },
  });
  return true;
}

/* 轨迹定位前置:把包含目标信号的所有 call 子树展开(RCA/深链接滚动定位用);
   非行信号(pre:step / skill.invoke)经 rowForSignal 落最近前行再展开。 */
function revealSignalInTrace(sigIndex) {
  const rows = wb.lastTraceView?.all ?? [];
  const target = rowForSignal(rows, sigIndex);
  if (!target) return;
  for (const c of rows) {
    if (c.kind !== "call") continue;
    const end = c.retLine ?? c.line + (c.kids ?? 0) + 1; // 与 collapseTrace 同语义
    if (c.line < target.line && target.line <= end) {
      wb.traceCollapsed.delete(c.sigIndex);
      wb.traceExpanded.add(c.sigIndex);
    }
  }
}

/* Resume(§4.4 Banner / ⌘K 命令):POST /resume 后端阻塞到恢复结束,
   产物写回原 run → Toast → 重新 load(恢复期间 run 转 running,回来即终态)。 */
async function doResume() {
  const btn = wb.els?.head?.querySelector('[data-action="wb-resume"]');
  if (btn) {
    btn.disabled = true;
    btn.textContent = "恢复中…";
    btn.setAttribute("aria-busy", "true");
  }
  try {
    await postJson(`/api/runs/${encodeURIComponent(wb.runId)}/resume`);
    toast("已从 checkpoint 恢复执行", "success");
  } catch (e) {
    toast(e.message ?? "resume 失败", "error");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Resume ▶";
      btn.removeAttribute("aria-busy");
    }
    return false;
  }
  if (wbRouteMatches(wb.runId)) await load(); // 恢复产物已落盘:整页刷新(终态 Banner)
  return true;
}

/* ⌘K Stop(§5):复用 live bar 的不可逆确认条(不直接中止) */
export function wbRequestStop() {
  if (!wbRunning()) return false;
  wb.live?.bar?.requestStop();
  return true;
}

/* ⌘K Resume(§5):仅 run 详情页可用 */
export const wbResumable = () => wbActive() && !wbRunning();

export function wbDoResume() {
  if (!wbResumable()) return false;
  return doResume();
}

/* ── 键盘信号导航(§5:j/k 下/上一条,gg/G 首/尾)────────────────
   在当前可见行集(deriveTraceView 视图行去掉折叠隐藏,尊重帧过滤)中移动;
   选中经 selection store(source "timeline")走既有三联动。 */
function visibleSignalIndexes() {
  return (wb.lastTraceView?.rows ?? []).filter((r) => !r.hidden).map((r) => r.sigIndex);
}

export function wbNavSignal(delta) {
  if (!wbActive() || !wb.signals.length) return false;
  const order = visibleSignalIndexes();
  if (!order.length) return false;
  const cur = store.get("selection")?.signalIndex;
  let next;
  if (cur == null) {
    next = delta >= 0 ? order[0] : order[order.length - 1];
  } else {
    const i = order.indexOf(cur);
    const j = i < 0 ? 0 : Math.min(order.length - 1, Math.max(0, i + (delta >= 0 ? 1 : -1)));
    next = order[j];
  }
  store.set({
    selection: { frameId: wb.signals[next]?.frame_id ?? null, signalIndex: next, source: "timeline" },
  });
  return true;
}

export function wbNavEdge(which) {
  if (!wbActive() || !wb.signals.length) return false;
  const order = visibleSignalIndexes();
  if (!order.length) return false;
  const next = which === "first" ? order[0] : order[order.length - 1];
  store.set({
    selection: { frameId: wb.signals[next]?.frame_id ?? null, signalIndex: next, source: "timeline" },
  });
  return true;
}

/* ── 渲染:上下文检视器(右栏,三态:Skeleton/空提示/错误重试)─────── */

function renderInspectorPanel() {
  const el = wb.els?.inspector;
  if (!el) return;
  const sel = store.get("selection");
  const fid = sel?.frameId ?? null;
  if (!fid) {
    el.innerHTML = emptyBlock("选择一帧查看上下文", "点击左侧帧树的帧,或点击时间线信号联动定位", "select");
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
  // §4.4 RCA 来源:出错卡片定位(成对渲染的 tc-result;孤儿 tool 消息回退 focusMessageIndex)
  const isRca = sel?.source === "rca";
  const fe = isRca ? wb.rca?.first_error : null;
  const signal = sel?.signalIndex != null ? wb.signals[sel.signalIndex] : null;
  const traceRow = signal ? rowForSignal(wb.lastTraceView?.all ?? [], sel.signalIndex) : null;
  const focusIdx = signal ? focusMessageIndex(signal, traceRow?.step ?? null, msgs) : null;
  let msgsHtml = msgs.length
    ? renderMessages(msgs)
    : emptyBlock("该帧无上下文消息", "frame.context.messages 为空", "inbox");
  if (isRca && fe) {
    // 出错卡片:红色高亮 + 一次性脉冲(优先精确命中 focusIdx 对应的失败 tool 结果)
    const exact =
      focusIdx != null
        ? `class="tc-result" data-ok="false" data-msg-index="${focusIdx}"`
        : null;
    if (exact && msgsHtml.includes(exact)) {
      msgsHtml = msgsHtml.replace(
        exact,
        `class="tc-result rca-target rca-pulse" data-ok="false" data-msg-index="${focusIdx}"`);
    } else {
      msgsHtml = msgsHtml.replace(
        `class="tc-result" data-ok="false"`,
        `class="tc-result rca-target rca-pulse" data-ok="false"`);
    }
  }
  el.innerHTML =
    `<div class="insp-head">` +
    `<span class="insp-title">${esc(shortSkill(f.skill))} · f-${esc(shortId(fid))}</span>` +
    statusPill(f.status) +
    (steps != null ? `<span class="meta-chip">${esc(steps)} steps</span>` : "") +
    (cost != null ? `<span class="meta-chip mono">${esc(fmtCost(cost))}</span>` : "") +
    `<button class="btn btn-mini" data-action="wb-copy-frame" data-tip="复制该帧全部消息 JSON">复制全部 JSON</button>` +
    `</div>` +
    // §4.4 veto 归因卡(出错卡片上方):裁决来源 kind / 理由全文 / 被否决参数 JSON
    (fe?.kind === "vetoed" ? vetoCardHtml(fe) : "") +
    (f.error
      ? banner("danger", "帧错误", esc(typeof f.error === "string" ? f.error : JSON.stringify(f.error)))
      : "") +
    msgsHtml;
  // RCA:滚动到出错卡片(红色高亮目标);其余:时间线选中 → 滚动到对应消息卡片(§4.2 规则 1)
  if (isRca) {
    const target = el.querySelector(".rca-target");
    if (target) {
      target.scrollIntoView({ block: "center" });
      return;
    }
  }
  if (focusIdx != null) {
    el.querySelector(`[data-msg-index="${focusIdx}"]`)?.scrollIntoView({ block: "start" });
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
    if (act === "wb-rca-jump") return wbJumpFirstError(), true; // §4.4 定位首个错误
    if (act === "wb-resume") return doResume(), true; // §4.4 Resume
    if (act === "us-sort") return wb.usage?.sortBy(action.dataset.key), true; // §4.5 列排序
    if (act === "us-retry") return wb.usage?.reload(), true;
    if (act === "ft-toggle") {
      const fid = action.dataset.frameId;
      if (wb.collapsedFrames.has(fid)) wb.collapsedFrames.delete(fid);
      else wb.collapsedFrames.add(fid);
      return renderTreePanel(), true;
    }
    if (act === "tr-toggle") {
      // call 行折叠/展开(§4.2):按当前有效态取反,显式集覆盖深度默认折叠
      const sig = Number(action.dataset.sig);
      const row = (wb.lastTraceView?.rows ?? []).find((r) => r.sigIndex === sig);
      if (!row || row.kind !== "call") return true;
      if (isCallCollapsed(row, wb.traceCollapsed, wb.traceExpanded)) {
        wb.traceCollapsed.delete(sig);
        wb.traceExpanded.add(sig);
      } else {
        wb.traceExpanded.delete(sig);
        wb.traceCollapsed.add(sig);
      }
      return renderTimelinePanel({ skipScroll: true }), true;
    }
    if (act === "tr-payload") {
      // 行 payload 展开/收起(§4.2:hover 出现的 {} 按钮)
      const sig = Number(action.dataset.sig);
      if (wb.tracePayload.has(sig)) wb.tracePayload.delete(sig);
      else wb.tracePayload.add(sig);
      return renderTimelinePanel({ skipScroll: true }), true;
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
  const chev = t.closest?.(".tr-chev");
  if (chev) return workbenchClick(e, chev); // chevron 聚焦时 Enter = 折叠/展开
  const tl = t.closest?.(".tl-row");
  if (tl) return selectSignal(tl.dataset), true;
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

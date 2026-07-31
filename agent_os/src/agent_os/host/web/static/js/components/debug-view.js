/* 调试台页面(Agent OS Debugger P4,#/debug/<session_id>;计划 §4 ASCII 布局):
   顶部控制条(会话状态 + ▶ Continue / ⇥ Into / ⇢ Over / ↥ Out / ■ Stop,非 paused 禁用)、
   左栏调用栈(frame_stack,暂停帧高亮)+ 左下断点列表(breakpoint-list.js)、
   中栏执行轨迹(复用 trace.js 行语言,行首 gutter 点击切换断点,暂停行高亮 + ▶)、
   右栏检视器(暂停点 payload + 选中帧 messages 复用 message-card + Modify/Inject 表单)。

   数据源(P3,host/web/app.py /api/debug/*):
     快照 GET /api/debug/sessions/{sid}         {state, pause_point, breakpoints, frame_stack, run_id}
     轨迹 GET /api/runs/{run_id}/signals        (调试 SSE 不带 run 信号,轨迹走 run 信号流)
     帧   GET /api/debug/sessions/{sid}/frames/{fid}  (live 内存态;暂停时 checkpoint 未落盘)
     SSE  GET /api/debug/sessions/{sid}/stream  state → bp_hit/paused/resumed → run_end

   SSE 消费(照 workbench.js §4.3 模式):EventSource 为主;
   不可用/断线 → 回退 2s 轮询快照(sseDown);run 信号流 SSE 本就不覆盖,
   故 2s ticker 常驻刷轨迹(signals 数变化才重绘,不打扰滚动)。
   paused 时轨迹滚动到暂停行(data-paused + ▶,底色 token --debug-paused-bg)。

   事件经 app.js 事件委托转发(debugClick/debugChange;命中即返回 true)。
   本模块持有页面私有状态(dbg),组件纯函数在 components/*(trace/message-card/breakpoint-list)。 */

import { store } from "../store.js";
import { ApiError, getJson, postJson } from "../api.js";
import { COPY_SVG, copyText, emptyBlock, esc, shortId, shortSkill, toast } from "../util.js";
import { statusPill } from "./status-pill.js";
import { mascotHtml, mascotStateFor } from "./mascot.js";
import { copy } from "../themes.js";
import { renderMessages } from "./message-card.js";
import {
  BREAKPOINT_KINDS,
  matchEditable,
  renderBpForm,
  renderBreakpoints,
} from "./breakpoint-list.js";
import { bodyHtml, buildTraceRows, fmtDur, indentHtml, rowForSignal } from "./trace.js";

/* 轨迹/回退轮询周期(照 workbench LIVE_POLL_MS;SSE 不可用时快照同周期轮询) */
const POLL_MS = 2000;

/* ── 页面私有状态(每次切换会话整体重置)────────────────────────── */

const dbg = {
  main: null,
  sessionId: null,
  status: "idle", // idle | loading | ready | error
  error: null,
  doc: null, // 会话快照(GET /api/debug/sessions/{sid} / SSE state 事件同形)
  signals: [], // run 信号流(轨迹数据源)
  sigCount: 0, // 上次渲染轨迹时的信号数(变化才重绘,不打扰滚动)
  frames: new Map(), // 帧检视缓存:fid -> { status, data?, error? }
  selectedFrameId: null, // 检视器选中帧(暂停时跟随暂停帧)
  es: null, // EventSource(调试会话流)
  sseDown: false, // SSE 不可用/断线 → 快照走轮询(照 workbench 回退模式)
  pollId: null, // 2s ticker(轨迹常驻;快照仅 sseDown)
  ended: false, // run_end 已到(或轮询发现 detached)
  endStatus: null, // run 终态 done/failed/aborted(run_end 事件带)
  ppKey: "", // 当前暂停点指纹(变化才滚动/预填 modify)
  scrolledKey: "", // 已滚动定位过的暂停点指纹(同一次暂停不重复滚动)
  modKey: null, // modify 预填对应的暂停点指纹(同一点保留用户编辑,新点重填)
  els: null, // { bar, stack, bps, trace, insp } 面板引用
};

const dbgRoute = () =>
  store.get("route").name === "debug-session" &&
  store.get("route").sessionId === dbg.sessionId;

const dbgActive = () => dbgRoute() && dbg.status === "ready";

/* 暂停点指纹:signal+帧+step+工具+原因;同一暂停点重复渲染不重复滚动/预填 */
const ppKeyOf = (doc) => {
  const pp = doc?.state === "paused" ? doc?.pause_point : null;
  return pp
    ? JSON.stringify([pp.signal, pp.frame_id, pp.step, pp.tool, pp.reason])
    : "";
};

/* ── 入口(app.js 路由分发;幂等)───────────────────────────────── */

export function openDebugView(main, sessionId) {
  if (dbg.main === main && dbg.sessionId === sessionId) return; // 同会话重入:不动
  closeDebugView();
  dbg.main = main;
  dbg.sessionId = sessionId;
  dbg.status = "loading";
  renderShell();
  load();
}

/* 离开调试台(app.js 路由分发时调用,幂等):SSE/定时器/页面态整体收尾 */
export function closeDebugView() {
  dbg.es?.close();
  if (dbg.pollId != null) clearInterval(dbg.pollId);
  dbg.main = null;
  dbg.sessionId = null;
  dbg.status = "idle";
  dbg.error = null;
  dbg.doc = null;
  dbg.signals = [];
  dbg.sigCount = 0;
  dbg.frames = new Map();
  dbg.selectedFrameId = null;
  dbg.es = null;
  dbg.sseDown = false;
  dbg.pollId = null;
  dbg.ended = false;
  dbg.endStatus = null;
  dbg.ppKey = "";
  dbg.scrolledKey = "";
  dbg.modKey = null;
  dbg.els = null;
}

/* ── 取数 ───────────────────────────────────────────────────── */

async function refreshSnapshot() {
  dbg.doc = await getJson(`/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}`);
}

async function refreshSignals() {
  const runId = dbg.doc?.run_id;
  if (!runId) return;
  const signals = await getJson(`/api/runs/${encodeURIComponent(runId)}/signals`);
  dbg.signals = Array.isArray(signals) ? signals : [];
}

async function load() {
  const sid = dbg.sessionId;
  try {
    await refreshSnapshot();
    await refreshSignals();
    if (dbg.sessionId !== sid || !dbgRoute()) return; // 加载期间路由已切走
    dbg.status = "ready";
    dbg.ppKey = ppKeyOf(dbg.doc);
    // 默认选中:暂停帧 > 栈顶帧(检视器不再是空屏)
    dbg.selectedFrameId =
      dbg.doc?.pause_point?.frame_id ?? dbg.doc?.frame_stack?.at(-1)?.frame_id ?? null;
    renderShell();
    if (dbg.selectedFrameId) loadFrame(dbg.selectedFrameId);
    connectSse();
    startTicker();
    scrollToPaused(); // 启动即断(带启动断点开会话)载入即定位暂停行
  } catch (e) {
    if (dbg.sessionId !== sid) return;
    dbg.status = "error";
    dbg.error = e;
    renderShell();
  }
}

/* 帧检视(live 内存态):选中懒加载;暂停/恢复转换时缓存整体失效(消息已变) */
async function loadFrame(fid) {
  if (!fid || dbg.frames.get(fid)?.status === "loading") return;
  dbg.frames.set(fid, { status: "loading" });
  if (dbg.selectedFrameId === fid) renderInsp();
  try {
    const data = await getJson(
      `/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/frames/${encodeURIComponent(fid)}`);
    dbg.frames.set(fid, { status: "ready", data });
  } catch (e) {
    dbg.frames.set(fid, { status: "error", error: e });
  }
  if (dbg.selectedFrameId === fid && dbgActive()) renderInsp();
}

/* ── SSE(照 workbench connectSse 模式)──────────────────────────
   state(连接快照)→ bp_hit/paused/resumed → run_end;
   open → 连接点转 ok;error → 转 down + 回退轮询快照。 */

function connectSse() {
  if (typeof EventSource !== "function") {
    dbg.sseDown = true; // 浏览器无 EventSource:全程轮询(快照+轨迹)
    return;
  }
  const es = new EventSource(`/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/stream`);
  dbg.es = es;
  es.onopen = () => {
    dbg.sseDown = false; // SSE 恢复:快照回到事件驱动
    renderConn();
  };
  es.addEventListener("state", (ev) => {
    try {
      dbg.doc = JSON.parse(ev.data);
    } catch {
      return; // 畸形行跳过
    }
    if (!dbgActive()) return;
    renderAll();
  });
  es.addEventListener("bp_hit", (ev) => {
    // hits 差分事件:原地更新计数,断点列表与 gutter 即时刷新
    let d = null;
    try {
      d = JSON.parse(ev.data);
    } catch {
      return;
    }
    const bp = dbg.doc?.breakpoints?.find((b) => b.id === d.breakpoint_id);
    if (bp) bp.hits = d.hits;
    if (!dbgActive()) return;
    renderBps();
    renderTracePanel({ force: true });
  });
  es.addEventListener("paused", async () => {
    // paused 事件只带 pause_point;拉全量快照(frame_stack/breakpoints 同步)+ 轨迹
    try {
      await refreshSnapshot();
      await refreshSignals();
    } catch {
      return;
    }
    if (!dbgActive()) return;
    onPaused();
  });
  es.addEventListener("resumed", () => {
    if (dbg.doc) {
      dbg.doc.state = "running";
      dbg.doc.pause_point = null;
    }
    dbg.frames = new Map(); // 恢复后帧上下文已变:缓存失效
    if (!dbgActive()) return;
    renderAll();
  });
  es.addEventListener("run_end", (ev) => {
    let d = null;
    try {
      d = JSON.parse(ev.data);
    } catch {
      /* status 缺失按 null 收尾 */
    }
    finishRun(d?.status ?? null);
  });
  es.onerror = () => {
    if (dbg.ended || !dbg.es) return;
    es.close();
    dbg.es = null;
    dbg.sseDown = true; // 回退 2s 轮询快照(照 workbench onerror 模式)
    toast("调试实时连接中断,回退轮询", "info");
    renderConn();
  };
}

/* 暂停到位:选中暂停帧、帧缓存失效、整页重绘并滚动到暂停行 */
function onPaused() {
  dbg.ppKey = ppKeyOf(dbg.doc);
  dbg.frames = new Map();
  const fid = dbg.doc?.pause_point?.frame_id ?? null;
  if (fid) {
    dbg.selectedFrameId = fid;
    loadFrame(fid);
  }
  renderAll();
  scrollToPaused();
}

/* ── 轮询(轨迹常驻;SSE 不可用时快照同周期)────────────────────── */

function startTicker() {
  if (dbg.pollId != null) clearInterval(dbg.pollId);
  dbg.pollId = setInterval(pollTick, POLL_MS);
}

async function pollTick() {
  if (!dbgActive() || dbg.ended) return;
  try {
    await refreshSignals();
    if (dbg.sseDown) {
      // 回退模式:快照走轮询;paused/detached 转换在此发现
      const prevKey = dbg.ppKey;
      await refreshSnapshot();
      if (!dbgActive() || dbg.ended) return;
      if (dbg.doc?.state === "detached") {
        await finishRun(null);
        return;
      }
      dbg.ppKey = ppKeyOf(dbg.doc);
      if (dbg.ppKey && dbg.ppKey !== prevKey) onPaused(); // 新暂停点
      else renderAll();
      return;
    }
    renderTracePanel(); // SSE 模式:ticker 只补轨迹(内部按信号数变化才重绘)
  } catch {
    /* 单次轮询失败静默:下个周期重试(连接异常由 SSE onerror / app 健康轮询承担) */
  }
}

/* 结束态:run_end(SSE)或轮询发现 detached;按钮禁用、停 ticker、终态横幅 */
async function finishRun(status) {
  if (dbg.ended) return;
  dbg.ended = true;
  dbg.endStatus = status ?? dbg.endStatus;
  dbg.es?.close();
  dbg.es = null;
  if (dbg.pollId != null) clearInterval(dbg.pollId);
  dbg.pollId = null;
  try {
    await refreshSnapshot(); // 终态快照(state=detached,breakpoints 带最终 hits)
    await refreshSignals(); // 轨迹补全到 run.finished/aborted
  } catch {
    /* 终态拉取失败:按现有数据收尾 */
  }
  if (!dbgActive()) return;
  renderAll();
  const label = dbg.endStatus ?? "detached";
  toast(`调试会话结束(run ${label})`, label === "failed" ? "error" : "info");
}

/* ── 渲染:页面骨架(loading / error / ready 三态,§5)────────────── */

function renderShell() {
  const main = dbg.main;
  if (!main) return;
  if (dbg.status === "loading" || dbg.status === "idle") {
    main.innerHTML =
      `<div class="dbg" role="status" aria-label="${esc(copy("session.waiting"))}">` +
      `<div class="card skeleton-pad">` +
      `<span class="skeleton skeleton-line w-40"></span>` +
      `<span class="skeleton skeleton-line w-70"></span></div></div>`;
    return;
  }
  if (dbg.status === "error") {
    const notFound = dbg.error instanceof ApiError && dbg.error.status === 404;
    main.innerHTML =
      `<div class="dbg"><div class="card panel-error">` +
      `<span class="error-msg">${notFound ? "调试会话不存在(可能已结束清理)" : "加载调试会话失败"}</span>` +
      `<span class="mono">${esc(dbg.sessionId)}</span>` +
      (notFound
        ? `<a class="btn" href="#/debug">返回调试首页</a>`
        : `<button class="btn" data-action="dbg-retry">重试</button>`) +
      `</div></div>`;
    return;
  }
  main.innerHTML =
    `<div class="dbg">` +
    `<div id="dbgBar"></div>` +
    `<div class="dbg-cols">` +
    `<div class="dbg-left">` +
    `<section class="dbg-panel dbg-stack" id="dbgStack" aria-label="调用栈"></section>` +
    `<section class="dbg-panel dbg-bps" id="dbgBps" aria-label="断点列表"></section>` +
    `</div>` +
    `<section class="dbg-panel dbg-trace" id="dbgTrace" aria-label="执行轨迹"></section>` +
    `<section class="dbg-panel dbg-insp" id="dbgInsp" aria-label="检视器"></section>` +
    `</div></div>`;
  dbg.els = {
    bar: main.querySelector("#dbgBar"),
    stack: main.querySelector("#dbgStack"),
    bps: main.querySelector("#dbgBps"),
    trace: main.querySelector("#dbgTrace"),
    insp: main.querySelector("#dbgInsp"),
  };
  renderAll();
}

function renderAll() {
  renderBar();
  renderStack();
  renderBps();
  renderTracePanel({ force: true });
  renderInsp();
}

/* ── 渲染:控制条(计划 §4 顶部:状态 + 五个命令按钮)──────────────── */

/* 会话状态徽标:paused/running 复用 StatusPill;detached 带 run 终态文案 */
function sessionPillHtml() {
  const st = dbg.doc?.state;
  if (st === "paused" || st === "running") return statusPill(st);
  const end = dbg.endStatus;
  const label = st === "detached" ? (end ? `已结束 · ${end}` : "已结束") : (st ?? "…");
  const tone = ["done", "failed", "aborted"].includes(end) ? end : "unknown";
  return (
    `<span class="status-pill" data-status="${tone}">` +
    `<span class="pill-dot" aria-hidden="true"></span>` +
    `<span class="pill-label">${esc(label)}</span></span>`
  );
}

/* 暂停点摘要行:"paused @ pre:tool.call · f-abc123 · step 2 · breakpoint" */
function pauseLabelHtml() {
  const pp = dbg.doc?.state === "paused" ? dbg.doc?.pause_point : null;
  if (!pp) return "";
  const bits = [
    `paused @ ${pp.signal ?? "?"}`,
    pp.frame_id ? `f-${shortId(pp.frame_id)}` : null,
    pp.step != null ? `step ${pp.step}` : null,
    pp.tool ?? pp.skill ?? null,
    pp.reason ?? null,
  ].filter(Boolean);
  return `<span class="dbg-pp-label mono" title="暂停点">${esc(bits.join(" · "))}</span>`;
}

const COMMANDS = [
  ["continue", "▶ Continue", "放行,命中下一断点再停"],
  ["step_into", "⇥ Into", "单步:任意帧的下一条 pre:step 即停"],
  ["step_over", "⇢ Over", "步过:同帧下一条 pre:step,跨过子帧不停"],
  ["step_out", "↥ Out", "步出:当前帧 pre:frame.pop 时停"],
  ["stop", "■ Stop", "中止 run(走正常中止路径,checkpoint 落盘)"],
];

function renderBar() {
  const el = dbg.els?.bar;
  if (!el) return;
  const paused = dbg.doc?.state === "paused";
  const sid = dbg.sessionId ?? "";
  const meta = (store.get("runs") ?? []).find((r) => r.run_id === dbg.doc?.run_id) ?? {};
  const connState = dbg.ended ? "off" : dbg.sseDown ? "down" : dbg.es ? "ok" : "poll";
  const connTitle = {
    ok: "实时连接正常(SSE)",
    down: "实时连接中断,已回退轮询",
    poll: "无 EventSource,轮询模式",
    off: "会话已结束",
  }[connState];
  el.innerHTML =
    `<div class="card dbg-bar">` +
    `<span class="dbg-brand"><span class="brand-mark" aria-hidden="true">▎</span>Agent OS Debug</span>` +
    `<span class="dbg-sid mono" title="session_id: ${esc(sid)} · run_id: ${esc(dbg.doc?.run_id ?? "")}">` +
    `#${esc(shortId(sid))}</span>` +
    (meta.skill
      ? `<span class="meta-chip" title="skill">${esc(shortSkill(meta.skill))}</span>`
      : "") +
    sessionPillHtml() +
    pauseLabelHtml() +
    `<span class="dbg-conn" data-state="${connState}" title="${connTitle}"` +
    ` aria-label="调试连接状态:${connTitle}"></span>` +
    `<div class="dbg-bar-btns" role="group" aria-label="调试命令">` +
    COMMANDS.map(
      ([cmd, label, tip]) =>
        `<button class="btn dbg-cmd" data-action="dbg-cmd" data-cmd="${cmd}"` +
        ` data-tip="${esc(tip)}" title="${esc(tip)}"${paused ? "" : " disabled"}>${label}</button>`).join("") +
    `</div>` +
    `<a class="btn btn-mini dbg-home-link" href="#/debug" title="返回调试首页">会话列表</a>` +
    (dbg.doc?.rerunnable
      ? `<button class="btn btn-mini dbg-rerun" data-action="dbg-rerun"` +
        ` data-tip="以同一 skill/input/启动断点重开新会话(当前会话停止并结束)"` +
        ` title="以同一 skill/input/启动断点重开新会话(当前会话停止并结束)">⟳ 重新运行</button>`
      : "") +
    // MascotLayer(MOE §2):主题声明 mascot 时挂在控制条右侧;层自身判主题,classic 下为空
    `<span class="dbg-mascot-slot">${mascotHtml(mascotStateFor(dbg.doc, dbg.endStatus))}</span>` +
    `</div>` +
    (dbg.ended
      ? `<div class="banner dbg-end" data-tone="${
          dbg.endStatus === "failed" ? "danger" : dbg.endStatus === "aborted" ? "aborted" : "done"
        }" role="status">` +
        `<span class="banner-icon" aria-hidden="true">●</span>` +
        `<div class="banner-main"><span class="banner-title">${esc(copy("debug.end.title"))}${
          dbg.endStatus ? `(${esc(dbg.endStatus)})` : ""
        }</span>` +
        `<span class="banner-body">会话已 detached;完整产物见 ` +
        `<a href="#/runs/${encodeURIComponent(dbg.doc?.run_id ?? "")}">run 详情</a></span></div>` +
        `</div>`
      : "");
}

/* SSE 连接态点(控制条内;不碰 TopBar live 指示——那是 workbench 的) */
function renderConn() {
  renderBar();
}

/* ── 渲染:调用栈(左栏,frame_stack,暂停帧高亮)───────────────────── */

function renderStack() {
  const el = dbg.els?.stack;
  if (!el) return;
  const stack = dbg.doc?.frame_stack ?? [];
  const pausedFid = dbg.doc?.state === "paused" ? dbg.doc?.pause_point?.frame_id : null;
  const rows = [...stack].reverse().map((f) => {
    const fid = f?.frame_id ?? "";
    const current = fid && fid === pausedFid;
    const selected = fid && fid === dbg.selectedFrameId;
    return (
      `<div class="dbg-frame" data-frame-id="${esc(fid)}" role="option" tabindex="0"` +
      ` aria-selected="${Boolean(selected)}"${current ? ` data-current="true"` : ""}` +
      ` title="${esc(f?.skill ?? "")} · ${esc(fid)}">` +
      `<span class="dbg-frame-mark" aria-hidden="true">${current ? "▶" : ""}</span>` +
      indentHtml(Math.max(0, (Number(f?.depth) || 1) - 1)) +
      `<span class="dbg-frame-skill">${esc(shortSkill(f?.skill))}</span>` +
      `<span class="dbg-frame-id mono">f-${esc(shortId(fid))}</span>` +
      `</div>`
    );
  });
  el.innerHTML =
    `<div class="dbg-sec-title">调用栈</div>` +
    (rows.join("") ||
      emptyBlock("帧栈为空", "run 尚未压栈(装配中或已结束)", "layers"));
}

/* ── 渲染:断点列表(左下,breakpoint-list.js)─────────────────────── */

function renderBps() {
  const el = dbg.els?.bps;
  if (!el) return;
  el.innerHTML =
    `<div class="dbg-sec-title">断点</div>` +
    `<div class="dbg-bp-list">${renderBreakpoints(dbg.doc?.breakpoints)}</div>` +
    renderBpForm();
}

/* ── 渲染:执行轨迹(中栏,复用 trace.js 行语言 + 断点 gutter)────── */

/* 行 → 可切换的断点目标(tool 行 → tool_call 断点;call 行 → skill_invoke 断点;
   其余行无断点语义,gutter 占位)。match 取 payload 原始名(与内核 fnmatch 同串)。 */
export function bpTargetForRow(row) {
  if (row?.kind === "tool") {
    const tool = row.payload?.pre?.tool ?? row.label;
    return tool ? { kind: "tool_call", match: String(tool) } : null;
  }
  if (row?.kind === "call") {
    const skill = row.payload?.skill ?? null;
    return skill ? { kind: "skill_invoke", match: String(skill) } : null;
  }
  return null;
}

/* 暂停点 → 信号下标:从尾向前找最近一条同名同帧信号(pre:step 再对 step 号,
   pre:tool.call 再对工具名);暂停总是发生在最近一次匹配的发射上。 */
export function pausedSignalIndex(signals, pausePoint) {
  const pp = pausePoint ?? null;
  if (!pp?.signal) return null;
  const sigs = Array.isArray(signals) ? signals : [];
  for (let i = sigs.length - 1; i >= 0; i -= 1) {
    const s = sigs[i];
    if (!s || s.name !== pp.signal) continue;
    if ((s.frame_id ?? null) !== (pp.frame_id ?? null)) continue;
    if (pp.signal === "pre:step" && pp.step != null && s.payload?.step !== pp.step) continue;
    if (pp.tool != null && (s.payload?.tool ?? null) !== pp.tool) continue;
    return i;
  }
  return null;
}

/* 轨迹 HTML:断点 gutter(点击切换)+ 行号 + ▶(暂停行)+ 彩虹轨 + 行主体 + 时长。
   行语言(bodyHtml/indentHtml/fmtDur)整体复用 trace.js,保证与 Workbench 轨迹同构。 */
export function renderDebugTrace(rows, { breakpoints = [], pausedLine = null } = {}) {
  const visible = (rows ?? []).filter((r) => r && !r.hidden);
  if (!visible.length) return "";
  const bps = Array.isArray(breakpoints) ? breakpoints : [];
  const body = visible
    .map((r) => {
      const target = bpTargetForRow(r);
      const bp = target
        ? bps.find((b) => b.kind === target.kind && b.match === target.match && b.enabled)
        : null;
      const paused = r.line === pausedLine;
      const gutter = target
        ? `<button class="dbg-gutter" data-action="dbg-gutter" data-kind="${esc(target.kind)}"` +
          ` data-match="${esc(target.match)}"${bp ? ` data-bp-id="${esc(bp.id)}"` : ""}` +
          ` data-on="${Boolean(bp)}" aria-pressed="${Boolean(bp)}"` +
          ` title="${bp ? `删除断点 ${esc(target.kind)} ${esc(target.match)}` : `在此打断点(${esc(target.kind)} ${esc(target.match)})`}"` +
          ` aria-label="切换断点 ${esc(target.kind)} ${esc(target.match)}">${bp ? "●" : ""}</button>`
        : `<span class="dbg-gutter dbg-gutter-sp" aria-hidden="true"></span>`;
      return (
        `<div class="tl-row dbg-row" data-signal-index="${r.sigIndex}"` +
        ` data-kind="${esc(r.kind)}" data-line="${r.line}"` +
        ` data-frame-id="${esc(r.frameId ?? "")}"${paused ? ` data-paused="true"` : ""}` +
        ` title="${esc(r.names ?? "")}">` +
        gutter +
        `<span class="tr-gutter" aria-hidden="true">${String(r.line).padStart(4, "0")}</span>` +
        `<span class="tr-mark" aria-hidden="true">${paused ? "▶" : ""}</span>` +
        indentHtml(r.depth) +
        `<span class="tr-body">${bodyHtml(r)}</span>` +
        `<span class="tr-dur">${esc(fmtDur(r.durMs))}</span>` +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="tl-list tr-list dbg-trace-list" role="listbox" aria-label="执行轨迹">` +
    body +
    `</div>`
  );
}

/* 帧栈 → trace 帧摘要(call/ret 行着色;弹栈帧不在栈上,由 ret 行自行收尾) */
function traceFrames() {
  const st = dbg.endStatus ?? "running";
  return (dbg.doc?.frame_stack ?? []).map((f) => ({
    frame_id: f?.frame_id,
    skill: f?.skill,
    depth: f?.depth,
    status: dbg.ended ? st : "running",
  }));
}

function renderTracePanel({ force = false } = {}) {
  const el = dbg.els?.trace;
  if (!el) return;
  // ticker 的周期渲染按信号数变化判断(不打扰滚动);事件驱动渲染(renderAll/
  // bp_hit/断点增删)一律 force——gutter 态与暂停行变化与信号数无关
  if (!force && dbg.sigCount === dbg.signals.length && el.innerHTML) return;
  dbg.sigCount = dbg.signals.length;
  const rows = buildTraceRows(dbg.signals, traceFrames());
  const ppIdx =
    dbg.doc?.state === "paused"
      ? pausedSignalIndex(dbg.signals, dbg.doc?.pause_point)
      : null;
  const pausedRow = ppIdx != null ? rowForSignal(rows, ppIdx) : null;
  el.innerHTML = dbg.signals.length
    ? renderDebugTrace(rows, {
        breakpoints: dbg.doc?.breakpoints ?? [],
        pausedLine: pausedRow?.line ?? null,
      })
    : emptyBlock("无信号数据", "run 尚未产生信号", "activity");
}

/* 暂停行滚动定位:同一暂停点只滚一次(用户之后翻看不打断) */
function scrollToPaused() {
  const el = dbg.els?.trace;
  if (!el || !dbg.ppKey || dbg.scrolledKey === dbg.ppKey) return;
  dbg.scrolledKey = dbg.ppKey;
  el.querySelector('[data-paused="true"]')?.scrollIntoView({ block: "center" });
}

/* ── 渲染:检视器(右栏:暂停点 payload + 帧 messages + 干预表单)──── */

const pretty = (v) => {
  try {
    return JSON.stringify(v ?? null, null, 2);
  } catch {
    return String(v);
  }
};

function pausePointHtml() {
  const pp = dbg.doc?.state === "paused" ? dbg.doc?.pause_point : null;
  if (!pp) return "";
  return (
    `<div class="dbg-pp">` +
    `<div class="dbg-sec-title">暂停点` +
    `<button class="copy-btn" data-action="dbg-copy-pause" data-tip="复制暂停点 JSON"` +
    ` aria-label="复制暂停点 JSON">${COPY_SVG}</button></div>` +
    `<div class="dbg-pp-meta">` +
    `<span class="meta-chip mono">${esc(pp.signal ?? "?")}</span>` +
    (pp.reason ? `<span class="meta-chip">${esc(pp.reason)}</span>` : "") +
    (pp.frame_id ? `<span class="meta-chip mono">f-${esc(shortId(pp.frame_id))}</span>` : "") +
    (pp.step != null ? `<span class="meta-chip">step ${esc(pp.step)}</span>` : "") +
    `</div>` +
    `<pre class="dbg-pp-payload msg-pre">${esc(pretty(pp.payload))}</pre>` +
    `</div>`
  );
}

function renderInsp() {
  const el = dbg.els?.insp;
  if (!el) return;
  // 表单值随 HTML 往返(textarea 内容为 value):重绘前读出,重绘时写回,
  // SSE/ticker 重绘不打断用户输入;modify 预填仅在新暂停点(ppKey 变化)时覆盖
  const prevMod = el.querySelector(".dbg-mod-patch")?.value;
  const prevInj = el.querySelector(".dbg-inj-text")?.value;

  const fid = dbg.selectedFrameId;
  const cached = fid ? dbg.frames.get(fid) : null;
  let frameHtml;
  if (!fid) {
    frameHtml = emptyBlock("选择一帧查看上下文", "点击左侧调用栈的帧", "select");
  } else if (!cached) {
    frameHtml = "";
    loadFrame(fid); // 懒加载
  } else if (cached.status === "loading") {
    frameHtml =
      `<div class="skeleton-stack skeleton-pad">` +
      `<span class="skeleton skeleton-line w-40"></span>` +
      `<span class="skeleton skeleton-line w-70"></span></div>`;
  } else if (cached.status === "error") {
    frameHtml =
      `<div class="panel-error">` +
      `<span class="error-msg">加载帧上下文失败</span>` +
      `<span class="mono">f-${esc(shortId(fid))}</span>` +
      `<button class="btn" data-action="dbg-retry-frame" data-frame-id="${esc(fid)}">重试</button>` +
      `</div>`;
  } else {
    const f = cached.data ?? {};
    const msgs = Array.isArray(f.messages) ? f.messages : [];
    frameHtml =
      `<div class="insp-head">` +
      `<span class="insp-title">${esc(shortSkill(f.skill))} · f-${esc(shortId(fid))}</span>` +
      `<button class="btn btn-mini" data-action="dbg-copy-frame" data-tip="复制该帧全部消息 JSON">复制全部 JSON</button>` +
      `</div>` +
      (msgs.length
        ? renderMessages(msgs)
        : emptyBlock("该帧无上下文消息", "frame.context.messages 为空", "inbox"));
  }

  // 干预表单(仅 paused 可用;modify 另要求暂停在 pre:tool.call,P3 否则 409)
  const paused = dbg.doc?.state === "paused";
  const pp = paused ? dbg.doc?.pause_point : null;
  const canModify = paused && pp?.signal === "pre:tool.call";
  // modify 预填当前 args;同一暂停点(modKey 未变)保留用户编辑,新暂停点才重填
  const modValue = canModify
    ? (dbg.modKey === dbg.ppKey && prevMod != null ? prevMod : pretty(pp?.payload?.args ?? {}))
    : "";
  dbg.modKey = canModify ? dbg.ppKey : null;
  const injValue = prevInj ?? "";
  const modifyHtml =
    `<div class="dbg-sec-title">Modify args` +
    `<span class="dbg-sec-hint">仅暂停在 pre:tool.call 可用;提交即放行</span></div>` +
    `<textarea class="input mono dbg-mod-patch" rows="6" spellcheck="false"` +
    ` aria-label="工具参数 patch(JSON)"${canModify ? "" : " disabled"}>${esc(modValue)}</textarea>` +
    `<button class="btn" data-action="dbg-modify"${canModify ? "" : " disabled"}>Modify 并放行</button>`;
  const injectHtml =
    `<div class="dbg-sec-title">Inject message` +
    `<span class="dbg-sec-hint">注入暂停帧(source=injected);提交即放行</span></div>` +
    `<textarea class="input dbg-inj-text" rows="3"` +
    ` aria-label="注入消息文本"${paused ? "" : " disabled"}>${esc(injValue)}</textarea>` +
    `<button class="btn" data-action="dbg-inject"${paused ? "" : " disabled"}>Inject 并放行</button>`;

  el.innerHTML =
    pausePointHtml() +
    `<div class="dbg-sec-title">帧上下文</div>` +
    frameHtml +
    `<div class="dbg-intervene">${modifyHtml}${injectHtml}</div>`;
}

/* ── 选择动作 ─────────────────────────────────────────────────── */

function selectFrame(fid) {
  if (!fid) return;
  dbg.selectedFrameId = fid;
  renderStack();
  renderInsp();
}

/* ── 操作(全部经 P3 REST;状态回流交给 SSE/轮询)────────────────── */

async function postCommand(cmd) {
  try {
    await postJson(`/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/command`, { cmd });
  } catch (e) {
    toast(e.message ?? `命令 ${cmd} 失败`, "error");
  }
}

/* 重新运行(⟳):后端停掉并结束当前会话,以同一 skill/input/启动断点重开,跳新会话 */
async function rerunSession() {
  try {
    const res = await postJson(
      `/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/rerun`);
    if (res?.session_id) {
      location.hash = `#/debug/${encodeURIComponent(res.session_id)}`;
      return;
    }
    toast(res?.error ?? "重新运行失败(会话未开始)", "error"); // 200 + failed(技能已卸载等)
  } catch (e) {
    toast(e.message ?? "重新运行失败", "error");
  }
}

async function addBreakpoint(kind, match) {
  try {
    await postJson(`/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/breakpoints`, {
      kind,
      match: match || "*",
    });
    await refreshSnapshot();
  } catch (e) {
    toast(e.message ?? "添加断点失败", "error");
  }
  if (dbgActive()) {
    renderBps();
    renderTracePanel({ force: true });
  }
}

async function removeBreakpoint(bpId) {
  try {
    const res = await fetch(
      `/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/breakpoints/${encodeURIComponent(bpId)}`,
      { method: "DELETE", headers: { Accept: "application/json" } });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError(data?.detail ?? `HTTP ${res.status}`, res.status);
    }
    await refreshSnapshot();
  } catch (e) {
    toast(e.message ?? "删除断点失败", "error");
  }
  if (dbgActive()) {
    renderBps();
    renderTracePanel({ force: true });
  }
}

/* gutter 切换:已有同 kind+match 断点 → 删除;否则新增(计划 §4:行首 gutter 点击切换) */
function toggleGutter(dataset) {
  const { kind, match, bpId } = dataset ?? {};
  if (!kind || !match) return;
  if (bpId) removeBreakpoint(bpId);
  else addBreakpoint(kind, match);
}

function addBreakpointFromForm() {
  const kindEl = dbg.els?.bps?.querySelector(".dbg-bp-add-kind");
  const matchEl = dbg.els?.bps?.querySelector(".dbg-bp-add-match");
  const kind = kindEl?.value ?? BREAKPOINT_KINDS[0];
  const match = matchEditable(kind) ? (matchEl?.value ?? "").trim() : "*";
  addBreakpoint(kind, match || "*");
}

async function doModify() {
  const text = dbg.els?.insp?.querySelector(".dbg-mod-patch")?.value ?? "";
  let patch;
  try {
    patch = JSON.parse(text);
  } catch (e) {
    toast(`patch 非法 JSON:${e.message}`, "error");
    return;
  }
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) {
    toast("patch 须为 JSON 对象(合并进本次工具调用 args)", "error");
    return;
  }
  try {
    await postJson(`/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/modify`, { patch });
    toast(copy("intervene.modified"), "success");
  } catch (e) {
    toast(e.message ?? "modify 失败", "error");
  }
}

async function doInject() {
  const text = (dbg.els?.insp?.querySelector(".dbg-inj-text")?.value ?? "").trim();
  if (!text) {
    toast("注入消息不能为空", "error");
    return;
  }
  try {
    await postJson(`/api/debug/sessions/${encodeURIComponent(dbg.sessionId)}/inject`, { text });
    toast(copy("intervene.injected"), "success");
  } catch (e) {
    toast(e.message ?? "inject 失败", "error");
  }
}

function copyPause() {
  const pp = dbg.doc?.pause_point;
  if (!pp) return;
  copyText(pretty(pp)).then((ok) =>
    toast(ok ? "已复制暂停点 JSON" : "复制失败", ok ? "success" : "error"));
}

function copyFrame() {
  const cached = dbg.selectedFrameId ? dbg.frames.get(dbg.selectedFrameId) : null;
  if (cached?.status !== "ready") return;
  copyText(pretty(cached.data?.messages ?? [])).then((ok) =>
    toast(ok ? "已复制帧上下文 JSON" : "复制失败", ok ? "success" : "error"));
}

function copyMessage(msgIndex) {
  const cached = dbg.selectedFrameId ? dbg.frames.get(dbg.selectedFrameId) : null;
  const m = cached?.status === "ready" ? cached.data?.messages?.[msgIndex] : null;
  if (!m) return;
  const text = typeof m.content === "string" && m.content ? m.content : pretty(m);
  copyText(text).then((ok) =>
    toast(ok ? "已复制消息原文" : "复制失败", ok ? "success" : "error"));
}

/* ── 事件接线(app.js 事件委托转发;命中即返回 true)───────────────── */

export function debugClick(e, action) {
  if (!dbg.main || store.get("route").name !== "debug-session") return false;
  if (action) {
    const act = action.dataset.action;
    if (act === "dbg-retry") return load(), true;
    if (act === "dbg-cmd") return postCommand(action.dataset.cmd), true;
    if (act === "dbg-rerun") return rerunSession(), true;
    if (act === "dbg-gutter") return toggleGutter(action.dataset), true;
    if (act === "dbg-bp-add") return addBreakpointFromForm(), true;
    if (act === "dbg-bp-del") return removeBreakpoint(action.dataset.bpId), true;
    if (act === "dbg-modify") return doModify(), true;
    if (act === "dbg-inject") return doInject(), true;
    if (act === "dbg-copy-pause") return copyPause(), true;
    if (act === "dbg-copy-frame") return copyFrame(), true;
    if (act === "dbg-copy-msg") return copyMessage(Number(action.dataset.msgIndex)), true;
    if (act === "dbg-retry-frame") {
      const fid = action.dataset.frameId;
      dbg.frames.delete(fid);
      return loadFrame(fid), true;
    }
    return false;
  }
  const frame = e.target.closest?.(".dbg-frame");
  if (frame) return selectFrame(frame.dataset.frameId), true;
  const row = e.target.closest?.(".dbg-row");
  if (row) {
    // 轨迹行点击 = 选中该帧检视(gutter 钮经 data-action 已拦截,走不到这里)
    if (row.dataset.frameId) selectFrame(row.dataset.frameId);
    return true;
  }
  return false;
}

/* change 委托:新增断点表单的 kind 切换 → match 输入禁用态(step/error 忽略 match) */
export function debugChange(e) {
  if (!dbg.main || store.get("route").name !== "debug-session") return false;
  const sel = e.target.closest?.(".dbg-bp-add-kind");
  if (!sel) return false;
  const matchEl = dbg.els?.bps?.querySelector(".dbg-bp-add-match");
  if (matchEl) matchEl.disabled = !matchEditable(sel.value);
  return true;
}

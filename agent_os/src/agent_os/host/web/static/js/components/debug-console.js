/* GDB 风格调试控制台(#/debug/<sid>/console;docs/TUI-DEBUG.md §5 命令方言 +
   §7 布局的 web 镜像——"命令窗是第一界面"的浏览器形态):

   布局(§7 GDB TUI 式):
     顶条 = #sid + skill chip + 状态徽标 + 暂停点摘要 + 连接态点 + 「三栏视图」回程
     左栏 = 调用栈窗(bt 行 #N skill f-id,▶ 当前帧,点击行 = frame N)
            + 断点窗(info b 表 Num/Kind/Match/Enb/Hits,行尾 ✕ 删除)
     右大窗 = 轨迹窗(复用 debug-view renderDebugTrace:gutter ● 点击切换断点 +
              ▶ 暂停行高亮滚动定位)
     底部 = 命令窗(滚动输出区 + (adb) 输入行:真实 <input>,↑↓ 历史,
            裸 Enter 重复白名单,kill 两段确认逐字学 GDB)

   数据面零新通道(debug-view.js 同款):快照 GET /api/debug/sessions/{sid} +
   signals GET /api/runs/{rid}/signals + 帧 GET …/frames/{fid} +
   调试 SSE(state/bp_hit/paused/resumed/run_end);断线 toast + 回退 2s 轮询快照,
   2s ticker 常驻刷轨迹(信号数变化才重绘)。命令方言纯函数在 debug-commands.js
   (parse/stopLine/info b/bt/help/apropos;逐字对齐 commands.py 与 theme.py dbg.*)。

   事件经 app.js 事件委托转发(consoleClick;命中即返回 true)。 */

import { store } from "../store.js";
import { ApiError, deleteJson, getJson, postJson } from "../api.js";
import { emptyBlock, esc, shortId, toast } from "../util.js";
import { statusPill } from "./status-pill.js";
import { copy } from "../themes.js";
import {
  bpTargetForRow,
  pausedSignalIndex,
  renderDebugTrace,
} from "./debug-view.js";
import { buildTraceRows, indentHtml, rowForSignal } from "./trace.js";
import {
  COMMAND_SPECS,
  MSG,
  REPEATABLE,
  aproposLines,
  btLines,
  fmt,
  frameRowLine,
  helpLines,
  infoBLines,
  outLineHtml,
  parse,
  parseBpSpec,
  parseErrorLines,
  shortJson,
  shortSkillName,
  stopLine,
} from "./debug-commands.js";

/* 轨迹/回退轮询周期(与 debug-view.js POLL_MS 同值同语义) */
const POLL_MS = 2000;

/* 命令窗输出上限(滚动backlog;超出截头,防长会话 DOM 膨胀) */
const OUT_CAP = 500;

/* 已知会话 localStorage 键(与 debug-home.js:25 同一本账,session 命令读它) */
const STORAGE_KEY = "agent-os.debug.sessions";

/* ── 页面私有状态(每次切换会话整体重置)────────────────────────── */

const dbc = {
  main: null,
  sessionId: null,
  status: "idle", // idle | loading | ready | error
  error: null,
  doc: null, // 会话快照(GET /api/debug/sessions/{sid} / SSE state 事件同形)
  signals: [], // run 信号流(轨迹数据源)
  sigCount: 0, // 上次渲染轨迹时的信号数(变化才重绘,不打扰滚动)
  es: null, // EventSource(调试会话流)
  sseDown: false, // SSE 不可用/断线 → 快照走轮询(照 debug-view 回退模式)
  noEs: false, // 浏览器无 EventSource(连接点显 poll,与断线 down 区分)
  pollId: null, // 2s ticker(轨迹常驻;快照仅 sseDown)
  ended: false, // run_end 已到(或轮询发现 detached)
  endStatus: null, // run 终态 done/failed/aborted
  ppKey: "", // 当前暂停点指纹(变化才滚动)
  scrolledKey: "", // 已滚动定位过的暂停点指纹
  ppReported: "", // 已打停止行的暂停点指纹(同一停点只打一次;先管道后盖章)
  bpNums: new Map(), // 断点 bp_id → 界面编号 1..N(客户端显示号,删后不复用)
  nextBpNum: 1,
  selectedFrame: 0, // 选中帧(#0 = 栈顶;GDB frame/up/down 语义,跨命令持续)
  outLines: [], // 命令窗输出行 [{ text, kind }](kind = cmd|out|err|stop)
  history: [], // 命令历史(↑↓ 翻阅)
  histIdx: 0, // 历史游标(= history.length 表示"新行")
  lastRaw: "", // 上一条命令原文(裸 Enter 重复用)
  lastName: "", // 上一条命令全名(REPEATABLE 白名单裁决)
  killArmed: false, // kill 两段确认:已问 `Kill the run being debugged? (y or n)`
  els: null, // { bar, stack, bps, trace, cmd, out, input }
};

const dbcRoute = () =>
  store.get("route").name === "debug-console" &&
  store.get("route").sessionId === dbc.sessionId;

const dbcActive = () => dbcRoute() && dbc.status === "ready";

/* 暂停点指纹(与 debug-view ppKeyOf 同形):同一暂停点不重复打停止行/滚动 */
const ppKeyOf = (doc) => {
  const pp = doc?.state === "paused" ? doc?.pause_point : null;
  return pp
    ? JSON.stringify([pp.signal, pp.frame_id, pp.step, pp.tool, pp.reason])
    : "";
};

/* ── 断点界面编号(客户端显示号,TUI D2 注 5 同款;REST 只有 bp_id)────── */

function assignBpNums() {
  for (const bp of dbc.doc?.breakpoints ?? []) {
    if (bp?.id && !dbc.bpNums.has(bp.id)) dbc.bpNums.set(bp.id, dbc.nextBpNum++);
  }
}

const numForBp = (bpId) => dbc.bpNums.get(bpId) ?? "?";

/* ── 入口(app.js 路由分发;幂等)───────────────────────────────── */

export function openDebugConsole(main, sessionId) {
  if (dbc.main === main && dbc.sessionId === sessionId) return; // 同会话重入:不动
  closeDebugConsole();
  dbc.main = main;
  dbc.sessionId = sessionId;
  dbc.status = "loading";
  renderShell();
  load();
}

/* 离开控制台(app.js 路由分发时调用,幂等):SSE/定时器/页面态整体收尾 */
export function closeDebugConsole() {
  dbc.es?.close();
  if (dbc.pollId != null) clearInterval(dbc.pollId);
  dbc.main = null;
  dbc.sessionId = null;
  dbc.status = "idle";
  dbc.error = null;
  dbc.doc = null;
  dbc.signals = [];
  dbc.sigCount = 0;
  dbc.es = null;
  dbc.sseDown = false;
  dbc.noEs = false;
  dbc.pollId = null;
  dbc.ended = false;
  dbc.endStatus = null;
  dbc.ppKey = "";
  dbc.scrolledKey = "";
  dbc.ppReported = "";
  dbc.bpNums = new Map();
  dbc.nextBpNum = 1;
  dbc.selectedFrame = 0;
  dbc.outLines = [];
  dbc.history = [];
  dbc.histIdx = 0;
  dbc.lastRaw = "";
  dbc.lastName = "";
  dbc.killArmed = false;
  dbc.els = null;
}

/* ── 取数 ───────────────────────────────────────────────────── */

async function refreshSnapshot() {
  dbc.doc = await getJson(`/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}`);
  assignBpNums();
}

async function refreshSignals() {
  const runId = dbc.doc?.run_id;
  if (!runId) return;
  const signals = await getJson(`/api/runs/${encodeURIComponent(runId)}/signals`);
  dbc.signals = Array.isArray(signals) ? signals : [];
}

async function load() {
  const sid = dbc.sessionId;
  try {
    await refreshSnapshot();
    await refreshSignals();
    if (dbc.sessionId !== sid || !dbcRoute()) return; // 加载期间路由已切走
    dbc.status = "ready";
    renderShell();
    if (dbc.doc?.state === "detached") {
      await finishRun(null); // 载入即终态(会话已摘下):横幅 + Run finished 一行
      return;
    }
    connectSse();
    startTicker();
    if (dbc.doc?.state === "paused") reportPause(); // 载入即停(启动即断):停止行 + ▶ 定位
    dbc.els?.input?.focus();
  } catch (e) {
    if (dbc.sessionId !== sid) return;
    dbc.status = "error";
    dbc.error = e;
    renderShell();
  }
}

/* ── SSE(照 debug-view connectSse 模式)──────────────────────────
   state(连接快照)→ bp_hit/paused/resumed → run_end;
   paused 只带 pause_point → 拉全量快照+signals 后打停止行并定位 ▶(§4/§6)。 */

function connectSse() {
  if (typeof EventSource !== "function") {
    dbc.sseDown = true; // 浏览器无 EventSource:全程轮询(快照+轨迹)
    dbc.noEs = true; // 区分"从未有 ES"(poll 模式)与"连上后断线"(down)
    return;
  }
  const es = new EventSource(`/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/stream`);
  dbc.es = es;
  es.onopen = () => {
    dbc.sseDown = false; // SSE 恢复:快照回到事件驱动
    dbc.noEs = false;
    renderBar();
  };
  es.addEventListener("state", (ev) => {
    try {
      dbc.doc = JSON.parse(ev.data);
    } catch {
      return; // 畸形行跳过
    }
    assignBpNums();
    if (!dbcActive()) return;
    renderAll();
  });
  es.addEventListener("bp_hit", (ev) => {
    // hits 差分事件:原地更新计数,断点窗与 gutter 即时刷新
    let d = null;
    try {
      d = JSON.parse(ev.data);
    } catch {
      return;
    }
    const bp = dbc.doc?.breakpoints?.find((b) => b.id === d.breakpoint_id);
    if (bp) bp.hits = d.hits;
    if (!dbcActive()) return;
    renderBps();
    renderTracePanel({ force: true });
  });
  es.addEventListener("paused", async () => {
    try {
      await refreshSnapshot();
      await refreshSignals();
    } catch {
      return;
    }
    if (!dbcActive()) return;
    reportPause();
  });
  es.addEventListener("resumed", () => {
    if (dbc.doc) {
      dbc.doc.state = "running";
      dbc.doc.pause_point = null;
    }
    if (!dbcActive()) return;
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
    if (dbc.ended || !dbc.es) return;
    es.close();
    dbc.es = null;
    dbc.sseDown = true; // 回退 2s 轮询快照(照 debug-view onerror 模式)
    toast("调试实时连接中断,回退轮询", "info"); // dbg.conn.down_msg 同文(theme.py)
    appendLines(["调试实时连接中断,回退轮询(2s 快照)。"], "err");
    renderBar();
  };
}

/* 暂停到位(§6):打停止行(同一停点只打一次)、选帧跟随暂停帧、整页重绘 + ▶ 定位 */
function reportPause() {
  dbc.ppKey = ppKeyOf(dbc.doc);
  const isNew = dbc.ppKey && dbc.ppKey !== dbc.ppReported;
  if (isNew) {
    dbc.ppReported = dbc.ppKey;
    const line = stopLine(dbc.doc, numForBp);
    if (line) appendLines([line], "stop");
    // 选帧跟随暂停帧(GDB 习惯:停止即改检视上下文)
    const fid = dbc.doc?.pause_point?.frame_id;
    const idx = stackFrames().findIndex((f) => f?.frame_id === fid);
    if (idx >= 0) dbc.selectedFrame = idx;
  }
  renderAll();
  scrollToPaused();
}

/* ── 轮询(轨迹常驻;SSE 不可用时快照同周期)────────────────────── */

function startTicker() {
  if (dbc.pollId != null) clearInterval(dbc.pollId);
  dbc.pollId = setInterval(pollTick, POLL_MS);
}

async function pollTick() {
  if (!dbcActive() || dbc.ended) return;
  try {
    await refreshSignals();
    if (dbc.sseDown) {
      // 回退模式:快照走轮询;paused/detached 转换在此发现(照 debug-view pollTick)
      const prevKey = ppKeyOf(dbc.doc);
      await refreshSnapshot();
      if (!dbcActive() || dbc.ended) return;
      if (dbc.doc?.state === "detached") {
        await finishRun(null);
        return;
      }
      if (ppKeyOf(dbc.doc) && ppKeyOf(dbc.doc) !== prevKey) reportPause(); // 新暂停点
      else renderAll();
      return;
    }
    renderTracePanel(); // SSE 模式:ticker 只补轨迹(内部按信号数变化才重绘)
  } catch {
    /* 单次轮询失败静默:下个周期重试(连接异常由 SSE onerror / app 健康轮询承担) */
  }
}

/* 结束态:run_end(SSE)或轮询发现 detached;命令窗打印终态行(§6)+ 横幅 +
   输入禁用;停 ticker、关流(§4:GDB `[Inferior 1 exited …]` 的对应物) */
async function finishRun(status) {
  if (dbc.ended) return;
  dbc.ended = true;
  dbc.endStatus = status ?? dbc.endStatus;
  dbc.killArmed = false;
  dbc.es?.close();
  dbc.es = null;
  if (dbc.pollId != null) clearInterval(dbc.pollId);
  dbc.pollId = null;
  try {
    await refreshSnapshot(); // 终态快照(state=detached,breakpoints 带最终 hits)
    await refreshSignals(); // 轨迹补全到 run.finished/aborted
  } catch {
    /* 终态拉取失败:按现有数据收尾 */
  }
  if (!dbcActive()) return;
  const label = dbc.endStatus ?? "detached";
  appendLines([fmt(MSG.endRun, { status: label })], "stop");
  renderAll();
  if (dbc.els?.input) dbc.els.input.disabled = true;
  toast(`调试会话结束(run ${label})`, label === "failed" ? "error" : "info");
}

/* ── 渲染:页面骨架(loading / error / ready 三态)────────────────── */

function renderShell() {
  const main = dbc.main;
  if (!main) return;
  if (dbc.status === "loading" || dbc.status === "idle") {
    main.innerHTML =
      `<div class="dbc" role="status" aria-label="${esc(copy("session.waiting"))}">` +
      `<div class="card skeleton-pad">` +
      `<span class="skeleton skeleton-line w-40"></span>` +
      `<span class="skeleton skeleton-line w-70"></span></div></div>`;
    return;
  }
  if (dbc.status === "error") {
    const notFound = dbc.error instanceof ApiError && dbc.error.status === 404;
    main.innerHTML =
      `<div class="dbc"><div class="card panel-error">` +
      `<span class="error-msg">${notFound ? "调试会话不存在(可能已结束清理)" : "加载调试会话失败"}</span>` +
      `<span class="mono">${esc(dbc.sessionId)}</span>` +
      (notFound
        ? `<a class="btn" href="#/debug">返回调试首页</a>`
        : `<button class="btn" data-action="dbc-retry">重试</button>`) +
      `</div></div>`;
    return;
  }
  main.innerHTML =
    `<div class="dbc">` +
    `<div id="dbcBar"></div>` +
    `<div class="dbc-cols">` +
    `<div class="dbc-left">` +
    `<section class="dbc-panel dbc-stack" id="dbcStack" aria-label="调用栈"></section>` +
    `<section class="dbc-panel dbc-bps" id="dbcBps" aria-label="断点"></section>` +
    `</div>` +
    `<section class="dbc-panel dbc-trace" id="dbcTrace" aria-label="执行轨迹"></section>` +
    `</div>` +
    `<section class="dbc-panel dbc-cmd" id="dbcCmd" aria-label="命令窗"></section>` +
    `</div>`;
  dbc.els = {
    bar: main.querySelector("#dbcBar"),
    stack: main.querySelector("#dbcStack"),
    bps: main.querySelector("#dbcBps"),
    trace: main.querySelector("#dbcTrace"),
    cmd: main.querySelector("#dbcCmd"),
    out: null,
    input: null,
  };
  buildCmdPane(dbc.els.cmd); // 真实 DOM(输入行监听持久,不随 innerHTML 重绘丢失)
  renderAll();
}

function renderAll() {
  renderBar();
  renderStack();
  renderBps();
  renderTracePanel({ force: true });
  renderOut();
}

/* ── 渲染:顶条(#sid + skill chip + 状态徽标 + 暂停点摘要 + 连接点 + 回程)── */

/* 会话状态徽标:paused/running 复用 StatusPill;detached 带 run 终态文案
   (与 debug-view sessionPillHtml 同语式) */
function sessionPillHtml() {
  const st = dbc.doc?.state;
  if (st === "paused" || st === "running") return statusPill(st);
  const end = dbc.endStatus;
  const label = st === "detached" ? (end ? `已结束 · ${end}` : "已结束") : (st ?? "…");
  const tone = ["done", "failed", "aborted"].includes(end) ? end : "unknown";
  return (
    `<span class="status-pill" data-status="${tone}">` +
    `<span class="pill-dot" aria-hidden="true"></span>` +
    `<span class="pill-label">${esc(label)}</span></span>`
  );
}

/* 暂停点摘要行(与 debug-view pauseLabelHtml 同形) */
function pauseLabelHtml() {
  const pp = dbc.doc?.state === "paused" ? dbc.doc?.pause_point : null;
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

function renderBar() {
  const el = dbc.els?.bar;
  if (!el) return;
  const sid = dbc.sessionId ?? "";
  const meta = (store.get("runs") ?? []).find((r) => r.run_id === dbc.doc?.run_id) ?? {};
  const skill = meta.skill ?? dbc.doc?.frame_stack?.[0]?.skill ?? null;
  const connState = dbc.ended ? "off" : dbc.noEs ? "poll" : dbc.sseDown ? "down" : dbc.es ? "ok" : "poll";
  const connTitle = {
    ok: "实时连接正常(SSE)",
    down: "实时连接中断,已回退轮询",
    poll: "无 EventSource,轮询模式",
    off: "会话已结束",
  }[connState];
  el.innerHTML =
    `<div class="card dbg-bar">` +
    `<span class="dbg-brand"><span class="brand-mark" aria-hidden="true">▎</span>Agent OS Debug</span>` +
    `<span class="meta-chip mono" title="GDB 风格命令控制台(docs/TUI-DEBUG.md §5)">(adb)</span>` +
    `<span class="dbg-sid mono" title="session_id: ${esc(sid)} · run_id: ${esc(dbc.doc?.run_id ?? "")}">` +
    `#${esc(shortId(sid))}</span>` +
    (skill ? `<span class="meta-chip" title="skill">${esc(shortSkillName(skill))}</span>` : "") +
    sessionPillHtml() +
    pauseLabelHtml() +
    `<span class="dbg-conn" data-state="${connState}" title="${connTitle}"` +
    ` aria-label="调试连接状态:${connTitle}"></span>` +
    `<span class="dbc-bar-links">` +
    `<a class="btn btn-mini" href="#/debug/${encodeURIComponent(sid)}"` +
    ` title="回三栏视图(q 命令同款)">三栏视图</a>` +
    `<a class="btn btn-mini" href="#/debug" title="返回调试首页">会话列表</a>` +
    `</span>` +
    `</div>` +
    (dbc.ended
      ? `<div class="banner dbg-end" data-tone="${
          dbc.endStatus === "failed" ? "danger" : dbc.endStatus === "aborted" ? "aborted" : "done"
        }" role="status">` +
        `<span class="banner-icon" aria-hidden="true">●</span>` +
        `<div class="banner-main"><span class="banner-title">${esc(copy("debug.end.title"))}${
          dbc.endStatus ? `(${esc(dbc.endStatus)})` : ""
        }</span>` +
        `<span class="banner-body">会话已 detached;完整产物见 ` +
        `<a href="#/runs/${encodeURIComponent(dbc.doc?.run_id ?? "")}">run 详情</a></span></div>` +
        `</div>`
      : "");
}

/* ── 渲染:调用栈窗(bt 行 #N skill f-id,▶ 当前帧;点击行 = frame N)────── */

const stackFrames = () => [...(dbc.doc?.frame_stack ?? [])].reverse(); // #0 = 栈顶

function renderStack() {
  const el = dbc.els?.stack;
  if (!el) return;
  const frames = stackFrames();
  const pausedFid = dbc.doc?.state === "paused" ? dbc.doc?.pause_point?.frame_id : null;
  const rows = frames.map((f, n) => {
    const fid = f?.frame_id ?? "";
    const current = fid && fid === pausedFid;
    const selected = n === dbc.selectedFrame;
    return (
      `<div class="dbc-frame" data-frame-n="${n}" role="option" tabindex="0"` +
      ` aria-selected="${Boolean(selected)}"${current ? ` data-current="true"` : ""}` +
      ` title="frame ${n} · ${esc(f?.skill ?? "")} · ${esc(fid)}(点击 = frame ${n})">` +
      `<span class="dbg-frame-mark" aria-hidden="true">${current ? "▶" : ""}</span>` +
      `<span class="dbc-frame-num mono">#${n}</span>` +
      indentHtml(Math.max(0, (Number(f?.depth) || 1) - 1)) +
      `<span class="dbg-frame-skill">${esc(shortSkillName(f?.skill))}</span>` +
      `<span class="dbg-frame-id mono">f-${esc(shortId(fid))}</span>` +
      `</div>`
    );
  });
  el.innerHTML =
    `<div class="dbg-sec-title">调用栈 (bt)</div>` +
    (rows.join("") || emptyBlock("帧栈为空", "run 尚未压栈(装配中或已结束)", "layers"));
}

/* ── 渲染:断点窗(info b 表常驻;行尾 ✕ 删除;bp_hit 原地刷新 ×hits)────── */

function renderBps() {
  const el = dbc.els?.bps;
  if (!el) return;
  const bps = (dbc.doc?.breakpoints ?? []).map((bp) => ({
    ...bp,
    num: numForBp(bp.id),
  }));
  const rows = bps.map(
    (bp) =>
      `<div class="dbc-bp-row" data-bp-id="${esc(bp.id)}">` +
      `<span class="dbc-bp-cells mono">${esc(
        `${String(bp.num).padEnd(4)}${String(bp.kind ?? "").padEnd(13)}` +
          `${String(bp.match ?? "").padEnd(17)}${(bp.enabled === false ? "n" : "y").padEnd(5)}×${bp.hits ?? 0}`)}</span>` +
      `<button class="icon-btn dbc-bp-del" data-action="dbc-bp-del" data-bp-id="${esc(bp.id)}"` +
      ` title="delete ${bp.num}(删除断点)" aria-label="删除断点 ${bp.num}">✕</button>` +
      `</div>`);
  el.innerHTML =
    `<div class="dbg-sec-title">断点 (info b)</div>` +
    (bps.length
      ? `<div class="dbc-bp-head mono">${esc(MSG.infoBHeader)}</div>` + rows.join("")
      : emptyBlock("无断点", "b <spec> 加断点;轨迹行 gutter 点击同款", "terminal"));
}

/* ── 渲染:轨迹窗(行语言/gutter/暂停行整体复用 debug-view renderDebugTrace)── */

function renderTracePanel({ force = false } = {}) {
  const el = dbc.els?.trace;
  if (!el) return;
  // ticker 的周期渲染按信号数变化判断(不打扰滚动);事件驱动渲染一律 force
  if (!force && dbc.sigCount === dbc.signals.length && el.innerHTML) return;
  dbc.sigCount = dbc.signals.length;
  const st = dbc.endStatus ?? "running";
  const frames = (dbc.doc?.frame_stack ?? []).map((f) => ({
    frame_id: f?.frame_id,
    skill: f?.skill,
    depth: f?.depth,
    status: dbc.ended ? st : "running",
  }));
  const rows = buildTraceRows(dbc.signals, frames);
  const ppIdx =
    dbc.doc?.state === "paused" ? pausedSignalIndex(dbc.signals, dbc.doc?.pause_point) : null;
  const pausedRow = ppIdx != null ? rowForSignal(rows, ppIdx) : null;
  el.innerHTML = dbc.signals.length
    ? renderDebugTrace(rows, {
        breakpoints: dbc.doc?.breakpoints ?? [],
        pausedLine: pausedRow?.line ?? null,
      })
    : emptyBlock("无信号数据", "run 尚未产生信号", "activity");
}

/* 暂停行滚动定位:同一暂停点只滚一次(用户之后翻看不打断) */
function scrollToPaused() {
  const el = dbc.els?.trace;
  if (!el || !dbc.ppKey || dbc.scrolledKey === dbc.ppKey) return;
  dbc.scrolledKey = dbc.ppKey;
  el.querySelector('[data-paused="true"]')?.scrollIntoView({ block: "center" });
}

/* ── 命令窗:真实 DOM(输出区 + (adb) 输入行;监听持久)────────────────── */

function buildCmdPane(cmdEl) {
  if (!cmdEl) return;
  const out = document.createElement("div");
  out.className = "dbc-out";
  out.setAttribute("role", "log");
  out.setAttribute("aria-label", "命令输出");
  const row = document.createElement("div");
  row.className = "dbc-input-row";
  const prompt = document.createElement("span");
  prompt.className = "dbc-prompt mono";
  prompt.textContent = MSG.prompt;
  const input = document.createElement("input");
  input.className = "dbc-input mono";
  input.spellcheck = false;
  input.setAttribute("aria-label", "调试命令输入");
  input.placeholder = "help 查看命令(唯一前缀可缩写;裸 Enter 重复步进)";
  input.addEventListener("keydown", onInputKey);
  row.appendChild(prompt);
  row.appendChild(input);
  cmdEl.appendChild(out);
  cmdEl.appendChild(row);
  dbc.els.out = out;
  dbc.els.input = input;
  if (dbc.ended) input.disabled = true;
}

function renderOut() {
  const out = dbc.els?.out;
  if (!out) return;
  out.innerHTML = dbc.outLines.map((l) => outLineHtml(l.text, l.kind)).join("");
  out.scrollTop = out.scrollHeight; // 新输出滚到底(GDB 命令窗习惯)
}

function appendLines(lines, kind = "out") {
  for (const text of Array.isArray(lines) ? lines : [lines]) {
    dbc.outLines.push({ text: String(text), kind });
  }
  if (dbc.outLines.length > OUT_CAP) {
    dbc.outLines = dbc.outLines.slice(-OUT_CAP);
  }
  renderOut();
}

/* ── 命令窗:输入行(Enter 提交 / ↑↓ 历史 / 裸 Enter 重复 / kill 两段确认)── */

function onInputKey(e) {
  if (e.key === "Enter") {
    e.preventDefault?.();
    const value = dbc.els?.input?.value ?? "";
    if (dbc.els?.input) dbc.els.input.value = "";
    dbc.histIdx = dbc.history.length;
    submitLine(value);
    return;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault?.();
    if (dbc.histIdx > 0) {
      dbc.histIdx -= 1;
      if (dbc.els?.input) dbc.els.input.value = dbc.history[dbc.histIdx] ?? "";
    }
    return;
  }
  if (e.key === "ArrowDown") {
    e.preventDefault?.();
    if (dbc.histIdx < dbc.history.length) {
      dbc.histIdx += 1;
      if (dbc.els?.input) {
        dbc.els.input.value = dbc.history[dbc.histIdx] ?? "";
      }
    }
  }
}

/* 提交一行(kill 两段确认拦截优先;空行 = 按白名单重复上一命令;
   TUI app.py run_command 对译) */
function submitLine(text) {
  const stripped = String(text ?? "").trim();
  if (dbc.ended) return; // 终态:输入已禁用(双保险)
  if (dbc.killArmed) {
    killAnswer(stripped);
    return;
  }
  if (!stripped) {
    repeatLast();
    return;
  }
  appendLines([`${MSG.prompt}${stripped}`], "cmd");
  if (dbc.history[dbc.history.length - 1] !== stripped) dbc.history.push(stripped);
  dbc.histIdx = dbc.history.length;
  const cmd = parse(stripped);
  if (cmd.error) {
    appendLines(parseErrorLines(cmd.error), "err");
    return;
  }
  // 裸 Enter 重复记的是"上一条命令"(解析成功即记,与 TUI store_last 同款)
  dbc.lastRaw = stripped;
  dbc.lastName = cmd.name;
  executeCommand(cmd);
}

/* 裸 Enter 重复(§5.2 白名单;kill/run/detach/set args/inject 永不因空行重复) */
function repeatLast() {
  if (!REPEATABLE.has(dbc.lastName) || !dbc.lastRaw) return;
  appendLines([`${MSG.prompt}${dbc.lastRaw}`], "cmd");
  const cmd = parse(dbc.lastRaw);
  if (cmd.error) return;
  executeCommand(cmd);
}

/* kill 两段确认第二击(逐字学 GDB;n/其他:静默回到提示符,GDB 同款) */
function killAnswer(answer) {
  dbc.killArmed = false;
  appendLines([`${MSG.prompt}${answer}`], "cmd");
  if (!["y", "yes"].includes(answer.toLowerCase())) return;
  doStop();
}

/* ── 命令执行(会话命令经 REST 出海;只读命令本地成文)────────────────── */

const hasRun = () => dbc.doc && dbc.doc.state !== "detached" && dbc.doc.run_id;

/* 恢复类命令公共守卫 + 出海(c/s/n/finish;commands.py _resume 对译) */
async function resumeCmd(cmd) {
  if (!hasRun()) return appendLines([MSG.noSession], "err");
  if (dbc.doc?.state !== "paused") return appendLines([MSG.notPaused], "err");
  try {
    await postJson(`/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/command`, { cmd });
  } catch (e) {
    return appendLines([e.message ?? `命令 ${cmd} 失败`], "err");
  }
  // 异步源:停点/终态经 SSE·轮询回流打停止行(§4;先回声后回流)
  appendLines([fmt(MSG.resumed, { cmd })]);
}

async function cmdBreak(args) {
  if (!args.length) return appendLines([fmt(MSG.usage, { usage: "b <spec>" })], "err");
  const spec = parseBpSpec(args[0]);
  if (spec.error) return appendLines(parseErrorLines(spec.error), "err");
  if (spec.until != null) {
    // live REST 断点端点只有 kind/match(DebugBreakpointBody):诚实人话,不造假
    return appendLines([MSG.noUntil], "err");
  }
  try {
    const bp = await postJson(
      `/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/breakpoints`,
      { kind: spec.kind, match: spec.match || "*" });
    if (bp?.id && !dbc.bpNums.has(bp.id)) dbc.bpNums.set(bp.id, dbc.nextBpNum++);
    appendLines([fmt(MSG.bpSet, { num: numForBp(bp?.id), kind: spec.kind, match: spec.match })]);
    await refreshSnapshot();
  } catch (e) {
    return appendLines([e.message ?? "添加断点失败"], "err");
  }
  if (dbcActive()) {
    renderBps();
    renderTracePanel({ force: true });
  }
}

async function removeBreakpointById(bpId) {
  const num = numForBp(bpId);
  try {
    await deleteJson(
      `/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/breakpoints/${encodeURIComponent(bpId)}`);
    appendLines([fmt(MSG.bpDel, { num })]);
    await refreshSnapshot();
  } catch (e) {
    return appendLines([e.message ?? "删除断点失败"], "err");
  }
  if (dbcActive()) {
    renderBps();
    renderTracePanel({ force: true });
  }
}

function cmdDelete(args) {
  if (!args.length || !/^\d+$/.test(args[0])) {
    return appendLines([fmt(MSG.usage, { usage: "delete <N>" })], "err");
  }
  const num = Number(args[0]);
  const bp = (dbc.doc?.breakpoints ?? []).find((b) => numForBp(b.id) === num);
  if (!bp) return appendLines([fmt(MSG.noBp, { num })], "err");
  removeBreakpointById(bp.id);
}

/* gutter 切换(复用 debug-view 行语义):已有同 kind+match 断点 → 删除;否则新增。
   回声进命令窗(快捷键只是命令的回声——教用户命令语言,§1 原则 1)。 */
function toggleGutter(dataset) {
  const { kind, match, bpId } = dataset ?? {};
  if (!kind || !match) return;
  if (bpId) {
    removeBreakpointById(bpId);
    return;
  }
  (async () => {
    try {
      const bp = await postJson(
        `/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/breakpoints`,
        { kind, match });
      if (bp?.id && !dbc.bpNums.has(bp.id)) dbc.bpNums.set(bp.id, dbc.nextBpNum++);
      appendLines([fmt(MSG.bpSet, { num: numForBp(bp?.id), kind, match })]);
      await refreshSnapshot();
    } catch (e) {
      appendLines([e.message ?? "添加断点失败"], "err");
      return;
    }
    if (dbcActive()) {
      renderBps();
      renderTracePanel({ force: true });
    }
  })();
}

/* 选帧(GDB frame/up/down:选帧即改检视上下文;回声行进命令窗) */
function cmdFrame(n) {
  const frames = stackFrames();
  if (!frames.length) return appendLines([MSG.noStack], "err");
  if (!(0 <= n && n < frames.length)) return appendLines([fmt(MSG.noFrame, { num: n })], "err");
  dbc.selectedFrame = n;
  appendLines([frameRowLine(n, frames[n])]);
  renderStack();
}

/* 选中帧的完整 FrameDoc(GET …/frames/{fid},live 内存态;找不到给人话) */
async function selectedFrameDoc() {
  const frames = stackFrames();
  if (!frames.length) {
    appendLines([MSG.noStack], "err");
    return null;
  }
  dbc.selectedFrame = Math.min(Math.max(dbc.selectedFrame, 0), frames.length - 1);
  const fid = String(frames[dbc.selectedFrame]?.frame_id ?? "");
  try {
    return await getJson(
      `/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/frames/${encodeURIComponent(fid)}`);
  } catch (e) {
    appendLines([e.message ?? `加载帧 ${fid} 失败`], "err");
    return null;
  }
}

const pretty = (v) => {
  try {
    return JSON.stringify(v ?? null, null, 2);
  } catch {
    return String(v);
  }
};

/* p [key](暂停点 payload 全量或子键,客户端取值零裁决;commands.py _cmd_print 对译) */
function cmdPrint(args) {
  if (dbc.doc?.state !== "paused" || !dbc.doc?.pause_point) {
    return appendLines([MSG.notPaused], "err");
  }
  const payload = dbc.doc.pause_point.payload ?? {};
  let value = payload;
  if (args.length) {
    for (const key of args[0].split(".")) {
      if (value && typeof value === "object" && !Array.isArray(value) && key in value) {
        value = value[key];
      } else {
        return appendLines(
          [fmt(MSG.usage, { usage: `p <key>(payload 无此键: ${args[0]})` })], "err");
      }
    }
  }
  appendLines(pretty(value).split("\n"));
}

async function cmdX(args) {
  const doc = await selectedFrameDoc();
  if (!doc) return;
  if (args[0] === "working") return appendLines(pretty(doc.working).split("\n"));
  const lines = [];
  for (const m of doc.messages ?? []) {
    if (!m || typeof m !== "object") continue;
    const role = String(m.role ?? "?");
    if (m.content) lines.push(`[${role}] ${m.content}`);
    else if (m.tool_calls?.length) {
      for (const tc of m.tool_calls) lines.push(`[${role}] tool_call ${tc.name}(${shortJson(tc.args)})`);
    } else lines.push(`[${role}] (empty)`);
  }
  appendLines(lines.length ? lines : [MSG.emptyStack]);
}

async function cmdInfoFrame() {
  const doc = await selectedFrameDoc();
  if (!doc) return;
  const usage = doc.usage ?? {};
  const lines = [
    `frame_id: ${doc.frame_id}`,
    `skill: ${doc.skill}`,
    `status: ${doc.status}`,
    `input: ${shortJson(doc.input)}`,
    `usage: steps=${usage.steps ?? 0} cost=${usage.cost ?? 0}`,
  ];
  if (doc.error) lines.push(`error: ${doc.error}`);
  appendLines(lines);
}

async function cmdInfoArgs() {
  const doc = await selectedFrameDoc();
  if (!doc) return;
  appendLines(pretty(doc.input).split("\n"));
}

/* set args <json>(§5.1:仅停在 pre:tool.call;提交即放行)。
   JSON 从原始行取(值可含空格,word 切分吃不了);非法/非对象本地拦,不打后端。 */
async function cmdSetArgs(raw) {
  const m = /^set\s+args\s+([\s\S]*)$/.exec(raw);
  if (!m) return appendLines([fmt(MSG.usage, { usage: "set args <json>" })], "err");
  let patch;
  try {
    patch = JSON.parse(m[1]);
  } catch (e) {
    return appendLines([fmt(MSG.usage, { usage: `set args <json>(JSON 解析错: ${e.message})` })], "err");
  }
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) {
    return appendLines([fmt(MSG.usage, { usage: "set args <json>(须为对象)" })], "err");
  }
  if (!hasRun()) return appendLines([MSG.noSession], "err");
  if (dbc.doc?.state !== "paused") return appendLines([MSG.notPaused], "err");
  const pp = dbc.doc?.pause_point ?? {};
  if (pp.signal !== "pre:tool.call") return appendLines([MSG.setArgsPos], "err");
  try {
    await postJson(`/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/modify`, { patch });
  } catch (e) {
    return appendLines([e.message ?? "modify 失败"], "err"); // 409 竞态原样转述(§6)
  }
  appendLines([fmt(MSG.patched, { patch: JSON.stringify(patch) })]);
}

/* inject <text>(注入一条 user 消息进暂停帧;注入即放行) */
async function cmdInject(raw) {
  const m = /^inject\s+([\s\S]*)$/.exec(raw);
  const text = (m?.[1] ?? "").trim();
  if (!text) return appendLines([fmt(MSG.usage, { usage: "inject <text>" })], "err");
  if (!hasRun()) return appendLines([MSG.noSession], "err");
  if (dbc.doc?.state !== "paused") return appendLines([MSG.notPaused], "err");
  const pp = dbc.doc?.pause_point ?? {};
  try {
    const res = await postJson(
      `/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/inject`, { text });
    const fid = res?.frame_id ?? pp.frame_id ?? "-";
    appendLines([fmt(MSG.injected, { frame: fid, skill: shortSkillName(pp.skill) })]);
  } catch (e) {
    return appendLines([e.message ?? "inject 失败"], "err");
  }
}

/* kill 第二击的 Stop 出海(commands.py execute_stop 对译;run_end 经 SSE 回流) */
async function doStop() {
  if (!hasRun()) return appendLines([MSG.noSession], "err");
  try {
    await postJson(`/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/command`, { cmd: "stop" });
  } catch (e) {
    return appendLines([e.message ?? "stop 失败"], "err");
  }
  appendLines([fmt(MSG.resumed, { cmd: "stop" })]);
}

function cmdKill() {
  if (dbc.doc?.state === "paused" || dbc.doc?.state === "running") {
    dbc.killArmed = true;
    return appendLines([MSG.killConfirm]); // 两段确认第一击(逐字学 GDB)
  }
  appendLines([MSG.noSession], "err");
}

async function cmdDetach() {
  if (!hasRun()) return appendLines([MSG.noSession], "err");
  try {
    await deleteJson(`/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}`);
  } catch (e) {
    return appendLines([e.message ?? "detach 失败"], "err");
  }
  appendLines([MSG.detached]); // run_end 经 SSE/轮询回流打终态行(§4)
}

async function cmdRerun() {
  if (!hasRun()) return appendLines([MSG.noSession], "err");
  try {
    const res = await postJson(`/api/debug/sessions/${encodeURIComponent(dbc.sessionId)}/rerun`);
    if (res?.session_id) {
      appendLines([fmt(MSG.rerun, { sid: res.session_id, run_id: res.run_id ?? "?" })]);
      location.hash = `#/debug/${encodeURIComponent(res.session_id)}/console`;
      return;
    }
    appendLines([res?.error ?? "重新运行失败(会话未开始)"], "err"); // 200 + failed 原样转述
  } catch (e) {
    appendLines([e.message ?? "重新运行失败"], "err");
  }
}

/* 已知会话(本机账本,与 debug-home 同键;session <SID> = attach 切换) */
function loadKnown() {
  try {
    if (typeof localStorage === "undefined") return [];
    const list = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((x) => x?.session_id) : [];
  } catch {
    return [];
  }
}

function cmdSession(args) {
  if (!args.length) {
    const known = loadKnown();
    if (!known.length) return appendLines([MSG.emptySessions]);
    return appendLines([
      "Session        Run          Skill",
      ...known.map((k) =>
        `${String(k.session_id).padEnd(15)}${String(k.run_id ?? "-").padEnd(13)}${shortSkillName(k.skill)}`),
    ]);
  }
  // attach 切换:消逝会话在目标页吃诚实 404(不本地预检,TUI D4 注 8 同款)
  location.hash = `#/debug/${encodeURIComponent(args[0])}/console`;
}

function cmdQuit() {
  // q = 回三栏视图(§交付:console 不做退 TUI;会话保留,不 detach)
  if (dbc.doc?.state === "paused") appendLines([MSG.quitHint]);
  location.hash = `#/debug/${encodeURIComponent(dbc.sessionId)}`;
}

/* 执行路由(commands.py execute 对译;REST 出海,停点/终态经 SSE·轮询回流) */
function executeCommand(cmd) {
  const { name, args } = cmd;
  switch (name) {
    case "run": // console 不做开会话表单:诚实人话指向 #/debug 首页
      return appendLines([MSG.runToHome]);
    case "break":
      return cmdBreak(args);
    case "info": {
      const sub = args[0];
      if (sub === "breakpoints") {
        const bps = (dbc.doc?.breakpoints ?? []).map((bp) => ({ ...bp, num: numForBp(bp.id) }));
        return appendLines(infoBLines(bps));
      }
      if (sub === "frame") return cmdInfoFrame();
      if (sub === "args") return cmdInfoArgs();
      return cmdSession([]); // info sessions(本机账本)
    }
    case "delete":
      return cmdDelete(args);
    case "enable":
    case "disable": // 后端缺口:命令照收,诚实人话(§1 原则 6,不造假)
      return appendLines([MSG.enableDisable], "err");
    case "continue":
      return resumeCmd("continue");
    case "step":
      return resumeCmd("step_into");
    case "next":
      return resumeCmd("step_over");
    case "finish":
      return resumeCmd("step_out");
    case "until": // REST 断点端点无 until 字段:诚实人话(TUI live 同款,D2 注 6)
      if (!args.length || !/^\d+$/.test(args[0]) || Number(args[0]) < 1) {
        return appendLines([fmt(MSG.usage, { usage: "until <N>(N ≥ 1)" })], "err");
      }
      return appendLines([MSG.noUntil], "err");
    case "backtrace":
      return appendLines(btLines(dbc.doc));
    case "frame":
      if (!args.length || !/^-?\d+$/.test(args[0])) {
        return appendLines([fmt(MSG.usage, { usage: "frame <N>" })], "err");
      }
      return cmdFrame(Number(args[0]));
    case "up":
      return cmdFrame(dbc.selectedFrame + 1);
    case "down":
      if (dbc.selectedFrame <= 0) return appendLines([fmt(MSG.noFrame, { num: -1 })], "err");
      return cmdFrame(dbc.selectedFrame - 1);
    case "print":
      return cmdPrint(args);
    case "x":
      return cmdX(args);
    case "set":
      return cmdSetArgs(cmd.raw);
    case "inject":
      return cmdInject(cmd.raw);
    case "kill":
      return cmdKill();
    case "detach":
      return cmdDetach();
    case "rerun":
      return cmdRerun();
    case "session":
      return cmdSession(args);
    case "quit":
      return cmdQuit();
    case "help":
      return appendLines(helpLines(args));
    case "apropos":
      return appendLines(aproposLines(args));
    default:
      return appendLines([fmt(MSG.unknown, { cmd: name })], "err");
  }
}

/* ── 事件接线(app.js 事件委托转发;命中即返回 true)───────────────── */

export function consoleClick(e, action) {
  if (!dbc.main || store.get("route").name !== "debug-console") return false;
  if (action) {
    const act = action.dataset.action;
    if (act === "dbc-retry") return load(), true;
    if (act === "dbg-gutter") return toggleGutter(action.dataset), true; // 复用 debug-view gutter
    if (act === "dbc-bp-del") return removeBreakpointById(action.dataset.bpId), true;
    return false;
  }
  const frame = e.target.closest?.(".dbc-frame");
  if (frame) {
    // 点击栈行 = `frame N`(GDB 习惯:点击即命令,回声进命令窗)
    appendLines([`${MSG.prompt}frame ${frame.dataset.frameN}`], "cmd");
    cmdFrame(Number(frame.dataset.frameN));
    return true;
  }
  return false;
}

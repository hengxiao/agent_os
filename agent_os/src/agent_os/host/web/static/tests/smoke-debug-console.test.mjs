/* GDB 风格调试控制台 DOM-stub 冒烟(#/debug/<sid>/console;docs/TUI-DEBUG.md
   §5 命令方言 + §7 布局的 web 镜像;基建照 smoke-debug.test.mjs):
   1) 四窗渲染:顶条(#sid/skill chip/状态徽标/暂停点摘要/连接点/「三栏视图」
      回程)+ 调用栈窗(#N 行,▶ 当前帧)+ 断点窗(info b 表 + 行尾 ✕)+
      轨迹窗(gutter + ▶ 暂停行)+ 底部命令窗((adb) 输入行);
   2) 载入即停(启动即断会话):停止行逐字 + ▶ 定位;
   3) 命令回声:b <spec> 增 / delete <N> 删 / info b 表 / enable 诚实人话 /
      gutter 切换(复用 debug-view 行语义);
   4) 缩写(fin = finish)与错误语式逐字(歧义候选 / Undefined / not paused /
      set args 本地拦 / until 诚实人话 / run 指向 #/debug);
   5) 裸 Enter 重复白名单(步进可重复;p 不重复)+ ↑↓ 历史;
   6) kill 两段确认逐字(n 静默不出海;y → POST stop);
   7) 干预:set args(非法 JSON 本地拦)/ inject 回声;
   8) SSE paused → 停止行 + ▶ 定位;run_end → Run finished 行 + 终态横幅 +
      输入禁用;SSE 断线 → 回退轮询人话 + 连接点 down;
   9) bt / frame / up / down 选帧;q → 回 #/debug/<sid> 三栏视图。
   运行:node static/tests/smoke-debug-console.test.mjs(DOM 用 dom-stub.mjs)。 */

import assert from "node:assert/strict";
import { StubEl, makeDocument } from "./dom-stub.mjs";
import { makeSignals } from "./fixtures.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub(照 smoke-debug:document / location / localStorage / ES / fetch)── */
const doc = makeDocument();
const toastStack = new StubEl("div");
toastStack.setAttribute("id", "toastStack");
doc.body.appendChild(toastStack);
doc.querySelector = (sel) => doc.body.querySelector(sel);
const locationStub = { hash: "" };
const storageMap = new Map();
const localStorageStub = {
  getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
  setItem: (k, v) => storageMap.set(k, String(v)),
  removeItem: (k) => storageMap.delete(k),
};

class ESStub {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.handlers = {};
    this.closed = false;
    ESStub.instances.push(this);
  }
  addEventListener(type, fn) {
    (this.handlers[type] ??= []).push(fn);
  }
  close() {
    this.closed = true;
  }
  emit(type, data) {
    for (const fn of this.handlers[type] ?? []) fn({ data: JSON.stringify(data) });
  }
  open() {
    this.onopen?.();
  }
  error() {
    this.onerror?.();
  }
}

/* ── 数据夹具(可变 state:逐段推进;与 smoke-debug 同一事实锚点)────────── */
const BP1 = { id: "bp1", kind: "tool_call", match: "system.python.exec", enabled: true, hits: 1 };
const PP_TOOL = {
  signal: "pre:tool.call", run_id: "r1", frame_id: "f1", depth: 1, step: 1,
  tool: "system.python.exec", skill: null,
  payload: { frame_id: "f1", depth: 1, tool: "system.python.exec", args: { code: "print(1)" } },
  breakpoint_ids: ["bp1"], reason: "breakpoint",
};
const PP_STEP_F2 = {
  signal: "pre:step", run_id: "r1", frame_id: "f2", depth: 2, step: 1,
  tool: null, skill: "local:fib@1.0.0",
  payload: { frame_id: "f2", depth: 2, step: 1 },
  breakpoint_ids: [], reason: "step:into",
};
const SNAPSHOT = () => ({
  session_id: "dbg-s1",
  run_id: "r1",
  state: "paused",
  pause_point: structuredClone(PP_TOOL),
  breakpoints: [structuredClone(BP1)],
  rerunnable: true,
  frame_stack: [
    { frame_id: "f1", skill: "local:fib@1.0.0", depth: 1 },
    { frame_id: "f2", skill: "local:fib@1.0.0", depth: 2 },
  ],
});
const state = { snapshot: SNAPSHOT(), signals: makeSignals() };
const FRAME = (fid) => ({
  frame_id: fid, skill: "local:fib@1.0.0", status: "running",
  input: { n: 6 },
  usage: { steps: 1, cost: 0 },
  messages: [
    { role: "user", content: '{"n": 6}', source: "parent_input" },
    { role: "assistant", content: "", tool_calls: [{ id: "c1", name: "system.python.exec", args: { code: "print(5)" } }] },
  ],
  working: { note: "scratch" },
});

const requests = [];
let bpSeq = 1;
globalThis.document = doc;
globalThis.location = locationStub;
globalThis.localStorage = localStorageStub;
globalThis.EventSource = ESStub;
globalThis.fetch = async (url, options = {}) => {
  const u = String(url);
  const method = (options.method ?? "GET").toUpperCase();
  const body = options.body ? JSON.parse(options.body) : undefined;
  requests.push({ method, url: u, body });
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (u === "/api/debug/sessions/dbg-s1" && method === "GET") return reply(state.snapshot);
  if (u === "/api/debug/sessions/dbg-s1" && method === "DELETE") return reply({ ok: true });
  if (u === "/api/runs/r1/signals") return reply(state.signals);
  if (u === "/api/debug/sessions/dbg-s1/frames/f1") return reply(FRAME("f1"));
  if (u === "/api/debug/sessions/dbg-s1/frames/f2") return reply(FRAME("f2"));
  if (u === "/api/debug/sessions/dbg-s1/breakpoints" && method === "POST") {
    const bp = { id: `bp${++bpSeq}`, enabled: true, hits: 0, ...body };
    state.snapshot.breakpoints.push(bp);
    return reply(bp);
  }
  if (u.startsWith("/api/debug/sessions/dbg-s1/breakpoints/") && method === "DELETE") {
    const id = u.split("/").pop();
    state.snapshot.breakpoints = state.snapshot.breakpoints.filter((b) => b.id !== id);
    return reply({ ok: true });
  }
  if (u === "/api/debug/sessions/dbg-s1/command") return reply({ ok: true, cmd: body?.cmd });
  if (u === "/api/debug/sessions/dbg-s1/rerun" && method === "POST") {
    return reply({ session_id: "dbg-s2", run_id: "r2" });
  }
  if (u === "/api/debug/sessions/dbg-s1/modify") return reply({ ok: true });
  if (u === "/api/debug/sessions/dbg-s1/inject") return reply({ ok: true, frame_id: "f1" });
  throw new Error(`未 stub 的请求: ${method} ${u}`);
};

const { store } = await import("../js/store.js");
const {
  closeDebugConsole,
  consoleClick,
  openDebugConsole,
} = await import("../js/components/debug-console.js");

const setRoute = (route) => store.set({ route });

/* 打开控制台并等数据到位(快照 + signals → ready) */
async function openConsole() {
  setRoute({ name: "debug-console", sessionId: "dbg-s1", runId: null });
  const main = new StubEl("main");
  openDebugConsole(main, "dbg-s1");
  await flush();
  await flush();
  await flush();
  return main;
}

const cmdEl = (main) => main.querySelector("#dbcCmd");
const inputOf = (main) => cmdEl(main).querySelector(".dbc-input");
const outHtml = (main) => cmdEl(main).querySelector(".dbc-out").innerHTML;

/* 键入一行并 Enter(真实 input 的 keydown 路径) */
async function typeCmd(main, line) {
  const input = inputOf(main);
  input.value = line;
  input.trigger("keydown", { key: "Enter" });
  await flush();
  await flush();
  await flush();
}

/* 事件委托驱动(照 smoke-debug clickAction):fabricate 带 dataset 的目标元素 */
const clickAction = (dataset) => {
  const act = new StubEl("button");
  Object.assign(act.dataset, dataset);
  return consoleClick({ target: act }, act);
};

/* ══ 1+2. 四窗渲染 + 载入即停(停止行逐字 + ▶ 定位)════════════════════ */
{
  const main = await openConsole();

  const bar = main.querySelector("#dbcBar");
  assert.match(bar.innerHTML, /Agent OS Debug/, "顶条品牌");
  assert.match(bar.innerHTML, /\(adb\)/, "(adb) chip");
  assert.match(bar.innerHTML, /#dbg-s1/, "#sid");
  assert.match(bar.innerHTML, /data-status="paused"/, "状态徽标 paused");
  assert.match(bar.innerHTML, /paused @ pre:tool\.call/, "暂停点摘要");
  assert.match(bar.innerHTML, /data-state="poll"|data-state="ok"/, "连接态点");
  assert.match(bar.innerHTML, /href="#\/debug\/dbg-s1"[^>]*>\s*三栏视图/, "「三栏视图」回程链接");

  const stack = main.querySelector("#dbcStack");
  assert.match(stack.innerHTML, /调用栈 \(bt\)/, "调用栈窗标题");
  assert.match(stack.innerHTML, /data-frame-n="0"[\s\S]*?data-frame-n="1"/, "#0 栈顶在前");
  assert.match(stack.innerHTML, /data-frame-n="1"[^>]*data-current="true"/, "▶ 当前帧(f1)");
  assert.match(stack.innerHTML, /data-frame-n="1"[^>]*aria-selected="true"/, "选帧跟随暂停帧");

  const bps = main.querySelector("#dbcBps");
  assert.match(bps.innerHTML, /断点 \(info b\)/, "断点窗标题");
  assert.match(bps.innerHTML, /Num {2}Kind {9}Match {12}Enb {2}Hits/, "info b 表头");
  assert.match(bps.innerHTML, /1 {3}tool_call {4}system\.python\.execy {4}×1/, "断点行(Num/Kind/Match/Enb/Hits;match 恰满 17 列,TUI 同款无分隔)");
  assert.match(bps.innerHTML, /data-action="dbc-bp-del" data-bp-id="bp1"/, "行尾 ✕ 删除");

  const trace = main.querySelector("#dbcTrace");
  assert.match(trace.innerHTML, /dbg-trace-list/, "轨迹窗(复用 debug-view 行语言)");
  assert.match(
    trace.innerHTML,
    /data-signal-index="6" data-kind="tool"[^>]*data-paused="true"/,
    "▶ 暂停行 = pre:tool.call 合并行");
  assert.match(trace.innerHTML, /data-action="dbg-gutter"/, "gutter 可切换断点");

  const out = outHtml(main);
  const input = inputOf(main);
  assert.ok(input, "(adb) 真实输入行");
  const promptEl = input.parentNode.children.find((c) => c.classList.contains("dbc-prompt"));
  assert.equal(promptEl?.textContent, "(adb) ", "命令窗提示符逐字");
  assert.equal(input.disabled, false, "输入可用");
  assert.ok(
    out.includes("Breakpoint 1, pre:tool.call system.python.exec, frame f1 (), step 1"),
    "载入即停:停止行逐字(§6)");
  assert.match(out, /data-kind="stop"/, "停止行 stop 着色");

  closeDebugConsole();
}

/* ══ 3. 命令回声:bp 增删 / info b / enable 人话 / gutter 切换 ═══════════ */
{
  const main = await openConsole();

  /* b system.fs.* → POST + 回声 + 断点窗上新 */
  requests.length = 0;
  await typeCmd(main, "b system.fs.*");
  const added = requests.find((r) => r.method === "POST" && r.url.endsWith("/breakpoints"));
  assert.deepEqual(added.body, { kind: "tool_call", match: "system.fs.*" }, "b 请求体");
  let out = outHtml(main);
  assert.ok(out.includes("(adb) b system.fs.*"), "命令回声(cmd 行)");
  assert.ok(out.includes("Breakpoint 2 set: tool_call system.fs.*"), "bp set 回声逐字");
  assert.match(main.querySelector("#dbcBps").innerHTML, /system\.fs\.\*/, "新断点上窗");

  /* info b → 表进命令窗 */
  await typeCmd(main, "info b");
  out = outHtml(main);
  assert.ok(out.includes("Num  Kind         Match            Enb  Hits"), "info b 表头");
  assert.ok(out.includes("2   tool_call    system.fs.*"), "info b 行");

  /* delete 2 → DELETE + 回声;下窗 */
  requests.length = 0;
  await typeCmd(main, "delete 2");
  assert.ok(
    requests.some((r) => r.method === "DELETE" && r.url.endsWith("/breakpoints/bp2")),
    "delete 2 → bp_id 映射(bp2)");
  out = outHtml(main);
  assert.ok(out.includes("Breakpoint 2 deleted."), "bp del 回声逐字");
  assert.doesNotMatch(main.querySelector("#dbcBps").innerHTML, /system\.fs\.\*/, "断点下窗");
  requests.length = 0;
  await typeCmd(main, "delete 9");
  assert.ok(outHtml(main).includes("No breakpoint number 9."), "无此号人话逐字");
  assert.ok(!requests.some((r) => r.method === "DELETE"), "无此号不出海");

  /* enable/disable 诚实人话(后端缺口,不造假) */
  await typeCmd(main, "enable 1");
  assert.ok(outHtml(main).includes("Not supported: delete and re-add."), "enable 诚实拒绝逐字");

  /* gutter 切换(复用 debug-view dbg-gutter 动作):新增 → 删除 */
  requests.length = 0;
  assert.equal(
    clickAction({ action: "dbg-gutter", kind: "skill_invoke", match: "local:fib@1.0.0" }),
    true, "gutter 点击(consoleClick 命中)");
  await flush();
  await flush();
  const gAdd = requests.find((r) => r.method === "POST" && r.url.endsWith("/breakpoints"));
  assert.deepEqual(gAdd.body, { kind: "skill_invoke", match: "local:fib@1.0.0" }, "gutter 新增");
  assert.ok(outHtml(main).includes("Breakpoint 3 set: skill_invoke local:fib@1.0.0"),
    "gutter 新增回声");
  requests.length = 0;
  assert.equal(
    clickAction({ action: "dbg-gutter", kind: "skill_invoke", match: "local:fib@1.0.0", bpId: "bp3" }),
    true, "gutter 再点 = 删除");
  await flush();
  await flush();
  assert.ok(
    requests.some((r) => r.method === "DELETE" && r.url.endsWith("/breakpoints/bp3")),
    "gutter 删除");

  closeDebugConsole();
}

/* ══ 4. 缩写 / 歧义 / 错误语式 / 本地拦截 ═══════════════════════════════ */
{
  const main = await openConsole();

  /* fin = finish(唯一前缀缩写)→ POST step_out + 回声 */
  requests.length = 0;
  await typeCmd(main, "fin");
  assert.deepEqual(
    requests.find((r) => r.url.endsWith("/command"))?.body, { cmd: "step_out" }, "fin → step_into 族(step_out)");
  assert.ok(outHtml(main).includes("resumed (step_out)"), "回声 resumed (step_out)");

  /* 歧义 / 未知(逐字;命令窗输出经 esc," 落为 &quot;) */
  await typeCmd(main, "d");
  assert.ok(outHtml(main).includes("Ambiguous command &quot;d&quot;: delete, detach, disable, down."),
    "歧义候选逐字");
  await typeCmd(main, "foo");
  assert.ok(outHtml(main).includes("Undefined command: &quot;foo&quot;. Try &quot;help&quot;."),
    "未知逐字");

  /* running 态发 c → The run is not paused.(SSE state 推进) */
  const es = ESStub.instances.at(-1);
  es.open();
  state.snapshot = { ...state.snapshot, state: "running", pause_point: null };
  es.emit("state", state.snapshot);
  await flush();
  requests.length = 0;
  await typeCmd(main, "c");
  assert.ok(outHtml(main).includes("The run is not paused."), "非 paused 人话逐字");
  assert.ok(!requests.some((r) => r.url.endsWith("/command")), "守卫本地拦,不出海");

  /* set args 非法 JSON 本地拦(不打后端) */
  await typeCmd(main, "set args {oops");
  assert.ok(outHtml(main).includes("Usage: set args &lt;json&gt;(JSON 解析错:"), "非法 JSON 本地拦");
  assert.ok(!requests.some((r) => r.url.endsWith("/modify")), "非法 JSON 不出海");
  /* running 态 set args → not paused(本地预检) */
  await typeCmd(main, 'set args {"a":1}');
  assert.ok(!requests.some((r) => r.url.endsWith("/modify")), "running 态 set args 不出海");

  /* until 诚实人话(REST 无 until 字段) */
  await typeCmd(main, "until 3");
  assert.ok(outHtml(main).includes("until 步数断点在 web 控制台不可用"), "until 诚实人话");

  /* run 收但指向 #/debug 首页(console 不做开会话表单) */
  await typeCmd(main, "run demo.fib");
  assert.ok(outHtml(main).includes("#/debug"), "run 人话指向调试首页");

  /* help / apropos 从命令表生成 */
  await typeCmd(main, "help");
  assert.ok(outHtml(main).includes("命令(GDB 方言;唯一前缀可缩写;裸 Enter 重复步进类命令):"),
    "help 表头");
  await typeCmd(main, "apropos 断点");
  assert.ok(outHtml(main).includes("break &lt;spec&gt;"), "apropos 命中");

  closeDebugConsole();
}

/* ══ 5. 裸 Enter 重复白名单 + ↑↓ 历史 ═══════════════════════════════════ */
{
  state.snapshot = SNAPSHOT(); // 块间复位(上一块推进过状态)
  const main = await openConsole();

  requests.length = 0;
  await typeCmd(main, "s"); // step_into
  assert.equal(requests.filter((r) => r.url.endsWith("/command")).length, 1, "s 出海一次");
  /* 裸 Enter:白名单重复上一命令 */
  await typeCmd(main, "");
  const cmds = requests.filter((r) => r.url.endsWith("/command"));
  assert.equal(cmds.length, 2, "裸 Enter 重复 s");
  assert.deepEqual(cmds[1].body, { cmd: "step_into" }, "重复的是 step_into");
  assert.ok(outHtml(main).includes("(adb) s"), "重复也回声");

  /* p(只读)不在白名单:裸 Enter 不再发 */
  requests.length = 0;
  await typeCmd(main, "p");
  assert.ok(outHtml(main).includes('&quot;code&quot;: &quot;print(1)&quot;') ||
    outHtml(main).includes('"code": "print(1)"'), "p 打印 payload");
  await typeCmd(main, "");
  assert.equal(requests.length, 0, "p 后裸 Enter 不重复(白名单外)");

  /* ↑↓ 命令历史 */
  const input = inputOf(main);
  input.trigger("keydown", { key: "ArrowUp" });
  assert.equal(input.value, "p", "↑ 上一条");
  input.trigger("keydown", { key: "ArrowUp" });
  assert.equal(input.value, "s", "↑ 再上一条");
  input.trigger("keydown", { key: "ArrowDown" });
  assert.equal(input.value, "p", "↓ 回一条");
  input.trigger("keydown", { key: "ArrowDown" });
  assert.equal(input.value, "", "↓ 到底回空行");

  closeDebugConsole();
}

/* ══ 6. kill 两段确认(逐字学 GDB)═══════════════════════════════════════ */
{
  state.snapshot = SNAPSHOT(); // 块间复位(上一块推进过状态)
  const main = await openConsole();

  requests.length = 0;
  await typeCmd(main, "kill");
  assert.ok(outHtml(main).includes("Kill the run being debugged? (y or n)"), "确认问句逐字");
  assert.ok(!requests.some((r) => r.url.endsWith("/command")), "第一击不出海");

  /* n:静默回到提示符(GDB 同款),不出海 */
  await typeCmd(main, "n");
  assert.ok(!requests.some((r) => r.url.endsWith("/command")), "n 不出海");

  /* 再问一次,y → POST stop + 回声 */
  await typeCmd(main, "kill");
  await typeCmd(main, "y");
  const stop = requests.find((r) => r.url.endsWith("/command"));
  assert.deepEqual(stop?.body, { cmd: "stop" }, "y → stop 出海");
  assert.ok(outHtml(main).includes("resumed (stop)"), "stop 回声");

  /* kill 后裸 Enter 不重复(危险命令;且确认已答) */
  requests.length = 0;
  await typeCmd(main, "");
  assert.equal(requests.length, 0, "kill 不属裸 Enter 白名单");

  closeDebugConsole();
}

/* ══ 7. 干预:set args / inject(回声逐字)════════════════════════════════ */
{
  state.snapshot = SNAPSHOT(); // 块间复位(上一块推进过状态)
  const main = await openConsole();

  /* paused @ pre:tool.call:set args 出海 */
  requests.length = 0;
  await typeCmd(main, 'set args {"code": "print(41)"}');
  const mod = requests.find((r) => r.url.endsWith("/modify"));
  assert.deepEqual(mod?.body, { patch: { code: "print(41)" } }, "modify 请求体");
  assert.ok(outHtml(main).includes("args patched: {&quot;code&quot;:&quot;print(41)&quot;}(提交即放行)"),
    "patched 回声逐字(提交即放行;引号经 esc)");

  /* inject */
  await typeCmd(main, "inject 请改用迭代实现");
  const inj = requests.find((r) => r.url.endsWith("/inject"));
  assert.deepEqual(inj?.body, { text: "请改用迭代实现" }, "inject 请求体");
  assert.ok(outHtml(main).includes("injected -&gt; frame f1 ()(注入即放行)"),
    "injected 回声逐字(注入即放行)");

  /* 停在 pre:step 时 set args → §6 语式本地拦 */
  state.snapshot = {
    ...state.snapshot, state: "paused", pause_point: structuredClone(PP_STEP_F2),
  };
  const es = ESStub.instances.at(-1);
  es.emit("state", state.snapshot);
  await flush();
  requests.length = 0;
  await typeCmd(main, 'set args {"code":"x"}');
  assert.ok(outHtml(main).includes("Cannot set args: not paused at pre:tool.call."),
    "停错点语式逐字");
  assert.ok(!requests.some((r) => r.url.endsWith("/modify")), "停错点不出海");

  closeDebugConsole();
}

/* ══ 8. SSE paused → ▶ 定位;run_end → 横幅 + 输入禁用;断线回退 ═══════════ */
{
  const main = await openConsole();
  const es = ESStub.instances.at(-1);
  es.open();
  assert.match(main.querySelector("#dbcBar").innerHTML, /data-state="ok"/, "open → 连接点 ok");

  /* paused(f2 pre:step):停止行 + ▶ 定位 + 选帧跟随 */
  state.snapshot = { ...state.snapshot, state: "paused", pause_point: structuredClone(PP_STEP_F2) };
  es.emit("paused", { session_id: "dbg-s1", pause_point: PP_STEP_F2 });
  await flush();
  await flush();
  await flush();
  const out = outHtml(main);
  assert.ok(out.includes("Step finished, pre:step, frame f2 (fib), step 1"),
    "SSE paused → 停止行逐字");
  assert.match(
    main.querySelector("#dbcTrace").innerHTML,
    /data-signal-index="19"[^>]*data-paused="true"/,
    "▶ 落最近可见前行(pre:step 不占行)");
  assert.match(
    main.querySelector("#dbcStack").innerHTML,
    /data-frame-n="0"[^>]*aria-selected="true"/, "选帧跟随 f2(#0)");

  /* 同一停点重进不重复打(ppReported 去重) */
  es.emit("state", state.snapshot);
  await flush();
  const count = (outHtml(main).match(/Step finished, pre:step, frame f2/g) ?? []).length;
  assert.equal(count, 1, "同一停点只打一次停止行");

  /* run_end:Run finished 行 + 终态横幅 + 输入禁用 + 流关闭 */
  state.snapshot = { ...state.snapshot, state: "detached", pause_point: null };
  es.emit("run_end", { session_id: "dbg-s1", run_id: "r1", status: "done" });
  await flush();
  await flush();
  await flush();
  assert.ok(outHtml(main).includes("Run finished: done"), "终态行逐字");
  assert.match(main.querySelector("#dbcBar").innerHTML, /run 已结束\(done\)/, "终态横幅");
  assert.equal(inputOf(main).disabled, true, "run_end 后输入禁用");
  assert.equal(es.closed, true, "run_end 后 SSE 关闭");
  requests.length = 0;
  await typeCmd(main, "c");
  assert.ok(!requests.some((r) => r.url.endsWith("/command")), "终态后命令不再出海");

  closeDebugConsole();

  /* 断线回退:SSE error → toast + 人话 + 连接点 down */
  state.snapshot = SNAPSHOT(); // 复位(run_end 子块落了 detached)
  const main2 = await openConsole();
  const es2 = ESStub.instances.at(-1);
  es2.error();
  await flush();
  assert.match(main2.querySelector("#dbcBar").innerHTML, /data-state="down"/, "断线 → 连接点 down");
  assert.ok(outHtml(main2).includes("调试实时连接中断,回退轮询"), "断线人话进命令窗");
  closeDebugConsole();

  /* 无 EventSource:全程轮询(连接点 poll,快照仍渲染) */
  const RealES = globalThis.EventSource;
  delete globalThis.EventSource;
  const main3 = await openConsole();
  assert.match(main3.querySelector("#dbcBar").innerHTML, /data-state="poll"/, "无 ES → poll 模式");
  assert.match(main3.querySelector("#dbcTrace").innerHTML, /dbg-trace-list/, "轨迹仍渲染");
  closeDebugConsole();
  globalThis.EventSource = RealES;
}

/* ══ 9. bt / frame / up / down / session / q ════════════════════════════ */
{
  state.snapshot = SNAPSHOT();
  const main = await openConsole();

  await typeCmd(main, "bt");
  let out = outHtml(main);
  assert.ok(out.includes("#0  fib (f2)"), "bt #0 栈顶");
  assert.ok(out.includes("#1  fib (f1) at pre:tool.call step 1"), "bt 暂停帧 at 后缀");

  /* frame 0 → 选帧回声 + 栈窗选中 */
  await typeCmd(main, "frame 0");
  assert.ok(outHtml(main).includes("#0  fib (f2)"), "frame 回声行");
  assert.match(
    main.querySelector("#dbcStack").innerHTML,
    /data-frame-n="0"[^>]*aria-selected="true"/, "frame 0 选中");
  /* 点击栈行 = frame N */
  const row = new StubEl("div");
  row.className = "dbc-frame";
  row.dataset.frameN = "1";
  assert.equal(consoleClick({ target: row }, null), true, "栈行点击命中");
  assert.ok(outHtml(main).includes("(adb) frame 1"), "点击 = frame N(回声)");
  assert.match(
    main.querySelector("#dbcStack").innerHTML,
    /data-frame-n="1"[^>]*aria-selected="true"/, "点击选中 #1");

  /* up/down 边界 */
  await typeCmd(main, "up");
  assert.ok(outHtml(main).includes("No frame number 2."), "up 越界人话");
  await typeCmd(main, "down");
  await typeCmd(main, "down");
  assert.ok(outHtml(main).includes("No frame number -1."), "down 越界人话");

  /* x messages / info frame / info args(帧检视进命令窗) */
  await typeCmd(main, "frame 0");
  await typeCmd(main, "x messages");
  out = outHtml(main);
  assert.ok(out.includes("[user] {&quot;n&quot;: 6}") || out.includes('[user] {"n": 6}'),
    "x messages 行");
  assert.ok(out.includes("tool_call system.python.exec"), "x messages tool_call 行");
  await typeCmd(main, "info frame");
  assert.ok(outHtml(main).includes("frame_id: f2"), "info frame");
  await typeCmd(main, "info args");
  assert.ok(outHtml(main).includes("&quot;n&quot;: 6") || outHtml(main).includes('"n": 6'),
    "info args");

  /* session 列表(本机账本,与 debug-home 同键) */
  storageMap.set("agent-os.debug.sessions",
    JSON.stringify([{ session_id: "dbg-s1", run_id: "r1", skill: "demo.fib" }]));
  await typeCmd(main, "session");
  assert.ok(outHtml(main).includes("dbg-s1"), "session 列表");

  /* q → 回 #/debug/<sid> 三栏视图(paused 给提示行) */
  await typeCmd(main, "q");
  assert.equal(locationStub.hash, "#/debug/dbg-s1", "q = 三栏视图回程");
  assert.ok(outHtml(main).includes("(退出不 detach;paused 会话保留给下次连接)"), "q 提示行");

  closeDebugConsole();
}

console.log("smoke-debug-console.test.mjs: all assertions passed");

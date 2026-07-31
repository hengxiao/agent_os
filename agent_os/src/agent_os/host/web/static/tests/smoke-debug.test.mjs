/* P4 DOM-stub 冒烟测试(Agent OS Debugger Web 调试台;计划 §4):
   1) 调试首页(#/debug):表单渲染(schema 骨架预填)+ 启动前断点预填 →
      POST /api/debug/sessions(breakpoints 数组随请求发出,启动即断)→ 跳调试台;
   2) 调试台渲染(paused):控制条(状态徽标 + 暂停点摘要 + 五命令可用)、调用栈
      (暂停帧高亮)、轨迹(断点 gutter + 暂停行 data-paused + ▶)、断点列表、
      检视器(暂停点 payload + 帧 messages + Modify/Inject 表单可用);
   3) 命令按钮禁用态:SSE state(running)→ 五命令 + 干预表单全禁用;
   4) 断点增删 DOM:表单添加 / 列表删除 / 轨迹 gutter 切换(有则删、无则增);
   5) 干预与命令请求:command/modify/inject 的 POST body 形状;
   6) SSE 事件 → DOM:bp_hit(hits 刷新)/ paused(滚动定位暂停行)/ resumed /
      run_end(结束横幅 + 全禁用);
   7) 首页活跃会话列表:localStorage 已知会话 × GET 快照;「结束」DELETE;
   8) SSE 不可用回退:无 EventSource 时连接点转 poll(轮询模式,快照仍渲染)。
   运行:node static/tests/smoke-debug.test.mjs(DOM 用 dom-stub.mjs,无浏览器)。 */

import assert from "node:assert/strict";
import { StubEl, makeDocument } from "./dom-stub.mjs";
import { makeMessages, makeSignals } from "./fixtures.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub:document / location / history / localStorage / fetch ── */
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

/* EventSource stub:实例入列,测试侧经 emit/open/error 驱动(照 workbench 消费面) */
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

/* ── 数据夹具(可变 state:测试逐段推进会话状态)────────────────── */
const SKILLS = [{ name: "demo.fib", version: "1.0.0", kind: "prompt", description: "斐波那契演示" }];
const SKILL_FIB = {
  name: "demo.fib",
  description: "Use when 计算斐波那契数列",
  inputs: { type: "object", properties: { n: { type: "integer", minimum: 1 } }, required: ["n"] },
};
const BP1 = { id: "bp1", kind: "tool_call", match: "system.python.exec", enabled: true, hits: 1 };
const PP_TOOL = {
  signal: "pre:tool.call",
  run_id: "r1",
  frame_id: "f1",
  depth: 1,
  step: 1,
  tool: "system.python.exec",
  skill: null,
  payload: { frame_id: "f1", depth: 1, tool: "system.python.exec", args: { code: "print(1)" } },
  breakpoint_ids: ["bp1"],
  reason: "breakpoint",
};
const PP_STEP_F2 = {
  signal: "pre:step",
  run_id: "r1",
  frame_id: "f2",
  depth: 2,
  step: 1,
  tool: null,
  skill: "local:fib@1.0.0",
  payload: { frame_id: "f2", depth: 2, step: 1 },
  breakpoint_ids: [],
  reason: "step:into",
};
const state = {
  snapshot: {
    session_id: "dbg-s1",
    run_id: "r1",
    state: "paused",
    pause_point: PP_TOOL,
    breakpoints: [structuredClone(BP1)],
    rerunnable: true, // live 会话带 origin → 控制条出「⟳ 重新运行」
    frame_stack: [
      { frame_id: "f1", skill: "local:fib@1.0.0", depth: 1 },
      { frame_id: "f2", skill: "local:fib@1.0.0", depth: 2 },
    ],
  },
  signals: makeSignals(),
};
const FRAME = (fid) => ({
  frame_id: fid,
  skill: "local:fib@1.0.0",
  status: "running",
  usage: { steps: 1, cost: 0 },
  messages: makeMessages(),
});

const requests = []; // { method, url, body } 请求日志(断言 POST/DELETE 形状)
let bpSeq = 1; // 断点 id 自增(与内核 uuid 语义对齐:唯一;length+1 会撞号)
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
  const notFound = () => ({
    ok: false,
    status: 404,
    json: async () => ({ detail: "not found" }),
  });
  if (u === "/api/skills") return reply(SKILLS);
  if (u === "/api/skills/demo.fib") return reply(SKILL_FIB);
  if (u === "/api/debug/sessions" && method === "POST") {
    return reply({ session_id: "dbg-s1", run_id: "r1" });
  }
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
  if (u === "/api/debug/sessions/dbg-gone") return notFound();
  throw new Error(`未 stub 的请求: ${method} ${u}`);
};

const { store } = await import("../js/store.js");
const { closeDebugHome, openDebugHome } = await import("../js/components/debug-home.js");
const {
  closeDebugView,
  debugChange,
  debugClick,
  openDebugView,
  renderDebugTrace,
} = await import("../js/components/debug-view.js");
const { renderBreakpoints, renderBpForm } = await import("../js/components/breakpoint-list.js");

const setRoute = (route) => store.set({ route });

/* 事件委托驱动(照 smoke-d5 clickAction):fabricate 带 dataset 的目标元素 */
const clickAction = (dataset) => {
  const act = new StubEl("button");
  Object.assign(act.dataset, dataset);
  return debugClick({ target: act }, act);
};

/* ══ 1. 调试首页:表单 + 启动前断点 → POST(启动即断)══════════════ */
{
  setRoute({ name: "debug-home", runId: null });
  const main = new StubEl("main");
  openDebugHome(main);
  await flush();
  await flush(); // skills 列表 + manifest 到位

  const newCard = main.querySelector("#dhNew");
  assert.ok(newCard, "新会话卡片渲染");
  const skillSel = newCard.querySelector(".dh-skill");
  assert.equal(skillSel.disabled, false, "技能下拉就绪");
  assert.equal(skillSel.children.length, 1, "技能选项填充");
  assert.equal(skillSel.value, "demo.fib");
  const inputArea = newCard.querySelector(".dh-input");
  assert.match(inputArea.value, /"n": 1/, "input 按 schema 骨架预填");
  const submit = newCard.querySelector(".btn-primary");
  assert.equal(submit.disabled, false, "骨架合法即可提交");

  /* 活跃会话空态(dom-stub:appendChild 不进 innerHTML 串,按子树断言) */
  const sessCard = main.querySelector("#dhSessions");
  assert.ok(
    sessCard.children.some((c) => String(c.innerHTML).includes("还没有调试会话")),
    "会话空态引导");

  /* 启动前断点:＋两行(tool_call 带 glob + step) */
  const bpHead = newCard.children.find((c) => c.classList.contains("dh-bp-head"));
  const bpAdd = bpHead.children.find((c) => c.classList.contains("btn-mini"));
  bpAdd.trigger("click");
  bpAdd.trigger("click");
  const rowsBox = newCard.querySelector(".dh-bp-rows");
  assert.equal(rowsBox.children.length, 2, "两行断点预填");
  const row1 = rowsBox.children[0];
  row1.querySelector(".dh-bp-kind").value = "tool_call";
  row1.querySelector(".dh-bp-kind").trigger("change"); // kind 切换 → match 解禁
  assert.equal(row1.querySelector(".dh-bp-match").disabled, false, "tool_call 的 match 可编辑");
  row1.querySelector(".dh-bp-match").value = "system.*";
  const row2 = rowsBox.children[1];
  assert.equal(row2.querySelector(".dh-bp-kind").value, "step");
  assert.equal(row2.querySelector(".dh-bp-match").disabled, true, "step 忽略 match(禁用)");

  /* 回归:真实浏览器里 children 是 HTMLCollection(可迭代、有 length/索引,但无 .map)——
     collectBreakpoints 必须展开再 map,否则提交即抛 TypeError:POST 不发、按钮卡「启动中…」 */
  const rowsArr = [...rowsBox.children];
  Object.defineProperty(rowsBox, "children", {
    configurable: true,
    get: () => {
      const like = { length: rowsArr.length, item: (i) => rowsArr[i] ?? null };
      rowsArr.forEach((r, i) => { like[i] = r; });
      like[Symbol.iterator] = function* () { yield* rowsArr; };
      return like;
    },
  });

  requests.length = 0;
  submit.trigger("click");
  await flush();
  await flush();
  const created = requests.find(
    (r) => r.method === "POST" && r.url === "/api/debug/sessions");
  assert.ok(created, "POST /api/debug/sessions 发出");
  assert.deepEqual(created.body, {
    skill: "demo.fib",
    input: { n: 1 },
    breakpoints: [
      { kind: "tool_call", match: "system.*" },
      { kind: "step", match: "*" },
    ],
  }, "启动前断点随会话创建请求发出(启动即断)");
  assert.equal(locationStub.hash, "#/debug/dbg-s1", "创建成功跳调试台");
  const known = JSON.parse(storageMap.get("agent-os.debug.sessions"));
  assert.equal(known[0].session_id, "dbg-s1", "会话记入 localStorage");
  assert.equal(known[0].run_id, "r1");
  closeDebugHome();
}

/* ══ 2. 调试台渲染(paused)════════════════════════════════════ */
{
  setRoute({ name: "debug-session", sessionId: "dbg-s1", runId: null });
  const main = new StubEl("main");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush(); // 快照 + signals → ready → 帧上下文懒加载

  const bar = main.querySelector("#dbgBar");
  assert.match(bar.innerHTML, /Agent OS Debug/, "控制条品牌");
  assert.match(bar.innerHTML, /data-status="paused"/, "会话状态徽标 paused");
  assert.match(bar.innerHTML, /已暂停/, "状态双编码(色 + 文字)");
  assert.match(bar.innerHTML, /paused @ pre:tool\.call/, "暂停点摘要");
  assert.match(bar.innerHTML, /f-f1/, "暂停帧短码");
  for (const cmd of ["continue", "step_into", "step_over", "step_out", "stop"]) {
    assert.match(bar.innerHTML, new RegExp(`data-cmd="${cmd}"`), `命令按钮 ${cmd}`);
  }
  assert.equal(
    (bar.innerHTML.match(/class="btn dbg-cmd"/g) ?? []).length, 5, "五个命令按钮");
  assert.ok(!/dbg-cmd"[^>]*disabled/.test(bar.innerHTML), "paused 时命令可用");
  assert.match(bar.innerHTML, /▶ Continue|⇥ Into|⇢ Over|↥ Out|■ Stop/, "按钮图标语言");
  assert.match(bar.innerHTML, /data-state="poll"|data-state="ok"/, "连接状态点");
  assert.match(bar.innerHTML, /data-action="dbg-rerun"/, "rerunable 会话出「⟳ 重新运行」");

  /* 调用栈:栈顶在前,暂停帧高亮 */
  const stack = main.querySelector("#dbgStack");
  assert.match(stack.innerHTML, /调用栈/, "左栏标题");
  assert.match(stack.innerHTML, /data-frame-id="f2"/, "栈帧 f2");
  assert.match(stack.innerHTML, /data-frame-id="f1"[^>]*data-current="true"/, "暂停帧高亮");
  assert.ok(
    stack.innerHTML.indexOf('data-frame-id="f2"') < stack.innerHTML.indexOf('data-frame-id="f1"'),
    "栈顶帧在前");

  /* 执行轨迹:断点 gutter + 暂停行 */
  const trace = main.querySelector("#dbgTrace");
  assert.match(trace.innerHTML, /dbg-trace-list/, "轨迹列表");
  assert.match(
    trace.innerHTML,
    /class="dbg-gutter" data-action="dbg-gutter" data-kind="tool_call" data-match="system\.python\.exec" data-bp-id="bp1" data-on="true"/,
    "tool 行 gutter:已有断点(红点)");
  assert.match(trace.innerHTML, /aria-pressed="true"/, "断点双编码(aria-pressed)");
  assert.match(
    trace.innerHTML,
    /data-kind="skill_invoke" data-match="local:fib@1\.0\.0" data-on="false"/,
    "call 行 gutter:无断点(可打)");
  assert.match(trace.innerHTML, /data-paused="true"/, "暂停行高亮标记");
  assert.match(
    trace.innerHTML,
    /data-signal-index="6" data-kind="tool"[^>]*data-paused="true"/,
    "暂停行 = pre:tool.call(system.python.exec) 合并行");
  assert.match(trace.innerHTML, /<span class="tr-mark" aria-hidden="true">▶<\/span>/, "暂停行 ▶");

  /* 断点列表:kind/match/hits/enabled 勾选 + 删除 + 新增表单 */
  const bps = main.querySelector("#dbgBps");
  assert.match(bps.innerHTML, /data-bp-id="bp1"/, "断点行");
  assert.match(bps.innerHTML, /system\.python\.exec/, "match glob");
  assert.match(bps.innerHTML, /×1/, "hits 计数");
  assert.match(bps.innerHTML, /dbg-bp-enabled" disabled checked/, "enabled 勾选(只读)");
  assert.match(bps.innerHTML, /data-action="dbg-bp-del"/, "删除钮");
  assert.match(bps.innerHTML, /class="input dbg-bp-add-kind"/, "新增表单 kind 下拉");
  assert.match(bps.innerHTML, /class="input mono dbg-bp-add-match"[^>]*disabled/, "step 默认 match 禁用");

  /* 检视器:暂停点 payload + 帧 messages + 干预表单 */
  const insp = main.querySelector("#dbgInsp");
  assert.match(insp.innerHTML, /暂停点/, "暂停点区块");
  assert.match(insp.innerHTML, /print\(1\)/, "暂停点 payload(args)");
  await flush();
  await flush(); // 帧上下文到位重绘
  const insp2 = main.querySelector("#dbgInsp");
  assert.match(insp2.innerHTML, /msg-list/, "帧 messages 渲染(message-card 复用)");
  assert.match(insp2.innerHTML, /vetoed — 调用被 sidecar 否决/, "成对渲染 veto 卡");
  const patchEl = insp2.querySelector(".dbg-mod-patch");
  assert.match(insp2.innerHTML, /aria-label="工具参数 patch\(JSON\)">/,
    "pre:tool.call 暂停:Modify 可用");
  assert.match(insp2.innerHTML, /&quot;code&quot;: &quot;print\(1\)&quot;/,
    "Modify 预填当前 args(textarea 内容;stub 中 .value 只模拟输入,预填走 HTML 串)");
  void patchEl;
  assert.match(insp2.innerHTML, /data-action="dbg-modify"(?![^>]*disabled)/, "Modify 钮可用");
  assert.match(insp2.innerHTML, /aria-label="注入消息文本">/, "paused:Inject 可用");

  closeDebugView();
}

/* ══ 3. 命令按钮禁用态(SSE state → running)═════════════════════ */
{
  setRoute({ name: "debug-session", sessionId: "dbg-s1", runId: null });
  const main = new StubEl("main");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush();
  const es = ESStub.instances.at(-1);
  assert.ok(es, "SSE 已连接");
  assert.match(es.url, /\/api\/debug\/sessions\/dbg-s1\/stream/, "SSE 流地址");
  es.open();
  assert.match(main.querySelector("#dbgBar").innerHTML, /data-state="ok"/, "open → 连接点 ok");

  state.snapshot = { ...state.snapshot, state: "running", pause_point: null };
  es.emit("state", state.snapshot);
  await flush();
  const bar = main.querySelector("#dbgBar");
  assert.match(bar.innerHTML, /data-status="running"/, "状态徽标转 running");
  assert.equal(
    (bar.innerHTML.match(/dbg-cmd"[^>]*disabled/g) ?? []).length, 5,
    "running:五命令全禁用");
  const insp = main.querySelector("#dbgInsp");
  assert.match(insp.innerHTML, /aria-label="工具参数 patch\(JSON\)" disabled>/, "running:Modify 禁用");
  assert.match(insp.innerHTML, /aria-label="注入消息文本" disabled>/, "running:Inject 禁用");
  assert.doesNotMatch(main.querySelector("#dbgTrace").innerHTML, /data-paused="true"/,
    "running:无暂停行");

  closeDebugView();
}

/* ══ 4. 断点增删 DOM(表单添加 / 列表删除 / gutter 切换)═══════════ */
{
  setRoute({ name: "debug-session", sessionId: "dbg-s1", runId: null });
  const main = new StubEl("main");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush();

  /* 表单添加:kind 下拉 tool_call(change 解禁 match)+ glob
     (dom-stub:区域元素不带 class,手动补上让 closest 命中——真实浏览器自带) */
  const bps = main.querySelector("#dbgBps");
  const kindEl = bps.querySelector(".dbg-bp-add-kind");
  kindEl.className = "input dbg-bp-add-kind";
  kindEl.value = "tool_call";
  assert.equal(debugChange({ target: kindEl }), true, "change 委托命中");
  const matchEl = bps.querySelector(".dbg-bp-add-match");
  assert.equal(matchEl.disabled, false, "kind 切换后 match 解禁");
  kindEl.value = "step";
  debugChange({ target: kindEl });
  assert.equal(matchEl.disabled, true, "step 时 match 禁用");
  kindEl.value = "tool_call";
  debugChange({ target: kindEl });
  matchEl.value = "system.fs.*";
  requests.length = 0;
  assert.equal(clickAction({ action: "dbg-bp-add" }), true, "点击添加断点");
  await flush();
  await flush();
  const added = requests.find((r) => r.method === "POST" && r.url.endsWith("/breakpoints"));
  assert.deepEqual(added.body, { kind: "tool_call", match: "system.fs.*" }, "添加请求体");
  assert.match(main.querySelector("#dbgBps").innerHTML, /system\.fs\.\*/, "新断点上列表");

  /* 列表删除 */
  requests.length = 0;
  assert.equal(clickAction({ action: "dbg-bp-del", bpId: "bp1" }), true, "点击删除 bp1");
  await flush();
  await flush();
  const del = requests.find((r) => r.method === "DELETE" && r.url.includes("/breakpoints/"));
  assert.match(del.url, /\/breakpoints\/bp1$/, "DELETE 地址");
  assert.doesNotMatch(main.querySelector("#dbgBps").innerHTML, /data-bp-id="bp1"/, "bp1 已下榜");
  assert.doesNotMatch(
    main.querySelector("#dbgTrace").innerHTML,
    /data-match="system\.python\.exec" data-bp-id/,
    "gutter 红点同步消失");

  /* gutter 切换:无断点的 call 行 → 新增 skill_invoke 断点 */
  requests.length = 0;
  assert.equal(
    clickAction({ action: "dbg-gutter", kind: "skill_invoke", match: "local:fib@1.0.0" }),
    true, "gutter 点击(无断点)");
  await flush();
  await flush();
  const gAdd = requests.find((r) => r.method === "POST" && r.url.endsWith("/breakpoints"));
  assert.deepEqual(gAdd.body, { kind: "skill_invoke", match: "local:fib@1.0.0" }, "gutter 新增");

  /* gutter 切换:已有断点 → 删除 */
  requests.length = 0;
  const bpId = state.snapshot.breakpoints.find((b) => b.kind === "skill_invoke")?.id;
  assert.ok(bpId, "skill_invoke 断点已在快照");
  assert.equal(
    clickAction({ action: "dbg-gutter", kind: "skill_invoke", match: "local:fib@1.0.0", bpId }),
    true, "gutter 点击(已有断点)");
  await flush();
  await flush();
  assert.ok(
    requests.some((r) => r.method === "DELETE" && r.url.endsWith(`/breakpoints/${bpId}`)),
    "gutter 删除");

  closeDebugView();
}

/* ══ 5. 干预与命令请求形状 ═══════════════════════════════════ */
{
  setRoute({ name: "debug-session", sessionId: "dbg-s1", runId: null });
  const main = new StubEl("main");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush();
  await flush();

  requests.length = 0;
  assert.equal(clickAction({ action: "dbg-cmd", cmd: "step_into" }), true, "点击 ⇥ Into");
  await flush();
  const cmd = requests.find((r) => r.url.endsWith("/command"));
  assert.deepEqual(cmd.body, { cmd: "step_into" }, "command 请求体");

  /* Modify:patch JSON → POST /modify */
  const insp = main.querySelector("#dbgInsp");
  insp.querySelector(".dbg-mod-patch").value = '{"code": "print(41)"}';
  assert.equal(clickAction({ action: "dbg-modify" }), true, "点击 Modify 并放行");
  await flush();
  const mod = requests.find((r) => r.url.endsWith("/modify"));
  assert.deepEqual(mod.body, { patch: { code: "print(41)" } }, "modify 请求体");

  /* 非法 JSON patch:不发请求 */
  requests.length = 0;
  insp.querySelector(".dbg-mod-patch").value = "{oops";
  assert.equal(clickAction({ action: "dbg-modify" }), true);
  await flush();
  assert.ok(!requests.some((r) => r.url.endsWith("/modify")), "非法 JSON 拦截");

  /* Inject */
  insp.querySelector(".dbg-inj-text").value = "调试注入的补充说明";
  assert.equal(clickAction({ action: "dbg-inject" }), true, "点击 Inject 并放行");
  await flush();
  const inj = requests.find((r) => r.url.endsWith("/inject"));
  assert.deepEqual(inj.body, { text: "调试注入的补充说明" }, "inject 请求体(frame_id 缺省=暂停帧)");

  /* 重新运行(⟳):POST /rerun → 跳新会话 */
  requests.length = 0;
  const hashBefore = locationStub.hash;
  assert.equal(clickAction({ action: "dbg-rerun" }), true, "点击 ⟳ 重新运行");
  await flush();
  const rerun = requests.find((r) => r.method === "POST" && r.url.endsWith("/rerun"));
  assert.ok(rerun, "rerun 请求发出");
  assert.equal(locationStub.hash, "#/debug/dbg-s2", "跳新会话");
  locationStub.hash = hashBefore; // 复位:后续段落仍操作 dbg-s1

  closeDebugView();
}

/* ══ 6. SSE 事件 → DOM(bp_hit / paused / resumed / run_end)═════ */
{
  setRoute({ name: "debug-session", sessionId: "dbg-s1", runId: null });
  const main = new StubEl("main");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush();
  const es = ESStub.instances.at(-1);
  es.open();

  /* bp_hit:hits 原地刷新(bp1 已在 §4 删除;针对存活的 bp2=system.fs.*) */
  es.emit("bp_hit", { session_id: "dbg-s1", breakpoint_id: "bp2", kind: "tool_call",
    match: "system.fs.*", hits: 2 });
  await flush();
  assert.match(main.querySelector("#dbgBps").innerHTML, /×2/, "bp_hit → hits 刷新");

  /* paused:快照推进到 f2 pre:step(子帧),轨迹滚动定位暂停行 */
  state.snapshot = {
    ...state.snapshot,
    state: "paused",
    pause_point: PP_STEP_F2,
    breakpoints: [structuredClone(BP1)],
  };
  es.emit("paused", { session_id: "dbg-s1", pause_point: PP_STEP_F2 });
  await flush();
  await flush();
  await flush(); // 快照 + signals 刷新 + 帧上下文
  const bar = main.querySelector("#dbgBar");
  assert.match(bar.innerHTML, /paused @ pre:step/, "paused 事件 → 暂停点摘要更新");
  assert.match(bar.innerHTML, /step:into/, "暂停原因");
  const trace = main.querySelector("#dbgTrace");
  assert.match(
    trace.innerHTML,
    /data-signal-index="19"[^>]*data-paused="true"/,
    "暂停行 = f2 call 行(pre:step 不占行,落最近前行)");
  const stack = main.querySelector("#dbgStack");
  assert.match(stack.innerHTML, /data-frame-id="f2"[^>]*data-current="true"/, "暂停帧切换到 f2");
  const insp = main.querySelector("#dbgInsp");
  assert.match(insp.innerHTML, /aria-label="工具参数 patch\(JSON\)" disabled>/,
    "pre:step 暂停:Modify 禁用(仅 pre:tool.call 可用)");
  assert.match(insp.innerHTML, /aria-label="注入消息文本">/, "Inject 仍可用");

  /* resumed:命令按钮回禁用,暂停行消失 */
  es.emit("resumed", { session_id: "dbg-s1" });
  await flush();
  const bar2 = main.querySelector("#dbgBar");
  assert.match(bar2.innerHTML, /data-status="running"/, "resumed → running");
  assert.equal((bar2.innerHTML.match(/dbg-cmd"[^>]*disabled/g) ?? []).length, 5, "恢复后全禁用");

  /* run_end:结束横幅 + 连接点熄灭 + 流关闭 */
  state.snapshot = { ...state.snapshot, state: "detached", pause_point: null };
  es.emit("run_end", { session_id: "dbg-s1", run_id: "r1", status: "done" });
  await flush();
  await flush();
  await flush();
  assert.equal(es.closed, true, "run_end 后 SSE 关闭");
  const html = main.querySelector("#dbgBar").innerHTML;
  assert.match(html, /run 已结束\(done\)/, "结束横幅");
  assert.match(html, /data-tone="done"/, "done 绿横幅");
  assert.match(html, /data-state="off"/, "连接点熄灭");
  assert.match(html, /已结束 · done/, "状态徽标带终态");
  assert.match(html, /#\/runs\/r1/, "横幅链接到 run 详情");

  closeDebugView();
}

/* ══ 7. 首页活跃会话列表(已知会话 × 快照;「结束」DELETE)══════════ */
{
  setRoute({ name: "debug-home", runId: null });
  state.snapshot = { ...state.snapshot, state: "paused", pause_point: PP_TOOL };
  const main = new StubEl("main");
  openDebugHome(main);
  await flush();
  await flush();
  await flush(); // skills + 会话快照
  const card = main.querySelector("#dhSessions");
  const row = card.children.find((c) => c.classList.contains("dh-sess"));
  assert.ok(row, "已知会话上列表");
  assert.equal(row.dataset.sessionId, "dbg-s1");
  assert.match(row.querySelector(".dh-sess-pill").innerHTML, /data-status="paused"/, "会话状态");
  assert.match(row.querySelector(".dh-sess-label").textContent, /fib/, "技能名");
  const open = row.children.find((c) => c.textContent === "打开");
  assert.equal(open.href, "#/debug/dbg-s1", "打开链接");
  requests.length = 0;
  const end = row.children.find((c) => c.textContent === "结束");
  end.trigger("click");
  await flush();
  await flush();
  assert.ok(
    requests.some((r) => r.method === "DELETE" && r.url === "/api/debug/sessions/dbg-s1"),
    "结束 = DELETE 会话(detach 放行)");
  assert.equal(storageMap.get("agent-os.debug.sessions"), "[]", "结束后摘除记录");
  closeDebugHome();
}

/* ══ 8. SSE 不可用回退(无 EventSource → poll 模式)═══════════════ */
{
  const RealES = globalThis.EventSource;
  delete globalThis.EventSource;
  setRoute({ name: "debug-session", sessionId: "dbg-s1", runId: null });
  const main = new StubEl("main");
  openDebugView(main, "dbg-s1");
  await flush();
  await flush();
  await flush();
  assert.match(
    main.querySelector("#dbgBar").innerHTML, /data-state="poll"/,
    "无 EventSource:连接点 poll(回退 2s 轮询快照)");
  assert.match(main.querySelector("#dbgTrace").innerHTML, /dbg-trace-list/, "轨迹仍渲染");
  closeDebugView();
  globalThis.EventSource = RealES;
}

/* ── 纯函数面:断点列表渲染 / gutter 目标 / 轨迹渲染 ── */
{
  assert.match(renderBreakpoints([BP1]), /data-bp-id="bp1"/, "bp 行渲染");
  assert.match(renderBreakpoints([]), /还没有断点/, "空断点引导");
  assert.match(renderBpForm(), /dbg-bp-add-kind/, "新增表单渲染");
  const html = renderDebugTrace(
    [{ line: 1, kind: "tool", depth: 1, label: "t", detail: "", durMs: null,
      frameId: "f1", sigIndex: 0, sigEnd: 0, step: 1, payload: { pre: { tool: "t" } },
      ts: 0, names: "pre:tool.call" }],
    { breakpoints: [{ kind: "tool_call", match: "t", enabled: true, id: "bx", hits: 1 }],
      pausedLine: 1 });
  assert.match(html, /data-on="true"/, "纯渲染:gutter 红点");
  assert.match(html, /data-paused="true"/, "纯渲染:暂停行");
  assert.equal(renderDebugTrace([], {}), "", "空轨迹");
}

console.log("smoke-debug.test.mjs: all assertions passed");

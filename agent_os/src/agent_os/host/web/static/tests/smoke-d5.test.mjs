/* D5 DOM-stub 冒烟测试(docs/WEB-UI.md §4.4/§4.5/§5/§6.3):
   1) 键盘模块:installGlobalKeys 分发(j/k/gg/G/⌘J/⌘K)//输入控件与浮层抑制;
   2) RCA 动线:异常 run 打开 → Banner(定位 ⌘J + Resume)→ 点定位 → 三栏到位
      (selection source "rca" / 时间线聚焦行 / 检视器 veto 归因卡 + rca-target 脉冲);
   3) Usage 折叠栏:展开懒加载 → 表格 + 标题行合计 → cost 列点击排序;
   4) ⌘K 命令条:⌘K 打开(toggle)→ fuzzy 过滤 → Enter 执行 → 关闭;
   5) 时间线窗口化:700 信号只渲染视窗子集 + 占位行,滚动后窗口增量替换。
   运行:node static/tests/smoke-d5.test.mjs(DOM 用 dom-stub.mjs,无浏览器)。 */

import assert from "node:assert/strict";
import { StubEl, makeDocument } from "./dom-stub.mjs";
import { makeMessages, makeSignals } from "./fixtures.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub:document / location / history / fetch(先于被测模块 import)── */
const doc = makeDocument();
const toastStack = new StubEl("div");
toastStack.setAttribute("id", "toastStack");
doc.body.appendChild(toastStack);
doc.querySelector = (sel) => doc.body.querySelector(sel);
const locationStub = { hash: "" };
const historyStub = { calls: [], replaceState(a, b, url) { this.calls.push(String(url)); } };

const SIGNALS = makeSignals();
const DETAIL_FAIL = {
  run_id: "r-fail",
  status: "failed",
  error: "ToolGuard: 禁止危险命令",
  started_at: new Date().toISOString(),
  result: null,
  usage: { steps: 5, cost: 0.8 },
  config: { max_steps: 200, max_cost: 2 },
  frames: [
    { frame_id: "f1", skill: "local:fib@1.0.0", depth: 1, status: "done", usage: { steps: 3, cost: 0.3 } },
    { frame_id: "f2", skill: "local:fib@1.0.0", depth: 2, status: "failed", usage: { steps: 2, cost: 0.5 } },
  ],
};
const RCA_FAIL = {
  status: "failed",
  first_error: {
    kind: "vetoed",
    frame_id: "f2",
    skill: "local:fib@1.0.0",
    call: { name: "system.shell.exec", args: { command: "rm -rf /" } },
    message: "ToolGuard: 禁止危险命令 rm -rf",
  },
};
const FRAME_F2 = {
  frame_id: "f2",
  skill: "local:fib@1.0.0",
  status: "failed",
  usage: { steps: 2, cost: 0.5 },
  messages: makeMessages(),
};
const USAGE = {
  run: { steps: 5, prompt_tokens: 160, completion_tokens: 60, cache_read_tokens: 14,
    cache_write_tokens: 13, thinking_tokens: 10, cost: 0.8 },
  frames: [
    { frame_id: "f1", skill: "local:fib@1.0.0", depth: 1, status: "done", steps: 3,
      prompt_tokens: 100, completion_tokens: 40, cache_read_tokens: 10,
      cache_write_tokens: 5, thinking_tokens: 7, cost: 0.3 },
    { frame_id: "f2", skill: "local:fib@1.0.0", depth: 2, status: "failed", steps: 2,
      prompt_tokens: 60, completion_tokens: 20, cache_read_tokens: 4,
      cache_write_tokens: 8, thinking_tokens: 3, cost: 0.5 },
  ],
};
const LONG_SIGNALS = Array.from({ length: 1400 }, (_, i) => ({
  v: 1,
  type: "signal",
  name: i % 2 ? "post:tool.call" : "pre:tool.call",
  run_id: "r-long",
  frame_id: "f1",
  ts: i,
  payload: { tool: "t", ok: true },
}));
const DETAIL_LONG = {
  run_id: "r-long",
  status: "done",
  started_at: new Date().toISOString(),
  result: { ok: true },
  usage: { steps: 350, cost: 1.2 },
  config: {},
  frames: [
    { frame_id: "f1", skill: "local:fib@1.0.0", depth: 1, status: "done", usage: { steps: 350, cost: 1.2 } },
  ],
};

globalThis.document = doc;
globalThis.location = locationStub;
globalThis.history = historyStub;
globalThis.fetch = async (path) => {
  const url = String(path);
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (url === "/api/runs/r-fail") return reply(DETAIL_FAIL);
  if (url === "/api/runs/r-fail/signals") return reply(SIGNALS);
  if (url === "/api/runs/r-fail/rca") return reply(RCA_FAIL);
  if (url === "/api/runs/r-fail/frames/f2") return reply(FRAME_F2);
  if (url === "/api/runs/r-fail/frames/f1") return reply({ ...FRAME_F2, frame_id: "f1", status: "done" });
  if (url === "/api/runs/r-fail/usage") return reply(USAGE);
  if (url === "/api/runs/r-long") return reply(DETAIL_LONG);
  if (url === "/api/runs/r-long/signals") return reply(LONG_SIGNALS);
  if (url === "/api/runs/r-long/usage") return reply({ run: {}, frames: [] });
  if (url === "/api/skills") return reply([{ name: "fib", version: "1.0.0", kind: "prompt" }]);
  throw new Error(`未 stub 的请求: GET ${url}`);
};

const { store } = await import("../js/store.js");
const {
  closeWorkbench,
  openWorkbench,
  wbJumpFirstError,
  wbNavEdge,
  wbNavSignal,
  workbenchClick,
} = await import("../js/workbench.js");
const { installGlobalKeys, isEditableTarget } = await import("../js/shortcuts.js");
const { COMMANDS, openCommandPalette } = await import("../js/components/command-palette.js");

const clickAction = (dataset) => {
  const act = new StubEl("button");
  Object.assign(act.dataset, dataset);
  return workbenchClick({ target: act }, act);
};

/* ══ 1. 键盘模块(分发与抑制)═══════════════════════════════ */
{
  assert.equal(isEditableTarget(new StubEl("input")), true);
  assert.equal(isEditableTarget(new StubEl("textarea")), true);
  assert.equal(isEditableTarget(new StubEl("div")), false);
  assert.equal(isEditableTarget(null), false);

  const seen = [];
  const main = new StubEl("main"); // 占位的页面容器(本段不打开 workbench)
  void main;
  let overlayFlag = false;
  const uninstall = installGlobalKeys(
    {
      onPalette: () => seen.push("palette"),
      onHelp: () => seen.push("help"),
      onSearchFocus: () => seen.push("search"),
      onSignalStep: (d) => seen.push(`step:${d}`),
      onSignalEdge: (w) => seen.push(`edge:${w}`),
      onJumpError: () => seen.push("jump"),
    },
    { doc, hasOverlay: () => overlayFlag });

  const pd = () => {};
  doc.trigger("keydown", { key: "j", preventDefault: pd });
  doc.trigger("keydown", { key: "k", preventDefault: pd });
  doc.trigger("keydown", { key: "g", preventDefault: pd });
  doc.trigger("keydown", { key: "g", preventDefault: pd }); // gg 连击
  doc.trigger("keydown", { key: "G", preventDefault: pd });
  doc.trigger("keydown", { key: "/", preventDefault: pd });
  doc.trigger("keydown", { key: "?", preventDefault: pd });
  doc.trigger("keydown", { key: "k", metaKey: true, preventDefault: pd });
  doc.trigger("keydown", { key: "j", metaKey: true, preventDefault: pd });
  assert.deepEqual(seen, [
    "step:1", "step:-1", "edge:first", "edge:last", "search", "help", "palette", "jump",
  ], "全部按键正确分发");

  /* 输入控件抑制:除 Esc 与 ⌘K 外不生效(⌘J 也不生效) */
  seen.length = 0;
  const inputEl = new StubEl("input");
  doc.trigger("keydown", { key: "j", target: inputEl, preventDefault: pd });
  doc.trigger("keydown", { key: "/", target: inputEl, preventDefault: pd });
  doc.trigger("keydown", { key: "j", target: inputEl, metaKey: true, preventDefault: pd });
  doc.trigger("keydown", { key: "k", target: inputEl, metaKey: true, preventDefault: pd });
  assert.deepEqual(seen, ["palette"], "输入框聚焦时仅 ⌘K 生效(§5)");

  /* 浮层抑制:除 ⌘K 外不穿透 */
  seen.length = 0;
  overlayFlag = true;
  doc.trigger("keydown", { key: "j", preventDefault: pd });
  doc.trigger("keydown", { key: "?", preventDefault: pd });
  doc.trigger("keydown", { key: "k", metaKey: true, preventDefault: pd });
  assert.deepEqual(seen, ["palette"], "浮层打开时仅 ⌘K 穿透");
  overlayFlag = false;

  /* 其它修饰键组合放行 */
  seen.length = 0;
  doc.trigger("keydown", { key: "c", ctrlKey: true, preventDefault: pd });
  doc.trigger("keydown", { key: "j", altKey: true, preventDefault: pd });
  assert.deepEqual(seen, [], "Ctrl/Alt 组合不劫持");
  uninstall();
}

/* ══ 2. RCA 动线(异常 run)═════════════════════════════════ */
{
  store.set({ route: { name: "run-detail", runId: "r-fail" }, selectedRunId: "r-fail" });
  const main = new StubEl("main");
  openWorkbench(main, "r-fail");
  await flush();
  await flush();
  await flush(); // detail+signals → rca → ready

  const head = main.querySelector("#wbHead");
  assert.match(head.innerHTML, /rca-banner" data-tone="danger"/, "异常 Banner(红)");
  assert.match(head.innerHTML, /run failed — /, "Banner 含 status + error 摘要");
  assert.match(head.innerHTML, /ToolGuard: 禁止危险命令/, "error 摘要全文");
  assert.match(head.innerHTML, /data-action="wb-rca-jump"/, "定位首个错误按钮");
  assert.match(head.innerHTML, /定位首个错误 ⌘J/, "按钮标注 ⌘J");
  assert.match(head.innerHTML, /data-action="wb-resume"/, "Resume 按钮");

  /* 一键定位:三步一次完成 */
  historyStub.calls.length = 0;
  assert.equal(clickAction({ action: "wb-rca-jump" }), true, "点击定位首个错误");
  await flush();
  await flush(); // selection → 三栏联动 + 帧上下文懒加载
  const sel = store.get("selection");
  assert.deepEqual(sel, { frameId: "f2", signalIndex: 24, source: "rca" },
    "selection = first_error 帧 + veto 信号,source rca");
  assert.ok(
    historyStub.calls.some((u) => u.includes("frame=f2") && u.includes("signal=24")),
    "深链接同步(可分享)");

  const tree = main.querySelector("#wbTree");
  assert.match(tree.innerHTML, /data-frame-id="f2"/, "帧树含 f2 行");
  assert.equal(store.get("selection")?.frameId, "f2",
    "帧树选中经 selection store(aria 定点更新为真实 DOM 操作,stub 不可见;渲染侧由 frame-tree 单测覆盖)");

  const timeline = main.querySelector("#wbTimeline");
  assert.match(
    timeline.innerHTML,
    /class="tl-row" data-signal-index="24"[^>]*aria-selected="true"/,
    "时间线聚焦 veto 信号行");
  assert.match(timeline.innerHTML, /data-vetoed="true"/, "veto 行红色标记");
  assert.doesNotMatch(timeline.innerHTML, /tl-filter/, "rca 源不过滤时间线(保持全量上下文)");

  const inspector = main.querySelector("#wbInspector");
  assert.match(inspector.innerHTML, /rca-veto/, "veto 归因卡(出错卡片上方)");
  assert.match(inspector.innerHTML, /裁决来源:<b class="mono">vetoed<\/b>/, "归因卡裁决来源");
  assert.match(inspector.innerHTML, /ToolGuard: 禁止危险命令 rm -rf/, "归因卡理由全文");
  assert.match(inspector.innerHTML, /rca-veto-args/, "被否决参数 JSON 折叠");
  assert.match(inspector.innerHTML, /data-copy-label="已复制被否决参数 JSON"/, "参数 JSON 复制");
  assert.match(
    inspector.innerHTML,
    /class="tc-result rca-target rca-pulse" data-ok="false"/,
    "出错 tool 卡片红色高亮 + 一次性脉冲");

  /* ⌘J 快捷键触发同一动线(经键盘模块) */
  store.set({ selection: null });
  const uninstall = installGlobalKeys({ onJumpError: () => wbJumpFirstError() }, { doc });
  doc.trigger("keydown", { key: "j", metaKey: true, preventDefault() {} });
  await flush();
  await flush();
  assert.equal(store.get("selection")?.source, "rca", "⌘J 触发定位");
  assert.equal(store.get("selection")?.signalIndex, 24);
  uninstall();

  /* j/k 导航(经键盘模块 → workbench 可见行集移动) */
  const seen2 = installGlobalKeys(
    { onSignalStep: (d) => wbNavSignal(d), onSignalEdge: (w) => wbNavEdge(w) },
    { doc });
  store.set({ selection: null });
  doc.trigger("keydown", { key: "j", preventDefault() {} });
  assert.equal(store.get("selection")?.signalIndex, 0, "j 无选中时到首条");
  doc.trigger("keydown", { key: "j", preventDefault: () => {} });
  assert.equal(store.get("selection")?.signalIndex, 1, "j 下一条");
  doc.trigger("keydown", { key: "k", preventDefault() {} });
  assert.equal(store.get("selection")?.signalIndex, 0, "k 上一条");
  doc.trigger("keydown", { key: "G", preventDefault() {} });
  assert.equal(store.get("selection")?.signalIndex, 35, "G 到末条");
  doc.trigger("keydown", { key: "g", preventDefault() {} });
  doc.trigger("keydown", { key: "g", preventDefault() {} });
  assert.equal(store.get("selection")?.signalIndex, 0, "gg 回首条");
  assert.equal(store.get("selection")?.source, "timeline", "j/k 走 timeline 联动");
  seen2();

  /* ══ 3. Usage 折叠栏 ═══════════════════════════════════ */
  const usageDetails = main.querySelector("#wbUsage");
  const usBody = usageDetails.querySelector(".us-body");
  usageDetails.open = true;
  usageDetails.trigger("toggle"); // 展开懒加载
  await flush();
  assert.match(usBody.innerHTML, /<table class="us-table">/, "usage 表格渲染");
  assert.match(usBody.innerHTML, /f-bbb222|f-f1|f-f2/, "帧行短码");
  assert.match(usBody.innerHTML, /合计\(2 帧\)/, "合计行");
  assert.match(usBody.innerHTML, /\$0\.80/, "合计 cost");
  assert.equal(
    usageDetails.querySelector(".us-summary-totals").textContent,
    "· 5 steps · $0.80",
    "标题行合计(折叠也可见)");
  assert.match(usBody.innerHTML, /us-cache-read/, "cache_read 分色");
  assert.match(usBody.innerHTML, /us-cache-write/, "cache_write 分色");
  assert.match(usBody.innerHTML, /<span style="width:100%">/, "cost 内联条(最大值满宽)");

  /* cost 列点击排序(首次 = 数字列降序) */
  clickAction({ action: "us-sort", key: "cost" });
  const sorted = usBody.innerHTML;
  assert.ok(
    sorted.indexOf('data-frame-id="f2"') < sorted.indexOf('data-frame-id="f1"'),
    "cost 降序:f2(0.5)在 f1(0.3)前");
  assert.match(sorted, /cost ▼/, "列头降序箭头");
  clickAction({ action: "us-sort", key: "cost" }); // 再点切换升序
  assert.ok(
    usBody.innerHTML.indexOf('data-frame-id="f1"') < usBody.innerHTML.indexOf('data-frame-id="f2"'),
    "同列再点切换升序");

  closeWorkbench();
}

/* ══ 4. ⌘K 命令条 ════════════════════════════════════════ */
{
  const picked = [];
  let palette = null;
  const togglePalette = () => {
    if (palette && !palette.isClosed()) {
      palette.close();
      palette = null;
      return;
    }
    if (doc.body.querySelector(".modal-overlay")) return;
    palette = openCommandPalette({
      commands: COMMANDS,
      onPick: (id) => {
        palette = null;
        picked.push(id);
      },
    });
  };
  const uninstall = installGlobalKeys({ onPalette: togglePalette }, { doc });

  doc.trigger("keydown", { key: "k", metaKey: true, preventDefault() {} });
  const overlay = doc.body.querySelector(".modal-overlay");
  assert.ok(overlay, "⌘K 打开命令条(居中 Modal)");
  assert.ok(overlay.querySelector(".cp-input"), "过滤输入框");
  assert.match(overlay.querySelector(".cp-list").innerHTML, /data-cmd="new-run"/, "命令列表");
  assert.equal(doc.activeElement, overlay.querySelector(".cp-input"), "打开聚焦输入");

  /* fuzzy 过滤 */
  const cpInput = overlay.querySelector(".cp-input");
  cpInput.value = "tools";
  cpInput.trigger("input");
  const listHtml = overlay.querySelector(".cp-list").innerHTML;
  assert.match(listHtml, /data-cmd="goto-tools"/, "过滤命中 跳 Tools");
  assert.equal((listHtml.match(/cp-item"/g) ?? []).length + (listHtml.match(/cp-item is-active/g) ?? []).length >= 1, true);

  /* Enter 执行高亮命令 → 关闭 */
  doc.trigger("keydown", { key: "Enter", preventDefault() {} });
  assert.deepEqual(picked, ["goto-tools"], "Enter 执行过滤后首条命令");
  assert.equal(doc.body.querySelector(".modal-overlay"), null, "执行后关闭");

  /* toggle:再开 → ⌘K 关闭 */
  doc.trigger("keydown", { key: "k", metaKey: true, preventDefault() {} });
  assert.ok(doc.body.querySelector(".modal-overlay"), "再次 ⌘K 打开");
  doc.trigger("keydown", { key: "k", metaKey: true, preventDefault() {} });
  assert.equal(doc.body.querySelector(".modal-overlay"), null, "toggle 关闭");

  /* Esc 关闭 */
  doc.trigger("keydown", { key: "k", metaKey: true, preventDefault() {} });
  doc.trigger("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(doc.body.querySelector(".modal-overlay"), null, "Esc 关闭");
  uninstall();
}

/* ══ 5. 轨迹窗口化(>500 渲染行;1400 信号 → 700 合并行)═══════════ */
{
  store.set({ route: { name: "run-detail", runId: "r-long" }, selectedRunId: "r-long" });
  const main = new StubEl("main");
  openWorkbench(main, "r-long");
  await flush();
  await flush();

  const timeline = main.querySelector("#wbTimeline");
  const first = timeline.innerHTML;
  const rendered = (first.match(/class="tl-row"/g) ?? []).length;
  assert.ok(rendered > 0 && rendered <= 80, `窗口化只渲染子集(渲染 ${rendered} 行 ≪ 700)`);
  assert.match(first, /还有 \d+ 行/, "底部占位行显示剩余行数");
  assert.match(first, /data-side="bottom"/);
  assert.doesNotMatch(first, /data-side="top"/, "顶部无裁切无占位");

  /* 滚动 → 窗口增量替换 */
  timeline.scrollTop = 24 * 300;
  timeline.trigger("scroll");
  const scrolled = timeline.innerHTML;
  assert.match(scrolled, /data-side="top"/, "滚动后顶部占位出现");
  assert.match(scrolled, /data-signal-index="[4-6]\d\d"/, "新窗口渲染目标区信号");
  const rendered2 = (scrolled.match(/class="tl-row"/g) ?? []).length;
  assert.ok(rendered2 <= 130, `滚动后仍为子集(渲染 ${rendered2} 行)`);

  closeWorkbench();
  store.set({ route: { name: "runs", runId: null }, selectedRunId: null, selection: null });
}

console.log("smoke-d5.test.mjs: all assertions passed");

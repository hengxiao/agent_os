/* inline skill(merge)DOM-stub 冒烟测试(SKILL-INLINING.md §4.2/§7/§9):
   1) Skills 浏览器:列表项 inline 徽标(⇥ inline chip)+ 详情 Meta 区 inline 行;
   2) debug trace:post:context.inline 信号 → ⇥ inline 专门行(弱化色,payload 可展开);
   3) 帧检视器:帧 detail working._inline_caps 存在 → "内联能力"小节
      (name@version · chars 字符);无该字段(普通帧/off 档)不显示。
   运行:node static/tests/smoke-inline.test.mjs(DOM 用 dom-stub.mjs,无浏览器)。 */

import assert from "node:assert/strict";
import { StubEl, makeDocument } from "./dom-stub.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub:document / location / history / fetch(先于被测模块 import)── */
const doc = makeDocument();
const toastStack = new StubEl("div");
toastStack.setAttribute("id", "toastStack");
doc.body.appendChild(toastStack);
doc.querySelector = (sel) => doc.body.querySelector(sel);
const locationStub = { hash: "" };
const historyStub = { replaceState() {} };

const DATE_STYLE = {
  name: "date_style", version: "1.0.0", kind: "prompt", inline: true,
  description: "日期规范能力。Use when 输出含日期。",
  permissions: { tools: [], skills: [], blackboard: [] },
};
const REPORT_WRITER = {
  name: "report_writer", version: "1.0.0", kind: "prompt", inline: false,
  description: "写简报。Use when 需要带日期的简报。",
  permissions: { tools: [], skills: ["date_style"], blackboard: [] },
};
const DATE_STYLE_DETAIL = {
  ...DATE_STYLE,
  inputs: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
  outputs: null, model: null, limits: null,
  prompt: "输出中出现日期时,一律规范为 ISO 8601(YYYY-MM-DD)。",
  entry: null, handler: null, lint: [],
};

const INLINE_SIGNALS = [
  { v: 1, type: "signal", name: "run.started", run_id: "r-inline", frame_id: null, ts: 0, payload: { skill: "report_writer" } },
  { v: 1, type: "signal", name: "pre:frame.push", run_id: "r-inline", frame_id: "f1", ts: 1, payload: { skill: "local:report_writer@1.0.0", depth: 1 } },
  { v: 1, type: "signal", name: "post:frame.push", run_id: "r-inline", frame_id: "f1", ts: 1.1, payload: {} },
  { v: 1, type: "signal", name: "post:context.inline", run_id: "r-inline", frame_id: "f1", ts: 1.2,
    payload: { frame_id: "f1", skills: [{ name: "date_style", version: "1.0.0", chars: 42 }] } },
  { v: 1, type: "signal", name: "pre:llm.request", run_id: "r-inline", frame_id: "f1", ts: 2, payload: { model: "mock/x" } },
  { v: 1, type: "signal", name: "post:llm.response", run_id: "r-inline", frame_id: "f1", ts: 2.4, payload: { usage: { prompt: 1, completion: 1 } } },
  { v: 1, type: "signal", name: "pre:frame.pop", run_id: "r-inline", frame_id: "f1", ts: 3, payload: { result: { report: "ok" } } },
  { v: 1, type: "signal", name: "run.finished", run_id: "r-inline", frame_id: null, ts: 4, payload: {} },
];
const DETAIL_INLINE = {
  run_id: "r-inline",
  status: "done",
  started_at: new Date().toISOString(),
  result: { report: "ok" },
  usage: { steps: 1, cost: 0.01 },
  frames: [
    { frame_id: "f1", skill: "local:report_writer@1.0.0", depth: 1, status: "done", usage: { steps: 1 } },
  ],
};
const FRAME_INLINE = {
  frame_id: "f1",
  skill: "local:report_writer@1.0.0",
  status: "done",
  usage: { steps: 1, cost: 0.01 },
  input: { topic: "t" },
  messages: [{ role: "user", content: "{}", tool_calls: [], meta: {} }],
  working: {
    _inline_caps: {
      text: "## 内联能力(直接运用,无需调用)\n\n### date_style@1.0.0\n…",
      hidden: ["skill__date_style"],
      skills: [{ name: "date_style", version: "1.0.0", chars: 42 }],
    },
  },
};
const PLAIN_SIGNALS = INLINE_SIGNALS.filter((s) => s.name !== "post:context.inline")
  .map((s) => ({ ...s, run_id: "r-plain" }));
const DETAIL_PLAIN = { ...DETAIL_INLINE, run_id: "r-plain" };
const FRAME_PLAIN = { ...FRAME_INLINE, working: {} }; // 普通帧/off 档:无 _inline_caps

globalThis.document = doc;
globalThis.location = locationStub;
globalThis.history = historyStub;
globalThis.fetch = async (path) => {
  const url = String(path);
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (url === "/api/skills") return reply([DATE_STYLE, REPORT_WRITER]);
  if (url === "/api/skills/date_style") return reply(DATE_STYLE_DETAIL);
  if (url === "/api/runs/r-inline") return reply(DETAIL_INLINE);
  if (url === "/api/runs/r-inline/signals") return reply(INLINE_SIGNALS);
  if (url === "/api/runs/r-inline/usage") return reply({ run: {}, frames: [] });
  if (url === "/api/runs/r-inline/frames/f1") return reply(FRAME_INLINE);
  if (url === "/api/runs/r-plain") return reply(DETAIL_PLAIN);
  if (url === "/api/runs/r-plain/signals") return reply(PLAIN_SIGNALS);
  if (url === "/api/runs/r-plain/usage") return reply({ run: {}, frames: [] });
  if (url === "/api/runs/r-plain/frames/f1") return reply(FRAME_PLAIN);
  throw new Error(`未 stub 的请求: GET ${url}`);
};

const { store } = await import("../js/store.js");
const { closeSkillsView, openSkillsView } = await import("../js/components/skills-view.js");
const { closeWorkbench, openWorkbench } = await import("../js/workbench.js");

/* ══ 1. Skills 浏览器:inline 徽标 + 详情 Meta 行 ══════════════════ */
{
  store.set({ skillsets: [], skillSet: null });
  const main = new StubEl("main");
  main.ownerDocument = doc;
  openSkillsView(main, "date_style");
  await flush();
  await flush();
  await flush(); // 列表 → 默认/指定选中 → 详情

  const list = main.querySelector(".brw-list");
  assert.match(list.innerHTML, /<span class="inline-chip"[^>]*>⇥ inline<\/span>/,
    "inline 技能名旁徽标");
  assert.match(list.innerHTML, /title="merge:指令并入调用方 SYSTEM,不产生调用帧"/,
    "徽标 tooltip 说明 merge 语义");
  const badges = list.innerHTML.match(/inline-chip/g) ?? [];
  assert.equal(badges.length, 1, "非 inline 技能(report_writer)不打标");

  const detail = main.querySelector(".brw-detail");
  assert.match(detail.innerHTML, /<span class="kv-label">inline<\/span>/, "Meta 区 inline 行");
  assert.match(detail.innerHTML, /⇥ true/, "Meta 行值 inline: true");
  closeSkillsView();
}

/* ══ 2. debug trace:post:context.inline → ⇥ inline 行 ════════════ */
{
  store.set({ route: { name: "run-detail", runId: "r-inline" }, selectedRunId: "r-inline" });
  const main = new StubEl("main");
  openWorkbench(main, "r-inline");
  await flush();
  await flush();
  await flush();
  await flush(); // detail+signals → ready → 默认选根帧 → 帧上下文预取

  const timeline = main.querySelector("#wbTimeline");
  assert.match(timeline.innerHTML, /class="tl-row"[^>]*data-kind="inline"/, "inline 专门行");
  assert.match(timeline.innerHTML, /<span class="tr-kw" data-k="inline">⇥ inline<\/span>/,
    "⇥ inline 前缀(弱化色,不占 call/ret 语义色)");
  assert.match(timeline.innerHTML, /date_style@1\.0\.0 · 42 chars/, "行文本:name@version · chars");
  const inlineRowCount = (timeline.innerHTML.match(/data-kind="inline"/g) ?? []).length;
  assert.equal(inlineRowCount, 1, "一次性信号只出一行");

  /* ══ 3. 帧检视器:working._inline_caps → "内联能力"小节 ════════ */
  const inspector = main.querySelector("#wbInspector");
  assert.match(inspector.innerHTML, /<div class="insp-caps">/, "内联能力小节");
  assert.match(inspector.innerHTML, /⇥ 内联能力/, "小节标题");
  assert.match(inspector.innerHTML, /date_style@1\.0\.0 · 42 字符/, "技能行:name@version · chars 字符");
  closeWorkbench();
}

/* ══ 4. 负例:无 inline 信号/无 _inline_caps → 不出行不出节 ═══════ */
{
  store.set({ route: { name: "run-detail", runId: "r-plain" }, selectedRunId: "r-plain" });
  const main = new StubEl("main");
  openWorkbench(main, "r-plain");
  await flush();
  await flush();
  await flush();
  await flush();

  const timeline = main.querySelector("#wbTimeline");
  assert.doesNotMatch(timeline.innerHTML, /data-kind="inline"/, "无 inline 信号不出行");
  const inspector = main.querySelector("#wbInspector");
  assert.doesNotMatch(inspector.innerHTML, /insp-caps/, "无 _inline_caps 不显示小节");
  closeWorkbench();
  store.set({ route: { name: "runs", runId: null }, selectedRunId: null, selection: null });
}

console.log("smoke-inline.test.mjs: all assertions passed");

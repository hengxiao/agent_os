/* lab-iterate.js 单测(docs/LAB-ITERATION.md;Flow C 样板):
   纯函数——边注卡 / 成员分节渲染(锚点单元)/ diff 视图(红绿行、字段两列、tests 绿色);
   DOM 冒烟——openIterate 装载、边注弹框、生成流程(fetch stub)、接受后版本 +1。
   运行:node static/tests/lab-iterate.test.mjs */

import assert from "node:assert/strict";
import { makeDocument, StubEl } from "./dom-stub.mjs";

const { diffViewHtml, memberSectionsHtml, noteCardHtml, openIterate, closeIterate } =
  await import("../js/components/lab-iterate.js");

const DOC = {
  name: "weather.query",
  manifest: {
    name: "weather.query",
    description: "根据天气与日程推荐晚餐,综合温度、降水与用户偏好。",
    inputs: { type: "object" },
    outputs: { type: "object" },
    permissions: { tools: [], skills: [] },
    trust: { confirm: "first" },
  },
  prompt: "你是晚餐规划师。\n雨天优先热汤。\n输出 JSON。",
  tests: ["case1.json", "case2.json"],
};

/* ── memberSectionsHtml:锚点单元 + 已挂边注 ───────────────── */
{
  const notes = [
    { anchor: { member: "weather.query", kind: "field", path: "description" }, text: "太啰嗦" },
  ];
  const html = memberSectionsHtml(DOC, notes);
  for (const path of ["description", "inputs/outputs", "prompt", "permissions", "trust", "tests"]) {
    assert.ok(html.includes(`\\"path\\":\\"${path}\\"`) || html.includes(path), `锚点 ${path}`);
  }
  assert.ok(html.includes("太啰嗦"), "已存边注渲染在内容旁");
  assert.ok(html.includes("💬"), "锚点按钮");
  assert.ok(noteCardHtml(notes[0]).includes("太啰嗦"), "边注卡");
}

/* ── diffViewHtml:红绿行 / 字段两列 / tests 绿色 / 无变化态 ── */
{
  const html = diffViewHtml({
    has_changes: true,
    members: [{
      member: "weather.query",
      status: "changed",
      fields: [{ kind: "changed", path: "description", old: "旧", new: "新" }],
      prompt_diff: [
        { kind: "same", text: "你是晚餐规划师。" },
        { kind: "del", text: "雨天优先热汤。" },
        { kind: "add", text: "雨天加班优先便携。" },
      ],
      tests: { added: ["case2b.json"], removed: [] },
    }],
  });
  assert.ok(html.includes('data-kind="changed"'), "字段变更行");
  assert.ok(html.includes('data-kind="add"'), "绿行");
  assert.ok(html.includes('data-kind="del"'), "红行");
  assert.ok(html.includes("case2b.json"), "tests 新增");
  assert.ok(!html.includes("你是晚餐规划师。"), "same 行不渲染");
  assert.ok(diffViewHtml({ has_changes: false, members: [] }).length > 0, "无变化态文案");
}

/* ── DOM 冒烟:装载 → 边注弹框 → 生成 → 接受后版本 +1 ──────── */
{
  const doc = makeDocument();
  globalThis.document = doc;
  const toastStack = doc.createElement("div");
  toastStack.setAttribute("id", "toastStack");
  doc.body.appendChild(toastStack);
  doc.querySelector = (sel) => doc.body.querySelector(sel);

  const calls = [];
  let accepted = false;
  const CLOSURE = {
    root: "weather.query", root_tier: "none",
    members: [{ name: "weather.query", ref_by: null, status: "draft", tier: "none", depth: 0 }],
    errors: [],
  };
  const DIFF = {
    has_changes: true,
    members: [{
      member: "weather.query", status: "changed",
      fields: [{ kind: "changed", path: "description", old: "旧", new: "新" }],
      prompt_diff: [{ kind: "add", text: "雨天加班优先便携。" }],
      tests: { added: ["case2b.json"], removed: [] },
    }],
  };
  globalThis.fetch = async (path, options = {}) => {
    const url = String(path);
    calls.push({ url, method: options.method ?? "GET", body: options.body });
    const reply = (data) => ({ ok: true, status: 200, json: async () => data });
    if (url === "/api/lab/packages/weather.query/closure") return reply(CLOSURE);
    if (url === "/api/lab/drafts/weather.query") return reply(DOC);
    if (url === "/api/lab/drafts/weather.query/comments") return reply({ round: "r1", comments: [] });
    if (url === "/api/lab/drafts/weather.query/versions") {
      return reply(accepted ? [{ version: "v001", source: "iterate" }] : []);
    }
    if (url === "/api/lab/drafts/weather.query/iterate") {
      return reply({ candidate: true, reply: "description 精简 + 雨天便携 + 新反例", diff: DIFF });
    }
    if (url === "/api/lab/drafts/weather.query/candidate/accept") {
      accepted = true;
      return reply({ version: "v001", versions: [{ version: "v001", source: "iterate" }] });
    }
    throw new Error(`未 stub 的请求: ${url}`);
  };

  const main = doc.createElement("main");
  doc.body.appendChild(main);
  const handle = openIterate(main, "weather.query");
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const region = (sel) => handle.root.querySelector(sel)?.innerHTML ?? "";
  assert.ok(region(".it-top").includes("weather.query"), "包名渲染");
  assert.ok(region(".it-left").includes("根据天气与日程"), "working 内容渲染");

  // 生成 → 右栏 diff 视图 + 接受
  const gen = new StubEl("button");
  gen.dataset.it = "generate";
  gen.parentNode = handle.root;
  handle.root.trigger("click", { target: gen });
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const iterPost = calls.find((c) => c.url.endsWith("/iterate"));
  assert.ok(iterPost, "iterate 请求发出");
  assert.ok(region(".it-right").includes('data-kind="add"'), "diff 绿行渲染");
  assert.ok(region(".it-right").includes("description 精简"), "助手回复摘要渲染");

  const accept = new StubEl("button");
  accept.dataset.it = "accept";
  accept.parentNode = handle.root;
  handle.root.trigger("click", { target: accept });
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(calls.find((c) => c.url.endsWith("/candidate/accept")), "accept 请求发出");
  assert.ok(region(".it-top").includes("v001"), "接受后版本下拉出现 v001");

  /* 边注弹框键盘交互(商业 widget 标准):role=dialog、Enter 提交、Esc 关闭 */
  const unit = new StubEl("div");
  unit.dataset.anchor = JSON.stringify({ member: "weather.query", kind: "field", path: "description" });
  unit.parentNode = handle.root;
  const noteBtn = new StubEl("button");
  noteBtn.dataset.itNote = "";
  noteBtn.closest = (sel) =>
    sel === "[data-it-note]" ? noteBtn : sel === "[data-anchor]" ? unit : null;
  handle.root.trigger("click", { target: noteBtn });
  const form = unit.querySelector(".it-note-form");
  assert.ok(form, "💬 弹边注弹框");
  assert.equal(form.getAttribute("role"), "dialog", "弹框 role=dialog");
  const input = form.querySelector("input");
  input.value = "太啰嗦";
  input.trigger("keydown", { key: "Enter" });
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(region(".it-left").includes("太啰嗦"), "Enter 提交 → 边注挂到左栏");

  const unit2 = new StubEl("div");
  unit2.dataset.anchor = JSON.stringify({ member: "weather.query", kind: "case", path: "case1.json" });
  unit2.parentNode = handle.root;
  const noteBtn2 = new StubEl("button");
  noteBtn2.dataset.itNote = "";
  noteBtn2.closest = (sel) =>
    sel === "[data-it-note]" ? noteBtn2 : sel === "[data-anchor]" ? unit2 : null;
  handle.root.trigger("click", { target: noteBtn2 });
  const form2 = unit2.querySelector(".it-note-form");
  assert.ok(form2, "第二个弹框");
  form2.querySelector("input").trigger("keydown", { key: "Escape" });
  assert.equal(unit2.querySelector(".it-note-form"), null, "Esc 关闭弹框且不留批注");
  closeIterate();
}

console.log("lab-iterate.test.mjs: all assertions passed");

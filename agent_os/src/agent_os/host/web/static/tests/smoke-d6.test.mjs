/* D6 DOM-stub 冒烟测试(一站多 skill set):
   1) app.js 侧栏 set 切换器:≥2 sets 可见(全部 + 各 set 带技能数)、run 列表
      set chip、选择 → 列表过滤 + hash ?set= 写入、切回全部恢复;
   2) Launch Modal 多 set:技能下拉按 <optgroup> 分组、option value 含 set、
      详情按 ?skill_set= 拉取、提交 body 带 skill_set;
   3) 无 sets:set 下拉隐藏、run 列表无 set chip、Modal 无 optgroup、提交不带
      skill_set(界面与单站完全一致);
   4) Skills 页多 set:列表按 set 分组显示组头、详情按 ?skill_set= 消歧、
      工具栏下拉与侧栏经 store 联动、↻ Reload 带 skill_set 限定。
   运行:node static/tests/smoke-d6.test.mjs(DOM 用 dom-stub.mjs,无浏览器)。
   注意:app.js 启动后带 5s 轮询 setInterval,断言完毕显式 process.exit(0)。 */

import assert from "node:assert/strict";
import { StubEl, makeDocument } from "./dom-stub.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub:document(含 app.js 依赖的 id 元素)/ location / history /
      window / fetch(先于被测模块 import)── */
const doc = makeDocument();
const mk = (tag, id) => {
  const el = new StubEl(tag);
  el.ownerDocument = doc;
  if (id) el.setAttribute("id", id);
  return el;
};
const sidebar = mk("aside", "sidebar");
sidebar.appendChild(mk("button", "collapseBtn"));
const setSelect = mk("select", "setSelect");
setSelect.hidden = true; // index.html 初始 hidden
sidebar.appendChild(setSelect);
sidebar.appendChild(mk("input", "searchInput"));
sidebar.appendChild(mk("div", "statusChips"));
const runList = mk("div", "runList");
sidebar.appendChild(runList);
const main = mk("main", "main");
const toastStack = mk("div", "toastStack");
for (const el of [sidebar, main, mk("button", "liveIndicator"), mk("button", "newRunBtn"), toastStack]) {
  doc.body.appendChild(el);
}
doc.querySelector = (sel) => doc.body.querySelector(sel); // $ / toast() 用
doc.querySelectorAll = (sel) => doc.body.querySelectorAll(sel); // renderNav/renderChips 用

const locationStub = { hash: "" };
const historyCalls = [];
const SETS = [
  { name: "set_a", skills: 1, path: "/sets/set_a" },
  { name: "set_b", skills: 1, path: "/sets/set_b" },
];
const RUNS = [
  { run_id: "r1", skill: "alpha_echo", status: "done", started_at: "2026-07-25T01:02:00Z", cost: 0.01, skill_set: "set_a" },
  { run_id: "r2", skill: "beta_add", status: "done", started_at: "2026-07-25T01:01:00Z", cost: 0.02, skill_set: "set_b" },
  { run_id: "r3", skill: "fib", status: "failed", started_at: "2026-07-25T01:00:00Z", cost: 0.03, skill_set: "default" },
];
const ALPHA = {
  name: "alpha_echo", version: "1.0.0", kind: "prompt",
  description: "返回输入原文。Use when 测试 set A。",
  permissions: { tools: [], skills: [], blackboard: [] },
};
const ALPHA_DETAIL = {
  ...ALPHA,
  inputs: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
  outputs: { type: "object", properties: { echo: { type: "string" } }, required: ["echo"] },
  model: null, limits: null, prompt: "原样返回输入。", entry: null, handler: null, lint: [],
};
const BETA = {
  name: "beta_add", version: "1.0.0", kind: "code",
  description: "两数相加。Use when 测试 set B。",
  permissions: { tools: [], skills: [], blackboard: [] },
};
const BETA_DETAIL = {
  ...BETA,
  inputs: {
    type: "object",
    properties: { a: { type: "integer" }, b: { type: "integer" } },
    required: ["a", "b"],
  },
  outputs: null, model: null, limits: null, prompt: null, entry: null,
  handler: "beta_handlers:add", lint: [],
};
const GAMMA = {
  name: "fib", version: "1.0.0", kind: "prompt",
  description: "菲波拉契。Use when 单站。",
  permissions: { tools: [], skills: [], blackboard: [] },
};
const GAMMA_DETAIL = {
  ...GAMMA,
  inputs: { type: "object", properties: { n: { type: "integer" } }, required: ["n"] },
  outputs: null, model: null, limits: null, prompt: "fib", entry: null, handler: null, lint: [],
};

const http = { gets: [], posts: [], reloadBodies: [] };
globalThis.document = doc;
globalThis.location = locationStub;
globalThis.history = {
  replaceState: (_a, _b, url) => {
    historyCalls.push(String(url));
    if (url) locationStub.hash = String(url); // replaceState 改 URL 但不触发 hashchange
  },
};
globalThis.window = { addEventListener() {} };
globalThis.fetch = async (path, options = {}) => {
  const url = String(path);
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (options.method === "POST") {
    const body = options.body ? JSON.parse(options.body) : null;
    if (url === "/api/runs") {
      http.posts.push(body);
      return reply({ run_id: "r-new-1" });
    }
    if (url === "/api/skills/reload") {
      http.reloadBodies.push(body);
      return reply({ reloaded: true });
    }
    throw new Error(`未 stub 的 POST: ${url}`);
  }
  http.gets.push(url);
  if (url === "/api/skillsets") return reply(SETS);
  if (url === "/api/runs") return reply(RUNS);
  if (url === "/api/skills?skill_set=set_a") return reply([ALPHA]);
  if (url === "/api/skills?skill_set=set_b") return reply([BETA]);
  if (url === "/api/skills/alpha_echo?skill_set=set_a") return reply(ALPHA_DETAIL);
  if (url === "/api/skills/beta_add?skill_set=set_b") return reply(BETA_DETAIL);
  if (url === "/api/skills") return reply([GAMMA]); // 无 sets 的裸拉(单站行为)
  if (url === "/api/skills/fib") return reply(GAMMA_DETAIL);
  throw new Error(`未 stub 的 GET: ${url}`);
};

const { store } = await import("../js/store.js");
await import("../js/app.js"); // 启动:applyRoute + loadSkillsets + poll(5s interval)
const { openLaunchDialog } = await import("../js/components/launch-dialog.js");
const { openSkillsView, closeSkillsView } = await import("../js/components/skills-view.js");

const count = (html, needle) => html.split(needle).length - 1;

/* ══ 1. app.js 侧栏 set 切换器:可见 / chip / 过滤 / hash ══════════ */
{
  await flush();
  await flush(); // loadSkillsets + poll 两段取数

  assert.equal(setSelect.hidden, false, "≥2 sets:set 下拉可见");
  assert.equal(setSelect.children.length, 3, "下拉 = 全部 + 2 sets");
  assert.equal(setSelect.children[0].value, "", "首项 = 全部(空值)");
  assert.equal(setSelect.children[1].textContent, "set_a (1)", "各 set 带技能数");
  assert.equal(setSelect.value, "", "默认选中全部");

  assert.equal(count(runList.innerHTML, "run-item\""), 3, "全部时 3 个 run 都在");
  assert.equal(count(runList.innerHTML, "run-set"), 3, "多 set:列表项带 set chip");
  assert.match(runList.innerHTML, />set_a</, "chip 显示 set_a");
  assert.match(runList.innerHTML, />default</, "全局 run chip 显示 default");

  setSelect.value = "set_b";
  setSelect.trigger("change");
  await flush();
  assert.equal(store.get("skillSet"), "set_b", "选择写入 store");
  assert.equal(count(runList.innerHTML, "run-item\""), 1, "按 set_b 过滤后只剩 1 个 run");
  assert.match(runList.innerHTML, /beta_add/, "留下的是 set_b 的 run");
  assert.ok(historyCalls.at(-1).includes("set=set_b"), "选择写入 hash ?set=");

  setSelect.value = "";
  setSelect.trigger("change");
  await flush();
  assert.equal(store.get("skillSet"), null, "切回全部");
  assert.equal(count(runList.innerHTML, "run-item\""), 3, "全部恢复 3 个 run");
  assert.ok(!historyCalls.at(-1).includes("set="), "全部时 hash 去掉 set 参数");
}

/* ══ 2. Launch Modal 多 set:optgroup 分组 + 提交带 skill_set ═══════ */
{
  const modal = openLaunchDialog();
  const { els } = modal;
  await flush();
  await flush(); // 逐 set 拉取 + 首个技能详情
  await flush();

  const groups = els.skill.children.filter((c) => c.tagName === "OPTGROUP");
  assert.equal(groups.length, 2, "多 set:技能下拉按 <optgroup> 分组");
  assert.equal(groups[0].getAttribute("label"), "set_a (1)", "组标签带技能数");
  assert.equal(groups[1].getAttribute("label"), "set_b (1)");
  assert.equal(groups[0].children[0].value, "set_a/alpha_echo", "option value 含 set");
  assert.equal(els.skill.value, "set_a/alpha_echo", "默认选中首组首项");
  assert.ok(
    http.gets.includes("/api/skills/alpha_echo?skill_set=set_a"),
    "详情按 ?skill_set= 消歧",
  );
  assert.match(els.input.value, /"text": ""/, "inputs 骨架预填");

  els.skill.value = "set_b/beta_add"; // 切到 set_b 的技能
  els.skill.trigger("change");
  await flush();
  await flush();
  assert.ok(http.gets.includes("/api/skills/beta_add?skill_set=set_b"), "切换后按新 set 拉详情");
  els.input.value = '{"a": 19, "b": 23}';
  els.input.trigger("input");
  assert.equal(els.run.disabled, false, "合法输入放行 Run");
  els.run.trigger("click");
  await flush();
  assert.equal(http.posts.length, 1, "提交了一次 POST /api/runs");
  assert.equal(http.posts[0].skill, "beta_add", "skill 为纯技能名(不含 set 前缀)");
  assert.equal(http.posts[0].skill_set, "set_b", "提交带 skill_set");
  assert.ok(locationStub.hash.includes("r-new-1"), "成功后跳 #/runs/<id>");
  modal.close();
}

/* ══ 3. 无 sets:下拉隐藏 / 无 chip / Modal 无分组 / 提交不带 skill_set ══ */
{
  store.set({ skillsets: [] });
  await flush();
  assert.equal(setSelect.hidden, true, "无 sets:set 下拉隐藏");
  assert.equal(count(runList.innerHTML, "run-set"), 0, "无 sets:列表项无 set chip");

  const modal = openLaunchDialog();
  const { els } = modal;
  await flush();
  await flush();
  await flush();
  assert.equal(els.skill.children.length, 1, "无 sets:option 直挂(无 optgroup)");
  assert.equal(els.skill.children[0].tagName, "OPTION");
  assert.equal(els.skill.value, "fib", "value 即技能名");
  assert.ok(http.gets.includes("/api/skills"), "无 sets:裸拉 /api/skills(现行为)");
  els.input.value = '{"n": 3}';
  els.input.trigger("input");
  els.run.trigger("click");
  await flush();
  assert.ok(!("skill_set" in http.posts.at(-1)), "无 sets:提交不带 skill_set");
  modal.close();
}

/* ══ 4. Skills 页:分组 / 下拉联动 / 详情消歧 / scoped reload ══════ */
{
  store.set({ skillsets: SETS });
  await flush();
  const view = openSkillsView(main, null);
  await flush();
  await flush(); // 逐 set 拉取 + 首技能详情
  await flush();

  assert.equal(view.els.setSel.hidden, false, "Skills 页 set 下拉可见");
  assert.equal(view.els.setSel.value, "", "默认跟随侧栏 = 全部");
  assert.match(view.els.list.innerHTML, /brw-group[^>]*>set_a \(1\)/, "全部:按 set 分组组头");
  assert.match(view.els.list.innerHTML, /brw-group[^>]*>set_b \(1\)/);
  assert.equal(count(view.els.list.innerHTML, "brw-item\""), 2, "全部:两组技能都在");
  assert.ok(
    http.gets.includes("/api/skills/alpha_echo?skill_set=set_a"),
    "全部:详情按技能所属 set 拉取",
  );

  view.els.setSel.value = "set_b"; // Skills 页下拉切 set_b(change 委托 root,stub 不冒泡)
  view.root.trigger("change", { target: view.els.setSel });
  await flush();
  await flush();
  await flush();
  assert.equal(store.get("skillSet"), "set_b", "Skills 页选择写回 store");
  assert.equal(setSelect.value, "set_b", "侧栏下拉经 store 联动同步");
  assert.ok(!view.els.list.innerHTML.includes("brw-group"), "指定 set:无组头");
  assert.match(view.els.list.innerHTML, /beta_add/, "指定 set:只列该 set 技能");
  assert.ok(!view.els.list.innerHTML.includes("alpha_echo"), "其它 set 技能不出现");

  view.root.trigger("click", { target: view.els.reload }); // ↻ Reload(指定 set 下限 scoped)
  await flush();
  await flush();
  assert.deepEqual(http.reloadBodies[0], { skill_set: "set_b" }, "Reload 带 skill_set 限定");

  closeSkillsView();
}

console.log("smoke-d6.test.mjs: all assertions passed");
process.exit(0); // app.js 的 5s 轮询 setInterval 仍在,显式退出

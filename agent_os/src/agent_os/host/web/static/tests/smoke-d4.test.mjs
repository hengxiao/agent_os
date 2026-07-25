/* D4 DOM-stub 冒烟测试(WEB-UI.md §4.6/§4.7):
   1) Skills 浏览器:三态(loading 骨架 / error+重试 / empty 引导)→ 列表
      (自引用 fib 不标循环,cyc_a↔cyc_b 互环标 ⚠)→ 搜索过滤 → 选中渲染详情
      (lint --warn 横幅 / Meta / 路由规则 desc-seg / Inputs·Outputs SchemaView /
      Permissions skills 跳链 / Model & Limits / Prompt mono+复制)→ ↻ Reload
      (按钮 loading → Toast reloaded)→ Run ▶(Launch Modal 以该技能预填)。
   2) Tools 浏览器:三态 → 权限筛选 chips(全部/EXEC)→ 详情(大 PermBadge /
      EXEC 提示行 / Parameters SchemaView / 执行属性 chips / Examples / 来源)。
   运行:node static/tests/smoke-d4.test.mjs(DOM 用 dom-stub.mjs,无浏览器)。 */

import assert from "node:assert/strict";
import { StubEl, makeDocument } from "./dom-stub.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub:document / location / fetch(先于被测模块 import)── */
const doc = makeDocument();
const toastStack = new StubEl("div");
toastStack.setAttribute("id", "toastStack");
doc.body.appendChild(toastStack);
doc.querySelector = (sel) => doc.body.querySelector(sel); // toast() 用 document.querySelector
const locationStub = { hash: "" };

const FIB = {
  name: "fib",
  version: "1.0.0",
  kind: "prompt",
  description: "生成前 n 个菲波拉契数。Use when 需要菲波拉契数列;Do not use when 需要大 n。",
  permissions: { tools: ["python_exec"], skills: ["fib"], blackboard: ["status"] },
};
const CYC_A = {
  name: "cyc_a",
  version: "0.1.0",
  kind: "prompt",
  description: "Use when 测试环。",
  permissions: { tools: [], skills: ["cyc_b"], blackboard: [] },
};
const CYC_B = {
  name: "cyc_b",
  version: "0.1.0",
  kind: "code",
  description: "Use when 测试环。",
  permissions: { tools: [], skills: ["cyc_a"], blackboard: [] },
};
const FIB_DETAIL = {
  ...FIB,
  inputs: { type: "object", properties: { n: { type: "integer", minimum: 1 } }, required: ["n"] },
  outputs: {
    type: "object",
    properties: { seq: { type: "array", items: { type: "integer" } } },
    required: ["seq"],
  },
  model: { prefer: ["mock/fib"], temperature: null },
  limits: { max_steps: 8, timeout: 60, retry: 0 },
  prompt: "你是菲波拉契数列生成器。\n最终答案只输出一个 JSON 对象。",
  entry: null,
  handler: null,
  lint: ["技能 fib: description 应含 'Use when / Do not use when' 触发条件(§6.1 lint)"],
};
const TOOLS = [
  {
    name: "fs_read",
    description: "读取工作目录内文件。Use when 需要查看文件内容。",
    permission: "READ",
    parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
    timeout: 30,
    idempotent: true,
    cacheable: true,
    concurrency_safe: true,
    untrusted_source: false,
  },
  {
    name: "python_exec",
    description: "执行 Python 代码。Use when 需要计算;Do not use when 仅读写文件。",
    permission: "EXEC",
    parameters: { type: "object", properties: { code: { type: "string" } }, required: ["code"] },
    timeout: 10,
    idempotent: false,
    cacheable: false,
    concurrency_safe: false,
    untrusted_source: false,
    examples: [{ code: "print(1 + 1)" }],
  },
  {
    name: "http_fetch",
    description: "抓取 URL。Use when 需要外部资料。",
    permission: "NET",
    parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"] },
    timeout: 30,
    idempotent: false,
    cacheable: false,
    concurrency_safe: true,
    untrusted_source: true,
  },
];

const http = { reloads: 0, reloadReply: true, failSkills: false, failTools: false };
globalThis.document = doc;
globalThis.location = locationStub;
globalThis.fetch = async (path, options = {}) => {
  const url = String(path);
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  const fail = (msg) => ({ ok: false, status: 500, json: async () => ({ detail: msg }) });
  if (url === "/api/skills/reload" && options.method === "POST") {
    http.reloads += 1;
    return reply({ reloaded: http.reloadReply });
  }
  if (url === "/api/skills") return http.failSkills ? fail("skills 装配失败") : reply([FIB, CYC_A, CYC_B]);
  if (url === "/api/skills/fib") return http.failSkills ? fail("skills 装配失败") : reply(FIB_DETAIL);
  if (url === "/api/tools") return http.failTools ? fail("tools 装配失败") : reply(TOOLS);
  throw new Error(`未 stub 的请求: ${options.method ?? "GET"} ${url}`);
};

const { openSkillsView, closeSkillsView } = await import("../js/components/skills-view.js");
const { openToolsView, closeToolsView } = await import("../js/components/tools-view.js");

/* 构造委托假目标:挂进 root 后 trigger(与 smoke-d3 的 clickLb 同法) */
const clickFake = (root, { classes = "", dataset = {} }) => {
  const fake = new StubEl("div");
  fake.className = classes;
  Object.assign(fake.dataset, dataset);
  root.appendChild(fake);
  root.trigger("click", { target: fake });
  fake.remove();
};

/* ══ 1. Skills 浏览器 ═══════════════════════════════════════ */
{
  const main = new StubEl("main");
  const view = openSkillsView(main, null);
  const { root, els } = view;
  assert.match(els.list.innerHTML, /skeleton-row/, "列表 loading 骨架");
  assert.match(els.detail.innerHTML, /选择一个技能/, "未选中:详情空态引导");
  await flush();

  /* 列表就绪:自引用 fib 无警示,互环 cyc_a/cyc_b 标 ⚠;默认选中首项 fib(空屏规避) */
  assert.match(els.list.innerHTML, /data-name="fib"/);
  assert.match(els.list.innerHTML, /data-name="fib"[^>]*aria-selected="true"/, "默认选中首项");
  assert.match(els.list.innerHTML, /v1\.0\.0/, "version 列示");
  assert.match(els.list.innerHTML, /kind-chip">prompt</, "kind chip");
  const fibRow = els.list.innerHTML.match(/data-name="fib"[\s\S]*?<\/div>\n?/)?.[0] ?? "";
  const cycRows = els.list.innerHTML.split('data-name="').slice(2).join('data-name="');
  assert.ok(!/data-name="fib"(?![\s\S]*data-name)[\s\S]*cycle-warn/.test(els.list.innerHTML.split('data-name="cyc')[0]),
    "fib 自引用不标循环");
  assert.match(cycRows, /cycle-warn/, "cyc_a/cyc_b 互环标 --warn 警示");
  assert.match(cycRows, /循环依赖/, "警示含 title 说明");

  /* 搜索过滤 */
  els.search.value = "cyc";
  root.trigger("input", { target: els.search });
  assert.ok(!els.list.innerHTML.includes('data-name="fib"'), "搜索过滤掉 fib");
  assert.match(els.list.innerHTML, /data-name="cyc_a"/);
  els.search.value = "";
  root.trigger("input", { target: els.search });
  assert.match(els.list.innerHTML, /data-name="fib"/, "清空搜索恢复");

  /* 选中 fib:深链接 + 详情分区渲染 */
  clickFake(root, { classes: "brw-item", dataset: { name: "fib" } });
  assert.equal(locationStub.hash, "#/skills/fib", "点击跳 #/skills/<name>");
  await flush();
  const dh = () => els.detail.innerHTML;
  assert.match(dh(), /data-tone="warn"/, "lint 警告 --warn 横幅(自检入口)");
  assert.match(dh(), /lint-list/, "横幅列 lint 明细");
  assert.match(dh(), /Meta/, "Meta 分区");
  assert.match(dh(), /kind<\/span>[\s\S]*?prompt/, "Meta kind");
  assert.match(dh(), /desc-seg" data-kind="use">Use when/, "Use when 分行高亮");
  assert.match(dh(), /desc-seg" data-kind="avoid">Do not use when/, "Do not use when 分行高亮");
  assert.match(dh(), /Inputs/, "Inputs 分区");
  assert.match(dh(), /sv-name is-required">n</, "Inputs 必填加粗");
  assert.match(dh(), /sv-constraints">minimum 1</, "约束灰注");
  assert.match(dh(), /array&lt;integer&gt;/, "Outputs array<integer>(esc 后)");
  assert.match(dh(), /Permissions/, "Permissions 分区");
  assert.match(dh(), /kind-chip mono">python_exec</, "tools 组 chip");
  assert.match(dh(), /href="#\/skills\/fib"/, "skills 组可点击跳链");
  assert.match(dh(), /kind-chip mono">status</, "blackboard 组");
  assert.match(dh(), /max_steps<\/span>[\s\S]*?8/, "Model & Limits");
  assert.match(dh(), /mock\/fib/, "model prefer");
  assert.match(dh(), /Prompt 模板/, "Prompt 分区");
  assert.match(dh(), /prompt-block/, "Prompt mono 只读块");
  assert.match(dh(), /data-copy-label="Prompt 已复制"/, "Prompt 复制按钮");

  /* ↻ Reload:按钮 loading → POST → Toast reloaded → 列表/详情刷新 */
  clickFake(root, { classes: "btn", dataset: { skls: "reload" } });
  assert.equal(els.reload.textContent, "重载中…", "Reload 按钮 loading");
  assert.equal(els.reload.disabled, true);
  await flush();
  await flush(); // reload → loadList → ensureDetail 三段取数
  assert.equal(http.reloads, 1, "POST /api/skills/reload 被调");
  assert.equal(els.reload.textContent, "↻ Reload", "结束后按钮还原");
  const toastEl = toastStack.children.at(-1);
  assert.match(toastEl.querySelector(".toast-msg").textContent, /已重载/, "Toast reloaded true");
  assert.match(dh(), /Prompt 模板/, "Reload 后详情缓存失效并重新加载");

  /* Run ▶:带 fib 预填打开 Launch Modal */
  clickFake(root, { classes: "btn btn-primary", dataset: { skls: "run" } });
  await flush();
  await flush(); // modal 的技能列表 + 详情两段取数
  const skillSel = doc.body.querySelector(".ld-skill");
  assert.ok(doc.body.querySelector(".modal-overlay"), "Launch Modal 打开");
  assert.equal(skillSel.value, "fib", "Run ▶ 以当前技能预填");
  assert.match(doc.body.querySelector(".ld-input").value, /"n": 1/, "inputs 骨架预填");
  doc.trigger("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(doc.body.querySelector(".modal-overlay"), null, "Esc 关 Modal");

  /* error 态:列表加载失败 → 面板内重试恢复 */
  http.failSkills = true;
  closeSkillsView();
  const view2 = openSkillsView(main, null);
  await flush();
  assert.match(view2.els.list.innerHTML, /加载技能列表失败/, "error 态错误条");
  assert.match(view2.els.list.innerHTML, /data-skls="retry-list"/, "面板内重试按钮");
  http.failSkills = false;
  clickFake(view2.root, { classes: "btn", dataset: { skls: "retry-list" } });
  await flush();
  assert.match(view2.els.list.innerHTML, /data-name="fib"/, "重试后列表恢复");
  closeSkillsView();
}

/* ══ 2. Tools 浏览器 ════════════════════════════════════════ */
{
  const main = new StubEl("main");
  const view = openToolsView(main, null);
  const { root, els } = view;
  assert.match(els.list.innerHTML, /skeleton-row/, "列表 loading 骨架");
  await flush();

  /* 列表就绪:name + PermBadge;未指定选中项时默认选中首项(空屏规避) */
  assert.match(els.list.innerHTML, /data-name="fs_read"/);
  assert.match(els.list.innerHTML, /perm-badge" data-perm="READ"/, "READ 徽标");
  assert.match(els.list.innerHTML, /perm-badge" data-perm="EXEC"/, "EXEC 徽标");
  assert.match(
    els.list.innerHTML,
    /data-name="fs_read"[^>]*aria-selected="true"/,
    "默认选中列表首项",
  );
  assert.match(els.detail.innerHTML, /perm-badge-lg" data-perm="READ"/, "首项详情直接渲染");

  /* 权限筛选 chips:EXEC 单选 */
  const execChip = els.chips.children[4]; // [全部, READ, WRITE, NET, EXEC]
  root.trigger("click", { target: execChip });
  assert.ok(execChip.classList.contains("is-active"), "chip 选中态");
  assert.ok(!els.list.innerHTML.includes('data-name="fs_read"'), "EXEC 筛选掉 READ 工具");
  assert.match(els.list.innerHTML, /data-name="python_exec"/);
  root.trigger("click", { target: els.chips.children[0] }); // 全部
  assert.match(els.list.innerHTML, /data-name="fs_read"/, "全部恢复");

  /* 搜索过滤 */
  els.search.value = "http";
  root.trigger("input", { target: els.search });
  assert.match(els.list.innerHTML, /data-name="http_fetch"/);
  assert.ok(!els.list.innerHTML.includes('data-name="fs_read"'), "搜索过滤");
  els.search.value = "";
  root.trigger("input", { target: els.search });

  /* 选中 python_exec:详情分区渲染(EXEC 级首要视觉) */
  clickFake(root, { classes: "brw-item", dataset: { name: "python_exec" } });
  assert.equal(locationStub.hash, "#/tools/python_exec", "点击跳 #/tools/<name>");
  const dh = () => els.detail.innerHTML;
  assert.match(dh(), /perm-badge perm-badge-lg" data-perm="EXEC"/, "头部大 PermBadge");
  assert.match(dh(), /危险操作,受 sidecar\/人工闸门约束/, "EXEC 提示行");
  assert.match(dh(), /desc-seg" data-kind="use">Use when/, "Spec 路由规则渲染");
  assert.match(dh(), /sv-name is-required">code</, "Parameters 必填");
  assert.match(dh(), /timeout: <b>10s</, "执行属性 timeout");
  assert.match(dh(), /idempotent: <b>false</, "执行属性 idempotent");
  assert.match(dh(), /concurrency_safe: <b>false</, "执行属性 concurrency_safe");
  assert.match(dh(), /untrusted_source: <b>false</, "执行属性 untrusted_source");
  assert.match(dh(), /example-card/, "Examples mono 卡片");
  assert.match(dh(), /print\(1 \+ 1\)/, "示例内容");
  assert.match(dh(), /builtin/, "来源");

  /* fs_read:READ 级无 EXEC 提示行 */
  clickFake(root, { classes: "brw-item", dataset: { name: "fs_read" } });
  assert.ok(!dh().includes("危险操作"), "READ 级无 EXEC 提示行");

  /* 深链接不存在的工具:空态 */
  openToolsView(main, "ghost");
  assert.match(dh(), /工具 ghost 不存在/, "未知工具空态");

  /* error 态 + 重试 */
  http.failTools = true;
  closeToolsView();
  const view2 = openToolsView(main, null);
  await flush();
  assert.match(view2.els.list.innerHTML, /加载工具列表失败/, "error 态");
  http.failTools = false;
  clickFake(view2.root, { classes: "btn", dataset: { tools: "retry" } });
  await flush();
  assert.match(view2.els.list.innerHTML, /data-name="fs_read"/, "重试恢复");
  closeToolsView();
}

console.log("smoke-d4.test.mjs: all assertions passed");

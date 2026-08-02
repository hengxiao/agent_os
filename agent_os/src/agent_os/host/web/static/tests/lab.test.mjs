/* lab.js 单测(docs/SKILL-DEV.md;L1):
   纯函数——命名校验/JSON 提示/草稿↔表单映射/tier 徽标与悬停明细/编辑器与顶部条 HTML;
   DOM 冒烟——openLab 加载草稿、saveCurrentDraft 保存流程(全局 fetch stub)。
   运行:node static/tests/lab.test.mjs(无需真实 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import { makeDocument } from "./dom-stub.mjs";

const {
  draftToForm,
  editorHtml,
  formToManifest,
  isValidDraftName,
  jsonError,
  statusLine,
  tierBadgeHtml,
  tierTitle,
  topbarHtml,
  openLab,
  closeLab,
  saveCurrentDraft,
} = await import("../js/components/lab.js");

/* ── isValidDraftName:docs/NAMING.md §2(≥2 段点分小写 snake_case)────── */
{
  assert.ok(isValidDraftName("weather.query"));
  assert.ok(isValidDraftName("ops.scan.workspace"));
  assert.ok(!isValidDraftName("single"));
  assert.ok(!isValidDraftName("../etc"));
  assert.ok(!isValidDraftName("a/b.c"));
  assert.ok(!isValidDraftName("Upper.Case"));
  assert.ok(!isValidDraftName(""));
  assert.ok(!isValidDraftName(null));
}

/* ── jsonError:实时合法性提示 ─────────────────────────────── */
{
  assert.equal(jsonError('{"a":1}'), null);
  assert.equal(jsonError(""), null);
  assert.equal(jsonError("  "), null);
  assert.ok(jsonError("{bad") !== null);
}

/* ── draftToForm / formToManifest:七组全字段映射(往返)────── */
const DRAFT = {
  name: "ops.cleanup.execute",
  manifest: {
    name: "ops.cleanup.execute",
    version: "0.3.1",
    kind: "prompt",
    description: "清理。Use when x;Do not use when y",
    inputs: { type: "object", properties: { targets: { type: "array" } } },
    outputs: { type: "object", properties: { destroyed: { type: "array" } } },
    entry: null,
    handler: null,
    logic: { mode: "sandbox" },
    permissions: {
      tools: ["system.file.delete"],
      skills: ["common.text.extract_json"],
      blackboard: ["shared"],
    },
    model: { prefer: ["kimi/k2"], temperature: 0.2 },
    context_policy: { max_tokens: 4096, compress: "off" },
    limits: { max_steps: 8, timeout: 60, retry: 1, max_tool_calls: 20 },
    trust: { confirm: "always", reversal: "软删除", blast_radius: "targets 清单" },
    inline: false,
    verifier: "checks.run",
  },
  prompt: "你是清理执行员。",
  handler: null,
  parse_error: null,
  tests: {},
};

{
  const form = draftToForm(DRAFT);
  // 身份/指令
  assert.equal(form.name, "ops.cleanup.execute");
  assert.equal(form.version, "0.3.1");
  assert.equal(form.kind, "prompt");
  assert.equal(form.prompt, "你是清理执行员。");
  // 契约(JSON 文本域)
  assert.deepEqual(JSON.parse(form.inputsText), DRAFT.manifest.inputs);
  assert.deepEqual(JSON.parse(form.outputsText), DRAFT.manifest.outputs);
  // 权限(chips)
  assert.deepEqual(form.tools, ["system.file.delete"]);
  assert.deepEqual(form.skills, ["common.text.extract_json"]);
  assert.deepEqual(form.blackboard, ["shared"]);
  // 策略
  assert.equal(form.modelPrefer, "kimi/k2");
  assert.equal(form.modelTemperature, 0.2);
  assert.equal(form.cpMaxTokens, 4096);
  assert.equal(form.cpCompress, "off");
  assert.equal(form.limMaxSteps, 8);
  assert.equal(form.limMaxToolCalls, 20);
  // 信任/行为
  assert.equal(form.trustConfirm, "always");
  assert.equal(form.trustReversal, "软删除");
  assert.equal(form.trustBlastRadius, "targets 清单");
  assert.equal(form.inline, false);
  assert.equal(form.verifier, "checks.run");
  assert.equal(form.logicMode, "sandbox");

  const manifest = formToManifest(form);
  assert.equal(manifest.name, "ops.cleanup.execute");
  assert.equal(manifest.kind, "prompt");
  assert.deepEqual(manifest.inputs, DRAFT.manifest.inputs);
  assert.deepEqual(manifest.permissions.tools, ["system.file.delete"]);
  assert.deepEqual(manifest.permissions.blackboard, ["shared"]);
  assert.deepEqual(manifest.model, { prefer: ["kimi/k2"], temperature: 0.2 });
  assert.deepEqual(manifest.limits, { max_steps: 8, timeout: 60, retry: 1, max_tool_calls: 20 });
  assert.deepEqual(manifest.trust,
    { confirm: "always", reversal: "软删除", blast_radius: "targets 清单" });
  assert.equal(manifest.verifier, "checks.run");
  assert.equal(manifest.inline, undefined, "inline=false 不写空壳");
}

/* formToManifest:空可选块整体省略(与 skills.yaml 单条同形) */
{
  const form = draftToForm({ name: "a.b", manifest: { name: "a.b" }, prompt: "" });
  const manifest = formToManifest(form);
  assert.equal(manifest.version, "0.1.0");
  assert.deepEqual(manifest.permissions, { tools: [], skills: [] });
  assert.ok(!("model" in manifest));
  assert.ok(!("limits" in manifest));
  assert.ok(!("trust" in manifest));
  assert.ok(!("logic" in manifest));
  assert.ok(!("blackboard" in (manifest.permissions ?? {})));
}

/* ── tier 徽标与悬停明细(升权卡片同一色板槽位)────────────── */
{
  assert.ok(tierBadgeHtml("irreversible").includes('data-perm="EXEC"'));
  assert.ok(tierBadgeHtml("reversible").includes('data-perm="WRITE"'));
  assert.ok(tierBadgeHtml("none").includes('data-perm="READ"'));
  assert.ok(tierBadgeHtml("bogus").includes('data-perm="READ"'), "未知档回落 none");
  const title = tierTitle({ top: [{ kind: "tool", name: "system.file.delete", tier: "irreversible" }] });
  assert.equal(title, "tool system.file.delete: irreversible");
}

/* ── editorHtml:七组渲染;inline ≥L2 禁用并提示 ────────────── */
{
  const view = {
    form: draftToForm(DRAFT),
    tier: "irreversible",
    tierDetail: { top: [{ kind: "tool", name: "system.file.delete", tier: "irreversible" }] },
    toolsCatalog: [{ name: "system.file.delete", perm: "WRITE" }, { name: "system.file.read", perm: "READ" }],
    skillsCatalog: [{ name: "common.text.extract_json" }],
  };
  const html = editorHtml(view);
  for (const g of ["identity", "contract", "instruction", "permissions", "policy", "trust", "behavior"]) {
    assert.ok(html.includes(`data-group="${g}"`), `分组 ${g}`);
  }
  assert.ok(html.includes('data-perm="EXEC"'), "推导档徽标");
  assert.ok(html.includes("disabled"), "≥L2 时 inline 禁用");
  assert.ok(html.includes("禁止 inline") || html.includes("禁 inline"), "硬闸门提示");
  assert.ok(html.includes("system.file.delete"), "已选 chips");
  assert.ok(html.includes('data-field="inputsText"'), "inputs JSON 文本域");
  assert.ok(html.includes('data-field="limMaxToolCalls"'), "limits 全字段");
  const l1 = editorHtml({ ...view, tier: "none" });
  assert.ok(!/data-field="inline"[^>]*disabled/.test(l1), "L1 时 inline 可用");
}

/* ── topbarHtml / statusLine ─────────────────────────────── */
{
  const html = topbarHtml({
    name: "ops.cleanup.execute",
    drafts: [{ name: "ops.cleanup.execute" }, { name: "weather.query" }],
    skillsCatalog: [{ name: "common.text.extract_json" }],
    tier: "reversible",
    tierDetail: null,
  });
  assert.ok(html.includes('value="weather.query"'), "草稿下拉");
  assert.ok(html.includes('data-lab="create"'), "新建按钮");
  assert.ok(html.includes('data-lab="save"'), "保存按钮");
  assert.ok(html.includes("disabled"), "检查/提交置灰(L2)");
  assert.ok(html.includes('data-perm="WRITE"'));
  assert.match(statusLine({ savedAt: null }), /草稿|draft|草/i);
  assert.match(statusLine({ savedAt: new Date(2026, 7, 1, 9, 5).getTime() }), /09:05/);
}

/* ── DOM 冒烟:openLab 加载 → saveCurrentDraft PUT(fetch stub)── */
{
  const doc = makeDocument();
  globalThis.document = doc;
  // toast 需要 #toastStack(同 smoke-d5 先例)
  const toastStack = doc.createElement("div");
  toastStack.setAttribute("id", "toastStack");
  doc.body.appendChild(toastStack);
  doc.querySelector = (sel) => doc.body.querySelector(sel);
  const calls = [];
  const DRAFT_MIN = {
    name: "weather.query",
    manifest: {
      name: "weather.query", version: "0.1.0", kind: "prompt",
      description: "x", inputs: { type: "object" }, outputs: { type: "object" },
      permissions: { tools: ["system.file.write"], skills: [] },
    },
    prompt: "你是天气员。", handler: null, parse_error: null, tests: {},
  };
  globalThis.fetch = async (path, options = {}) => {
    const url = String(path);
    calls.push({ url, method: options.method ?? "GET", body: options.body });
    const reply = (data) => ({ ok: true, status: 200, json: async () => data });
    if (url === "/api/lab/drafts") return reply([{ name: "weather.query", tier: "reversible", mtime: 1 }]);
    if (url === "/api/lab/drafts/weather.query") return reply(DRAFT_MIN);
    if (url.startsWith("/api/lab/drafts/weather.query/tier")) {
      return reply({ tier: "reversible", sources: [], top: [{ kind: "tool", name: "system.file.write", tier: "reversible" }], parse_error: null });
    }
    if (url === "/api/tools") return reply([{ name: "system.file.write", permission: "WRITE" }]);
    if (url === "/api/skills") return reply([]);
    throw new Error(`未 stub 的请求: ${url}`);
  };

  const main = doc.createElement("main");
  doc.body.appendChild(main);
  openLab(main);
  await new Promise((r) => setTimeout(r, 0)); // 异步装载(drafts + catalogs + draft + tier)
  await new Promise((r) => setTimeout(r, 0));

  const saved = await saveCurrentDraft();
  assert.ok(saved, "保存返回草稿");
  const put = calls.find((c) => c.method === "PUT");
  assert.ok(put, "PUT 发出");
  const payload = JSON.parse(put.body);
  assert.equal(payload.manifest.name, "weather.query");
  assert.deepEqual(payload.manifest.permissions.tools, ["system.file.write"]);
  assert.equal(payload.prompt, "你是天气员。");
  const tierCalls = calls.filter((c) => c.url.includes("/tier"));
  assert.ok(tierCalls.length >= 1, "推导档刷新请求");
  assert.ok(tierCalls[0].url.includes("tools=system.file.write"), "未保存白名单随查询覆盖");
  closeLab();
}

/* ── L2:五关卡片 / promoteReady / 过期提示(§1.4/§2.3)──────────────── */
{
  const {
    gateCardsHtml,
    isReportStale,
    promoteReady,
    statusLine,
    topbarHtml,
  } = await import("../js/components/lab.js");

  const report = {
    report_id: "r1",
    status: "fail",
    created_at: 1000,
    gates: {
      g1: { status: "pass", findings: [] },
      g2: { status: "warn", findings: [{ level: "warn", clause: "TIER-STANDARDS.md §2", message: "参数缺 type" }] },
      g3: { status: "fail", findings: [{ level: "fail", clause: "TIER-STANDARDS.md §4", message: "L2 必填 trust.reversal" }] },
      g4: { status: "skip", note: "冒烟试跑,L3 实现", findings: [] },
      g5: { status: "skip", note: "提示词卫生,L5 实现", findings: [] },
    },
  };
  const html = gateCardsHtml(report);
  assert.ok(html.includes('data-status="pass"'), "绿卡");
  assert.ok(html.includes('data-status="warn"'), "黄卡");
  assert.ok(html.includes('data-status="fail"'), "红卡");
  assert.ok((html.match(/data-status="skip"/g) ?? []).length >= 2, "G4/G5 灰卡占位");
  assert.ok(html.includes("TIER-STANDARDS.md §4"), "fail 带条款号");
  assert.ok(html.includes("trust.reversal"), "finding 文本");
  assert.ok(html.includes("L3 实现"), "skip note");

  // promoteReady:fail 拒;stale 拒;warn 需 ack;pass 放
  assert.equal(promoteReady({ report }), false);
  assert.equal(promoteReady({ report: { ...report, status: "pass" } }), true);
  assert.equal(promoteReady({ report: { ...report, status: "warn" }, ackWarn: false }), false);
  assert.equal(promoteReady({ report: { ...report, status: "warn" }, ackWarn: true }), true);
  assert.equal(isReportStale({ report, savedAt: 1000 * 1000 + 1 }), true, "保存晚于报告 → 过期");
  assert.equal(isReportStale({ report, savedAt: 1 }), false);
  assert.equal(promoteReady({ report: { ...report, status: "pass" }, savedAt: 1000 * 1000 + 1 }), false);

  // topbar:pass 报告 → 提交点亮;无报告 → 熄灭
  const top = topbarHtml({ name: "a.b", drafts: [], skillsCatalog: [], tier: "none",
    report: { status: "pass", created_at: 1 }, ackWarn: false });
  assert.ok(!/data-lab="promote"[^>]*disabled/.test(top), "全绿后提交按钮点亮");
  const topNoReport = topbarHtml({ name: "a.b", drafts: [], skillsCatalog: [], tier: "none" });
  assert.ok(/data-lab="promote"[^>]*disabled/.test(topNoReport), "无报告提交熄灭");
  assert.ok(/data-lab="check"/.test(topNoReport) && !/data-lab="check"[^>]*disabled/.test(topNoReport),
    "检查按钮 L2 起解锁");

  // 状态栏:过期提示
  assert.match(statusLine({ report, savedAt: 1000 * 1000 + 1 }), /改动|changed|改/);
  assert.ok(!statusLine({ report, savedAt: 1 }).includes("⚠"));
}

/* ── L2 DOM 冒烟:validate → 卡片/ack 门 → promote(fetch stub)────────── */
{
  const { openLab, closeLab, runCheck } = await import("../js/components/lab.js");
  const doc = makeDocument();
  globalThis.document = doc;
  const toastStack = doc.createElement("div");
  toastStack.setAttribute("id", "toastStack");
  doc.body.appendChild(toastStack);
  doc.querySelector = (sel) => doc.body.querySelector(sel);

  const calls = [];
  const DRAFT_MIN = {
    name: "weather.query",
    manifest: { name: "weather.query", version: "0.1.0", kind: "prompt",
      description: "x", inputs: { type: "object" }, outputs: { type: "object" },
      permissions: { tools: [], skills: [] } },
    prompt: "你是天气员。", handler: null, parse_error: null, tests: {},
  };
  const REPORT = {
    report_id: "r-1", status: "warn", created_at: 100, manifest_hash: "h",
    gates: { g1: { status: "warn", findings: [{ level: "warn", clause: "c", message: "m" }] },
      g2: { status: "pass", findings: [] }, g3: { status: "pass", findings: [] },
      g4: { status: "skip", findings: [] }, g5: { status: "skip", findings: [] } },
  };
  globalThis.fetch = async (path, options = {}) => {
    const url = String(path);
    calls.push({ url, method: options.method ?? "GET", body: options.body });
    const reply = (data) => ({ ok: true, status: 200, json: async () => data });
    if (url === "/api/lab/drafts") return reply([{ name: "weather.query", tier: "none", mtime: 1 }]);
    if (url === "/api/lab/drafts/weather.query") return reply(DRAFT_MIN);
    if (url.startsWith("/api/lab/drafts/weather.query/tier")) return reply({ tier: "none", sources: [], top: [] });
    if (url === "/api/tools") return reply([]);
    if (url === "/api/skills") return reply([]);
    if (url === "/api/lab/drafts/weather.query/validate") return reply(REPORT);
    if (url === "/api/lab/drafts/weather.query/promote") {
      return reply({ name: "weather.query", version: "0.1.0", action: "appended" });
    }
    throw new Error(`未 stub 的请求: ${url}`);
  };

  const main = doc.createElement("main");
  doc.body.appendChild(main);
  openLab(main);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const { confirmPromote } = await import("../js/components/lab.js");
  const report = await runCheck();
  assert.equal(report.report_id, "r-1");
  const promotePost = () => calls.find((c) => c.url.endsWith("/promote"));
  assert.ok(!promotePost(), "未确认前不发 promote");

  // warn + ack → promote 请求体带 report_id / warnings_ack / version
  const result = await confirmPromote({ version: "1.0.0", warningsAck: true });
  assert.equal(result.action, "appended");
  const body = JSON.parse(promotePost().body);
  assert.deepEqual(body, { report_id: "r-1", version: "1.0.0", warnings_ack: true });

  // 报告已消费:再提交返回 null(下一次迭代需重新检查)
  assert.equal(await confirmPromote({ warningsAck: true }), null);

  // 被拒路径(409):上抛给调用方(按钮处理器 toast)
  globalThis.fetch = async (path, options = {}) => {
    const reply = (data) => ({ ok: true, status: 200, json: async () => data });
    if (String(path) === "/api/lab/drafts") return reply([{ name: "weather.query" }]);
    if (String(path) === "/api/lab/drafts/weather.query") return reply(DRAFT_MIN);
    if (String(path).startsWith("/api/lab/drafts/weather.query/tier")) return reply({ tier: "none", sources: [], top: [] });
    if (String(path) === "/api/tools" || String(path) === "/api/skills") return reply([]);
    if (String(path) === "/api/lab/drafts/weather.query/validate") return reply(REPORT);
    return { ok: false, status: 409, json: async () => ({ detail: "报告与当前草稿不一致" }) };
  };
  openLab(main);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  await runCheck();
  await assert.rejects(confirmPromote({ warningsAck: true }), /不一致/, "409 被拒原因上抛");
  closeLab();
}

/* ── L3:测试面板渲染 / 试跑流程 / 结果与 outputs 校验(§2.1 右栏)──────── */
{
  const { testPanelHtml, testResultHtml } = await import("../js/components/lab.js");

  // 面板骨架:输入框 / 用例下拉 / 试跑按钮
  const panel = testPanelHtml({
    draftTests: ["case1.json", "bad.json"],
    testCase: "bad.json",
    testInput: '{"city": "北京"}',
    testRun: null,
  });
  assert.ok(panel.includes("data-lab-input"), "输入 JSON 编辑框");
  assert.ok(panel.includes('value="bad.json" selected'), "用例下拉选中");
  assert.ok(panel.includes('data-lab="test-run"'), "试跑按钮");

  // 结果区:running / done + outputs ✓ / ✗
  assert.match(testResultHtml({ status: "running" }), /试跑中|running|LOADING|试行中/);
  const done = testResultHtml({
    status: "done", result: { answer: "ok" },
    check: { ok: true, error: null },
  });
  assert.ok(done.includes("&quot;answer&quot;"), "result 摘要(esc 后实体)");
  assert.ok(done.includes('data-ok="true"'), "outputs 校验绿");
  const bad = testResultHtml({
    status: "done", result: { wrong: 1 },
    check: { ok: false, error: "outputs 校验失败: 缺 answer" },
  });
  assert.ok(bad.includes('data-ok="false"'), "outputs 校验红");
  assert.ok(bad.includes("缺 answer"), "失败原因透出");
  assert.equal(testResultHtml(null), "", "未跑为空");
}

/* ── L3 DOM 冒烟:试跑全流程(fetch stub:POST → 轮询 check → signals)───── */
{
  const { openLab, closeLab, startTestRun } = await import("../js/components/lab.js");
  const doc = makeDocument();
  globalThis.document = doc;
  const toastStack = doc.createElement("div");
  toastStack.setAttribute("id", "toastStack");
  doc.body.appendChild(toastStack);
  doc.querySelector = (sel) => doc.body.querySelector(sel);

  const calls = [];
  const DRAFT_MIN = {
    name: "weather.query",
    manifest: { name: "weather.query", version: "0.1.0", kind: "prompt",
      description: "x", inputs: { type: "object" }, outputs: { type: "object" },
      permissions: { tools: [], skills: [] } },
    prompt: "你是天气员。", handler: null, parse_error: null,
    tests: { "case1.json": '{"input": {}}' },
  };
  const SIGNALS = [
    { v: 1, type: "signal", name: "run.started", run_id: "run-1", ts: 1, payload: {} },
    { v: 1, type: "signal", name: "post:llm.response", run_id: "run-1", frame_id: "f1",
      ts: 2, payload: {} },
  ];
  globalThis.fetch = async (path, options = {}) => {
    const url = String(path);
    calls.push({ url, method: options.method ?? "GET", body: options.body });
    const reply = (data) => ({ ok: true, status: 200, json: async () => data });
    if (url === "/api/lab/drafts") return reply([{ name: "weather.query" }]);
    if (url === "/api/lab/drafts/weather.query") return reply(DRAFT_MIN);
    if (url.startsWith("/api/lab/drafts/weather.query/tier")) return reply({ tier: "none", sources: [], top: [] });
    if (url === "/api/tools" || url === "/api/skills") return reply([]);
    if (url === "/api/lab/drafts/weather.query/test-run") return reply({ run_id: "run-1" });
    if (url === "/api/lab/drafts/weather.query/runs/run-1/check") {
      return reply({ run_id: "run-1", status: "done", result: { answer: "ok" },
        error: null, outputs_check: { ok: true, error: null } });
    }
    if (url === "/api/runs/run-1/signals") return reply(SIGNALS);
    throw new Error(`未 stub 的请求: ${url}`);
  };

  const main = doc.createElement("main");
  doc.body.appendChild(main);
  openLab(main);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const run = await startTestRun();
  assert.equal(run.status, "done");
  assert.deepEqual(run.result, { answer: "ok" });
  assert.equal(run.check.ok, true);
  const post = calls.find((c) => c.url.endsWith("/test-run"));
  assert.deepEqual(JSON.parse(post.body), { input: {} },
    "未选用例时走输入框 JSON(空输入回落 {})");
  closeLab();
}

console.log("lab.test.mjs: all assertions passed");
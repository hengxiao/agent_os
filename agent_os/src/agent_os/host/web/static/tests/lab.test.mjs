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

console.log("lab.test.mjs: all assertions passed");

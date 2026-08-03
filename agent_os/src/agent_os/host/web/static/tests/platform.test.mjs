/* web_platform 前端样品单测(docs/WEB-PLATFORM.md §10):
   cards.js 双层渲染——技术层 cardHtml(六卡型结构/actions 按钮/warnings 勾选门)
   + 摘要层 summaryHtml(人话一句结论,数据驱动;断言可见文字不含禁忌词:
   tier/manifest/hash/G1-G5/条款号/ProviderError 等),详情层断言技术字段保留;
   app.js 对话流(fetch stub:会话列表 → 发消息 → agent 卡渲染 →
   点卡动作 → action 请求体 → 返回卡追加;骨架 loading / 错误态);
   tab 条模型(openTab 去重聚焦 / closeTab 回落)与五类详情视图
   (gate/plan/diff 数据在卡内,pack/run 拉取;loading/error/重试;✕ 关闭回对话)。
   运行:node static/tests/platform.test.mjs */

import assert from "node:assert/strict";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url); // "/static/js/" → 旧 web 共享模块

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const { cardHtml, summaryHtml } = await import("../../../web_platform/static/cards.js");
const { gateDetailHtml, packDetailHtml, planDetailHtml, runDetailHtml, diffDetailHtml, escDetailHtml, decomposeDetailHtml } =
  await import("../../../web_platform/static/details.js");

/* ── 两层边界工具 ─────────────────────────────────────────────
   摘要层禁区只看**用户可见文字**(剥标签+属性;data-detail/data-payload
   属性里的技术 JSON 不渲染上屏,不算泄漏);详情层则必须留得住技术面。 */
const visibleText = (html) => html.replace(/<[^>]*>/g, "");
const FORBIDDEN = [
  "irreversible", "reversible", "tier", "manifest", "package_hash", "manifest_hash",
  "report_id", "G1", "G2", "G3", "G4", "G5", "TIER-STANDARDS", "ESCALATION",
  "schema", "closure", "promote", "ProviderError",
];
const assertClean = (html, who) => {
  const t = visibleText(html);
  for (const w of FORBIDDEN) assert.ok(!t.includes(w), `${who} 摘要层禁见 ${w}`);
  return t;
};

/* ── 六卡型渲染 ─────────────────────────────────────────────── */

{
  const plan = cardHtml({
    type: "plan", v: 1,
    data: { goal: "做个晚餐技能", reuse: [{ name: "weather.query", reason: "已覆盖" }],
      create: [{ name: "lab.dinner", template: "prompt_query", reason: "主技能" }] },
    actions: [{ id: "scaffold.approve", label: "批准", method: "POST",
      endpoint: "/api/lab/drafts", payload: { name: "lab.dinner" } }],
  });
  assert.ok(plan.includes('data-card="plan"'));
  assert.ok(plan.includes("weather.query"), "复用行");
  assert.ok(plan.includes("lab.dinner"), "新建行");
  assert.ok(plan.includes('data-card-act="scaffold.approve"'), "批准动作按钮");
  assert.ok(plan.includes("lab.dinner"), "payload 随行");
}

{
  const pack = cardHtml({
    type: "skill_pack", v: 1,
    data: { name: "lab.dinner", tier: "reversible", members: ["lab.dinner", "lab.dinner.plan"] },
    actions: [],
  });
  assert.ok(pack.includes('data-perm="WRITE"'), "tier 徽标(reversible→WRITE 槽位)");
  assert.ok(pack.includes("lab.dinner.plan"), "成员 chips");
}

{
  const gate = cardHtml({
    type: "gate_report", v: 1,
    data: { draft: "lab.dinner", status: "warn",
      gates: { g1: { status: "pass", findings: [] },
        g2: { status: "warn", findings: [{ level: "warn", clause: "c", message: "description 太短" }] } } },
    actions: [],
  });
  assert.ok(gate.includes('data-status="warn"'), "黄关色点");
  assert.ok(gate.includes("description 太短"), "findings 可展开");
  assert.ok(gate.includes("/#/lab/lab.dinner"), "去修复跳 Lab");
}

{
  const diff = cardHtml({
    type: "diff", v: 1,
    data: { name: "lab.dinner", diff: { has_changes: true, members: [{
      member: "lab.dinner", status: "changed",
      fields: [{ kind: "changed", path: "description", old: "旧", new: "新" }],
      prompt_diff: [{ kind: "add", text: "雨天优先便携" }, { kind: "del", text: "旧句" }],
      tests: { added: [], removed: [] } }] } },
    actions: [
      { id: "candidate.accept", label: "✓ 接受", method: "POST",
        endpoint: "/api/lab/drafts/{name}/candidate/accept", payload: { name: "lab.dinner" } },
      { id: "candidate.discard", label: "放弃", method: "POST",
        endpoint: "/api/lab/drafts/{name}/candidate/discard", payload: { name: "lab.dinner" } },
    ],
  });
  assert.ok(diff.includes('data-kind="add"'), "绿行");
  assert.ok(diff.includes('data-kind="del"'), "红行");
  assert.ok(diff.includes('data-card-act="candidate.accept"'), "接受按钮");
  assert.ok(diff.includes('data-card-act="candidate.discard"'), "放弃按钮");
}

{
  const publish = cardHtml({
    type: "publish", v: 1,
    data: { root: "lab.dinner", plan_id: "plan-abc123",
      members: [
        { name: "lab.dinner", action: "create", from_version: null, to_version: "0.1.0" },
        { name: "lab.dinner.plan", action: "unchanged", from_version: "0.1.0", to_version: "0.1.0" },
      ] },
    actions: [{ id: "plan.confirm", label: "确认发布", method: "POST",
      endpoint: "/api/lab/packages/promote", payload: { plan_id: "plan-abc123" } }],
  });
  assert.ok(publish.includes('data-action="create"'), "create 三态行");
  assert.ok(publish.includes('data-action="unchanged"'), "unchanged 行");
  assert.ok(publish.includes("data-ack"), "warnings 勾选门");
  assert.ok(publish.includes('data-card-act="plan.confirm"'), "确认发布按钮");
}

{
  const table = cardHtml({
    type: "table", v: 1,
    data: { title: "最近失败 run", columns: ["run", "skill", "摘要"], rows: [["a1b2", "ops.janitor", "outputs 错"]] },
    actions: [],
  });
  assert.ok(table.includes("<th>run</th>"), "表头");
  assert.ok(table.includes("ops.janitor"), "行内容");
}

/* ── 详情链接(四卡型;plan/diff 不带)────────────────────────── */

{
  const gate = cardHtml({
    type: "gate_report", v: 1,
    data: { draft: "lab.dinner", status: "pass", gates: {} },
    actions: [],
  });
  assert.ok(gate.includes('data-detail-kind="gate"'), "gate 卡带详情链接");
  assert.ok(gate.includes('data-detail-ref="lab.dinner"'), "gate 详情锚 = draft 名");
}

{
  const pack = cardHtml({
    type: "skill_pack", v: 1,
    data: { name: "lab.dinner", tier: "reversible", members: ["lab.dinner"] },
    actions: [],
  });
  assert.ok(pack.includes('data-detail-kind="pack"'), "pack 卡带详情链接");
  assert.ok(pack.includes('data-detail-ref="lab.dinner"'), "pack 详情锚 = 包名");
}

{
  const publish = cardHtml({
    type: "publish", v: 1,
    data: { root: "lab.dinner", plan_id: "plan-abc123", members: [] },
    actions: [],
  });
  assert.ok(publish.includes('data-detail-kind="plan"'), "publish 卡带详情链接");
  assert.ok(publish.includes('data-detail-ref="plan-abc123"'), "plan 详情锚 = plan_id");
}

{
  const withRef = cardHtml({
    type: "table", v: 1,
    data: { title: "t", columns: ["a"], rows: [["b"]], ref: { kind: "run", id: "run-1" } },
    actions: [],
  });
  assert.ok(withRef.includes('data-detail-kind="run"'), "table(run 摘要)带详情链接");
  assert.ok(withRef.includes('data-detail-ref="run-1"'), "run 详情锚 = 完整 run_id");
  const noRef = cardHtml({
    type: "table", v: 1,
    data: { title: "t", columns: ["a"], rows: [["b"]] },
    actions: [],
  });
  assert.ok(!noRef.includes("data-detail-kind"), "无 ref 的 table 不带详情链接");
  const plan = cardHtml({ type: "plan", v: 1, data: { goal: "x", reuse: [], create: [] }, actions: [] });
  assert.ok(!plan.includes("data-detail-kind"), "plan 卡不带详情链接");
  const diff = cardHtml({ type: "diff", v: 1, data: { name: "x", diff: { members: [] } }, actions: [] });
  assert.ok(!diff.includes("data-detail-kind"), "diff 卡不带详情链接");
}

/* ── 摘要层(人话):六卡型 summaryHtml + 两层边界 ─────────────── */

{
  const s = summaryHtml({
    type: "plan", v: 1,
    data: { goal: "晚餐推荐",
      reuse: [{ name: "weather.query", reason: "已覆盖" }],
      create: [
        { name: "lab.dinner", template: "prompt_query", reason: "主技能" },
        { name: "lab.calendar", template: "prompt_query", reason: "读日程" },
      ] },
    actions: [{ id: "scaffold.approve", label: "批准", method: "POST",
      endpoint: "/api/lab/drafts", payload: {} }],
  });
  const t = assertClean(s, "plan");
  assert.ok(t.includes("晚餐推荐"), "plan 摘要带目标");
  assert.ok(t.includes("weather.query") && t.includes("lab.dinner") && t.includes("lab.calendar"), "技能名保留");
  assert.ok(!t.includes("prompt_query"), "template 结构数据不进摘要");
  assert.ok(s.includes("pf-card-lead"), "一句结论(加粗槽位)");
  assert.ok(s.includes('data-card-act="scaffold.approve"'), "动作按钮保留在摘要卡");
}

{
  const s = summaryHtml({
    type: "skill_pack", v: 1,
    data: { name: "lab.dinner", tier: "irreversible", members: ["lab.dinner", "lab.dinner.plan"] },
    actions: [],
  });
  const t = assertClean(s, "skill_pack");
  assert.ok(t.includes("lab.dinner"), "包名保留");
  assert.ok(t.includes("2"), "成员数入句");
  assert.ok(t.includes("审批"), "tier=irreversible → 人话审批提示");
  assert.ok(s.includes('data-detail-kind="pack"'), "成员表/tier 徽标收进详情链接");
  // 详情层:技术面留得住
  const dt = packDetailHtml({ root: "lab.dinner", root_tier: "irreversible",
    members: [{ name: "lab.dinner", depth: 0, tier: "irreversible", status: "draft" }], errors: [] });
  assert.ok(dt.includes("irreversible"), "详情层保留 tier 术语");
  assert.ok(dt.includes('data-perm="EXEC"'), "详情层保留 tier 徽标");
}

{
  const s = summaryHtml({
    type: "gate_report", v: 1,
    data: { draft: "lab.dinner", status: "warn",
      gates: {
        G1: { status: "pass", findings: [] },
        G2: { status: "warn", findings: [{ level: "warn", clause: "TIER-STANDARDS.md §2.1", message: "description 太短" }] },
      } },
    actions: [],
  });
  const t = assertClean(s, "gate_report");
  assert.ok(t.includes("1"), "通过计数入句");
  assert.ok(t.includes("description 太短"), "建议内容人话呈现");
  assert.ok(s.includes('data-detail-kind="gate"'), "五关/条款号收进详情链接");
  const dt = gateDetailHtml({
    draft: "lab.dinner", status: "warn",
    gates: { G2: { status: "warn", findings: [{ level: "warn", clause: "TIER-STANDARDS.md §2.1", message: "description 太短" }] } },
  });
  assert.ok(dt.includes("TIER-STANDARDS.md §2.1"), "详情层保留条款号");
  assert.ok(dt.includes("G2"), "详情层保留关号");
}

{
  const card = {
    type: "diff", v: 1,
    data: { name: "lab.dinner", diff: { has_changes: true, members: [{
      member: "lab.dinner", status: "changed",
      fields: [{ kind: "changed", path: "description", old: "旧文案", new: "新文案" }],
      prompt_diff: [{ kind: "add", text: "雨天优先便携" }, { kind: "del", text: "旧句" }],
      tests: { added: ["t1", "t2"], removed: [] } }] } },
    actions: [],
  };
  const s = summaryHtml(card);
  const t = assertClean(s, "diff");
  assert.ok(t.includes("lab.dinner"), "diff 摘要带对象名");
  assert.ok(t.includes("说明") && t.includes("措辞"), "字段路径翻译成人话");
  assert.ok(t.includes("+2"), "用例增减计数入句");
  assert.ok(!t.includes("旧文案") && !t.includes("新文案"), "新旧内容不进摘要");
  assert.ok(s.includes('data-detail-kind="diff"'), "红绿细节收进详情链接");
  const dt = diffDetailHtml(card.data);
  assert.ok(dt.includes('data-kind="add"') && dt.includes('data-kind="del"'), "详情层保留红绿行");
  assert.ok(dt.includes("旧文案") && dt.includes("新文案"), "详情层保留字段新旧值");
}

{
  const s = summaryHtml({
    type: "publish", v: 1,
    data: { root: "lab.dinner", plan_id: "plan-x", package_hash: "abc123hash",
      members: [
        { name: "a", action: "create", from_version: null, to_version: "0.1.0" },
        { name: "b", action: "create", from_version: null, to_version: "0.1.0" },
        { name: "c", action: "replace", from_version: "0.1.0", to_version: "0.2.0" },
        { name: "d", action: "unchanged", from_version: "0.1.0", to_version: "0.1.0" },
      ],
      blockers: [{ kind: "gate", member: "a", message: "x" }], warnings: ["w1"] },
    actions: [{ id: "plan.confirm", label: "确认发布", method: "POST",
      endpoint: "/api/lab/packages/promote", payload: { plan_id: "plan-x" } }],
  });
  const t = assertClean(s, "publish");
  assert.ok(t.includes("4"), "总数入句");
  assert.ok(t.includes("2 个新建") && t.includes("1 个更新") && t.includes("1 个不变"), "三态计数入句");
  assert.ok(t.includes("1 个阻塞") && t.includes("1 条警告"), "阻塞/警告计数入句");
  assert.ok(s.includes("data-ack"), "warnings 勾选门留在摘要(动作而非术语)");
  const dt = planDetailHtml({
    root: "lab.dinner", plan_id: "plan-x", package_hash: "abc123hash",
    members: [{ name: "a", action: "create", gate_status: "pass", from_version: null, to_version: "0.1.0" }],
    blockers: [], warnings: [],
  });
  assert.ok(dt.includes("abc123hash"), "详情层保留 package_hash");
  assert.ok(dt.includes('data-action="create"'), "详情层保留三态行");
}

{
  const s = summaryHtml({
    type: "table", v: 1,
    data: { title: "最近失败 run", columns: ["run", "skill", "错误摘要"],
      rows: [["a1b2c3d4", "demo.fib", "ProviderError: quota exceeded"]],
      ref: { kind: "run", id: "a1b2c3d4-full" } },
    actions: [],
  });
  const t = assertClean(s, "table(run)");
  assert.ok(t.includes("demo.fib"), "技能名保留");
  assert.ok(t.includes("模型服务不可用"), "ProviderError 翻译成人话");
  assert.ok(!t.includes("a1b2c3d4"), "run id 不进摘要");
  assert.ok(s.includes('data-detail-ref="a1b2c3d4-full"'), "run 详情锚保留");
  const dt = runDetailHtml({
    detail: { skill: "demo.fib", status: "failed", error: "ProviderError: quota exceeded", result: null },
    signals: [],
  });
  assert.ok(dt.includes("ProviderError: quota exceeded"), "详情层保留错误原文");
}

{
  // 无 run ref 的通用表(help 卡)= 本身即摘要,保持表格原样
  const s = summaryHtml({
    type: "table", v: 1,
    data: { title: "我能做什么", columns: ["说法", "效果"], rows: [["做个 X", "出计划"]] },
    actions: [],
  });
  assert.ok(s.includes("<table"), "通用表摘要 = 表格本体");
  assert.ok(!s.includes("data-detail-kind"), "通用表不带详情链接");
}

{
  // escalation(W2)摘要:人话 + 三按钮;L3 两枚(L2 才有"本次都批");禁忌词纪律
  const l3 = summaryHtml({
    type: "escalation", v: 1,
    data: { question_id: "esc-1", skill: "ops.janitor", tier: "irreversible",
      reason_hint: "none → irreversible", params: { path: "/tmp/x" },
      requested: { tools: ["fs.write"], skills: [] },
      options: ["approve-once", "deny"], asked_at: 1 },
    actions: [],
  });
  const t = assertClean(l3, "escalation");
  assert.ok(t.includes("ops.janitor"), "升权摘要带 skill 名");
  assert.ok(t.includes("需要你批准"), "人话决策请求");
  assert.ok(!t.includes("approve-once") && !t.includes("/tmp/x"), "协议串/参数不进摘要");
  const buttons = l3.match(/data-decision="esc-1"/g) ?? [];
  assert.equal(buttons.length, 2, "L3 两枚按钮(无 approve-run)");
  assert.ok(!l3.includes('data-answer="approve-run"'), "L3 不出\"本次都批\"");
  assert.ok(l3.includes('data-detail-kind="esc"'), "esc 详情链接");

  const l2 = summaryHtml({
    type: "escalation", v: 1,
    data: { question_id: "esc-2", skill: "ops.janitor", tier: "reversible",
      reason_hint: "none → reversible", params: {}, requested: {},
      options: ["approve-once", "approve-run", "deny"], asked_at: 1 },
    actions: [],
  });
  assert.ok(l2.includes('data-answer="approve-run"'), "L2 有\"本次都批\"");

  const resolved = summaryHtml({
    type: "escalation", v: 1,
    data: { question_id: "esc-1", skill: "ops.janitor", tier: "irreversible",
      options: ["approve-once", "deny"], asked_at: 1, resolved: "approve-once" },
    actions: [],
  });
  assert.ok(resolved.includes("已批准"), "已决状态字");
  assert.ok(resolved.includes("disabled"), "已决按钮置灰");

  const dt = escDetailHtml({
    skill: "ops.janitor", reason_hint: "none → irreversible",
    params: { path: "/tmp/x" }, requested: { tools: ["fs.write"], skills: [] },
  });
  assert.ok(dt.includes("none → irreversible"), "详情层保留 reason_hint 原文");
  assert.ok(dt.includes("/tmp/x"), "详情层保留参数 JSON");
  assert.ok(dt.includes("fs.write"), "详情层保留请求权限集");
}

{
  // plan 摘要(N6):人话不变,结构 + 路由角标收 decompose 详情
  const s = summaryHtml({
    type: "plan", v: 1,
    data: { goal: "晚餐推荐",
      reuse: [{ name: "weather.query", reason: "已覆盖" }],
      create: [{ name: "dinner.planner", template: "prompt_query", reason: "主技能" }],
      route_meta: { route: "llm" } },
    actions: [],
  });
  const t = assertClean(s, "plan(N6)");
  assert.ok(s.includes('data-detail-kind="decompose"'), "decompose 详情链接");
  assert.ok(!t.includes("llm") && !t.includes("route_meta"), "路由标注不进摘要层(禁忌词纪律)");
  const dt = decomposeDetailHtml({
    goal: "晚餐推荐",
    reuse: [{ name: "weather.query", reason: "已覆盖" }],
    create: [{ name: "dinner.planner", template: "prompt_query", reason: "主技能" }],
    route_meta: { route: "rule", reason: "llm_unavailable" },
  });
  assert.ok(dt.includes("规则路由"), "详情层路由角标");
  assert.ok(dt.includes("llm_unavailable"), "详情层 reason 机器码");
  assert.ok(dt.includes("prompt_query"), "详情层 template 结构数据");
  assert.ok(dt.includes("dinner.planner"), "详情层分解成员");
}

/* ── M2 嵌套层级(docs/APP-MODEL.md §6):第 3 层卡只读 ────────── */
{
  const card = {
    type: "gate_report", v: 1,
    data: { draft: "lab.dinner", status: "pass", gates: {} },
    actions: [],
  };
  assert.ok(summaryHtml(card, 1).includes("data-detail-kind"), "第 1 层(对话流)有打开链接");
  assert.ok(summaryHtml(card, 2).includes("data-detail-kind"), "第 2 层(tab 内嵌)有打开链接");
  assert.ok(!summaryHtml(card, 3).includes("data-detail-kind"), "第 3 层只读,无打开链接(防套娃)");
  assert.ok(summaryHtml(card, 3).includes("pf-card-lead"), "第 3 层内容照渲(只读 ≠ 不读)");
}

{
  // M3 新 kind 的 Card Surface(debug/lab_draft):人话摘要,禁忌词纪律沿用
  const dbg = summaryHtml({
    type: "debug", v: 1,
    data: { state: "paused", pause_point: { signal: "pre:tool.call", frame_id: "f2" } },
    actions: [],
  });
  const dt = assertClean(dbg, "debug");
  assert.ok(dt.includes("暂停在"), "debug 卡:暂停点人话");
  assert.ok(dt.includes("等你放行"), "debug 卡:待决人话");
  const draft = summaryHtml({
    type: "lab_draft", v: 1,
    data: { name: "lab.dinner", tier: "reversible", gate_status: "pass" },
    actions: [],
  });
  const tt = assertClean(draft, "lab_draft");
  assert.ok(tt.includes("lab.dinner"), "草稿卡带名");
  assert.ok(!tt.includes("reversible"), "tier 术语不进摘要");
}

/* ── app.js 对话流(fetch stub)───────────────────────────────── */

{
  const doc = makeDocument();
  globalThis.document = doc;
  doc.querySelector = (sel) => doc.body.querySelector(sel);
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };

  // index.html 骨架元素(与 static/index.html 同 id)
  const mk = (tag, id) => {
    const el = doc.createElement(tag);
    el.setAttribute("id", id);
    doc.body.appendChild(el);
    return el;
  };
  mk("div", "themes");
  mk("div", "tabs");
  mk("select", "sessionSel");
  mk("button", "newSession");
  mk("div", "log");
  mk("div", "detailHost");
  mk("div", "inputBar");
  const intent = mk("textarea", "intent");
  mk("button", "send");

  const calls = [];
  let badRunFail = true; // bad-run 首轮加载炸,重试后成功
  let presentCalls = 0;  // 决策轮询:首轮炸(静默)→ 次轮插入 → 之后幂等
  globalThis.fetch = async (path, options = {}) => {
    const url = String(path);
    calls.push({ url, method: options.method ?? "GET", body: options.body });
    const reply = (data) => ({ ok: true, status: 200, json: async () => data });
    if (url === "/platform/api/sessions" && !options.method) {
      return reply([{ id: "s1", title: "晚餐技能", messages: 2, cards: 1, created_at: 1, last_at: 2 }]);
    }
    if (url === "/platform/api/sessions" && options.method === "POST") {
      return reply({ id: "s2", title: "", created_at: 3, messages: [] });
    }
    if (url === "/platform/api/sessions/s1") {
      return reply({ id: "s1", title: "晚餐技能", messages: [
        { id: "m1", role: "user", text: "帮我做个晚餐技能", cards: [], ts: 1 },
      ] });
    }
    if (url === "/platform/api/sessions/s1/messages") {
      return reply({ id: "m2", role: "agent", text: "计划如下", ts: 2,
        cards: [{ type: "plan", v: 1,
          data: { goal: "x", reuse: [], create: [{ name: "lab.dinner", template: "prompt_query", reason: "主技能" }] },
          actions: [{ id: "scaffold.approve", label: "批准", method: "POST",
            endpoint: "/api/lab/drafts", payload: { name: "lab.dinner" } }] }] });
    }
    if (url === "/platform/api/cards/action") {
      const body = JSON.parse(options.body ?? "{}");
      if (body.action_id === "plan.confirm") {
        return { ok: false, status: 409, json: async () => ({ detail: "找不到提交计划: plan-x(请重新生成)" }) };
      }
      return reply({ ok: true, text: "首稿完成: lab.dinner",
        cards: [{ type: "skill_pack", v: 1,
          data: { name: "lab.dinner", tier: "reversible", members: ["lab.dinner"] }, actions: [] }] });
    }
    // M1 新 action 管道(docs/APP-MODEL.md §4):服务端按 manifest 绑定参数
    if (url === "/platform/api/apps/spawn") {
      const body = JSON.parse(options.body ?? "{}");
      return reply({ instance: { id: `app-spawn-${body.ref}`, kind: body.kind, ref: body.ref,
        title: body.title, state: body.state ?? {}, created_by: body.created_by ?? "", created_at: 1 },
        opened: true });
    }
    if (url === "/platform/api/apps/app-p1/actions/scaffold.approve") {
      return reply({ ok: true, text: "首稿完成: lab.dinner",
        cards: [{ type: "skill_pack", v: 1, instance: "app-p2",
          data: { name: "lab.dinner", tier: "reversible", members: ["lab.dinner"] }, actions: [] }],
        instance: { id: "app-p1", kind: "plan", state: {} } });
    }
    if (url === "/platform/api/apps/app-e1/actions/approve-once") {
      return reply({ ok: true, text: "已记录你的决定。", state: { resolved: "approve-once" },
        instance: { id: "app-e1", kind: "escalation", state: { resolved: "approve-once" } } });
    }
    // M3:debug 会话(创建/快照)+ lab-draft 读取 + tab 面动作
    if (url === "/api/debug/sessions" && options.method === "POST") {
      return reply({ session_id: "sess-1", run_id: "run-1", mode: "replay" });
    }
    if (url === "/api/debug/sessions/sess-1") {
      return reply({ session_id: "sess-1", run_id: "run-1", state: "paused",
        pause_point: { signal: "pre:tool.call", frame_id: "f2" },
        breakpoints: [{ kind: "tool", match: "*", hits: 1 }],
        frame_stack: [{ frame_id: "f0", skill: "demo.fib" }, { frame_id: "f2", skill: "demo.fib" }],
        rerunnable: false });
    }
    if (url === "/api/lab/drafts/lab.dinner") {
      return reply({ name: "lab.dinner", manifest: { description: "晚餐推荐" },
        prompt: "你是晚餐规划师。", tests: {} });
    }
    if (url === "/platform/api/apps/app-spawn-sess-1/actions/debug.continue") {
      return reply({ ok: true, text: "已放行。", state: { last_command: "continue" },
        instance: { id: "app-spawn-sess-1", kind: "debug", state: {} } });
    }
    if (url === "/platform/api/apps/app-spawn-lab.dinner/actions/draft.check") {
      return reply({ ok: true, text: "检查完成: pass",
        cards: [{ type: "gate_report", v: 1, data: { draft: "lab.dinner", status: "pass", gates: {} }, actions: [] }],
        instance: { id: "app-spawn-lab.dinner", kind: "lab-draft", state: {} } });
    }
    // M4a:run.launch(发起面)+ iterate run 通道 + ad-hoc run 的 instance fallback
    if (url === "/platform/api/apps/app-spawn-run-1/actions/run.launch") {
      return reply({ ok: true, text: "已发起 demo.fib,新 run: run-2。", run_id: "run-2",
        skill: "demo.fib", status: "running",
        cards: [{ type: "table", v: 1, data: { title: "新 run", columns: ["run", "skill", "摘要"],
          rows: [["run-2", "demo.fib", "已启动"]], ref: { kind: "run", id: "run-2" } }, actions: [] }],
        run_instance: { id: "app-run-2", kind: "run", ref: "run-2",
          state: { run_id: "run-2", skill: "demo.fib", status: "running" } } });
    }
    if (url === "/api/runs/run-2") {
      return reply({ run_id: "run-2", skill: "demo.fib", status: "running", result: null, error: "" });
    }
    if (url === "/api/runs/run-2/signals") return reply([]);
    if (url === "/platform/api/apps/app-p1/actions/iterate.generate") {
      return reply({ ok: true, text: "已生成候选: description 精简", run_id: "run-9",
        run_status: "done", skill: "lab.dinner",
        cards: [{ type: "diff", v: 1, data: { name: "lab.dinner", diff: { has_changes: true, members: [] } }, actions: [] }],
        run_instance: { id: "app-run-9", kind: "run", ref: "run-9",
          state: { run_id: "run-9", skill: "lab.dinner", status: "done" } } });
    }
    if (url === "/api/runs/run-9" || url === "/api/runs/run-9/signals") {
      // ad-hoc run(不走产物面):API 404 → 前端回落 instance state(M4a)
      return { ok: false, status: 404, json: async () => ({ detail: "找不到 run: run-9" }) };
    }
    if (url === "/platform/api/apps/app-spawn-run-9") {
      return reply({ id: "app-spawn-run-9", kind: "run", ref: "run-9", title: "run-9",
        state: { run_id: "run-9", skill: "lab.dinner", status: "done" },
        created_by: "app-p1", created_at: 1 });
    }
    if (url === "/api/lab/packages/lab.dinner/closure?mode=runtime") {
      return reply({ root: "lab.dinner", root_tier: "reversible", members: [
        { name: "lab.dinner", depth: 0, tier: "reversible", status: "draft" },
        { name: "weather.query", depth: 1, tier: "none", status: "production" },
      ], errors: [] });
    }
    if (url === "/api/runs/run-1") {
      return reply({ run_id: "run-1", skill: "ops.janitor", status: "failed", error: "outputs 错", result: null });
    }
    if (url === "/api/runs/run-1/signals") return reply([]);
    if (url === "/api/runs/bad-run") {
      if (badRunFail) {
        badRunFail = false;
        throw new Error("boom");
      }
      return reply({ id: "bad-run", skill: "ops.janitor", status: "ok", result: { x: 1 } });
    }
    if (url === "/api/runs/bad-run/signals") return reply([]);
    if (url === "/platform/api/decisions/esc-1" && options.method === "POST") {
      return reply({ ok: true });
    }
    if (url === "/platform/api/decisions/esc-gone" && options.method === "POST") {
      return { ok: false, status: 404, json: async () => ({ detail: "找不到 supervisor 问题: esc-gone" }) };
    }
    if (url === "/platform/api/sessions/s1/decisions/present") {
      presentCalls += 1;
      if (presentCalls === 1) throw new Error("net down"); // 首轮失败:静默
      if (presentCalls === 2) {
        return reply({ presented: [{ id: "m-esc", role: "agent", text: "「ops.janitor」请求批准", ts: 9,
          cards: [{ type: "escalation", v: 1,
            data: { question_id: "esc-1", skill: "ops.janitor", tier: "irreversible",
              reason_hint: "none → irreversible", params: { path: "/tmp/x" },
              requested: { tools: ["fs.write"] }, options: ["approve-once", "deny"], asked_at: 1 },
            actions: [] }] }] });
      }
      return reply({ presented: [] });
    }
    throw new Error(`未 stub 的请求: ${options.method ?? "GET"} ${url}`);
  };

  await import("../../../web_platform/static/app.js");
  const probe = globalThis.__platform;
  assert.ok(probe, "测试探针在");
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const logHtml = () => doc.querySelector("#log").innerHTML;
  assert.ok(doc.querySelector("#sessionSel").innerHTML.includes("晚餐技能"), "会话下拉渲染");
  assert.ok(doc.querySelector("#tabs").innerHTML.includes('data-tab="conv"'), "conversation 固定首 tab");
  assert.ok(logHtml().includes("帮我做个晚餐技能"), "历史消息渲染(刷新恢复)");

  // 发消息:输入 → 发送 → agent 消息 + plan 卡渲染
  intent.value = "帮我做个查天气的技能";
  doc.querySelector("#send").trigger("click", { target: doc.querySelector("#send") });
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const msgPost = calls.find((c) => c.url.endsWith("/messages"));
  assert.ok(msgPost, "messages 请求发出");
  assert.deepEqual(JSON.parse(msgPost.body), { text: "帮我做个查天气的技能" });
  assert.ok(logHtml().includes("计划如下"), "agent 消息渲染");
  assert.ok(logHtml().includes('data-card="plan"'), "plan 卡渲染");
  assert.ok(!logHtml().includes("pf-card-tag"), "对话流走摘要层(无技术卡型标签)");
  assert.ok(!visibleText(logHtml()).includes("prompt_query"), "对话流摘要可见文字不含结构数据");
  assert.ok(!logHtml().includes("pf-skel"), "骨架 loading 已撤");

  // 点卡动作:批准 → action 请求体 → 返回 skill_pack 卡追加
  const actBtn = new StubEl("button");
  actBtn.dataset.cardAct = "scaffold.approve";
  actBtn.dataset.payload = JSON.stringify({ name: "lab.dinner" });
  actBtn.parentNode = doc.body;
  doc.trigger("click", { target: actBtn });
  await new Promise((r) => setTimeout(r, 0));
  const actPost = calls.find((c) => c.url.endsWith("/cards/action"));
  assert.ok(actPost, "cards/action 请求发出");
  const actBody = JSON.parse(actPost.body);
  assert.equal(actBody.action_id, "scaffold.approve");
  assert.equal(actBody.session_id, "s1", "动作结果挂进当前会话");
  assert.ok(logHtml().includes("首稿完成"), "动作结果以 agent 消息呈现");
  assert.ok(logHtml().includes('data-card="skill_pack"'), "返回的新卡渲染");

  // 错误态:plan.confirm 409 → 失败原因以 agent 消息呈现(同一会话内)
  const badBtn = new StubEl("button");
  badBtn.dataset.cardAct = "plan.confirm";
  badBtn.dataset.payload = JSON.stringify({ plan_id: "plan-x" });
  badBtn.parentNode = doc.body;
  doc.trigger("click", { target: badBtn });
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(logHtml().includes("找不到提交计划"), "错误以 agent 消息呈现原因");

  /* ── tab 条模型(纯函数)────────────────────────────────── */

  const { openTab, closeTab } = await import("../../../web_platform/static/app.js");
  const t0 = [{ id: "conv", kind: "conversation", title: "", ref: "conv" }];
  const r1 = openTab(t0, { id: "d:gate:a", kind: "gate", title: "完整报告", ref: "a" });
  assert.equal(r1.opened, true, "新 ref 开 tab");
  assert.equal(r1.active, "d:gate:a", "新 tab 激活");
  const r2 = openTab(r1.tabs, { id: "d:gate:a2", kind: "gate", title: "完整报告", ref: "a" });
  assert.equal(r2.opened, false, "同 ref 去重不重复开");
  assert.equal(r2.active, "d:gate:a", "同 ref 聚焦已有 tab");
  assert.equal(r2.tabs.length, 2, "tab 数不变");
  const c1 = closeTab(r1.tabs, "d:gate:a");
  assert.deepEqual(c1.tabs, t0, "关闭后剩 conversation");
  assert.equal(c1.active, "conv", "关闭回落 conversation");

  /* ── 详情视图全流程(事件委托 → 开 tab → 渲染 → ✕ 关闭)────── */

  const tick = async (n = 3) => {
    for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
  };
  const tabsHtml = () => doc.querySelector("#tabs").innerHTML;
  const detailHtml = () => doc.querySelector("#detailHost").innerHTML;

  // gate 卡 → 点"查看完整报告" → 新 tab 激活 → 详情渲染(数据在卡内,不拉取)
  const gateLink = new StubEl("button");
  gateLink.dataset.detailKind = "gate";
  gateLink.dataset.detailRef = "lab.dinner";
  gateLink.dataset.detail = JSON.stringify({
    draft: "lab.dinner", status: "warn",
    gates: { g2: { status: "warn", findings: [{ level: "warn", clause: "c", message: "description 太短" }] } },
  });
  gateLink.parentNode = doc.body;
  doc.trigger("click", { target: gateLink });
  await tick();
  assert.equal(probe.state.active, "d:gate:lab.dinner", "详情 tab 激活");
  assert.ok(tabsHtml().includes('data-tab="d:gate:lab.dinner"'), "详情 tab 上条");
  assert.ok(tabsHtml().includes("data-tab-x"), "详情 tab 可关闭(✕)");
  const spawnPost = calls.find((c) => c.url === "/platform/api/apps/spawn");
  assert.ok(spawnPost, "M2:开详情 tab 即 spawn 登记 instance");
  assert.equal(JSON.parse(spawnPost.body).kind, "gate_report", "详情 kind → app kind 映射");
  assert.equal(JSON.parse(spawnPost.body).ref, "lab.dinner");
  await tick();
  assert.equal(
    probe.state.tabs.find((t) => t.id === "d:gate:lab.dinner")?.instance,
    "app-spawn-lab.dinner",
    "tab 自此带 app instance(Tab Surface 升格)");
  assert.ok(doc.querySelector("#log").hidden, "详情态隐藏对话流");
  assert.ok(doc.querySelector("#inputBar").hidden, "详情态隐藏输入区");
  assert.ok(!doc.querySelector("#detailHost").hidden, "详情宿主可见");
  assert.ok(detailHtml().includes("description 太短"), "gate 详情 findings 全展开");
  assert.ok(detailHtml().includes("pf-findings"), "findings 不折叠(无 details 折叠器)");

  // 同 ref 再点:不重复开 tab
  doc.trigger("click", { target: gateLink });
  await tick();
  assert.equal(
    probe.state.tabs.filter((t) => t.kind === "gate" && t.ref === "lab.dinner").length, 1,
    "同 ref 去重(tabs 模型)");

  // pack 详情:拉 closure → 成员树 + 生产成员链接旧 UI
  const packLink = new StubEl("button");
  packLink.dataset.detailKind = "pack";
  packLink.dataset.detailRef = "lab.dinner";
  packLink.dataset.detail = "{}";
  packLink.parentNode = doc.body;
  doc.trigger("click", { target: packLink });
  await tick();
  assert.equal(probe.state.active, "d:pack:lab.dinner", "pack tab 激活");
  assert.ok(
    calls.some((c) => c.url === "/api/lab/packages/lab.dinner/closure?mode=runtime"),
    "pack 详情拉 closure");
  assert.ok(detailHtml().includes("weather.query"), "成员树渲染");
  assert.ok(detailHtml().includes("/#/skills/weather.query"), "生产成员链旧 UI Skills 页");

  // run 详情:拉 detail + signals → 状态/错误渲染
  const runLink = new StubEl("button");
  runLink.dataset.detailKind = "run";
  runLink.dataset.detailRef = "run-1";
  runLink.dataset.detail = "{}";
  runLink.parentNode = doc.body;
  doc.trigger("click", { target: runLink });
  await tick();
  assert.equal(probe.state.active, "d:run:run-1", "run tab 激活");
  assert.ok(calls.some((c) => c.url === "/api/runs/run-1"), "run 详情拉 detail");
  assert.ok(calls.some((c) => c.url === "/api/runs/run-1/signals"), "run 详情拉 signals");
  assert.ok(detailHtml().includes("ops.janitor"), "run 详情渲染 skill");
  assert.ok(detailHtml().includes("outputs 错"), "run 详情渲染失败原因");
  const runSpawn = calls.find(
    (c) => c.url === "/platform/api/apps/spawn" && (c.body ?? "").includes('"run"')
  );
  assert.ok(runSpawn, "M3:run tab 也 spawn(run app kind)");
  assert.equal(JSON.parse(runSpawn.body).ref, "run-1");
  assert.equal(JSON.parse(runSpawn.body).state.run_id, "run-1", "spawn state 带 run_id(动作参数源)");
  assert.ok(detailHtml().includes('data-debug-run="run-1"'), "failed run 给开调试链接(M3)");

  // diff 详情:数据在卡内,红绿行全量渲染(摘要只留人话)
  const diffLink = new StubEl("button");
  diffLink.dataset.detailKind = "diff";
  diffLink.dataset.detailRef = "lab.dinner";
  diffLink.dataset.detail = JSON.stringify({ name: "lab.dinner", diff: { has_changes: true, members: [{
    member: "lab.dinner", status: "changed",
    fields: [{ kind: "changed", path: "description", old: "旧文案", new: "新文案" }],
    prompt_diff: [{ kind: "add", text: "雨天优先便携" }],
    tests: { added: [], removed: [] } }] } });
  diffLink.parentNode = doc.body;
  doc.trigger("click", { target: diffLink });
  await tick();
  assert.equal(probe.state.active, "d:diff:lab.dinner", "diff tab 激活");
  assert.ok(detailHtml().includes('data-kind="add"'), "diff 详情红绿行渲染");
  assert.ok(detailHtml().includes("旧文案"), "diff 详情字段新旧值渲染");

  // 加载失败 → 错误占位 + 重试成功
  const badLink = new StubEl("button");
  badLink.dataset.detailKind = "run";
  badLink.dataset.detailRef = "bad-run";
  badLink.dataset.detail = "{}";
  badLink.parentNode = doc.body;
  doc.trigger("click", { target: badLink });
  await tick();
  assert.ok(detailHtml().includes("加载失败"), "加载失败占位");
  assert.ok(detailHtml().includes("data-it-retry"), "重试按钮在");
  const retryBtn = new StubEl("button");
  retryBtn.dataset.itRetry = "";
  retryBtn.parentNode = doc.body;
  doc.trigger("click", { target: retryBtn });
  await tick();
  assert.ok(detailHtml().includes("ops.janitor"), "重试后详情渲染成功");
  assert.ok(!detailHtml().includes("加载失败"), "错误占位已撤");

  // ✕ 关闭(M2 关闭≠销毁):回落 conversation,进"最近关闭";重开回同 instance
  const x = new StubEl("button");
  x.dataset.tabX = "d:run:bad-run";
  x.parentNode = doc.body;
  doc.trigger("click", { target: x, stopPropagation: () => {} });
  await tick();
  assert.equal(probe.state.active, "conv", "关闭后回落 conversation");
  assert.ok(!tabsHtml().includes('data-tab="d:run:bad-run"'), "已关 tab 下条");
  assert.ok(!doc.querySelector("#log").hidden, "对话流恢复可见");
  assert.ok(doc.querySelector("#detailHost").hidden, "详情宿主隐藏");
  assert.equal(probe.state.closedTabs.length, 1, "关闭≠销毁:进最近关闭");
  assert.ok(tabsHtml().includes("最近关闭"), "最近关闭列表上屏");
  assert.ok(tabsHtml().includes('data-reopen="d:run:bad-run"'), "重开入口在");

  // 重开:回 tab 条并聚焦,同一 tab id(→ 同 instance)
  const reopenBtn = new StubEl("button");
  reopenBtn.dataset.reopen = "d:run:bad-run";
  reopenBtn.parentNode = doc.body;
  doc.trigger("click", { target: reopenBtn });
  await tick();
  assert.equal(probe.state.active, "d:run:bad-run", "重开聚焦");
  assert.ok(tabsHtml().includes('data-tab="d:run:bad-run"'), "重开回 tab 条(同 instance)");
  assert.equal(probe.state.closedTabs.length, 0, "重开后出最近关闭列表");
  // 还原:重关 run tab,后续决策流程在 conversation 面进行
  probe.closeDetail("d:run:bad-run");
  await tick();

  /* ── 升权决策(W2):轮询汇聚 → 就地作答 → 已决置灰 ────────────── */

  // 轮询失败:静默(不炸、不加错误气泡、消息数不变)
  const before = probe.state.messages.length;
  await probe.pollDecisions();
  assert.equal(probe.state.messages.length, before, "拉取失败静默,不打断对话");

  // 轮询成功:新 pending 以 agent 消息 + escalation 卡插进当前会话(服务端持久化)
  await probe.pollDecisions();
  await tick();
  assert.equal(probe.state.messages.length, before + 1, "主动汇报插入会话");
  assert.ok(logHtml().includes('data-card="escalation"'), "escalation 卡渲染");
  assert.ok(logHtml().includes("需要你批准"), "人话决策请求上屏");
  assert.ok(!visibleText(logHtml()).includes("none → irreversible"), "reason_hint 不上屏(收详情)");

  // 轮询幂等:再拉无新插入
  await probe.pollDecisions();
  assert.equal(probe.state.messages.length, before + 1, "重复轮询不重复插入");

  // 就地作答:批准一次 → POST 转发 → 卡片标记已决(置灰 + 状态字)
  const approveBtn = new StubEl("button");
  approveBtn.dataset.decision = "esc-1";
  approveBtn.dataset.answer = "approve-once";
  approveBtn.parentNode = doc.body;
  doc.trigger("click", { target: approveBtn });
  await tick();
  const decPost = calls.find((c) => c.url === "/platform/api/decisions/esc-1");
  assert.ok(decPost, "decisions 作答请求发出");
  assert.deepEqual(JSON.parse(decPost.body), { answer: "approve-once" });
  assert.ok(logHtml().includes("已批准"), "已决状态字");
  const escCard = probe.state.messages.at(-1).cards[0];
  assert.equal(escCard.data.resolved, "approve-once", "卡数据标记已决(重渲仍置灰)");

  // 已被别处处理(404):状态字 = 已被处理,不算错误
  const goneBtn = new StubEl("button");
  goneBtn.dataset.decision = "esc-gone";
  goneBtn.dataset.answer = "deny";
  goneBtn.parentNode = doc.body;
  doc.trigger("click", { target: goneBtn });
  await tick();
  assert.ok(!logHtml().includes("找不到 supervisor"), "404 不以错误气泡呈现");

  // esc 详情 tab:参数/权限/reason_hint 全量
  const escLink = new StubEl("button");
  escLink.dataset.detailKind = "esc";
  escLink.dataset.detailRef = "esc-1";
  escLink.dataset.detail = JSON.stringify({ skill: "ops.janitor", reason_hint: "none → irreversible",
    params: { path: "/tmp/x" }, requested: { tools: ["fs.write"] } });
  escLink.parentNode = doc.body;
  doc.trigger("click", { target: escLink });
  await tick();
  assert.equal(probe.state.active, "d:esc:esc-1", "esc tab 激活");
  assert.ok(detailHtml().includes("/tmp/x"), "esc 详情参数渲染");
  assert.ok(detailHtml().includes("none → irreversible"), "esc 详情 reason_hint 渲染");

  /* ── N7 凭证降级:meta.reason → 人话系统提示(不静默不裸错)────── */

  probe.state.active = "conv";
  probe.state.messages.push({ id: "m-deg", role: "agent", text: "计划如下", ts: 10,
    meta: { route: "rule", reason: "llm_unavailable" }, cards: [] });
  probe.renderMain();
  assert.ok(logHtml().includes("规则模式"), "降级系统提示上屏(copy)");
  assert.ok(!visibleText(logHtml()).includes("llm_unavailable"), "机器码不上屏");

  /* ── M1 action 管道(docs/APP-MODEL.md §4):有 instance 走新管道 ── */

  // 卡面带 instance 的按钮:POST 新管道 URL;前端只交事件参数(不带业务 payload)
  const pipeBtn = new StubEl("button");
  pipeBtn.dataset.appInst = "app-p1";
  pipeBtn.dataset.appAction = "scaffold.approve";
  pipeBtn.dataset.cardAct = "scaffold.approve"; // 兼容属性同在(新管道优先)
  pipeBtn.dataset.payload = "{}";
  pipeBtn.parentNode = doc.body;
  doc.trigger("click", { target: pipeBtn });
  await tick();
  const pipePost = calls.find((c) => c.url === "/platform/api/apps/app-p1/actions/scaffold.approve");
  assert.ok(pipePost, "新 action 管道请求发出");
  const pipeBody = JSON.parse(pipePost.body);
  assert.equal(pipeBody.surface, "card", "表面声明随行");
  assert.ok(!("name" in (pipeBody.args ?? {})), "前端不交业务参数(服务端按 manifest 绑定)");
  assert.ok(logHtml().includes("首稿完成"), "管道结果以 agent 消息呈现");
  assert.equal(
    probe.state.messages.at(-1).cards[0].instance, "app-p2",
    "结果卡带 instance(可选 spawn 的寻址面)");

  // 决策按钮带 instance:作答 = action id,走新管道
  const escPipeBtn = new StubEl("button");
  escPipeBtn.dataset.decision = "esc-1";
  escPipeBtn.dataset.answer = "approve-once";
  escPipeBtn.dataset.appInst = "app-e1";
  escPipeBtn.dataset.appAction = "approve-once";
  escPipeBtn.parentNode = doc.body;
  doc.trigger("click", { target: escPipeBtn });
  await tick();
  assert.ok(
    calls.some((c) => c.url === "/platform/api/apps/app-e1/actions/approve-once"),
    "决策作答走新管道(action id = answer)");

  /* ── M3 闭环(docs/APP-MODEL.md §8):run tab → 开 debug → 放行;
     包 tab → 草稿编辑;每步 spawn/去重/聚焦正确 ─────────────── */

  // run tab(failed)给"开调试"链接;点击 → replay 会话 → debug tab(spawn 登记)
  const dbgBtn = new StubEl("button");
  dbgBtn.dataset.debugRun = "run-1";
  dbgBtn.parentNode = doc.body;
  doc.trigger("click", { target: dbgBtn });
  await tick();
  const dbgPost = calls.find((c) => c.url === "/api/debug/sessions");
  assert.ok(dbgPost, "创建调试会话");
  assert.equal(JSON.parse(dbgPost.body).replay_run_id, "run-1", "replay 形态(从产物回放)");
  assert.equal(probe.state.active, "d:debug:sess-1", "debug tab 激活");
  assert.ok(
    calls.some((c) => c.url === "/platform/api/apps/spawn" && (c.body ?? "").includes('"debug"')),
    "debug app spawn 登记");
  assert.ok(detailHtml().includes("暂停在"), "debug tab 暂停点人话");
  assert.ok(detailHtml().includes("demo.fib"), "debug tab 帧栈渲染");
  assert.ok(detailHtml().includes('data-tab-act="debug.continue"'), "放行按钮在(暂停态)");

  // 放行:tab 面动作与卡面同一 action 管道(surface="tab"),结果回插对话
  const contBtn = new StubEl("button");
  contBtn.dataset.tabAct = "debug.continue";
  contBtn.parentNode = doc.body;
  doc.trigger("click", { target: contBtn });
  await tick();
  const contPost = calls.find(
    (c) => c.url === "/platform/api/apps/app-spawn-sess-1/actions/debug.continue"
  );
  assert.ok(contPost, "放行走 action 管道(instance = debug tab 的 spawn)");
  assert.equal(JSON.parse(contPost.body).surface, "tab", "全面 surface 标注");
  assert.ok(probe.state.messages.at(-1).text.includes("已放行"), "结果回插对话(因果可见)");

  // 包 tab:草稿成员带"编辑"链接;点击 → lab-draft tab(spawn + 拉取)
  doc.trigger("click", { target: packLink }); // 回包详情(去重聚焦)
  await tick();
  assert.ok(detailHtml().includes('data-detail-kind="draft"'), "草稿成员带编辑链接(M3)");
  const editLink = new StubEl("button");
  editLink.dataset.detailKind = "draft";
  editLink.dataset.detailRef = "lab.dinner";
  editLink.dataset.detail = "{}";
  editLink.parentNode = doc.body;
  doc.trigger("click", { target: editLink });
  await tick();
  assert.equal(probe.state.active, "d:draft:lab.dinner", "lab-draft tab 激活");
  assert.ok(
    calls.some((c) => c.url === "/platform/api/apps/spawn" && (c.body ?? "").includes('"lab-draft"')),
    "lab-draft app spawn 登记");
  assert.ok(detailHtml().includes("在 Lab 中编辑"), "深链旧 Lab 编辑器(降级面)");
  assert.ok(detailHtml().includes('data-tab-act="draft.check"'), "检查动作在 tab 面");

  // 去重聚焦:再点包详情,不重复 tab、不重复 spawn
  const packSpawns = calls.filter(
    (c) => c.url === "/platform/api/apps/spawn" && (c.body ?? "").includes('"skill_pack"')
  ).length;
  doc.trigger("click", { target: packLink });
  await tick();
  assert.equal(
    calls.filter((c) => c.url === "/platform/api/apps/spawn" && (c.body ?? "").includes('"skill_pack"')).length,
    packSpawns,
    "同 kind+ref 再开:不重复 spawn(M2 去重)");
  assert.equal(probe.state.active, "d:pack:lab.dinner", "去重聚焦已有 tab");

  /* ── M4a:run 真通道 + 发起面归一 ──────────────────────────── */

  // 发起面:run tab 有 launch textarea + 按钮;留空 → args {}(服务端按 schema 骨架)
  doc.trigger("click", { target: runLink }); // run-1 tab(去重聚焦)
  await tick();
  assert.ok(detailHtml().includes("data-launch-input"), "发起面 textarea 在(app 内改参)");
  assert.ok(detailHtml().includes('data-tab-act="run.launch"'), "再跑一次按钮在");
  const launchBtn = new StubEl("button");
  launchBtn.dataset.tabAct = "run.launch";
  launchBtn.parentNode = doc.body;
  doc.trigger("click", { target: launchBtn });
  await tick();
  const lPost = calls.find((c) => c.url === "/platform/api/apps/app-spawn-run-1/actions/run.launch");
  assert.ok(lPost, "launch 走 action 管道");
  assert.deepEqual(JSON.parse(lPost.body).args, {}, "留空 = 服务端骨架(可改参的缺省)");
  assert.equal(probe.state.active, "d:run:run-2", "run 通道产出 → 直接进新 run tab");
  assert.ok(detailHtml().includes("running"), "新 run tab 实时状态渲染");

  // iterate(卡面 run 态):run_instance → 进 run tab;ad-hoc run(无产物面)回落 instance state
  const itBtn = new StubEl("button");
  itBtn.dataset.appInst = "app-p1";
  itBtn.dataset.appAction = "iterate.generate";
  itBtn.dataset.cardAct = "iterate.generate";
  itBtn.dataset.payload = "{}";
  itBtn.parentNode = doc.body;
  doc.trigger("click", { target: itBtn });
  await tick();
  assert.ok(
    calls.some((c) => c.url === "/platform/api/apps/app-p1/actions/iterate.generate"),
    "iterate 走管道(run 态)");
  assert.equal(probe.state.active, "d:run:run-9", "run_instance → 直接进 run tab");
  assert.ok(
    calls.some((c) => c.url === "/platform/api/apps/app-spawn-run-9"),
    "产物面 404 → 回落 instance state(v0.2 §4 不发明标志位)");
  assert.ok(detailHtml().includes("done"), "instance state 渲染终态(run app 持有 run_id)");
}

console.log("platform.test.mjs: all assertions passed");

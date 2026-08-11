/* web_platform 前端渲染单测(docs/WEB-PLATFORM.md §10):
   cards.js 双层渲染——技术层 cardHtml(六卡型结构/actions 按钮/warnings 勾选门)
   + 摘要层 summaryHtml(人话一句结论,数据驱动;断言可见文字不含禁忌词:
   tier/manifest/hash/G1-G5/条款号/ProviderError 等),详情层断言技术字段保留;
   details.js 详情渲染(gate/pack/plan/run/diff/esc/decompose + doc 骨架/大纲)。
   C4.4 剪注:本文件原 app.js 旧壳集成段(对话流/tab 模型/详情 tab 全流程)
   随旧壳退役——接替:conversation-app.test.mjs(对话契约)+
   desktop-widget.test.mjs(desktop 结构)+ tests-ui 五套(真实浏览器全链)。
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
/* ── D1:doc 编辑器(docs/DOC-EDITOR.md §2/§7)──────────────────── */

{
  // 大纲解析纯函数(标题/层级/字符偏移)
  const { parseOutline } = await import("../../../web_platform/static/details.js");
  const outline = parseOutline("# 概述\n内容\n## 设计\n次段\n### 细节\n");
  assert.deepEqual(outline.map((h) => h.text), ["概述", "设计", "细节"], "标题逐项");
  assert.deepEqual(outline.map((h) => h.level), [1, 2, 3], "层级");
  assert.equal(outline[1].offset, "# 概述\n内容\n".length, "字符偏移(滚动定位)");
  assert.deepEqual(parseOutline("无标题\n纯文本\n"), [], "非 markdown 不炸");

  // 摘要卡(Card Surface:标题 + 首行 + 字数 + 状态)
  const docCard = summaryHtml({
    type: "doc", v: 1,
    data: { title: "新 UI", first_line: "概述", chars: 1234, versions: 2, has_bubbles: true },
    actions: [],
  });
  const docText = assertClean(docCard, "doc");
  assert.ok(docText.includes("新 UI") && docText.includes("概述"), "标题 + 首行摘要");
  assert.ok(docText.includes("1234 字") || docText.includes("1234"), "字数");
}


console.log("platform.test.mjs: all assertions passed");

/* web_platform 前端样品单测(docs/WEB-PLATFORM.md §10):
   cards.js 六卡型渲染(结构/actions 按钮/publish warnings 勾选门);
   app.js 对话流(fetch stub:会话列表 → 发消息 → agent 卡渲染 →
   点卡动作 → action 请求体 → 返回卡追加;骨架 loading / 错误态)。
   运行:node static/tests/platform.test.mjs */

import assert from "node:assert/strict";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url); // "/static/js/" → 旧 web 共享模块

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const { cardHtml } = await import("../../../web_platform/static/cards.js");

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
  mk("div", "sessions");
  mk("div", "log");
  const intent = mk("textarea", "intent");
  mk("button", "send");
  mk("button", "newSession");

  const calls = [];
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
    throw new Error(`未 stub 的请求: ${options.method ?? "GET"} ${url}`);
  };

  await import("../../../web_platform/static/app.js");
  const probe = globalThis.__platform;
  assert.ok(probe, "测试探针在");
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const logHtml = () => doc.querySelector("#log").innerHTML;
  assert.ok(doc.querySelector("#sessions").innerHTML.includes("晚餐技能"), "会话索引渲染");
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
}

console.log("platform.test.mjs: all assertions passed");

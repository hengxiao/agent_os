/* web_platform 前端样品单测(docs/WEB-PLATFORM.md §10):
   cards.js 六卡型渲染(结构/actions 按钮/publish warnings 勾选门/详情链接);
   app.js 对话流(fetch stub:会话列表 → 发消息 → agent 卡渲染 →
   点卡动作 → action 请求体 → 返回卡追加;骨架 loading / 错误态);
   tab 条模型(openTab 去重聚焦 / closeTab 回落)与四类详情视图
   (gate/pack/plan 数据在卡内,pack/run 拉取;loading/error/重试;✕ 关闭回对话)。
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
    if (url === "/api/lab/packages/lab.dinner/closure?mode=runtime") {
      return reply({ root: "lab.dinner", root_tier: "reversible", members: [
        { name: "lab.dinner", depth: 0, tier: "reversible", status: "draft" },
        { name: "weather.query", depth: 1, tier: "none", status: "production" },
      ], errors: [] });
    }
    if (url === "/api/runs/run-1") {
      return reply({ id: "run-1", skill: "ops.janitor", status: "failed", error: "outputs 错", result: null });
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

  // ✕ 关闭:回落 conversation,对话流恢复
  const x = new StubEl("button");
  x.dataset.tabX = "d:run:bad-run";
  x.parentNode = doc.body;
  doc.trigger("click", { target: x, stopPropagation: () => {} });
  await tick();
  assert.equal(probe.state.active, "conv", "关闭后回落 conversation");
  assert.ok(!tabsHtml().includes("d:run:bad-run"), "已关 tab 下条");
  assert.ok(!doc.querySelector("#log").hidden, "对话流恢复可见");
  assert.ok(doc.querySelector("#detailHost").hidden, "详情宿主隐藏");
}

console.log("platform.test.mjs: all assertions passed");

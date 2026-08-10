/* conversation app 薄壳测试(docs/DESKTOP-WIDGET.md §5-1;C4.2):
   def 结构 + layout 纯函数 + 实例适配层(会话装载/发送契约/open-doc 上行/
   升权作答/轮询汇聚/序列化)。fetch 全 stub(URL → 应答表);
   行为契约与 app.js 同端点——断 URL/方法/负载,不断 UI。
   运行:node static/tests/conversation-app.test.mjs */

import assert from "node:assert/strict";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url);

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const { getWidgetDef } = await import("../js/widgets/index.js");
const {
  CONVERSATION_DEF, createConversation, renderConversation, conversationLogHtml,
} = await import("../../../web_platform/static/conversation-app.js");

const DOC_CARD = {
  type: "doc_list", v: 1,
  data: { docs: [{ name: "demo.test", title: "demo.test", first_line: "测试文档", chars: 117 }] },
};

/* fetch 应答表(键 = METHOD URL) */
function stubFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method ?? "GET";
    calls.push({ url: String(url), method, options });
    const reply = routes[`${method} ${url}`];
    if (reply === undefined) throw new Error(`fetch 未 stub: ${method} ${url}`);
    return { ok: true, json: async () => (typeof reply === "function" ? reply() : reply) };
  };
  return calls;
}

{
  // ① def 结构(§5-1):注册面/compound/events/aria
  assert.equal(getWidgetDef("conversation"), CONVERSATION_DEF, "conversation 注册进 registry");
  assert.equal(typeof CONVERSATION_DEF.compound?.layout, "function", "薄壳 compound:layout 在 def");
  for (const ev of ["change", "open-doc"]) {
    assert.ok(CONVERSATION_DEF.events.includes(ev), `events 含 ${ev}`);
  }
  assert.equal(CONVERSATION_DEF.aria.label, "对话", "aria.label(任务栏题名)");
  assert.deepEqual(CONVERSATION_DEF.surfaces, ["tab"], "conversation 只有完整面");
  for (const k of ["session", "messages", "busy", "draft"]) {
    assert.ok(k in CONVERSATION_DEF.state_defaults, `state_defaults.${k}`);
  }
}

{
  // ② layout 纯函数:消息流/busy 骨架/draft/空态示例;同入同出,不改 state
  const s = {
    session: { id: "s1", title: "T" },
    messages: [
      { role: "user", text: "文档列表", cards: [] },
      { role: "agent", text: "这是索引", cards: [DOC_CARD] },
    ],
    busy: false,
    draft: "半成品",
  };
  const before = JSON.stringify(s);
  const html = renderConversation(s);
  assert.ok(html.includes("文档列表"), "用户消息上屏");
  assert.ok(html.includes("这是索引"), "agent 消息上屏");
  assert.ok(html.includes('data-detail-kind="doc"'), "doc 卡面渲染(renderCardSurface 复用)");
  assert.ok(html.includes(">半成品</textarea>"), "draft 进 composer(state 驱动)");
  assert.ok(!html.includes("pf-skel-line"), "非 busy 无骨架");
  const busy = renderConversation({ ...s, busy: true });
  assert.ok(busy.includes("pf-skel-line"), "busy 骨架在");
  const empty = renderConversation({ session: null, messages: [], busy: false, draft: "" });
  assert.ok(empty.includes("pf-empty") && empty.includes("data-example"), "空态示例 chips");
  assert.equal(renderConversation(s), html, "layout 纯:同入同出");
  assert.equal(JSON.stringify(s), before, "layout 不改 state");
}

{
  // ③ 实例:load=latest 装载最新会话;mount_view 首渲;send 契约;增量只刷 log 区
  const doc = makeDocument();
  globalThis.document = doc;
  const calls = stubFetch({
    "GET /platform/api/sessions": [{ id: "s1", title: "旧会话" }],
    "GET /platform/api/sessions/s1": { messages: [{ role: "agent", text: "历史消息", cards: [] }] },
    "POST /platform/api/sessions/s1/messages": { role: "agent", text: "索引在此", cards: [DOC_CARD] },
    "POST /platform/api/sessions/s1/decisions/present": { presented: [] },
    "POST /platform/api/sessions/s1/runs/present": { presented: [] },
  });
  const { inst, api } = await createConversation({ load: "latest" });
  assert.equal(inst.state.session.id, "s1", "latest = 会话列表首个(旧壳同语义)");
  assert.equal(inst.state.messages.length, 1, "历史消息装进 canonical state");
  assert.equal(inst.path, "/conv/s1", "寻址 = /conv/<会话>(§1)");

  const host = doc.createElement("div");
  doc.body.appendChild(host);
  inst.mount_view(host);
  assert.ok(host.innerHTML.includes("cv-log"), "mount_view 落对话 chrome");
  assert.ok(host.innerHTML.includes("历史消息"), "首渲含历史消息");

  // send:draft → 用户气泡 + busy → POST → agent 消息;端点/负载与 app.js 同
  inst.state.draft = "文档列表";
  const events = [];
  inst.on("change", (p) => events.push(p));
  await api.send();
  const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/messages"));
  assert.ok(post, "发送走 /sessions/<id>/messages(orchestrator 入口)");
  assert.equal(JSON.parse(post.options.body).text, "文档列表", "发送负载 = 意图原文");
  assert.equal(inst.state.messages.at(-2).text, "文档列表", "用户气泡进 state");
  assert.equal(inst.state.messages.at(-1).text, "索引在此", "agent 应答进 state");
  assert.equal(inst.state.busy, false, "busy 复位");
  assert.equal(inst.state.draft, "", "draft 清空");
  assert.ok(events.length >= 1, "change 事件上行");
  const logEl = host.querySelector("[data-cv-log]");
  assert.ok(logEl.innerHTML.includes("索引在此"), "增量刷 log 区(agent 消息上屏)");
  assert.ok(logEl.innerHTML.includes('data-detail-kind="doc"'), "agent 卡面上屏");

  // composer 不同步重排(draft 打字不进 log 渲染面)
  inst.state.draft = "正在输入";
  assert.ok(!logEl.innerHTML.includes("正在输入"), "draft 不污染 log 区(canonical 分层)");
}

{
  // ④ open-doc 上行(§5-2):文档卡点击 → 回调 + open-doc 事件;doc_create 新建路径
  const doc = makeDocument();
  globalThis.document = doc;
  stubFetch({
    "GET /platform/api/sessions": [{ id: "s1", title: "T" }],
    "GET /platform/api/sessions/s1": { messages: [{ role: "agent", text: "索引", cards: [DOC_CARD] }] },
  });
  const opened = [];
  const { inst } = await createConversation({ load: "latest", onOpenDoc: (name) => opened.push(name) });
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  inst.mount_view(host);
  const emitted = [];
  inst.on("open-doc", (p) => emitted.push(p.ref));
  const link = new StubEl("button");
  link.dataset.detailKind = "doc";
  link.dataset.detailRef = "demo.test";
  link.parentNode = host;
  host.trigger("click", { target: link });
  assert.deepEqual(opened, ["demo.test"], "onOpenDoc 回调(desktop 窗口区路径)");
  assert.deepEqual(emitted, ["demo.test"], "open-doc 事件上行(§5-2)");

  // 非 doc 的 detail kind:C4.2 不接(不上行不回调)
  const other = new StubEl("button");
  other.dataset.detailKind = "gate";
  other.dataset.detailRef = "x";
  other.parentNode = host;
  host.trigger("click", { target: other });
  assert.equal(opened.length, 1, "非 doc detail kind 不上行(C4.3 边界)");
}

{
  // ⑤ 升权作答 + 轮询汇聚(与 app.js 同端点)
  const doc = makeDocument();
  globalThis.document = doc;
  const escCard = {
    type: "escalation", v: 1, instance: "inst-esc-1",
    data: { question_id: "q-1", skill: "weather.query", tier: "write", tier_human: "写",
      reason_hint: "要写生产", options: ["approve-once", "deny"], asked_at: 1 },
  };
  const calls = stubFetch({
    "GET /platform/api/sessions": [{ id: "s1", title: "T" }],
    "GET /platform/api/sessions/s1": {
      messages: [{ role: "agent", text: "待批", cards: [escCard] }],
    },
    "POST /platform/api/apps/inst-esc-1/actions/approve-once": { ok: true },
    "POST /platform/api/sessions/s1/decisions/present": {
      presented: [{ role: "agent", text: "新升权到达", cards: [] }],
    },
    "POST /platform/api/sessions/s1/runs/present": { presented: [] },
  });
  const { inst, api } = await createConversation({ load: "latest" });
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  inst.mount_view(host);
  const btn = new StubEl("button");
  btn.dataset.decision = "q-1";
  btn.dataset.answer = "approve-once";
  btn.dataset.appInst = "inst-esc-1";
  btn.parentNode = host;
  host.trigger("click", { target: btn });
  await new Promise((r) => setTimeout(r, 30));
  const ans = calls.find((c) => c.url.includes("/actions/approve-once"));
  assert.ok(ans, "作答走 action 管道(approve-once)");
  assert.equal(JSON.parse(ans.options.body).session_id, "s1", "session_id 随行");
  const card = inst.state.messages[0].cards[0];
  assert.equal(card.data.resolved, "approve-once", "卡标记已决(_markResolved)");
  await api.poll();
  assert.ok(
    inst.state.messages.some((m) => m.text === "新升权到达"),
    "轮询汇聚 presented 进会话(系统主动开口)"
  );
}

{
  // ⑥ state 可序列化 + load=new 强制新会话
  stubFetch({
    "POST /platform/api/sessions": () => ({ id: "s-new", title: "新会话" }),
  });
  const { inst } = await createConversation({ load: "new" });
  assert.equal(inst.state.session.id, "s-new", "load=new 强制新会话(+ 新对话语义)");
  assert.deepEqual(inst.state.messages, [], "新会话消息空");
  const round = JSON.parse(JSON.stringify(inst.state));
  assert.equal(round.session.id, "s-new", "state JSON 往返");
}

console.log("conversation-app.test.mjs: all assertions passed");

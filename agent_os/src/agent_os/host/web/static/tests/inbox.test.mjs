/* inbox.js 纯逻辑单测(SUPERVISOR.md §5;S3 收件箱):
   sortedPending(high 在前,其余先问先排)、badgeModel(计数/hasHigh)、
   questionCardHtml(urgency 色条数据、options 按钮组/文本输入、错误条、深链接)。
   运行:node static/tests/inbox.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import { badgeModel, questionCardHtml, sortedPending } from "../js/components/inbox.js";

const q = (over = {}) => ({
  question_id: "q-1",
  run_id: "run-abcdef123456",
  frame_id: "f-abc123",
  question: "批准 ¥5000 报销吗?",
  context: { amount_cents: 500000 },
  options: ["approve", "reject"],
  urgency: "normal",
  previous_error: null,
  asked_at: 1785000000,
  ...over,
});

/* ── sortedPending:high 在前,其余先问先排(同后端语义,UI 防御)────── */
{
  const rows = [
    q({ question_id: "q-old", asked_at: 100 }),
    q({ question_id: "q-high", urgency: "high", asked_at: 300 }),
    q({ question_id: "q-new", asked_at: 200 }),
  ];
  const ids = sortedPending(rows).map((r) => r.question_id);
  assert.deepEqual(ids, ["q-high", "q-old", "q-new"]);
  assert.equal(rows[0].question_id, "q-old", "原数组不被改动(纯函数)");
  assert.deepEqual(sortedPending(null), []);
}

/* ── badgeModel:计数 + hasHigh(徽标显隐/变色数据源)──────────────── */
{
  assert.deepEqual(badgeModel([]), { count: 0, hasHigh: false });
  assert.deepEqual(badgeModel(null), { count: 0, hasHigh: false });
  assert.deepEqual(badgeModel([q(), q({ question_id: "q-2" })]), { count: 2, hasHigh: false });
  assert.deepEqual(
    badgeModel([q(), q({ question_id: "q-2", urgency: "high" })]),
    { count: 2, hasHigh: true });
}

/* ── questionCardHtml:结构(urgency / 链接 / context / options)───── */
{
  const html = questionCardHtml(q({ urgency: "high" }));
  assert.ok(html.includes('data-urgency="high"'), "high  urgency 色条数据");
  assert.ok(html.includes("高优"), "high 双编码 chip");
  assert.ok(html.includes("批准 ¥5000 报销吗?"));
  assert.ok(html.includes("#/runs/run-abcdef123456"), "run 深链接");
  assert.ok(html.includes("?frame=f-abc123"), "帧深链接");
  assert.ok(html.includes("amount_cents"), "context 可展开");
  assert.ok(html.includes('data-answer="approve"'), "options 渲染为按钮组");
  assert.ok(!html.includes("sup-input"), "有 options 不出现文本输入");
}

/* ── questionCardHtml:无 options → 文本输入;无 context → 无 details ─ */
{
  const html = questionCardHtml(q({ options: null, context: {} }));
  assert.ok(html.includes("sup-input"), "无 options 渲染文本输入");
  assert.ok(html.includes("回车提交"), "输入提示回车提交");
  assert.ok(!html.includes("sup-ctx"), "空 context 不渲染展开区");
  assert.ok(!html.includes('data-urgency="high"'), "normal 不高亮");
}

/* ── questionCardHtml:错误条(本地被拒 err 优先,否则 previous_error)─ */
{
  assert.ok(questionCardHtml(q(), "答案不在 options 内").includes("答案不在 options 内"));
  assert.ok(
    questionCardHtml(q({ previous_error: "须从 options 选择" })).includes("须从 options 选择"),
    "previous_error 经同一错误条呈现(§3 重答语义)");
  const both = questionCardHtml(q({ previous_error: "旧" }), "新");
  assert.ok(both.includes("新") && !both.includes("旧"), "本地 err 优先于 previous_error");
  assert.ok(!questionCardHtml(q()).includes("sup-error"), "无错误不渲染错误条");
}

/* ── questionCardHtml:XSS 转义 ──────────────────────────────── */
{
  const html = questionCardHtml(q({ question: '<img src=x onerror="alert(1)">' }));
  assert.ok(!html.includes("<img"), "question 转义");
  assert.ok(html.includes("&lt;img"), "转义实体出现");
}

console.log("inbox.test.mjs: all assertions passed");

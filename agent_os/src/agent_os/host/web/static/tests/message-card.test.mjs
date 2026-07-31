/* message-card.js 纯逻辑单测(WEB-UI.md §4.2 规则 3):
   pairMessages(tool_call/tool_result 成对、双 call、孤儿 tool result 容错、
   id 缺失按名兜底)、parseToolResult(veto 识别)、focusMessageIndex(时间线 → 消息映射)。
   运行:node static/tests/message-card.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  focusMessageIndex,
  pairMessages,
  parseToolResult,
  renderMessages,
} from "../js/components/message-card.js";
import { makeMessages } from "./fixtures.mjs";

const messages = makeMessages();
const units = pairMessages(messages);

/* ── pairMessages:成对与单元划分(§4.2 规则 3)──────────────────── */
{
  assert.equal(units.length, 6, "9 条消息 → 6 个渲染单元(2 对 + 4 单)");
  assert.deepEqual(units.map((u) => u.kind), ["message", "pair", "pair", "message", "message", "message"]);

  const [u0, p1, p2, u6, u7, u8] = units;
  assert.equal(u0.message.role, "user");
  assert.equal(p1.callIndex, 1);
  assert.deepEqual(p1.results.map((r) => r.index), [2], "单 call 单结果");
  assert.equal(p2.callIndex, 3);
  assert.deepEqual(p2.results.map((r) => r.index), [4, 5], "双 call 双结果按序配对");
  assert.equal(u6.message.role, "assistant", "无 tool_calls 的 assistant 不成对");
  assert.equal(u7.orphan, true, "配不到 assistant 的 tool result 标孤儿(容错)");
  assert.equal(u7.message.tool_call_id, "ghost");
  assert.equal(u8.message.source, "injected");
  // 单元不残留内部簿记字段(输出纯数据)
  for (const u of units) assert.ok(!("_pending" in u));
}

/* ── pairMessages:tool_call_id 缺失时按名兜底 ─────────────────── */
{
  const msgs = [
    { role: "assistant", content: "", tool_calls: [{ id: null, name: "system.file.read", args: {} }] },
    { role: "tool", content: "x", tool_call_id: null, name: "system.file.read" },
    { role: "tool", content: "y", tool_call_id: null, name: "no_such_tool" },
  ];
  const us = pairMessages(msgs);
  assert.equal(us.length, 2);
  assert.equal(us[0].kind, "pair");
  assert.deepEqual(us[0].results.map((r) => r.index), [1], "同名 call 未满足 → 兜底配对");
  assert.equal(us[1].orphan, true, "无同名 call → 孤儿容错");
}

/* ── parseToolResult:veto / ok / 纯文本 ───────────────────────── */
{
  const veto = parseToolResult(messages[4]);
  assert.equal(veto.ok, false);
  assert.equal(veto.errorKind, "vetoed");
  assert.match(veto.errorMessage, /ToolGuard/);
  const okRes = parseToolResult(messages[2]);
  assert.equal(okRes.ok, true);
  assert.match(okRes.pretty, /stdout/);
  const plain = parseToolResult({ content: "not json at all" });
  assert.equal(plain.ok, null, "非 JSON 按纯文本(ok 未知)");
}

/* ── focusMessageIndex:时间线选中 → 消息卡片下标(§4.2 规则 1)───── */
{
  const fibMsgs = [
    { role: "user", content: "{}" }, // 0
    { role: "assistant", content: "", tool_calls: [{ id: "a", name: "system.python.exec", args: {} }] }, // 1
    { role: "tool", content: "{}", tool_call_id: "a", name: "system.python.exec" }, // 2
    { role: "assistant", content: "", tool_calls: [{ id: "b", name: "system.file.read", args: {} }] }, // 3
    { role: "tool", content: "{}", tool_call_id: "b", name: "system.file.read" }, // 4
    { role: "assistant", content: "final" }, // 5
  ];
  assert.equal(focusMessageIndex({ name: "post:llm.response", payload: {} }, 1, fibMsgs), 1);
  assert.equal(focusMessageIndex({ name: "post:llm.response", payload: {} }, 3, fibMsgs), 5,
    "step N → 第 N 条 assistant");
  assert.equal(
    focusMessageIndex({ name: "post:tool.call", payload: { tool: "system.file.read" } }, 2, fibMsgs), 4,
    "tool.call → 该步 assistant 之后同名 tool 消息");
  assert.equal(
    focusMessageIndex({ name: "post:tool.call", payload: { tool: "system.python.exec" } }, 1, fibMsgs), 2);
  assert.equal(focusMessageIndex({ name: "post:llm.response", payload: {} }, 9, fibMsgs), null,
    "step 越界 → null(不滚动)");
  assert.equal(focusMessageIndex({ name: "post:frame.pop", payload: {} }, null, fibMsgs), 5,
    "帧 pop → 末条");
  assert.equal(focusMessageIndex({ name: "pre:frame.push", payload: {} }, null, fibMsgs), 0,
    "帧 push → 首条");
  assert.equal(focusMessageIndex(null, 1, fibMsgs), null);
  assert.equal(focusMessageIndex({ name: "x" }, 1, []), null);
}

/* ── renderMessages:成对卡片 / veto Banner / reasoning 折叠 / 孤儿 ─ */
{
  const html = renderMessages(messages);
  assert.match(html, /msg-pair/, "成对渲染为一张卡片");
  assert.match(html, /tc-results/, "卡片下半为 tool_result");
  assert.match(html, /data-tone="danger"[\s\S]*?vetoed|vetoed[\s\S]*?data-tone="danger"/, "veto 以红 Banner 嵌入");
  assert.match(html, /msg-reasoning/, "reasoning 折叠块存在");
  assert.match(html, /先算 f\(4\) 与 f\(3\),合并得 f\(5\);检查 outputs schema 通过,可以给出最终序列。/, "reasoning 全文可展开");
  assert.match(html, /data-orphan="true"/, "孤儿 tool result 容错标记");
  assert.match(html, /data-injected="true"/, "纠偏注入消息标记");
  assert.match(html, /data-tone="warn"/, "纠偏以黄 Banner 嵌入");
  assert.match(html, /data-action="wb-copy-msg"/, "每条消息有复制按钮");
  assert.match(html, /data-ok="false"/, "失败结果红 ✗");
  assert.match(html, /✓ ok/, "成功结果绿 ✓");
}

/* ── 大消息 >20k 折叠 head+tail(§6.3)──────────────────────────── */
{
  const big = "H".repeat(10_000) + "M".repeat(8_000) + "T".repeat(5_000);
  const html = renderMessages([{ role: "user", content: big, source: "system" }]);
  assert.match(html, /省略 11000 字符/, "head 8000 + tail 4000,中间省略 11000");
  assert.match(html, /msg-big/, "大消息折叠块存在");
}

console.log("message-card.test.mjs: all assertions passed");

/* rca-panel.js 纯逻辑单测(docs/WEB-UI.md §4.4):
   planRcaJump(vetoed 定位被否决 pre:tool.call / tool_error 定位 ok:false post /
   aborted 回退帧最后信号 / 无对应信号回退 / first_error null / 帧缺失)、
   rcaBannerHtml(状态 + 定位与 Resume 按钮)、vetoCardHtml(裁决来源/理由/参数 JSON)。
   运行:node static/tests/rca-panel.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import { planRcaJump, rcaBannerHtml, vetoCardHtml } from "../js/components/rca-panel.js";
import { makeFrames, makeSignals } from "./fixtures.mjs";

const signals = makeSignals();
const frames = makeFrames();

/* ── planRcaJump:vetoed → 该帧第一个被否决的 pre:tool.call(fixtures @24)── */
{
  const rca = {
    status: "failed",
    first_error: {
      kind: "vetoed",
      frame_id: "f2",
      skill: "local:fib@1.0.0",
      call: { name: "system.python.exec", args: { code: "rm -rf /" } },
      message: "ToolGuard: 禁止危险命令",
    },
  };
  const plan = planRcaJump(rca, frames, signals);
  assert.equal(plan.frameId, "f2");
  assert.equal(plan.signalIndex, 24, "veto 定位到被否决的 pre:tool.call");
  assert.equal(signals[plan.signalIndex].name, "pre:tool.call");
  assert.equal(plan.step, 1, "出错信号所在组 step 供检视器消息映射");
  assert.equal(plan.messageIndex, null, "messageIndex 由检视器帧上下文补算");
  assert.equal(plan.reason, null);
}

/* vetoed:call.name 过滤(指定工具优先);多个 veto 取首个 */
{
  const extra = [
    ...signals.slice(0, 25),
    // 第二个 veto(另一工具,同帧 @25 之前插入)
    { v: 1, type: "signal", name: "pre:tool.call", run_id: "r1", frame_id: "f2", ts: 0,
      payload: { tool: "system.shell.exec", args: {} } },
    ...signals.slice(25),
  ];
  const rca = {
    status: "failed",
    first_error: { kind: "vetoed", frame_id: "f2", call: { name: "system.shell.exec", args: {} }, message: "m" },
  };
  const plan = planRcaJump(rca, frames, extra);
  assert.equal(extra[plan.signalIndex].payload.tool, "system.shell.exec", "call.name 匹配优先");
}

/* ── planRcaJump:tool_error → 该帧第一个 ok:false 的 post:tool.call(@13)── */
{
  const rca = {
    status: "failed",
    first_error: {
      kind: "tool_error",
      frame_id: "f1",
      skill: "local:fib@1.0.0",
      call: { name: "system.shell.exec", args: { cmd: "ls" } },
      message: "exit code 1",
    },
  };
  const plan = planRcaJump(rca, frames, signals);
  assert.equal(plan.frameId, "f1");
  assert.equal(plan.signalIndex, 13, "tool_error 定位 ok:false 的 post:tool.call");
  assert.equal(plan.step, 2);
}

/* tool_error:call.name 不匹配任何失败调用 → 回退该帧首个 ok:false */
{
  const rca = {
    status: "failed",
    first_error: { kind: "tool_error", frame_id: "f1", call: { name: "ghost", args: {} }, message: "m" },
  };
  const plan = planRcaJump(rca, frames, signals);
  assert.equal(plan.signalIndex, 13, "名称失配回退该帧首个 ok:false");
}

/* ── planRcaJump:aborted → 该帧最后一个信号(f2 末信号 @30)── */
{
  const rca = {
    status: "aborted",
    first_error: { kind: "aborted", frame_id: "f2", skill: "local:fib@1.0.0", call: null, message: "MaxDepthExceeded: 8" },
  };
  const plan = planRcaJump(rca, frames, signals);
  assert.equal(plan.frameId, "f2");
  assert.equal(plan.signalIndex, 30, "aborted 定位该帧最后一个信号");
}

/* ── 回退:kind 无对应信号 → 该帧最后一个信号 ─────────────────── */
{
  const rca = {
    status: "failed",
    first_error: { kind: "tool_error", frame_id: "f2", call: { name: "system.python.exec", args: {} }, message: "m" },
  };
  const plan = planRcaJump(rca, frames, signals);
  assert.equal(plan.signalIndex, 30, "f2 无 ok:false post(veto 不发 post)→ 回退帧末信号");
}

/* ── 不可定位:first_error null / 帧缺失 ─────────────────────── */
{
  const p1 = planRcaJump({ status: "done", first_error: null }, frames, signals);
  assert.equal(p1.frameId, null);
  assert.equal(p1.signalIndex, null);
  assert.equal(p1.reason, "no-error");

  const p2 = planRcaJump(
    { status: "failed", first_error: { kind: "aborted", frame_id: "ghost", call: null, message: "m" } },
    frames,
    signals);
  assert.equal(p2.frameId, null);
  assert.equal(p2.reason, "frame-missing");

  const p3 = planRcaJump(null, null, null);
  assert.equal(p3.reason, "no-error", "全 null 输入不炸");
}

/* ── rcaBannerHtml:状态 + error 摘要 + 定位/Resume 按钮 ─────── */
{
  const html = rcaBannerHtml("failed", "boom");
  assert.match(html, /data-tone="danger"/);
  assert.match(html, /run failed/);
  assert.match(html, /boom/);
  assert.match(html, /data-action="wb-rca-jump"/, "定位首个错误按钮");
  assert.match(html, /⌘J/, "按钮标注快捷键");
  assert.match(html, /data-action="wb-resume"/, "Resume 按钮");
  assert.match(html, /data-copy-label="已复制错误全文"/, "错误全文一键复制(§5)");

  const aborted = rcaBannerHtml("aborted", "MaxDepthExceeded");
  assert.match(aborted, /data-tone="aborted"/, "aborted 独立一色(§3.1)");
  assert.match(aborted, /run aborted/);

  const long = "x".repeat(300);
  const truncated = rcaBannerHtml("failed", long);
  assert.ok(truncated.includes("…"), "长错误摘要截断");
  assert.ok(truncated.includes(`title="${"x".repeat(300)}"`), "全文进 title");
}

/* ── vetoCardHtml:裁决来源 / 理由全文 / 被否决参数 JSON ─────── */
{
  const html = vetoCardHtml({
    kind: "vetoed",
    skill: "local:fib@1.0.0",
    call: { name: "system.shell.exec", args: { command: "rm -rf /" } },
    message: "ToolGuard: 禁止危险命令 rm -rf",
  });
  assert.match(html, /rca-veto/);
  assert.match(html, /裁决来源:<b class="mono">vetoed<\/b>/, "裁决来源 kind");
  assert.match(html, /ToolGuard: 禁止危险命令 rm -rf/, "理由全文");
  assert.match(html, /system\.shell\.exec/, "被否决调用名");
  assert.match(html, /<details class="json-fold rca-veto-args">/, "参数 JSON 折叠");
  assert.match(html, /&quot;command&quot;: &quot;rm -rf \/&quot;/, "参数 JSON esc 渲染");
  assert.match(html, /data-copy-label="已复制被否决参数 JSON"/, "参数 JSON 一键复制");

  const noCall = vetoCardHtml({ kind: "vetoed", call: null, message: "m" });
  assert.ok(!noCall.includes("rca-veto-call"), "call 缺失时不渲染调用区");
}

console.log("rca-panel.test.mjs: all assertions passed");

/* progress-bar.js 纯逻辑单测(WEB-UI.md §4.3 Live 进度):
   deriveProgress(steps/cost 双轨派生、比例与 80% warn 阈值、usage 回退)、
   mergeSignal(SSE 增量合并:帧生长 / 帧 chips / pop / aborted / 幂等)、
   fmtElapsed(秒级时长)、liveBarHtml/endBannerHtml(渲染结构与结束态 Banner)。
   运行:node static/tests/progress-bar.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  createLiveState,
  deriveProgress,
  endBannerHtml,
  fmtElapsed,
  liveBarHtml,
  mergeSignal,
} from "../js/components/progress-bar.js";
import { makeSignals } from "./fixtures.mjs";

const sig = (name, frame_id, payload = {}) => ({
  v: 1,
  type: "signal",
  name,
  run_id: "r1",
  frame_id,
  ts: 0,
  payload: { frame_id, ...payload },
});

/* ── deriveProgress:steps = post:step 计数;cost = Σ llm.response usage.cost ── */
{
  const detail = { config: { max_steps: 10, max_cost: 1.0 }, usage: {} };
  const signals = [
    ...Array.from({ length: 8 }, (_, i) => sig("post:step", "f1", { step: i + 1 })),
    sig("post:llm.response", "f1", { usage: { prompt: 1, completion: 1, cost: 0.5 } }),
    sig("post:llm.response", "f1", { usage: { prompt: 1, completion: 1, cost: 0.35 } }),
  ];
  const prog = deriveProgress(detail, signals);
  assert.equal(prog.steps, 8);
  assert.equal(prog.stepsMax, 10);
  assert.ok(Math.abs(prog.cost - 0.85) < 1e-9, `cost 求和: ${prog.cost}`);
  assert.equal(prog.costMax, 1.0);
  assert.equal(prog.stepsWarn, false, "8/10 = 80%,恰好不超阈值(>80% 才 warn)");
  assert.equal(prog.costWarn, true, "0.85/1.0 > 80% → warn");

  const over = deriveProgress(detail, [...signals, sig("post:step", "f1", { step: 9 })]);
  assert.equal(over.stepsWarn, true, "9/10 > 80% → warn");
}

/* ── deriveProgress:fixture 信号流(D2 fixtures,cost 全 0)──────── */
{
  const prog = deriveProgress({ config: { max_steps: 200, max_cost: 2.0 } }, makeSignals());
  assert.equal(prog.steps, 4, "fixtures 含 4 条 post:step");
  assert.equal(prog.cost, 0);
  assert.equal(prog.stepsWarn, false);
  assert.equal(prog.costWarn, false);
}

/* ── deriveProgress:信号缺失回退 detail.usage;无 config 时 max=0 ── */
{
  const prog = deriveProgress({ usage: { steps: 5, cost: 0.4 }, config: null }, []);
  assert.equal(prog.steps, 5, "无 post:step 信号时回退 usage.steps");
  assert.ok(Math.abs(prog.cost - 0.4) < 1e-9);
  assert.equal(prog.stepsMax, 0);
  assert.equal(prog.costMax, 0);
  assert.equal(prog.stepsWarn, false, "max 缺失不 warn");

  const noLlm = deriveProgress({ usage: { cost: 0.7 }, config: { max_cost: 1 } }, [
    sig("post:step", "f1", { step: 1 }),
  ]);
  assert.ok(Math.abs(noLlm.cost - 0.7) < 1e-9, "有信号但无 llm.response:cost 回退 usage");

  assert.deepEqual(deriveProgress(null, null), {
    steps: 0, stepsMax: 0, cost: 0, costMax: 0, stepsWarn: false, costWarn: false,
  });
}

/* ── mergeSignal:帧生长(pre:frame.push → running 帧)──────────── */
{
  const live = createLiveState();
  mergeSignal(live, sig("run.started", null, { skill: "local:fib@1.0.0" }));
  assert.equal(live.frames.length, 0);
  assert.equal(live.lastEffect, "signal");

  mergeSignal(live, sig("pre:frame.push", "f1", { skill: "local:fib@1.0.0", depth: 1 }));
  assert.equal(live.frames.length, 1);
  assert.equal(live.frames[0].status, "running");
  assert.equal(live.frames[0].depth, 1);
  assert.equal(live.lastEffect, "tree");

  mergeSignal(live, sig("pre:frame.push", "f1", { skill: "local:fib@1.0.0", depth: 1 }));
  assert.equal(live.frames.length, 1, "重复 push 同帧不重复插入(幂等)");
}

/* ── mergeSignal:帧 chips(post:step → steps;llm.response → cost 累加)── */
{
  const live = createLiveState();
  mergeSignal(live, sig("pre:frame.push", "f1", { skill: "s", depth: 1 }));
  mergeSignal(live, sig("post:step", "f1", { step: 3 }));
  assert.equal(live.frames[0].usage.steps, 3);
  assert.equal(live.lastEffect, "chip");
  mergeSignal(live, sig("post:llm.response", "f1", { usage: { cost: 0.2 } }));
  mergeSignal(live, sig("post:llm.response", "f1", { usage: { cost: 0.3 } }));
  assert.ok(Math.abs(live.frames[0].usage.cost - 0.5) < 1e-9, "帧 cost 逐次累加");
}

/* ── mergeSignal:pop → done;run.aborted → 在跑帧 aborted ──────── */
{
  const live = createLiveState();
  mergeSignal(live, sig("pre:frame.push", "f1", { skill: "s", depth: 1 }));
  mergeSignal(live, sig("pre:frame.push", "f2", { skill: "s", depth: 2 }));
  mergeSignal(live, sig("post:frame.pop", "f2"));
  assert.equal(live.frames[1].status, "done");
  mergeSignal(live, sig("run.aborted", null, { error: "x" }));
  assert.equal(live.frames[0].status, "aborted", "在跑帧随 run.aborted 标中止");
  assert.equal(live.frames[1].status, "done", "已完帧不被 aborted 覆盖");

  // 容错:null / 畸形信号
  mergeSignal(live, null);
  mergeSignal(live, "junk");
  assert.equal(live.signals.length, 4, "合法信号全部入流,畸形忽略");
}

/* ── fmtElapsed:秒级时长文本 ────────────────────────────────── */
{
  assert.equal(fmtElapsed(1000, 13400), "12.4s");
  assert.equal(fmtElapsed(1000, 500), "0.0s", "时钟回拨兜底为 0");
  assert.equal(fmtElapsed(Number.NaN, 1000), "—");
  assert.equal(fmtElapsed(1000, Number.NaN), "—");
}

/* ── liveBarHtml:结构 / warn 色 / 无 max 占位 ────────────────── */
{
  const html = liveBarHtml(
    { steps: 8, stepsMax: 10, cost: 0.85, costMax: 1.0, stepsWarn: false, costWarn: true },
    { elapsed: "12.4s" }
  );
  assert.match(html, /live-dot/, "live 脉冲点");
  assert.match(html, /12\.4s/, "已用时长");
  assert.match(html, /8\/10/, "steps 计数");
  assert.match(html, /\$0\.85\/\$1\.00/, "cost 计数");
  assert.match(html, /pb-cost is-warn/, "cost 条超 80% 转 warn");
  assert.doesNotMatch(html, /pb-steps is-warn/, "steps 条未超阈值不 warn");
  assert.match(html, /width:80%/, "steps 条比例宽度");
  assert.match(html, /data-lb="stop"/, "Stop 按钮常驻");

  const noMax = liveBarHtml(
    { steps: 3, stepsMax: 0, cost: 0.1, costMax: 0, stepsWarn: false, costWarn: false },
    {}
  );
  assert.match(noMax, /3\/—/, "max 缺失显示占位");
  assert.match(noMax, /width:0%/, "max 缺失宽度为 0");

  const confirm = liveBarHtml(
    { steps: 0, stepsMax: 0, cost: 0, costMax: 0, stepsWarn: false, costWarn: false },
    { phase: "confirm" }
  );
  assert.match(confirm, /中止不可逆/, "确认条含不可逆提示");
  assert.match(confirm, /data-lb="confirm"/);
  const stopping = liveBarHtml(
    { steps: 0, stepsMax: 0, cost: 0, costMax: 0, stepsWarn: false, costWarn: false },
    { phase: "stopping" }
  );
  assert.match(stopping, /中止中…/, "stop loading 态");
}

/* ── endBannerHtml:结束态 Banner(done 绿 / failed 红 / aborted 紫)── */
{
  assert.match(endBannerHtml("done"), /data-tone="ok"/);
  assert.match(endBannerHtml("done"), /run done/);
  assert.match(endBannerHtml("failed", "Boom: x"), /data-tone="danger"/);
  assert.match(endBannerHtml("failed", "Boom: x"), /Boom: x/);
  assert.match(endBannerHtml("aborted", "web stop"), /data-tone="aborted"/);
  assert.doesNotMatch(endBannerHtml("done"), /live-dot/, "结束态无 live 脉冲(熄灭)");
}

console.log("progress-bar.test.mjs: all assertions passed");

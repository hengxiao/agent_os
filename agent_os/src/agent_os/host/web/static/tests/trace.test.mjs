/* trace.js 纯逻辑单测(WEB-UI.md §4.2 中栏,debug trace 视图):
   mergePairs(llm/tool/exec 配对、veto 未配对、帧隔离)、
   buildTraceRows(call/ret 配对与行号/depth 缩进/step 跟踪/skill. 调用名/
   帧 input 参数/未配对容错:未返回 call 与孤儿 ret/post:context.inline 内联能力行)、
   collapseTrace(子树折叠/边界)、deriveTraceView(过滤/聚焦/深度默认折叠)。
   运行:node static/tests/trace.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  DEFAULT_COLLAPSE_DEPTH,
  buildTraceRows,
  collapseTrace,
  deriveTraceView,
  findVetoedCalls,
  fmtDur,
  isCallCollapsed,
  mergePairs,
  renderTrace,
  rowForSignal,
  stepOfSignal,
} from "../js/components/trace.js";
import { makeSignals } from "./fixtures.mjs";

const signals = makeSignals();

/* ── mergePairs:配对与未配对 ────────────────────────────────── */
{
  const pairs = mergePairs(signals);
  const byKind = (k) => pairs.filter((p) => p.kind === k);
  assert.deepEqual(
    byKind("llm").map((p) => [p.pre, p.post]),
    [[4, 5], [10, 11], [16, 17], [22, 23], [27, 28]],
    "llm 全配对",
  );
  assert.deepEqual(
    byKind("tool").map((p) => [p.pre, p.post]),
    [[6, 7], [12, 13], [24, null]],
    "tool:@24 未配对(veto)",
  );
  assert.equal(byKind("exec").length, 0, "fixtures 无 logic.exec");

  /* 帧隔离:子帧的 pre 不吞父帧的配对 */
  const nested = [
    { name: "pre:tool.call", frame_id: "f1", ts: 0, payload: { tool: "a" } },
    { name: "pre:tool.call", frame_id: "f2", ts: 1, payload: { tool: "b" } },
    { name: "post:tool.call", frame_id: "f2", ts: 2, payload: { ok: true } },
    { name: "post:tool.call", frame_id: "f1", ts: 3, payload: { ok: true } },
  ];
  assert.deepEqual(
    mergePairs(nested).map((p) => [p.pre, p.post]),
    [[0, 3], [1, 2]],
    "tool.call 按帧配对",
  );

  /* exec 配对本身成立(折进 tool 行的 foldedInto 标记在 buildTraceRows 阶段,见下) */
  const wrapped = [
    { name: "pre:tool.call", frame_id: "f1", ts: 0, payload: { tool: "system.python.exec" } },
    { name: "pre:logic.exec", frame_id: "f1", ts: 1, payload: { tool: "system.python.exec", trust: "sandbox" } },
    { name: "post:logic.exec", frame_id: "f1", ts: 2, payload: { ok: true } },
    { name: "post:tool.call", frame_id: "f1", ts: 3, payload: { ok: true } },
  ];
  assert.deepEqual(
    mergePairs(wrapped).map((p) => [p.kind, p.pre, p.post]),
    [["tool", 0, 3], ["exec", 1, 2]],
    "exec 嵌套在 tool 内也各自配对",
  );
}

/* ── buildTraceRows:行模型(种类/行号/depth/配对)──────────────── */
{
  const rows = buildTraceRows(signals);
  assert.deepEqual(
    rows.map((r) => r.kind),
    ["run", "call", "llm", "tool", "llm", "tool", "llm", "call", "llm", "tool",
      "llm", "ret", "ret", "run"],
    "36 信号 → 14 行(pre/post 合并,step/invoke 不占行)",
  );
  assert.deepEqual(rows.map((r) => r.line), Array.from({ length: 14 }, (_, i) => i + 1),
    "行号 0001 起连续");
  assert.deepEqual(
    rows.map((r) => r.depth),
    [0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2, 1, 0, 0],
    "depth 跟随帧栈深(call 后子行 +1,ret 回父 depth)",
  );
  /* call ↔ ret 配对:kids 与 retLine */
  const root = rows[1];
  assert.equal(root.kind, "call");
  assert.equal(root.label, "fib", "根帧无 invoke,用 shortSkill");
  assert.equal(root.retLine, 13, "根 call 配对该帧 ret 行");
  assert.equal(root.kids, 10, "call 到 ret 之间 10 行子指令");
  const inner = rows[7];
  assert.equal(inner.label, "skill.fib", "invoke 紧随的 push 起 skill. 调用名");
  assert.equal(inner.retLine, 12);
  assert.equal(inner.kids, 3);
  /* ret 行:回到父 depth,配对帧状态 */
  assert.equal(rows[11].kind, "ret");
  assert.equal(rows[11].depth, 1);
  assert.equal(rows[12].depth, 0);
  /* 合并行 sigIndex/sigEnd 区间 */
  assert.deepEqual([rows[2].sigIndex, rows[2].sigEnd], [4, 5], "llm 行覆盖 pre+post");
  assert.deepEqual([rows[1].sigIndex, rows[1].sigEnd], [1, 2], "call 行覆盖 push pre+post");
  /* 行内 step(检视器消息定位):pre:step 只更新不占行 */
  assert.equal(rows[2].step, 1);
  assert.equal(rows[4].step, 2);
  assert.equal(rows[6].step, 3);
  assert.equal(rows[7].step, 3, "call 行取调用点(父帧)step");
  assert.equal(rows[11].step, 2, "ret 行取被弹帧最后 step");
  /* 状态:vetoed / ok:false / done */
  assert.equal(rows[9].status, "vetoed", "@24 无 post → vetoed");
  assert.equal(rows[5].status, "failed", "ok:false → failed");
  assert.equal(rows[3].status, "done");
  assert.equal(rows[0].status, "running");
  assert.equal(rows[13].status, "done", "run.finished → done");
  /* label/detail 文本 */
  assert.equal(rows[2].label, "mock/fib");
  assert.match(rows[2].detail, /1\/1 tok/);
  assert.equal(rows[3].label, "system.python.exec");
  assert.match(rows[3].detail, /print\(1\)/);
}

/* ── buildTraceRows:帧 input 参数与状态(frames 参数)───────────── */
{
  const frames = [
    { frame_id: "f1", skill: "local:fib@1.0.0", status: "done", input: { n: 4 } },
    { frame_id: "f2", skill: "local:fib@1.0.0", status: "failed", input: { n: 3 } },
  ];
  const rows = buildTraceRows(signals, frames);
  assert.equal(rows[1].detail, '{"n":4}', "call 行带参数(单行)");
  assert.equal(rows[7].detail, '{"n":3}');
  assert.equal(rows[1].status, "done", "帧状态着色");
  assert.equal(rows[7].status, "failed", "失败帧 call 红");
  assert.equal(rows[11].status, "failed", "失败帧 ret 红");
}

/* ── buildTraceRows:未配对容错(断电/中断轨迹)───────────────────── */
{
  /* 截断在 f2  veto 之后:f1/f2 两个 call 都无 ret */
  const cut = buildTraceRows(signals.slice(0, 25));
  const calls = cut.filter((r) => r.kind === "call");
  assert.equal(calls.length, 2);
  assert.ok(calls.every((r) => r.status === "open"), "无 ret 的 call 标未返回");
  assert.equal(calls[0].retLine, null);
  assert.equal(calls[0].kids, cut.length - calls[0].line, "未配对 call 子树计到末尾");

  /* 未配对 call 折叠:尾部 run 行(finished/aborted)不吞进子树 */
  const abortedTrail = buildTraceRows([
    { name: "run.started", frame_id: null, ts: 0, payload: {} },
    { name: "pre:frame.push", frame_id: "f1", ts: 1, payload: { skill: "local:fib@1.0.0" } },
    { name: "pre:llm.request", frame_id: "f1", ts: 2, payload: { model: "m" } },
    { name: "post:llm.response", frame_id: "f1", ts: 3, payload: { usage: {} } },
    { name: "run.aborted", frame_id: null, ts: 4, payload: {} },
  ]);
  const openCall = abortedTrail.find((r) => r.kind === "call");
  assert.equal(openCall.kids, 1, "子树只含帧内行,不含 run.aborted");
  const foldedAbort = collapseTrace(abortedTrail, openCall.line);
  const fab = Object.fromEntries(foldedAbort.map((r) => [r.kind, r]));
  assert.equal(fab.llm.hidden, true, "帧内行折叠");
  assert.equal(fab.run.hidden, undefined, "run 行保持可见");

  /* 孤儿 ret:无配对 push,按当前 depth 渲染 */
  const orphan = buildTraceRows([
    { name: "run.started", frame_id: null, ts: 0, payload: {} },
    { name: "pre:frame.pop", frame_id: "f9", ts: 1, payload: { depth: 3, result: 1 } },
    { name: "post:frame.pop", frame_id: "f9", ts: 2, payload: {} },
  ]);
  const ret = orphan.find((r) => r.kind === "ret");
  assert.equal(ret.depth, 0, "孤儿 ret 按当前 depth");
  assert.equal(ret.status, "orphan");
  assert.equal(ret.detail, "1");

  /* post:skill.invoke ok:false 且无 push(技能缺失):补一行失败调用,不静默 */
  const noSkill = buildTraceRows([
    { name: "pre:skill.invoke", frame_id: "f1", ts: 0, payload: { skill: "ghost" } },
    { name: "post:skill.invoke", frame_id: "f1", ts: 1, payload: { skill: "ghost", ok: false } },
  ]);
  assert.equal(noSkill.length, 1);
  assert.equal(noSkill[0].label, "skill.ghost");
  assert.equal(noSkill[0].status, "failed");

  /* exec 折进 tool 行:不重复出行,trust 标注带上 */
  const wrapped = buildTraceRows([
    { name: "pre:frame.push", frame_id: "f1", ts: 0, payload: { skill: "local:fib@1.0.0" } },
    { name: "post:frame.push", frame_id: "f1", ts: 0.1, payload: {} },
    { name: "pre:tool.call", frame_id: "f1", ts: 0.2, payload: { tool: "system.python.exec", args: { code: "1" } } },
    { name: "pre:logic.exec", frame_id: "f1", ts: 0.3, payload: { tool: "system.python.exec", trust: "sandbox" } },
    { name: "post:logic.exec", frame_id: "f1", ts: 0.4, payload: { ok: true } },
    { name: "post:tool.call", frame_id: "f1", ts: 0.5, payload: { ok: true } },
  ]);
  assert.deepEqual(wrapped.map((r) => r.kind), ["call", "tool"], "exec 不重复出行");
  assert.equal(wrapped[1].trust, "sandbox", "trust 标注折进 tool 行");
  assert.equal(Math.round(wrapped[1].durMs), 300, "tool 行 durMs = post-pre");

  /* 独立 logic.exec(code skill)→ exec 行 */
  const codeSkill = buildTraceRows([
    { name: "pre:logic.exec", frame_id: "f1", ts: 0, payload: { skill: "local:gather@1.0.0", trust: "trusted" } },
    { name: "post:logic.exec", frame_id: "f1", ts: 0.02, payload: { ok: true } },
  ]);
  assert.equal(codeSkill[0].kind, "exec");
  assert.equal(codeSkill[0].label, "gather");
  assert.match(codeSkill[0].detail, /trusted/);

  /* 未知信号 → obs 行 */
  const obs = buildTraceRows([
    { name: "budget.exceeded", frame_id: "f1", ts: 0, payload: { cost: 3 } },
  ]);
  assert.equal(obs[0].kind, "obs");
  assert.equal(obs[0].status, "failed");

  /* 未配对 tool 区分:vetoed(后续同帧 tool 信号非 post)vs open(流尾在途) */
  const vetoed = buildTraceRows([
    { name: "pre:tool.call", frame_id: "f1", ts: 0, payload: { tool: "system.shell.exec", args: {} } },
    { name: "post:step", frame_id: "f1", ts: 1, payload: { step: 1, calls: [] } },
  ]);
  assert.equal(vetoed[0].status, "vetoed", "pre 后同帧下一个 tool 类信号非 post → vetoed");
  const inflight = buildTraceRows([
    { name: "pre:tool.call", frame_id: "f1", ts: 0, payload: { tool: "system.python.exec", args: {} } },
  ]);
  assert.equal(inflight[0].status, "open", "流尾未配对 = 在途(live 中 post 未到),非 veto");

  /* 在途中的 logic.exec(调用内部执行)不构成 veto 证据 */
  const innerSigs = [
    { name: "pre:tool.call", frame_id: "f1", ts: 0, payload: { tool: "system.python.exec", args: {} } },
    { name: "pre:logic.exec", frame_id: "f1", ts: 1, payload: { tool: "system.python.exec" } },
  ];
  const innerExec = buildTraceRows(innerSigs);
  assert.equal(innerExec[0].status, "open", "exec 已起、post 未到 = 在途");
  assert.deepEqual([...findVetoedCalls(innerSigs)], [], "exec 信号不算 veto");
}

/* ── collapseTrace:子树折叠 ─────────────────────────────────── */
{
  const rows = buildTraceRows(signals);
  const folded = collapseTrace(rows, 2); // 根 call @line 2
  const byLine = Object.fromEntries(folded.map((r) => [r.line, r]));
  assert.equal(byLine[2].collapsed, true, "call 行标 collapsed");
  for (let l = 3; l <= 12; l += 1) assert.equal(byLine[l].hidden, true, `line ${l} 隐藏`);
  assert.equal(byLine[13].hidden, undefined, "配对 ret 不隐藏(显示 ← ret 收尾)");
  assert.equal(byLine[1].hidden, undefined, "call 之前的行不受影响");
  assert.equal(byLine[2].kids, 10, "+N 子指令计数");
  assert.notEqual(folded, rows, "返回新数组(纯)");
  assert.equal(rows[2].collapsed, undefined, "原数组不被修改");

  /* 再折一次 / 非 call 行:原样返回 */
  assert.equal(collapseTrace(folded, 2), folded, "已折叠幂等");
  assert.equal(collapseTrace(rows, 3), rows, "非 call 行不可折叠");

  /* 嵌套:折内层 call 只藏内层子树 */
  const inner = collapseTrace(rows, 8);
  const ib = Object.fromEntries(inner.map((r) => [r.line, r]));
  assert.equal(ib[9].hidden, true);
  assert.equal(ib[11].hidden, true);
  assert.equal(ib[12].hidden, undefined, "内层 ret 可见");
  assert.equal(ib[3].hidden, undefined, "兄弟行不受影响");
}

/* ── deriveTraceView:默认深度折叠 / 用户展开态 / 过滤 / 聚焦 ────── */
{
  /* 深递归信号:depth 1..7 嵌套 call,无其他行 */
  const deep = [];
  for (let d = 1; d <= 7; d += 1) {
    deep.push({ name: "pre:frame.push", frame_id: `f${d}`, ts: d, payload: { depth: d, skill: "local:fib@1.0.0" } });
  }
  for (let d = 7; d >= 1; d -= 1) {
    deep.push({ name: "pre:frame.pop", frame_id: `f${d}`, ts: 20 - d, payload: { depth: d, result: d } });
  }
  const view = deriveTraceView(deep, null);
  const byLine = Object.fromEntries(view.rows.map((r) => [r.line, r]));
  /* row.depth: call f1..f7 = 0..6;>4 的 call(f6@depth5,f7@depth6)默认折叠 */
  assert.equal(byLine[6].collapsed, true, "depth 5 的 call 默认折叠");
  assert.equal(byLine[7].hidden, true, "depth 6 的 call 随父折叠隐藏");
  assert.equal(byLine[5].collapsed, undefined, "depth 4 的 call 不默认折叠");
  assert.equal(byLine[8].hidden, true, "f6 的 ret 在折叠子树内");
  assert.equal(byLine[13].hidden, undefined, "f1 的 ret 可见");
  assert.equal(isCallCollapsed(byLine[6], new Set(), new Set()), true);
  assert.equal(isCallCollapsed(byLine[5], new Set(), new Set()), false);
  assert.equal(DEFAULT_COLLAPSE_DEPTH, 4, "规格:depth>4 默认折叠");

  /* 用户显式展开覆盖默认折叠 */
  const sig6 = byLine[6].sigIndex;
  const view2 = deriveTraceView(deep, null, { expanded: new Set([sig6]) });
  const b2 = Object.fromEntries(view2.rows.map((r) => [r.line, r]));
  assert.equal(b2[6].collapsed, undefined, "显式展开生效");
  assert.equal(b2[7].hidden, undefined, "内层 call 随父展开可见");
  assert.equal(b2[7].collapsed, undefined, "f7 无子行(kids=0),不折叠");

  /* 过滤(帧树选帧):只留该帧行 */
  const fv = deriveTraceView(signals, { frameId: "f2", signalIndex: null, source: "tree" });
  assert.equal(fv.filtered, true);
  assert.deepEqual(fv.rows.map((r) => r.kind), ["call", "llm", "tool", "llm", "ret"],
    "f2 帧的 call/llm/vetoed tool/llm/ret");
  assert.equal(fv.hiddenCount, 14 - 5);

  /* 过滤保留直接子帧 call/ret 边界行(parentFrameId 命中),边界 kids 视图内重算 */
  const f1 = deriveTraceView(signals, { frameId: "f1", signalIndex: null, source: "tree" });
  assert.deepEqual(
    f1.rows.map((r) => [r.kind, r.line]),
    [["call", 2], ["llm", 3], ["tool", 4], ["llm", 5], ["tool", 6], ["llm", 7],
      ["call", 8], ["ret", 12], ["ret", 13]],
    "f1 视图含 f2 的 call/ret 边界,不含其内部行",
  );
  const childCall = f1.rows.find((r) => r.line === 8);
  assert.equal(childCall.kids, 0, "边界 call 视图内无子行 → 无 chevron");
  const rootCall = f1.rows.find((r) => r.line === 2);
  assert.equal(rootCall.kids, 7, "根 call 视图内 7 行(5 内部行 + f2 边界 call/ret)");

  /* 聚焦:合并行区间命中;错位聚焦(f1 过滤 × f2 信号)落 f2 边界 call */
  const fo = deriveTraceView(signals, { frameId: "f2", signalIndex: 24, source: "timeline" });
  assert.deepEqual(fo.focused, { signalIndex: 24, line: 10, visible: true });
  const fo2 = deriveTraceView(signals, { frameId: "f1", signalIndex: 24, source: "tree" });
  assert.deepEqual(fo2.focused, { signalIndex: 24, line: 10, visible: true },
    "过滤视图内精确行缺失时:line 仍指精确行,visible 经回退行可滚动");
  const none = deriveTraceView(signals, null);
  assert.equal(none.focused, null);
}

/* ── stepOfSignal / findVetoedCalls / fmtDur ─────────────────── */
{
  assert.equal(stepOfSignal(signals, 24), 1);
  assert.equal(stepOfSignal(signals, 33), 3);
  assert.equal(stepOfSignal(signals, 0), null, "帧外信号无 step");
  assert.deepEqual([...findVetoedCalls(signals)], [24]);
  assert.equal(fmtDur(null), "");
  assert.equal(fmtDur(2), "", "<5ms 视为 mock 噪音");
  assert.equal(fmtDur(400), "0.4s");
  assert.equal(fmtDur(12), "0.01s");
  assert.equal(fmtDur(120), "0.12s");
  assert.equal(fmtDur(1500), "1.5s");
  assert.equal(fmtDur(12300), "12.3s");

  /* rowForSignal:精确覆盖优先;非行信号落最近前行 */
  const rows = buildTraceRows(signals);
  assert.equal(rowForSignal(rows, 24)?.sigIndex, 24, "veto 行精确命中");
  assert.equal(rowForSignal(rows, 5)?.sigIndex, 4, "post 下标命中合并行");
  assert.equal(rowForSignal(rows, 18)?.sigIndex, 16, "pre:skill.invoke 落最近前行");
  assert.equal(rowForSignal(rows, 3)?.sigIndex, 1, "pre:step 落最近前行(call)");
  assert.equal(rowForSignal([], 3), null);
}

/* ── buildTraceRows:post:context.inline 内联能力行(SKILL-INLINING.md §7)── */
{
  /* 帧首次 build 的一次性信号:专门行 kind=inline,弱化色(不占 call/ret 语义) */
  const sigs = [
    { name: "run.started", frame_id: null, ts: 0, payload: { skill: "report_writer" } },
    { name: "pre:frame.push", frame_id: "f1", ts: 1, payload: { skill: "local:report_writer@1.0.0" } },
    { name: "post:frame.push", frame_id: "f1", ts: 1.1, payload: {} },
    {
      name: "post:context.inline", frame_id: "f1", ts: 1.2,
      payload: {
        frame_id: "f1",
        skills: [
          { name: "date_style", version: "1.0.0", chars: 42 },
          { name: "tone_guide", version: "2.1.0", chars: 58 },
        ],
      },
    },
    { name: "pre:llm.request", frame_id: "f1", ts: 2, payload: { model: "mock/x" } },
    { name: "post:llm.response", frame_id: "f1", ts: 2.4, payload: { usage: { prompt: 1, completion: 1 } } },
    { name: "pre:frame.pop", frame_id: "f1", ts: 3, payload: { result: { report: "ok" } } },
    { name: "run.finished", frame_id: null, ts: 4, payload: {} },
  ];
  const rows = buildTraceRows(sigs);
  assert.deepEqual(rows.map((r) => r.kind), ["run", "call", "inline", "llm", "ret", "run"],
    "inline 信号生成专门行");
  const inline = rows[2];
  assert.equal(inline.kind, "inline");
  assert.equal(inline.label, "date_style@1.0.0(+1)", "首个技能 + 其余条数");
  assert.equal(inline.detail, "100 chars", "chars 合计");
  assert.equal(inline.status, "obs", "弱化样式(非 call/ret 语义色、非异常红)");
  assert.equal(inline.depth, 1, "帧内深度(信号在 push 之后)");
  assert.equal(inline.frameId, "f1");
  assert.deepEqual(inline.payload, {
    skills: [
      { name: "date_style", version: "1.0.0", chars: 42 },
      { name: "tone_guide", version: "2.1.0", chars: 58 },
    ],
  }, "payload 面板数据(frame_id 作为公共字段剥掉)");
  assert.equal(inline.names, "post:context.inline");
  /* 单技能:无 (+N) 标注 */
  const single = buildTraceRows([
    {
      name: "post:context.inline", frame_id: "f1", ts: 0,
      payload: { frame_id: "f1", skills: [{ name: "date_style", version: "1.0.0", chars: 42 }] },
    },
  ]);
  assert.equal(single[0].label, "date_style@1.0.0");
  assert.equal(single[0].detail, "42 chars");
  /* 渲染:⇥ inline 前缀 + 弱化 kw 色 */
  const html = renderTrace(deriveTraceView(sigs, null));
  assert.match(html, /data-kind="inline"/, "行 data-kind");
  assert.match(html, /<span class="tr-kw" data-k="inline">⇥ inline<\/span>/, "⇥ inline 前缀");
  assert.match(html, /date_style@1\.0\.0\(\+1\) · 100 chars/, "行文本");
  /* 畸形 payload(无 skills)容错:行仍在,label 占位 */
  const broken = buildTraceRows([
    { name: "post:context.inline", frame_id: "f1", ts: 0, payload: {} },
  ]);
  assert.equal(broken[0].kind, "inline");
  assert.equal(broken[0].label, "—");
  assert.equal(broken[0].detail, "");
}

console.log("trace.test.mjs: all assertions passed");

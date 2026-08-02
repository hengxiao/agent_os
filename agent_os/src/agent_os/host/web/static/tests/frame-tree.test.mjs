/* frame-tree.js 纯逻辑单测(docs/WEB-UI.md §4.2 左栏):
   buildFrameTree(depth 平表 → 树;pushOrder 修正同 depth 交叠的兄弟归属)、
   defaultCollapsedIds(depth>3 且有子帧)、findPath(联动展开祖先)、
   frameDurations(帧首末信号 ts 差)、spanBlockModel(块头视图模型 / metadata 省略规则)、
   renderFrameTree(嵌套跨度块 HTML)。
   运行:node static/tests/frame-tree.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  buildFrameTree,
  defaultCollapsedIds,
  findPath,
  flattenVisible,
  frameDurations,
  renderFrameTree,
  spanBlockModel,
} from "../js/components/frame-tree.js";
import { makeBranchFrames, makeFrames, makeSignals } from "./fixtures.mjs";

/* ── buildFrameTree:基本嵌套(链式 + 兄弟)─────────────────────── */
{
  const roots = buildFrameTree(makeFrames());
  assert.equal(roots.length, 1);
  assert.equal(roots[0].frame.frame_id, "f1");
  assert.equal(roots[0].children[0].frame.frame_id, "f2");
}

/* ── buildFrameTree:pushOrder 修正(depth 排序平表的误挂)────────── */
{
  // 真实压栈序 a→b→d(深)→c:d 是 b 的子帧;但 detail.frames 按 depth 稳定排序
  // 为 [a1, b2, c2, d3],无 pushOrder 时栈式重建会把 d 误挂到 c(最近的 d2)。
  const frames = makeBranchFrames();
  const naive = buildFrameTree(frames);
  const b = naive[0].children.find((n) => n.frame.frame_id === "b");
  const c = naive[0].children.find((n) => n.frame.frame_id === "c");
  assert.equal(b.children.length, 0);
  assert.equal(c.children[0]?.frame.frame_id, "d", "无 pushOrder:d 挂在 c 下(已知近似)");

  const fixed = buildFrameTree(frames, ["a", "b", "d", "c"]); // pre:frame.push 真实序
  const b2 = fixed[0].children.find((n) => n.frame.frame_id === "b");
  const c2 = fixed[0].children.find((n) => n.frame.frame_id === "c");
  assert.equal(b2.children[0]?.frame.frame_id, "d", "pushOrder 修正后 d 挂在 b 下");
  assert.equal(c2.children.length, 0);
  assert.equal(fixed[0].children.length, 2, "b/c 都是 a 的子帧");
}

/* ── defaultCollapsedIds:depth>3 且有子帧(§4.2/§6.3)────────────── */
{
  const chain = [1, 2, 3, 4, 5].map((d) => ({
    frame_id: `f${d}`,
    skill: "local:fib@1.0.0",
    depth: d,
    status: "done",
    usage: { steps: 1 },
  }));
  const roots = buildFrameTree(chain);
  const collapsed = defaultCollapsedIds(roots);
  assert.deepEqual([...collapsed], ["f4"], "仅 f4(depth 4,有子帧 f5)默认折叠;f5 是叶子不可折叠");

  const rows = flattenVisible(roots, collapsed);
  assert.deepEqual(rows.map((r) => r.node.frame.frame_id), ["f1", "f2", "f3", "f4"],
    "f4 折叠后 f5 不可见");
  assert.equal(rows[3].collapsed, true);
  assert.equal(rows[3].hasChildren, true);
}

/* ── findPath:联动展开祖先(时间线选中 → 帧树定位)────────────────── */
{
  const roots = buildFrameTree(makeFrames());
  const path = findPath(roots, "f2");
  assert.deepEqual(path.map((n) => n.frame.frame_id), ["f1", "f2"]);
  assert.equal(findPath(roots, "ghost"), null);
}

/* ── renderFrameTree:嵌套块结构 / metadata / 选中态 ────────────── */
{
  const html = renderFrameTree(buildFrameTree(makeFrames()), {
    selection: { frameId: "f2", signalIndex: null, source: "tree" },
  });
  assert.match(html, /data-frame-id="f2"[^>]*aria-selected="true"/, "选中块头 aria-selected");
  assert.match(html, /data-frame-id="f1"[^>]*aria-selected="false"/);
  assert.match(html, /3 steps/, "metadata steps 段");
  assert.match(html, /ft-block/, "跨度块");
  assert.match(html, /ft-kids/, "子块容器(嵌套)");
  assert.match(html, /status-dot/, "状态点");
  assert.match(html, />fib</, "skill 缩写");
  // 子块嵌在父块内部(c1 的 HTML 出现在父块闭合前由嵌套结构保证,此处校验顺序)
  assert.ok(
    html.indexOf('data-frame-id="f2"') > html.indexOf('data-frame-id="f1"'),
    "子帧块在父帧块之后(嵌套渲染)");
  // 空树
  assert.equal(renderFrameTree([]), "");
}

/* ── frameDurations:帧首末信号 ts 差(§4.2 块头时长)────────────── */
{
  // fixture 信号 ts 全 0 → 时长 0
  assert.equal(frameDurations(makeSignals()).get("f1"), 0);
  const sigs = [
    { name: "run.started", frame_id: null, ts: 9 }, // 无 frame_id:忽略
    { name: "pre:frame.push", frame_id: "a", ts: 10 },
    { name: "pre:llm.request", frame_id: "a", ts: 11.5 },
    { name: "pre:frame.push", frame_id: "b", ts: "bad" }, // 非数值 ts:忽略
    { name: "post:frame.pop", frame_id: "b", ts: 12.25 },
  ];
  const d = frameDurations(sigs);
  assert.equal(d.get("a"), 1500, "首末 ts 差 → ms");
  assert.equal(d.get("b"), 0, "单有效信号帧时长 0");
  assert.equal(frameDurations(null).size, 0, "非数组输入 → 空 Map");
}

/* ── spanBlockModel:视图模型与 metadata 无值省略 ──────────────── */
{
  const f = {
    frame_id: "f1",
    skill: "local:handle_ticket@1.0.0",
    depth: 1,
    status: "done",
    usage: { steps: 6 },
  };
  const m = spanBlockModel(f, { kind: "prompt", tokens: 128, cost: 0.02, durationMs: 1400 });
  assert.equal(m.skill, "handle_ticket");
  assert.equal(m.kind, "prompt");
  assert.deepEqual(m.meta, [
    { key: "steps", text: "6 steps" },
    { key: "tok", text: "128 tok" },
    { key: "cost", text: "$0.02" },
    { key: "dur", text: "1.4s" },
  ]);
  assert.equal(m.metaText, "6 steps · 128 tok · $0.02 · 1.4s");
  // 单数:1 step(非 "1 steps")
  assert.equal(spanBlockModel({ ...f, usage: { steps: 1 } }).metaText, "1 step");
  // 无值省略:0 tok / $0.00 / 0ms 不出现(§4.2:0 值是噪音)
  assert.deepEqual(
    spanBlockModel(f, { kind: "code", tokens: 0, cost: 0, durationMs: 0 }).meta,
    [{ key: "steps", text: "6 steps" }]);
  // tokens 紧凑格式化 + 时长分级
  assert.equal(spanBlockModel(f, { tokens: 1540, durationMs: 380 }).metaText,
    "6 steps · 1.5k tok · 380ms");
  assert.equal(spanBlockModel(f, { durationMs: 65000 }).metaText, "6 steps · 1m 5s");
  assert.equal(spanBlockModel(f, { durationMs: 3.82 }).metaText, "6 steps · 3.8ms");
  // 无 extras:steps/cost 回落 frame.usage,kind → null(不渲染 chip)
  const m3 = spanBlockModel({ ...f, usage: { steps: 2, cost: 0.5 } });
  assert.equal(m3.metaText, "2 steps · $0.50");
  assert.equal(m3.kind, null);
  assert.equal(m3.status, "done");
}

/* ── renderFrameTree + extras:kind chip / +N 折叠计数 / 状态 accent ── */
{
  const frames = [
    { frame_id: "p", skill: "local:orchestrator@1.0.0", depth: 1, status: "done", usage: { steps: 4 } },
    { frame_id: "c1", skill: "local:fetch@1.0.0", depth: 2, status: "done", usage: { steps: 0 } },
    { frame_id: "c2", skill: "local:summarize@1.0.0", depth: 2, status: "failed", usage: { steps: 1 } },
  ];
  const roots = buildFrameTree(frames);
  const extras = new Map([
    ["p", { kind: "prompt", tokens: 1200, cost: 0.03, durationMs: 2300 }],
  ]);
  const open = renderFrameTree(roots, { extras });
  assert.match(open, /ft-block[^>]*data-status="failed"/, "状态 accent 按 data-status");
  assert.match(open, /ft-kind[^>]*>prompt</, "kind chip");
  assert.match(open, /4 steps · 1\.2k tok · \$0\.03 · 2\.3s/, "metadata 行(弱色 mono)");
  assert.match(open, /ft-kids/, "展开渲染子块容器");
  const collapsedHtml = renderFrameTree(roots, { extras, collapsed: new Set(["p"]) });
  assert.match(collapsedHtml, /\+2</, "折叠块子帧计数 +N");
  assert.ok(!collapsedHtml.includes("ft-kids"), "折叠后不渲染子块容器");
}

/* ── 与信号流联动:pushOrder 来自 pre:frame.push ────────────────── */
{
  const signals = makeSignals();
  const order = signals.filter((s) => s.name === "pre:frame.push").map((s) => s.frame_id);
  assert.deepEqual(order, ["f1", "f2"]);
  const roots = buildFrameTree(makeFrames(), order);
  assert.equal(roots[0].children[0].frame.frame_id, "f2");
}

console.log("frame-tree.test.mjs: all assertions passed");

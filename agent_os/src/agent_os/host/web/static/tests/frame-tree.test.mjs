/* frame-tree.js 纯逻辑单测(WEB-UI.md §4.2 左栏):
   buildFrameTree(depth 平表 → 树;pushOrder 修正同 depth 交叠的兄弟归属)、
   defaultCollapsedIds(depth>3 且有子帧)、findPath(联动展开祖先)。
   运行:node static/tests/frame-tree.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  buildFrameTree,
  defaultCollapsedIds,
  findPath,
  flattenVisible,
  renderFrameTree,
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

/* ── renderFrameTree:行结构 / chips / 选中态 ───────────────────── */
{
  const html = renderFrameTree(buildFrameTree(makeFrames()), {
    selection: { frameId: "f2", signalIndex: null, source: "tree" },
  });
  assert.match(html, /data-frame-id="f2"[^>]*aria-selected="true"/, "选中行 aria-selected");
  assert.match(html, /data-frame-id="f1"[^>]*aria-selected="false"/);
  assert.match(html, /3 steps/, "steps chip");
  assert.match(html, /ft-guide/, "缩进引导线");
  assert.match(html, /status-dot/, "状态点");
  assert.match(html, />fib</, "skill 缩写");
  // 空树
  assert.equal(renderFrameTree([]), "");
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

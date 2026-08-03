/* ns-tree.js 纯逻辑单测(docs/NAMING.md §2 命名空间树;L4.5):
   buildNsTree(多级/单层链折叠/无点名单段/同名既叶子又命名空间)、
   filterNsTree(命中保留祖先/无命中空)、defaultExpanded(一级展开 + 小树阈值)、
   nsTreeHtml(叶子行 vs 折叠行/展开态)。
   运行:node static/tests/ns-tree.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  allNamespaces,
  buildNsTree,
  defaultExpanded,
  filterNsTree,
  flattenLeaves,
  nsTreeHtml,
} from "../js/components/ns-tree.js";

const NAMES = [
  "system.file.read",
  "system.file.write",
  "system.shell.exec",
  "system.time.now",
  "ops.scan.workspace",
  "ops.plan.write",
  "common.text.extract_json",
  "fib",
];

/* ── buildNsTree:多级 + 单层链折叠 + 无点名单段 ───────────────── */
{
  const tree = buildNsTree(NAMES);
  const segs = tree.children.map((c) => c.seg);
  assert.deepEqual(segs, ["common.text", "fib", "ops", "system"], "一级按字母序;单层链 common+text 折叠");

  const system = tree.children.find((c) => c.seg === "system");
  assert.deepEqual(
    system.children.map((c) => c.seg),
    ["file", "shell", "time"],
    "多子命名空间不折叠",
  );
  const file = system.children.find((c) => c.seg === "file");
  assert.equal(file.full, "system.file");
  assert.equal(file.count, 2, "叶子计数 = 本命名空间叶子总数");

  // 单层链折叠到叶子为止:time 只有一个叶子 now → time 自身保留(叶子不是命名空间)
  const time = system.children.find((c) => c.seg === "time");
  assert.equal(time.children.length, 1);
  assert.equal(time.children[0].leaf, "system.time.now");

  // ops 有两个子命名空间(plan/scan)→ 不折叠
  const ops = tree.children.find((c) => c.seg === "ops");
  assert.deepEqual(
    ops.children.map((c) => c.seg),
    ["plan", "scan"],
    "多子命名空间各出一行",
  );

  // 纯单层链:ops → scan(唯一子命名空间)→ 折叠为一行 "ops.scan"
  const chain = buildNsTree(["ops.scan.workspace"]);
  const only = chain.children[0];
  assert.equal(only.seg, "ops.scan", "ops + scan 单层链折叠");
  assert.equal(only.full, "ops.scan");
  assert.equal(only.children[0].leaf, "ops.scan.workspace");

  // 无点名单段 fib = 根层叶子条目
  const fib = tree.children.find((c) => c.seg === "fib");
  assert.equal(fib.leaf, "fib");
  assert.equal(fib.children.length, 0);
}

/* ── 同名既叶子又命名空间:common 自身是技能也是域 ─────────────── */
{
  const tree = buildNsTree(["common", "common.text.summarize"]);
  const common = tree.children[0];
  assert.equal(common.leaf, "common", "本层有同名叶子条目");
  assert.equal(common.children.length, 1, "同时是命名空间");
  assert.equal(common.count, 2, "计数 = 自身 + 下层叶子");
}

/* ── filterNsTree:命中叶子保留 + 祖先链;无命中空 ─────────────── */
{
  const tree = buildNsTree(NAMES);
  const hit = filterNsTree(tree, "extract");
  assert.deepEqual(flattenLeaves(hit), ["common.text.extract_json"], "只有命中叶子");
  assert.deepEqual(
    hit.children.map((c) => c.seg),
    ["common.text"],
    "祖先链保留(单层链折叠后的形态)",
  );
  assert.deepEqual(flattenLeaves(filterNsTree(tree, "zzz")), [], "无命中为空");
  assert.equal(filterNsTree(tree, ""), tree, "空查询原样返回");
}

/* ── defaultExpanded:一级展开 + 小树(<8)展开;大树折叠 ────────── */
{
  const tree = buildNsTree(NAMES);
  const expanded = defaultExpanded(tree);
  assert.ok(expanded.has("system"), "一级展开");
  assert.ok(expanded.has("ops.scan"), "小树默认展开(2 < 8)");
  assert.ok(expanded.has("common.text"), "小树默认展开(1 < 8)");

  const big = buildNsTree([
    ...Array.from({ length: 10 }, (_, i) => `app.big.skill_${i}`),
    "app.small.x",
  ]);
  const bigExpanded = defaultExpanded(big);
  assert.ok(bigExpanded.has("app"), "一级仍展开(不论大小)");
  assert.ok(!bigExpanded.has("app.big"), "大树(10 ≥ 8)的二级折叠");
  assert.ok(bigExpanded.has("app.small"), "小树(1 < 8)的二级展开");
}

/* ── nsTreeHtml:叶子行 vs 折叠行;展开态决定子层是否渲染 ───────── */
{
  const tree = buildNsTree(["system.file.read", "system.file.write", "ops.scan.workspace"]);
  const html = nsTreeHtml(tree, {
    expanded: new Set(["system.file"]),
    leafHtml: (item, depth) => `<li data-leaf="${item}" data-depth="${depth}"></li>`,
  });
  assert.ok(html.includes('data-ns-toggle="system.file"'), "命名空间折叠行(单层链折叠后)");
  assert.ok(!html.includes('data-ns-toggle="system.file.read"'), "叶子不出折叠行");
  assert.ok(html.includes('data-leaf="system.file.read"'), "展开的命名空间叶子上桌");
  assert.ok(!html.includes('data-leaf="ops.scan.workspace"'), "折叠的命名空间叶子不渲染");

  const all = nsTreeHtml(tree, {
    expanded: allNamespaces(tree),
    leafHtml: (item) => `<li data-leaf="${item}"></li>`,
  });
  assert.ok(all.includes('data-leaf="ops.scan.workspace"'), "全展开后叶子渲染");
}

console.log("ns-tree.test.mjs: all assertions passed");

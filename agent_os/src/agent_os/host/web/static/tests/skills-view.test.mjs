/* skills-view.js / util.js 纯逻辑单测(docs/WEB-UI.md §4.6):
   findCycles(permissions.skills 依赖图循环检测,Tarjan SCC):
     **自引用(fib 的 skills:[fib])是合法递归,必须不算循环**;
     a→b→a 算环(环上全部技能标注);链式/菱形不算;未知技能边忽略;
   splitRouteDescription(Use when / Do not use when 分行:行内出现同样切开)。
   运行:node static/tests/skills-view.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import { findCycles } from "../js/components/skills-view.js";
import { splitRouteDescription } from "../js/util.js";

const skill = (name, deps = []) => ({
  name,
  version: "1.0.0",
  kind: "prompt",
  permissions: { tools: [], skills: deps, blackboard: [] },
});

/* ── 自引用 = 合法递归,不算循环(instance/skills.yaml 的 fib)───── */
{
  assert.deepEqual(findCycles([skill("fib", ["fib"])]), new Set(),
    "fib → fib 自引用是合法递归,不标循环");
  assert.deepEqual(findCycles([skill("a", ["a"]), skill("b", ["a"])]), new Set(),
    "自引用 + 普通入边仍无环");
}

/* ── 互环:a→b→a 算环(环上全部技能都标注)────────────────────── */
{
  assert.deepEqual(findCycles([skill("a", ["b"]), skill("b", ["a"])]), new Set(["a", "b"]));
}

/* ── 三节点环 + 自引用混杂:环照旧检出,自引用不误伤 ────────────── */
{
  const manifests = [
    skill("a", ["b"]),
    skill("b", ["c"]),
    skill("c", ["a"]),
    skill("fib", ["fib"]), // 合法递归,不进环
    skill("user", ["a"]), // 依赖环上技能,但自己不在环上
  ];
  assert.deepEqual(findCycles(manifests), new Set(["a", "b", "c"]));
}

/* ── 链式 / 菱形依赖:无环 ───────────────────────────────────── */
{
  assert.deepEqual(findCycles([skill("a", ["b"]), skill("b", ["c"]), skill("c")]), new Set());
  assert.deepEqual(
    findCycles([skill("a", ["b", "c"]), skill("b", ["d"]), skill("c", ["d"]), skill("d")]),
    new Set(),
    "菱形汇聚不成环",
  );
}

/* ── 两个不相交的环:各自检出 ─────────────────────────────────── */
{
  const manifests = [
    skill("a", ["b"]),
    skill("b", ["a"]),
    skill("x", ["y"]),
    skill("y", ["x"]),
    skill("solo"),
  ];
  assert.deepEqual(findCycles(manifests), new Set(["a", "b", "x", "y"]));
}

/* ── 未知技能边 / 空输入 / 缺 permissions:不炸、不误报 ─────────── */
{
  assert.deepEqual(findCycles([skill("a", ["ghost"])]), new Set(), "指向未知技能不成环");
  assert.deepEqual(findCycles([]), new Set());
  assert.deepEqual(findCycles(null), new Set());
  assert.deepEqual(findCycles([{ name: "a" }, {}]), new Set(), "畸形条目跳过");
}

/* ── splitRouteDescription:行内标记同样切开 ─────────────────── */
{
  const fibDesc =
    "生成前 n 个菲波拉契数。Use when 需要菲波拉契数列;Do not use when 需要大 n 高效计算。";
  assert.deepEqual(splitRouteDescription(fibDesc), [
    { kind: "plain", text: "生成前 n 个菲波拉契数。" },
    { kind: "use", text: "Use when 需要菲波拉契数列;" },
    { kind: "avoid", text: "Do not use when 需要大 n 高效计算。" },
  ]);
  assert.deepEqual(splitRouteDescription("Use when 仅有触发条件。"), [
    { kind: "use", text: "Use when 仅有触发条件。" },
  ]);
  assert.deepEqual(splitRouteDescription("普通描述,没有标记。"), [
    { kind: "plain", text: "普通描述,没有标记。" },
  ]);
  assert.deepEqual(splitRouteDescription(""), []);
  assert.deepEqual(splitRouteDescription(null), []);
}

console.log("skills-view.test.mjs: all assertions passed");

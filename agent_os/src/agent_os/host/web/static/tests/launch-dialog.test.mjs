/* launch-dialog.js 纯逻辑单测(WEB-UI.md §4.3 New Run Modal):
   validateAgainstSchema(手写小型 JSON Schema 校验器:required 缺失 / type 不符 /
   minimum 违例 / 嵌套对象 / 非 JSON 输入)、skeletonFromSchema(示例骨架)、
   summarizeInputs(inputs 摘要)、buildOverrides(高级区 → overrides)。
   运行:node static/tests/launch-dialog.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  buildOverrides,
  skeletonFromSchema,
  summarizeInputs,
  validateAgainstSchema,
} from "../js/components/launch-dialog.js";

/* fib 技能的 inputs schema(与 instance/skills.yaml 同形) */
const FIB_SCHEMA = {
  type: "object",
  properties: { n: { type: "integer", minimum: 1 } },
  required: ["n"],
};

/* ── validateAgainstSchema:合法输入 ────────────────────────── */
{
  assert.deepEqual(validateAgainstSchema({ n: 3 }, FIB_SCHEMA), []);
  assert.deepEqual(validateAgainstSchema({ n: 3, extra: "x" }, FIB_SCHEMA), [],
    "未声明的额外字段不拦(子集校验,后端 jsonschema 兜底)");
  assert.deepEqual(validateAgainstSchema({ n: 1 }, FIB_SCHEMA), [], "minimum 边界值合法");
}

/* ── required 缺失 ─────────────────────────────────────────── */
{
  assert.deepEqual(validateAgainstSchema({}, FIB_SCHEMA), [
    { path: "$.n", message: "缺少必填字段" },
  ]);
}

/* ── type 不符(根 / 字段 / integer 严格性)────────────────── */
{
  assert.deepEqual(validateAgainstSchema([], FIB_SCHEMA), [
    { path: "$", message: "应为 object" },
  ], "数组不是 object");
  assert.deepEqual(validateAgainstSchema(null, FIB_SCHEMA), [
    { path: "$", message: "应为 object" },
  ]);
  assert.deepEqual(validateAgainstSchema({ n: "x" }, FIB_SCHEMA), [
    { path: "$.n", message: "应为 integer" },
  ]);
  assert.deepEqual(validateAgainstSchema({ n: 1.5 }, FIB_SCHEMA), [
    { path: "$.n", message: "应为 integer" },
  ], "浮点不是 integer");
  assert.deepEqual(validateAgainstSchema({ n: true }, FIB_SCHEMA), [
    { path: "$.n", message: "应为 integer" },
  ], "boolean 不是 integer");
}

/* ── 非 JSON 输入(编辑器 JSON.parse 之外的"值非对象"路径)───── */
{
  assert.deepEqual(validateAgainstSchema("not json", FIB_SCHEMA), [
    { path: "$", message: "应为 object" },
  ]);
  assert.deepEqual(validateAgainstSchema(123, FIB_SCHEMA), [
    { path: "$", message: "应为 object" },
  ]);
}

/* ── minimum / maximum 违例 ────────────────────────────────── */
{
  assert.deepEqual(validateAgainstSchema({ n: 0 }, FIB_SCHEMA), [
    { path: "$.n", message: "不能小于 minimum 1" },
  ]);
  const MAX_SCHEMA = {
    type: "object",
    properties: { x: { type: "number", maximum: 5 } },
  };
  assert.deepEqual(validateAgainstSchema({ x: 9 }, MAX_SCHEMA), [
    { path: "$.x", message: "不能大于 maximum 5" },
  ]);
  assert.deepEqual(validateAgainstSchema({ x: 5 }, MAX_SCHEMA), [], "maximum 边界值合法");
}

/* ── 嵌套对象(递归 properties/required)───────────────────── */
{
  const NESTED = {
    type: "object",
    properties: {
      a: {
        type: "object",
        properties: { b: { type: "number", minimum: 0 } },
        required: ["b"],
      },
    },
    required: ["a"],
  };
  assert.deepEqual(validateAgainstSchema({}, NESTED), [
    { path: "$.a", message: "缺少必填字段" },
  ]);
  assert.deepEqual(validateAgainstSchema({ a: {} }, NESTED), [
    { path: "$.a.b", message: "缺少必填字段" },
  ], "嵌套 required 路径逐级展开");
  assert.deepEqual(validateAgainstSchema({ a: { b: -1 } }, NESTED), [
    { path: "$.a.b", message: "不能小于 minimum 0" },
  ]);
  assert.deepEqual(validateAgainstSchema({ a: { b: 2 } }, NESTED), []);
}

/* ── array items(递归)────────────────────────────────────── */
{
  const ARR = {
    type: "object",
    properties: { seq: { type: "array", items: { type: "integer" } } },
  };
  assert.deepEqual(validateAgainstSchema({ seq: [1, "x", 3] }, ARR), [
    { path: "$.seq[1]", message: "应为 integer" },
  ]);
  assert.deepEqual(validateAgainstSchema({ seq: [] }, ARR), []);
}

/* ── 容错:空 schema / 未声明类型 ──────────────────────────── */
{
  assert.deepEqual(validateAgainstSchema({ n: 1 }, null), []);
  assert.deepEqual(validateAgainstSchema({ n: 1 }, {}), []);
  assert.deepEqual(validateAgainstSchema({ v: "x" }, { properties: { v: { type: "string" } } }), [],
    "未声明 type=object 时不做 required/properties 深入(子集哲学)");
}

/* ── skeletonFromSchema:示例骨架(§4.3:{"n": 1} 形式)───────── */
{
  assert.deepEqual(skeletonFromSchema(FIB_SCHEMA), { n: 1 }, "fib inputs → {\"n\": 1}");
  assert.deepEqual(skeletonFromSchema({
    type: "object",
    properties: {
      q: { type: "string" },
      k: { type: "integer", minimum: 3 },
      f: { type: "number" },
      ok: { type: "boolean" },
      tags: { type: "array" },
      sub: { type: "object", properties: { x: { type: "integer" } } },
    },
  }), { q: "", k: 3, f: 1, ok: false, tags: [], sub: { x: 1 } },
    "各类型默认值;integer 取 minimum");
  assert.deepEqual(skeletonFromSchema(null), {});
  assert.deepEqual(skeletonFromSchema({}), {}, "空 schema → 空对象");
}

/* ── summarizeInputs:inputs 摘要行 ─────────────────────────── */
{
  assert.deepEqual(summarizeInputs(FIB_SCHEMA), ["n: integer, minimum 1(必填)"]);
  assert.deepEqual(summarizeInputs({ type: "object" }), []);
  assert.deepEqual(summarizeInputs(null), []);
  assert.deepEqual(
    summarizeInputs({ properties: { q: { type: "string" } } }),
    ["q: string"],
    "非必填不带标注"
  );
}

/* ── buildOverrides:高级区 → POST body overrides ───────────── */
{
  assert.deepEqual(
    buildOverrides({ model: " mock/k ", maxCost: "0.5", maxSteps: "10" }),
    { model: "mock/k", max_cost: 0.5, max_steps: 10 }
  );
  assert.deepEqual(buildOverrides({ model: "", maxCost: "", maxSteps: "" }), {},
    "全空 = 无 overrides(继承配置)");
  assert.deepEqual(buildOverrides({ maxCost: "abc", maxSteps: "1.9" }), { max_steps: 1 },
    "非数字丢弃;max_steps 取整");
  assert.deepEqual(buildOverrides(), {});
}

console.log("launch-dialog.test.mjs: all assertions passed");

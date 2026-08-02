/* schema-view.js 纯逻辑单测(docs/WEB-UI.md §4.6/§4.7 SchemaView):
   schemaToRows(扁平 / required 标记 / 约束灰注:minimum·minLength·enum 等 /
   嵌套对象缩进 / array<object> 展开)、typeLabel(array<items>)、
   schemaView HTML(required 加粗类 / 约束 span / esc)。
   运行:node static/tests/schema-view.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import { schemaToRows, schemaView, typeLabel } from "../js/components/schema-view.js";

/* fib 技能的 inputs schema(与 instance/skills.yaml 同形) */
const FIB_SCHEMA = {
  type: "object",
  properties: { n: { type: "integer", minimum: 1 } },
  required: ["n"],
};

/* ── 扁平字段 + required + 约束 ────────────────────────────── */
{
  assert.deepEqual(schemaToRows(FIB_SCHEMA), [
    {
      depth: 0,
      name: "n",
      type: "integer",
      required: true,
      constraints: ["minimum 1"],
      description: "",
    },
  ]);
}

/* ── 可选字段 + 多约束(minimum/maximum 同列,顺序稳定)────────── */
{
  const rows = schemaToRows({
    type: "object",
    properties: { limit: { type: "integer", minimum: 1, maximum: 100, default: 10 } },
  });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].required, false, "不在 required 列表 → 非必填");
  assert.deepEqual(rows[0].constraints, ["minimum 1", "maximum 100", "default 10"]);
}

/* ── 字符串/枚举约束 + description 进灰注 ───────────────────── */
{
  const rows = schemaToRows({
    type: "object",
    properties: {
      mode: {
        type: "string",
        minLength: 2,
        maxLength: 8,
        pattern: "^[a-z]+$",
        enum: ["fast", "slow"],
        description: "运行模式",
      },
    },
  });
  assert.deepEqual(rows[0].constraints, [
    "minLength 2",
    "maxLength 8",
    'pattern "^[a-z]+$"',
    'enum ["fast","slow"]',
  ]);
  assert.equal(rows[0].description, "运行模式");
}

/* ── 嵌套对象:depth 缩进 + 子层 required 独立判定 ────────────── */
{
  const rows = schemaToRows({
    type: "object",
    properties: {
      config: {
        type: "object",
        properties: {
          retries: { type: "integer" },
          note: { type: "string" },
        },
        required: ["retries"],
      },
    },
    required: ["config"],
  });
  assert.deepEqual(
    rows.map((r) => [r.depth, r.name, r.type, r.required]),
    [
      [0, "config", "object", true],
      [1, "retries", "integer", true],
      [1, "note", "string", false],
    ],
  );
}

/* ── array:类型标签 array<items>;array<object> 展开一层 ─────── */
{
  assert.equal(typeLabel({ type: "array", items: { type: "integer" } }), "array<integer>");
  assert.equal(typeLabel({ type: "array" }), "array<any>");
  assert.equal(typeLabel({}), "any");
  assert.equal(typeLabel(undefined), "any");

  const rows = schemaToRows({
    type: "object",
    properties: {
      seq: { type: "array", items: { type: "integer" } },
      points: {
        type: "array",
        items: {
          type: "object",
          properties: { x: { type: "number" } },
          required: ["x"],
        },
      },
    },
  });
  assert.deepEqual(
    rows.map((r) => [r.depth, r.name, r.type, r.required]),
    [
      [0, "seq", "array<integer>", false],
      [0, "points", "array<object>", false],
      [1, "x", "number", true],
    ],
  );
}

/* ── 边界:空 schema / 非对象 → 空行集 ───────────────────────── */
{
  assert.deepEqual(schemaToRows({}), []);
  assert.deepEqual(schemaToRows(null), []);
  assert.deepEqual(schemaToRows({ type: "object" }), []);
}

/* ── schemaView HTML:required 加粗类 / 必填注记 / 约束灰注 / esc ── */
{
  const html = schemaView(FIB_SCHEMA);
  assert.match(html, /sv-name is-required/, "required 字段名加粗类");
  assert.match(html, /sv-req">必填</, "必填注记");
  assert.match(html, /sv-type mono">integer</, "类型列");
  assert.match(html, /sv-constraints">minimum 1</, "约束灰注");
  assert.match(html, /--depth:0/, "缩进变量");

  const escHtml = schemaView({
    type: "object",
    properties: { "a<b": { type: "string", description: 'x"&y' } },
  });
  assert.ok(!escHtml.includes("a<b"), "字段名 esc");
  assert.match(escHtml, /a&lt;b/, "字段名 esc 实体");
  assert.ok(!escHtml.includes('x"&y'), "描述 esc");

  assert.match(schemaView({}), /sv-empty/, "空 schema 占位");
}

console.log("schema-view.test.mjs: all assertions passed");

/* SchemaView(docs/WEB-UI.md §3.3/§4.6/§4.7):JSON Schema → 可读字段表。
   字段名 / 类型 / required(加粗)/ 约束(minimum 等,灰注);嵌套对象按 depth 缩进。
   纯函数组件:schemaToRows 不碰 DOM(node 单测可载);schemaView 只拼 HTML 字符串,
   样式由 app.css 按 .schema-view 消费 token。

   纯函数:
     schemaToRows(schema)  schema → [{ depth, name, type, required, constraints, description }]
     typeLabel(sch)        单节点类型标签(array → array<items.type>) */

import { esc } from "../util.js";

/* 展示的约束键(出现即灰注列出;值 JSON 化,枚举/默认值同样可读) */
const CONSTRAINT_KEYS = [
  "minimum",
  "maximum",
  "minLength",
  "maxLength",
  "pattern",
  "minItems",
  "maxItems",
  "enum",
  "default",
];

export function typeLabel(sch) {
  const t = sch?.type ?? "any";
  if (t === "array") return `array<${sch?.items?.type ?? "any"}>`;
  return t;
}

function constraintsOf(sch) {
  const out = [];
  for (const key of CONSTRAINT_KEYS) {
    if (sch?.[key] !== undefined) out.push(`${key} ${JSON.stringify(sch[key])}`);
  }
  return out;
}

export function schemaToRows(schema) {
  const rows = [];
  const walk = (name, sch, depth, required) => {
    rows.push({
      depth,
      name,
      type: typeLabel(sch),
      required,
      constraints: constraintsOf(sch),
      description: typeof sch?.description === "string" ? sch.description : "",
    });
    // 嵌套:object 的属性、array<object> 的属性,均缩进一层展示
    const nested =
      sch?.type === "object"
        ? [sch, depth]
        : sch?.type === "array" && sch?.items?.type === "object"
          ? [sch.items, depth]
          : null;
    if (nested) {
      const [obj, d] = nested;
      const req = new Set(obj?.required ?? []);
      for (const [key, sub] of Object.entries(obj?.properties ?? {})) {
        walk(key, sub, d + 1, req.has(key));
      }
    }
  };
  const required = new Set(schema?.required ?? []);
  for (const [name, sub] of Object.entries(schema?.properties ?? {})) {
    walk(name, sub, 0, required.has(name));
  }
  return rows;
}

/* rows → 字段表 HTML(required 字段名加粗 + 必填注记;约束/描述灰注) */
export function schemaView(schema) {
  const rows = schemaToRows(schema);
  if (!rows.length) return `<div class="schema-view sv-empty">(无字段)</div>`;
  const body = rows
    .map((r) => {
      const bits = [
        ...r.constraints,
        ...(r.description ? [r.description] : []),
      ];
      return (
        `<div class="sv-row" style="--depth:${r.depth}">` +
        `<span class="sv-name${r.required ? " is-required" : ""}">${esc(r.name)}` +
        (r.required ? `<span class="sv-req">必填</span>` : "") +
        `</span>` +
        `<span class="sv-type mono">${esc(r.type)}</span>` +
        (bits.length ? `<span class="sv-constraints">${esc(bits.join(" · "))}</span>` : "") +
        `</div>`
      );
    })
    .join("");
  return `<div class="schema-view" role="table">${body}</div>`;
}

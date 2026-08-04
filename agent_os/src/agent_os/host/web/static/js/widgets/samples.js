/* 沙盒样例表(docs/WIDGET-ARCH.md 沙盒节;**沙盒专用——生产页面不许 import**)。

   每个控件 kind → { mount: index.js 的 mount 函数名, samples: [{name, options}] };
   options 直接喂对应 mount(per-kind options 形态,W5 新形态签名)。
   选样原则:空态/典型/边界,对照 §2 各控件硬规则(错误条/倒置纠序/抽稀/
   截断/折叠/剥壳/色条/骨架……每条至少一个样例能演出来)。 */

export const SAMPLES = {
  "text-editor": {
    mount: "mountTextEditor",
    samples: [
      { name: "plain 典型", options: { value: "第一行\n第二行", field: "prompt", label: "prompt", rows: 4 } },
      { name: "mono 行号槽", options: { value: "# 标题\n\n- 甲\n- 乙", field: "body", label: "body", mono: true, rows: 8 } },
      { name: "空态", options: { value: "", field: "empty", label: "empty" } },
      { name: "readonly 灰底", options: { value: "只读内容", field: "ro", label: "ro", readonly: true } },
    ],
  },
  "json-editor": {
    mount: "mountJsonEditor",
    samples: [
      { name: "合法(绿勾)", options: { value: '{\n  "a": 1\n}', field: "inputsText", label: "inputs" } },
      { name: "缺右括号(错误条)", options: { value: '{\n  "a": 1', field: "inputsText", label: "inputs" } },
      { name: "schema 不合(行级)", options: {
        value: '{\n  "n": "oops"\n}', field: "inputsText", label: "inputs",
        schema: { properties: { n: { type: "integer" } } } } },
      { name: "空态", options: { value: "", field: "inputsText", label: "inputs" } },
    ],
  },
  "table-editor": {
    mount: "mountTableEditor",
    samples: [
      { name: "四列型典型", options: {
        columns: [
          { key: "name", type: "text", label: "名", required: true },
          { key: "n", type: "number", label: "数" },
          { key: "ok", type: "boolean", label: "好" },
          { key: "kind", type: "enum", label: "类", options: ["a", "b"] },
        ],
        rows: [{ name: "甲", n: 1, ok: true }, { name: "乙", n: 2, kind: "b" }] } },
      { name: "空态", options: { columns: [{ key: "name", type: "text", label: "名" }], rows: [] } },
    ],
  },
  "kv-editor": {
    mount: "mountKvEditor",
    samples: [
      { name: "典型", options: { entries: [{ key: "host", value: "127.0.0.1" }, { key: "port", value: "8391" }] } },
      { name: "重复 key 警示", options: { entries: [{ key: "a", value: "1" }, { key: "a", value: "2" }] } },
      { name: "空态", options: { entries: [] } },
    ],
  },
  "schema-form": {
    mount: "mountFormEditor",
    samples: [
      { name: "全类型典型", options: { schema: {
        type: "object",
        required: ["city"],
        properties: {
          city: { type: "string" },
          n: { type: "integer", minimum: 1, maximum: 10 },
          ok: { type: "boolean" },
          kind: { type: "string", enum: ["a", "b"] },
          addr: { type: "object", properties: { zip: { type: "string" } } },
          tags: { type: "array", items: { type: "string" } },
        } } } },
      { name: "空 schema", options: { schema: {} } },
    ],
  },
  "select-list": {
    mount: "mountSelectList",
    samples: [
      { name: "单选典型", options: { items: [
        { id: "a", label: "Alpha", hint: "第一项" },
        { id: "b", label: "Beta", hint: "第二项" },
        { id: "g", label: "Gamma" }] } },
      { name: "多选", options: { multi: true, items: [
        { id: "a", label: "Alpha" }, { id: "b", label: "Beta" }, { id: "g", label: "Gamma" }] } },
      { name: "空态", options: { items: [] } },
    ],
  },
  "ns-tree": {
    mount: "mountNsTreeWidget",
    samples: [
      { name: "命名空间典型", options: { items: [
        { name: "weather.query" }, { name: "weather.forecast" }, { name: "ops.janitor" }] } },
      { name: "深层折叠(过滤演自动展开)", options: { items: [
        ...[...Array(8)].map((_, i) => ({ name: `top.a.x${i + 1}` })),
        { name: "top.b.y1" }] } },
      { name: "空态", options: { items: [] } },
    ],
  },
  "date-picker": {
    mount: "mountDatePicker",
    samples: [
      { name: "单日", options: { mode: "date", value: "2026-08-04" } },
      { name: "datetime", options: { mode: "datetime", value: "2026-08-04T10:30" } },
      { name: "range + 快捷项", options: { mode: "range", value: { start: "2026-08-01", end: "2026-08-04" } } },
    ],
  },
  chart: {
    mount: "mountChart",
    samples: [
      { name: "line 典型", options: { label: "费用", series: [
        { name: "cost", points: [{ x: 1, y: 2 }, { x: 2, y: 5 }, { x: 3, y: 3 }] }] } },
      { name: "bar 型", options: { label: "费用", type: "bar", series: [
        { name: "cost", points: [{ x: 1, y: 2 }, { x: 2, y: 5 }, { x: 3, y: 3 }] }] } },
      { name: "多序列(图例显隐)", options: { label: "用量", series: [
        { name: "cost", points: [{ x: 1, y: 2 }, { x: 2, y: 5 }] },
        { name: "tokens", points: [{ x: 1, y: 10 }, { x: 2, y: 8 }] }] } },
      { name: ">500 点抽稀", options: { label: "长序列", series: [
        { name: "s", points: [...Array(1200)].map((_, i) => ({ x: i, y: Math.sin(i / 50) * 50 + 50 })) }] } },
      { name: "空态", options: { label: "空", series: [] } },
    ],
  },
  "log-viewer": {
    mount: "mountLogViewer",
    samples: [
      { name: "kind 色条三类", options: { lines: [
        { kind: "run.start", text: "run 开始" },
        { kind: "warn", text: "告警行" },
        { kind: "run.error", text: "错误行" },
        { kind: "info", text: "普通行" }] } },
      { name: "截断保尾(maxLines=5)", options: { maxLines: 5, lines: [...Array(8)].map((_, i) => ({ kind: "info", text: `行${i + 1}` })) } },
      { name: "空态", options: { lines: [] } },
    ],
  },
  "diff-viewer": {
    mount: "mountDiffViewer",
    samples: [
      { name: "split 双列", options: { diff: { members: [{
        member: "lab.d", status: "changed",
        fields: [{ kind: "changed", path: "description", old: "旧", new: "新" }],
        prompt_diff: [
          { kind: "del", text: "旧句" }, { kind: "add", text: "新句" }] }] } } },
      { name: "unified 折叠上下文", options: { mode: "unified", diff: { members: [{
        member: "lab.d", status: "changed",
        fields: [],
        prompt_diff: [
          { kind: "del", text: "旧句" },
          { kind: "same", text: "同句1" },
          { kind: "same", text: "同句2" },
          { kind: "add", text: "新句" }] }] } } },
    ],
  },
  "md-viewer": {
    mount: "mountMarkdownViewer",
    samples: [
      { name: "典型(全结构)", options: { source:
        "# 标题\n\n- 甲\n- 乙\n\n```\nlet a = 1;\n```\n\n**粗** 和 `行内` 和 [链接](https://example.com)\n\n| a | b |\n|---|---|\n| 1 | 2 |" } },
      { name: "XSS 剥壳", options: { source: "<script>alert(1)</script>\n\n[点我](javascript:alert(1))" } },
      { name: "空态", options: { source: "" } },
    ],
  },
  "chat-bubble": {
    mount: "mountBubble",
    samples: [
      { name: "典型(带种子消息)", options: {
        anchor: { member: "lab.demo", path: "doc.md#L2-L2" },
        seedMessages: [{ role: "user", text: "这段太绕" }, { role: "assistant", text: "建议拆开" }] } },
      { name: "空(新批注)", options: { anchor: { member: "lab.demo", path: "doc.md#L5-L5" } } },
    ],
  },
};

/* 沙盒样例表(docs/WIDGET-ARCH.md 沙盒节;**沙盒专用——生产页面不许 import**)。

   每个控件 kind → { mount: index.js 的 mount 函数名, samples: [{name, options}] };
   options 直接喂对应 mount(per-kind options 形态,W5 新形态签名)。
   选样原则:空态/典型/边界,对照 §2 各控件硬规则(错误条/倒置纠序/抽稀/
   截断/折叠/剥壳/色条/骨架……每条至少一个样例能演出来)。 */

export const SAMPLES = {
  "text-editor": {
    mount: "mountTextEditor",
    samples: [
      { name: "plain 典型", options: { value: "第一行\n第二行", field: "prompt", label: "prompt", rows: 4, updated_at: Date.now() / 1000 - 180 } },
      { name: "mono 行号槽", options: { value: "# 标题\n\n- 甲\n- 乙", field: "body", label: "body", mono: true, rows: 8, lang: "markdown" } },
      { name: "空态", options: { value: "", field: "empty", label: "empty" } },
      { name: "readonly 灰底", options: { value: "只读内容", field: "ro", label: "ro", readonly: true } },
    ],
  },
  // widget-libs 试点 2:CM6 对照件——样例与 text-editor 同参数(并排对比用)
  "text-editor-cm": {
    mount: "mountTextEditorCm",
    samples: [
      { name: "plain 典型", options: { value: "第一行\n第二行", field: "prompt", label: "prompt", rows: 4, updated_at: Date.now() / 1000 - 180 } },
      { name: "mono 行号槽", options: { value: "# 标题\n\n- 甲\n- 乙", field: "body", label: "body", mono: true, rows: 8, lang: "markdown" } },
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
      { name: "四列型典型", options: { title: "出行任务表",
        columns: [
          { key: "name", type: "text", label: "名", required: true },
          { key: "n", type: "number", label: "数" },
          { key: "ok", type: "boolean", label: "好" },
          { key: "kind", type: "enum", label: "类", options: ["a", "b"] },
        ],
        rows: [{ name: "甲", n: 1, ok: true }, { name: "乙", n: 2, kind: "b" }] } },
      { name: "空态(骨架+主操作)", options: { columns: [{ key: "name", type: "text", label: "名" }], rows: [] } },
    ],
  },
  "kv-editor": {
    mount: "mountKvEditor",
    samples: [
      { name: "典型", options: { title: "headers", entries: [{ key: "host", value: "127.0.0.1" }, { key: "port", value: "8391" }] } },
      { name: "重复 key 警示", options: { title: "headers", entries: [{ key: "a", value: "1" }, { key: "a", value: "2" }] } },
      { name: "空态", options: { entries: [] } },
    ],
  },
  "schema-form": {
    mount: "mountFormEditor",
    samples: [
      { name: "全类型典型", options: { title: "告警规则", schema: {
        type: "object",
        required: ["city"],
        properties: {
          city: { type: "string", description: "显示在告警列表与通知标题" },
          n: { type: "integer", minimum: 1, maximum: 10 },
          ok: { type: "boolean" },
          kind: { type: "string", enum: ["a", "b"] },
          addr: { type: "object", description: "alertmanager 接收方", properties: { zip: { type: "string" } } },
          tags: { type: "array", items: { type: "string" } },
        } } } },
      { name: "空 schema", options: { schema: {} } },
    ],
  },
  "select-list": {
    mount: "mountSelectList",
    samples: [
      { name: "单选典型", options: { title: "mcp.tools", items: [
        { id: "a", label: "net.http_fetch", hint: "v2.1 · 工具" },
        { id: "b", label: "net.http_server", hint: "v1.4 · 工具" },
        { id: "g", label: "data.http_export" }] } },
      { name: "多选", options: { multi: true, title: "mcp.tools · 多选", items: [
        { id: "a", label: "Alpha" }, { id: "b", label: "Beta" }, { id: "g", label: "Gamma" }] } },
      { name: "空态", options: { items: [] } },
    ],
  },
  "ns-tree": {
    mount: "mountNsTreeWidget",
    samples: [
      { name: "命名空间典型", options: { title: "skills", items: [
        { name: "weather.query" }, { name: "weather.forecast" }, { name: "ops.janitor" }] } },
      { name: "深层折叠(过滤演调光)", options: { title: "skills", items: [
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
      { name: "range 双月 + 快捷项", options: { title: "travel.dates", mode: "range", value: { start: "2026-08-01", end: "2026-08-04" } } },
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
      { name: "级别 chips + 时间戳", options: { title: "run · stdout", lines: [
        { kind: "pre", text: 'tool.call weather.query({"city":"昆明"})', ts: 1785800000 },
        { kind: "post", text: "weather.query → 200 OK · 84ms", ts: 1785800001 },
        { kind: "err", text: "http_fetch timeout after 3000ms", ts: 1785800002 },
        { kind: "info", text: "普通行", ts: 1785800003 }] } },
      { name: "截断保尾(maxLines=5)", options: { maxLines: 5, lines: [...Array(8)].map((_, i) => ({ kind: "info", text: `行${i + 1}` })) } },
      { name: "空态", options: { lines: [] } },
    ],
  },
  "diff-viewer": {
    mount: "mountDiffViewer",
    samples: [
      { name: "split 双列", options: { title: "prompt diff", diff: { members: [{
        member: "lab.d", status: "changed", tier: "escalate",
        fields: [{ kind: "changed", path: "description", old: "旧", new: "新" }],
        prompt_diff: [
          { kind: "del", text: "旧句" }, { kind: "add", text: "新句" }] }] } } },
      { name: "unified 折叠上下文", options: { mode: "unified", title: "prompt diff", diff: { members: [{
        member: "lab.d", status: "changed", tier: "reversible",
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
      { name: "典型(全结构)", options: { title: "trip_report.md", source:
        "# 标题\n\n- 甲\n- 乙\n\n> 引用一句。\n\n```\nlet a = 1;\n```\n\n**粗** 和 `行内` 和 [链接](https://example.com)\n\n| a | b |\n|---|---|\n| 1 | 2 |" } },
      { name: "XSS 剥壳", options: { source: "<script>alert(1)</script>\n\n[点我](javascript:alert(1))" } },
      { name: "空态", options: { source: "" } },
    ],
  },
  // widget-libs 试点 3:markdown-it 对照件——样例与 md-viewer 同参数(并排对比)
  "md-viewer-mi": {
    mount: "mountMarkdownViewerMi",
    samples: [
      { name: "典型(全结构)", options: { title: "trip_report.md", source:
        "# 标题\n\n- 甲\n- 乙\n\n> 引用一句。\n\n```\nlet a = 1;\n```\n\n**粗** 和 `行内` 和 [链接](https://example.com)\n\n| a | b |\n|---|---|\n| 1 | 2 |" } },
      { name: "XSS 剥壳", options: { source: "<script>alert(1)</script>\n\n[点我](javascript:alert(1))" } },
      { name: "空态", options: { source: "" } },
    ],
  },
  "chat-bubble": {
    mount: "mountBubble",
    samples: [
      { name: "典型(展示态 · 待处理)", options: {
        anchor: { member: "lab.demo", path: "plan.md#L7-L7" },
        quote: "第三天行程:上午故宫,下午颐和园,预算 ¥720。", content: "这段太绕,拆开说", status: "pending" } },
      { name: "已应用(带 AI 处理注)", options: {
        anchor: { member: "lab.demo", path: "plan.md#L7-L7" },
        quote: "第三天行程:上午故宫,下午颐和园,预算 ¥720。", content: "预算偏高", status: "applied",
        generation: { result: "applied", aiNote: "已把预算压到 ¥600" } } },
      { name: "空(新批注 · 输入态)", options: { anchor: { member: "lab.demo", path: "plan.md#L12-L12" } } },
    ],
  },
};

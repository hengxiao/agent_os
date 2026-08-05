/* Widget 库 W1 测试(docs/WIDGETS.md §5 五层):
   协议(注册校验/actions 仅 local 拒/events 未声明不发/widgets 目录零 fetch 静态扫描);
   渲染(微标/错误条/format 钮/禁用与 aria);
   交互(W-text 输入→dirty→commit→事件、revert 选区保留;
        W-json 即时校验/行级定位/失焦校验/format 幂等/schema 不合);
   边界(超长文本、非法 JSON、空值);
   主题(copy 六主题 key 在位)。
   运行:node static/tests/widgets.test.mjs */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url); // "/static/js/" → 共享模块(W4 起 cards.js 也入测)

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const { copy } = await import("../js/themes.js");
const {
  registerWidgetDef, getWidgetDef, listWidgetKinds, createWidget,
  mountTextEditor, mountJsonEditor, locateJsonError, jsonErrorAt, schemaErrorAt, formatJson,
  renderTextEditor, renderJsonEditor,
} = await import("../js/widgets/index.js");

/* ── 协议层 ───────────────────────────────────────────────────── */

{
  // 合法定义注册 + 查询
  const def = registerWidgetDef({
    kind: "t-probe", v: 1, state_schema: { type: "object" },
    actions: [{ id: "a", exec: "local" }], events: ["change"],
    aria: { role: "textbox" }, surfaces: ["tab"],
  });
  assert.equal(getWidgetDef("t-probe"), def);
  assert.ok(listWidgetKinds().includes("text-editor"), "W-text 已注册");
}

{
  // actions 只允许 local:endpoint/run 出现即拒(铁律 1)
  for (const bad of ["endpoint", "run"]) {
    assert.throws(
      () => registerWidgetDef({
        kind: "t-bad", v: 1, state_schema: { type: "object" },
        actions: [{ id: "a", exec: bad }], events: [], aria: { role: "x" }, surfaces: ["tab"],
      }),
      /必须是 local/,
      `exec=${bad} 拒注册`,
    );
  }
  // 结构不齐拒(缺 events 清单/aria.role/state_schema)
  assert.throws(() => registerWidgetDef({ kind: "t-b2", v: 1, state_schema: { type: "object" },
    actions: [], events: "change", aria: { role: "x" }, surfaces: ["tab"] }), /events/);
  assert.throws(() => registerWidgetDef({ kind: "t-b3", v: 1, state_schema: { type: "object" },
    actions: [], events: [], aria: {}, surfaces: ["tab"] }), /aria/);
  assert.throws(() => registerWidgetDef({ kind: "t-b4", v: 1, state_schema: null,
    actions: [], events: [], aria: { role: "x" }, surfaces: ["tab"] }), /state_schema/);
  // §17.7-3:context_provider 必有——缺省兜底(kind + state 摘要,可序列化);
  // 声明者可覆盖;非函数拒注册
  const d1 = registerWidgetDef({ kind: "t-ctx", v: 1, state_schema: { type: "object" },
    actions: [], events: [], aria: { role: "x" }, surfaces: ["tab"] });
  assert.equal(typeof d1.context_provider, "function", "未声明也有缺省 provider");
  const frag = d1.context_provider({ a: 1 });
  assert.equal(frag.kind, "t-ctx", "缺省 fragment 带 kind");
  assert.ok(frag.state_summary.includes("a"), "缺省 fragment 带 state 摘要");
  assert.deepEqual(JSON.parse(JSON.stringify(frag)), frag, "缺省 fragment 可 JSON 序列化");
  const d2 = registerWidgetDef({ kind: "t-ctx2", v: 1, state_schema: { type: "object" },
    actions: [], events: [], aria: { role: "x" }, surfaces: ["tab"],
    context_provider: (s) => ({ mine: s.v }) });
  assert.deepEqual(d2.context_provider({ v: 7 }), { mine: 7 }, "声明者覆盖缺省");
  assert.throws(() => registerWidgetDef({ kind: "t-ctx3", v: 1, state_schema: { type: "object" },
    actions: [], events: [], aria: { role: "x" }, surfaces: ["tab"], context_provider: "x" }),
    /context_provider/, "非函数 provider 拒注册(协议不合规)");
}

{
  // 实例:事件上行(未声明事件不发);destroy 注销回调;state 可序列化
  const def = getWidgetDef("t-probe");
  const seen = [];
  let unregistered = "";
  const w = createWidget(def, { path: "/t/p", onUnregister: (p) => { unregistered = p; } });
  w.on("change", (p) => seen.push(p));
  w.emit("change", { v: 1 });
  w.emit("evil", { v: 2 }); // 未声明
  assert.deepEqual(seen, [{ v: 1 }], "声明事件上行,未声明不发");
  assert.deepEqual(JSON.parse(JSON.stringify(w.state)), w.state, "state 可序列化");
  w.destroy();
  assert.equal(unregistered, "/t/p", "卸载即注销(经回调,widget 不碰注册端点)");
}

{
  // 静态扫描:widgets/ 目录零 fetch((代码评审纪律进测试;剥注释防自述误伤)
  const dir = join(import.meta.dirname, "../js/widgets");
  const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".js"))) {
    const src = stripComments(readFileSync(join(dir, f), "utf-8"));
    assert.ok(!src.includes("fetch("), `${f} 出现 fetch((widget 永不直接调后端)`);
  }
}

/* ── W-text(W5.1 新形态:自渲染;render 纯函数在 w-text.render.js)───── */

/* 新形态宿主 = 空挂点(控件自己产出全部元素,宿主不预置) */
function _textHost(doc, { field = "prompt", variant = "plain", rows = 4 } = {}) {
  const host = doc.createElement("div");
  host.dataset.widget = "text-editor";
  host.dataset.field = field;
  host.dataset.variant = variant;
  host.dataset.rows = String(rows);
  doc.body.appendChild(host);
  return host;
}

{
  // render 纯函数(W5.1 验收):同 state 同 html;不写 state;语义类齐
  const s1 = { value: "a\nb", dirty: true, mono: true, rows: 4, field: "prompt", label: "prompt" };
  const h1 = renderTextEditor(s1);
  assert.equal(h1, renderTextEditor(s1), "同 state 同 html(纯函数)");
  assert.deepEqual(s1, { value: "a\nb", dirty: true, mono: true, rows: 4, field: "prompt", label: "prompt" },
    "render 不改 state(无副作用)");
  assert.ok(h1.includes("is-dirty"), "dirty 左边条类");
  assert.ok(h1.includes("wd-gutter"), "mono 行号槽");
  assert.ok(h1.includes('aria-label="prompt"'), "aria 自渲染进 textarea");
  assert.ok(!renderTextEditor({ value: "x", mono: false, field: "d", label: "d" }).includes("wd-gutter"),
    "plain 无行号槽");
  assert.ok(renderTextEditor({ value: "x", readonly: true, field: "d", label: "d" }).includes("is-readonly"),
    "readonly 灰底类");
  assert.ok(renderTextEditor({ value: "<script>", field: "d", label: "d" }).includes("&lt;script&gt;"),
    "值转义(XSS 不注入)");
}

{
  const doc = makeDocument();
  globalThis.document = doc;
  const host = _textHost(doc);
  const events = [];
  const w = mountTextEditor(host, { value: "hello\nworld", path: "/t/text" });
  w.on("change", (p) => events.push(p));
  const ta = host.querySelector("textarea");
  assert.ok(ta, "控件自己产出 textarea(宿主不预置)");
  assert.ok(host.innerHTML.includes('aria-label="prompt"'), "aria-label 从 data-field 推导(渲染进串)");
  const micro = () => host.querySelector(".wd-micro")?.textContent ?? "";
  assert.ok(micro().includes("2 行"), "微标行数");
  assert.ok(micro().includes("11 字"), "微标字数");
  // 输入 → dirty + change + 微标局部刷新(不重渲:元素同一)
  ta.value = "hello\nworld\n!";
  host.trigger("input", { target: ta });
  assert.ok(w.state.dirty, "输入置 dirty");
  assert.equal(events.at(-1).dirty, true, "change 事件携带 dirty");
  assert.ok(micro().includes("3 行"), "微标随输入刷新");
  assert.equal(host.querySelector("textarea"), ta, "输入不重渲(元素同一)");
  // commit → dirty 清零 + commit 事件
  let committed = "";
  w.on("commit", (p) => { committed = p.value; });
  w.commit();
  assert.equal(committed, "hello\nworld\n!", "commit 事件值");
  assert.ok(!w.state.dirty, "commit 清 dirty");
  // revert:全量重渲 + **选区/焦点保留**(W5.1 必答题)
  ta.value = "被改掉的";
  host.trigger("input", { target: ta });
  ta.focus();
  ta.selectionStart = 2;
  ta.selectionEnd = 5;
  w.revert();
  const ta2 = host.querySelector("textarea");
  assert.ok(ta2 !== ta, "重渲换元素(自渲染全量重渲)");
  assert.equal(ta2.value, committed, "revert 回 baseline(元素值随 render 同步)");
  assert.equal(ta2.selectionStart, 2, "选区保留(start)");
  assert.equal(ta2.selectionEnd, 5, "选区保留(end)");
  assert.equal(doc.activeElement, ta2, "焦点保留(activeElement 恢复)");
  // Esc=blur
  ta2.blur = () => { ta2.focused = false; doc.activeElement = null; };
  host.trigger("keydown", { target: ta2, key: "Escape" });
  assert.ok(!ta2.focused, "Esc=blur");
  w.destroy();
  assert.equal(host.querySelector(".wd-micro"), null, "destroy 清空自渲染元素");
  assert.equal(host.querySelector("textarea"), null, "destroy 拆 textarea");
}

{
  // 边界:超长文本不炸;aria-label 无来源 → 拒装
  const doc = makeDocument();
  globalThis.document = doc;
  const big = _textHost(doc);
  assert.doesNotThrow(() => mountTextEditor(big, { value: "x".repeat(120000) }), "超长文本不炸");
  const bare = doc.createElement("div");
  doc.body.appendChild(bare);
  assert.throws(() => mountTextEditor(bare), /aria-label/, "aria-label 必填(无 field/label 拒装)");
}

/* ── W-json ───────────────────────────────────────────────────── */

function _jsonHost(doc, { field = "inputsText" } = {}) {
  const host = doc.createElement("div");
  host.dataset.widget = "json-editor";
  host.dataset.field = field;
  doc.body.appendChild(host);
  return host;
}

{
  // 定位纯函数:V8 position → 行号;SpiderMonkey 行号直读
  assert.equal(locateJsonError("{\n\"a\": bad\n}", "Unexpected token b in JSON at position 8").line, 2);
  assert.equal(locateJsonError("{\n}", "Unexpected end of JSON input").line >= 1, true);
  assert.equal(locateJsonError("{}", "Bad control character in string literal at line 3 column 5").line, 3);
  assert.equal(jsonErrorAt('{"a": 1}'), null, "合法 JSON 无错误");
  assert.equal(jsonErrorAt(""), null, "空值放行(必填由 schema 管)");
  // format 幂等
  const once = formatJson('{"b":2,"a":1}');
  assert.equal(formatJson(once), once, "format 幂等");
  // schema:required 缺失 + type 不合(行级)
  const sch = { required: ["city"], properties: { n: { type: "integer" } } };
  const miss = schemaErrorAt('{\n  "x": 1\n}', sch);
  assert.ok(miss && miss.message.includes("city"), "required 缺失提示");
  const badType = schemaErrorAt('{\n  "n": "oops"\n}', { properties: { n: { type: "integer" } } });
  assert.ok(badType && badType.line === 2, "type 不合行级定位");
  assert.equal(schemaErrorAt('{"city": "bj", "n": 1}', sch), null, "合 schema 放行");
}

{
  // render 纯函数(W5.1):同 state 同 html;format 钮/错误条/绿勾语义位
  const sj = { value: '{"a": 1}', mono: true, rows: 6, field: "inputsText", label: "inputsText", error: null };
  const hj = renderJsonEditor(sj);
  assert.equal(hj, renderJsonEditor(sj), "json render 纯(同 state 同 html)");
  assert.equal(sj.error, null, "render 不改 state");
  assert.ok(hj.includes("wd-format"), "format 钮在");
  assert.ok(hj.includes('data-wd-errbar="1" hidden'), "合法时错误条隐");
  assert.ok(hj.includes("wd-json-ok") && !hj.includes('wd-json-ok" hidden'), "合法 ✓ 徽标显(W6.1 胶囊)");
  const hjErr = renderJsonEditor({ ...sj, error: { line: 2, message: "bad token" } });
  assert.ok(hjErr.includes("bad token") && hjErr.includes('data-wd-errbar="1">'), "有错误 → 错误条显");
  assert.ok(hjErr.includes('wd-json-ok" hidden'), "有错误 → ✓ 徽标隐");
  assert.ok(hjErr.includes("wd-err-hint") && hjErr.includes("点击跳转"), "错误条带跳转提示(§3.2)");
  assert.ok(hjErr.includes('wd-format" data-wd-format="1" disabled'), "非法 → format 禁用(§3.2)");
}

{
  const doc = makeDocument();
  globalThis.document = doc;
  const host = _jsonHost(doc);
  const w = mountJsonEditor(host, { value: '{\n  "a": 1\n}', path: "/t/json" });
  const ta = host.querySelector("textarea");
  const bar = () => host.querySelector(".wd-errbar");
  assert.ok(ta, "自渲染产出 textarea");
  assert.equal(w.kind, "json-editor", "kind 归位");
  assert.ok(bar().hidden, "合法初值无错误条");
  // §3.2:**失焦才校验**——输入中不闪红(着色层照常局部刷新)
  ta.value = '{\n  "a": bad\n}';
  host.trigger("input", { target: ta });
  assert.equal(w.state.error, null, "输入中不校验(不闪红,§3.2)");
  assert.ok(bar().hidden, "输入中错误条不出");
  host.trigger("focusout", { target: ta });
  assert.ok(w.state.error, "失焦才校验(§3.2)");
  assert.ok(!bar().hidden && bar().textContent.includes("第 2 行"), "行级错误定位上错误条");
  assert.ok(host.querySelector(".wd-format").disabled, "非法 → format 禁用(§3.2)");
  // 修复:输入中旧错误不刷新,失焦复验恢复
  ta.value = '{\n  "a": 1\n}';
  host.trigger("input", { target: ta });
  assert.ok(w.state.error, "输入中旧错误不清(等失焦)");
  host.trigger("focusout", { target: ta });
  assert.equal(w.state.error, null, "失焦复验:错误恢复");
  assert.ok(bar().hidden, "错误条收起");
  assert.ok(!host.querySelector(".wd-format").disabled, "恢复合法 → format 解禁");
  // 错误条点击 → 跳到错误行(§2.2;光标 = 行首偏移)
  ta.value = "{bad";
  host.trigger("focusout", { target: ta });
  assert.ok(w.state.error, "失焦校验");
  const barEl = bar();
  barEl.closest = (sel) => (sel === "[data-wd-errbar]" ? barEl : null); // dom-stub:region 无 dataset
  host.trigger("click", { target: barEl });
  assert.equal(ta.selectionStart, 0, "点击错误条跳到错误行行首");
  // format:不合法不美化;合法 → 美化(重渲 + 选区保留,新元素值同步)
  const fmtBtn = host.querySelector(".wd-format");
  fmtBtn.closest = (sel) => (sel === "[data-wd-format]" ? fmtBtn : null); // dom-stub 同上
  host.trigger("click", { target: fmtBtn });
  assert.equal(host.querySelector("textarea").value, "{bad", "不合法不美化");
  const ta3 = host.querySelector("textarea");
  ta3.value = '{"b":2,"a":1}';
  host.trigger("input", { target: ta3 }); // state 同步
  host.trigger("click", { target: fmtBtn });
  assert.equal(host.querySelector("textarea").value, '{\n  "b": 2,\n  "a": 1\n}',
    "format 一键美化(重渲后新元素值随 render 同步)");
  // schema validate action
  host.querySelector("textarea").value = '{"city": 1}';
  assert.equal(w.validate({ properties: { city: { type: "string" } } }), false, "schema 不合");
  assert.ok(w.state.error.message.includes("city"), "字段级提示");
  host.querySelector("textarea").value = '{"city": "bj"}';
  assert.equal(w.validate(), true, "schema 校验通过");
}

{
  // 主题:copy key 在位(六主题 parity 由 themes-contract 扫描;此处钉 key 存在)
  for (const key of ["w.text.count", "w.json.format", "w.json.errline"]) {
    assert.notEqual(copy(key), key, `${key} 有文案`);
  }
}

console.log("widgets.test.mjs: all assertions passed");

/* ── W2:cascade(§16)/ W-table / W-kv / W-bubble ───────────────── */

const { contextCascade, registerContextProvider, mountTableEditor, mountKvEditor,
  entriesToObject, objectToEntries, dupKeys, mountBubble,
  renderTableEditor, renderKvEditor } =
  await import("../js/widgets/index.js");

{
  // cascade:级联顺序(近→远)、每级只出自己 fragment、级数裁剪、
  // 缺 provider 跳过不炸、信封与 §14 路径一致
  const providers = [
    { prefix: "/shell/tab/app-1/surface/tab/field/prompt", scope: "widget",
      fn: () => ({ span: [1, 2], full_text: "全文" }) },
    { prefix: "/shell/tab/app-1", scope: "app", fn: () => ({ kind: "lab-draft", ref: "d" }) },
    { prefix: "/shell", scope: "shell", fn: () => ({ theme: "classic" }) },
    { prefix: "/elsewhere", scope: "app", fn: () => ({ evil: true }) }, // 横向:非祖先
  ];
  const env = contextCascade("/shell/tab/app-1/surface/tab/field/prompt", { providers });
  assert.equal(env.trigger, "/shell/tab/app-1/surface/tab/field/prompt", "信封 trigger = §14 路径");
  assert.deepEqual(env.cascade.map((f) => f.scope), ["widget", "app", "shell"], "近→远");
  assert.equal(env.cascade[0].path, "/shell/tab/app-1/surface/tab/field/prompt", "widget 级路径");
  assert.equal(env.cascade[1].path, "/shell/tab/app-1", "app fragment 在 app 级");
  assert.ok(!env.cascade.some((f) => f.data.evil), "横向不打听(无兄弟/旁支)");
  assert.deepEqual(
    contextCascade("/shell/tab/app-1/surface/tab/field/prompt", { providers, levels: ["widget"] })
      .cascade.map((f) => f.scope),
    ["widget"],
    "级数裁剪(轻动作不背大信封)",
  );
  const broken = [{ prefix: "/shell/tab/app-1", scope: "app", fn: () => { throw new Error("x"); } }];
  assert.doesNotThrow(() => contextCascade("/shell/tab/app-1/x", { providers: broken }), "provider 异常不炸");
  assert.equal(contextCascade("/shell/tab/app-1/x", { providers: broken }).cascade.length, 0, "异常级缺席");
  // 全局注册表:注册/注销
  const unreg = registerContextProvider("/t", "app", () => ({ a: 1 }));
  assert.equal(contextCascade("/t/x").cascade.length, 1, "全局注册生效");
  unreg();
  assert.equal(contextCascade("/t/x").cascade.length, 0, "注销即止");
  // §17.7-3:同位替换(重挂载不堆叠过期闭包)+ widget 实例 register/destroy 挂接
  registerContextProvider("/r", "app", () => ({ v: 1 }));
  registerContextProvider("/r", "app", () => ({ v: 2 }));
  const envR = contextCascade("/r/x");
  assert.equal(envR.cascade.length, 1, "同 prefix+scope 替换不堆叠");
  assert.equal(envR.cascade[0].data.v, 2, "最新注册赢");
  const wctx = createWidget(getWidgetDef("t-ctx"), { path: "/t/ctx", state: { a: 1 } });
  wctx.register();
  const envW = contextCascade("/t/ctx");
  assert.equal(envW.cascade[0].scope, "widget", "register 后 widget fragment 进级联");
  assert.equal(envW.cascade[0].data.kind, "t-ctx", "缺省 provider 供 fragment");
  wctx.destroy();
  assert.equal(contextCascade("/t/ctx").cascade.length, 0, "destroy 注销(注销随 destroy)");
  // §17.8 静态扫描:registerContextProvider 调用点 > 0(注册面不再是死代码)
  const { readFileSync } = await import("node:fs");
  let callsites = 0;
  for (const rel of ["../js/widgets/widget.js", "../js/components/lab-iterate.js",
    "../../../web_platform/static/doc-editor.js"]) {
    const src = readFileSync(new URL(rel, import.meta.url), "utf8");
    callsites += (src.match(/registerContextProvider\(/g) || []).length;
  }
  assert.ok(callsites > 0, "registerContextProvider 有真实调用点(>0)");
}

{
  // W5.2:四控件 render 纯函数(同 state 同 html、不改 state、XSS 转义、§2.3-2.6 语义类)
  const st = { rows: [{ id: "r1", cells: { name: "甲<script>", n: 1, ok: true, kind: "a" } }], selected: ["r1"],
    schema: { columns: [
      { key: "name", type: "text", label: "名", required: true },
      { key: "n", type: "number", label: "数" },
      { key: "ok", type: "boolean", label: "好" },
      { key: "kind", type: "enum", label: "类", options: ["a", "b"] },
    ] } };
  const ht = renderTableEditor(st);
  assert.equal(ht, renderTableEditor(st), "table render 纯(同 state 同 html)");
  assert.deepEqual(st.selected, ["r1"], "render 不改 state");
  assert.ok(ht.includes("⠿"), "行首拖柄(§2.3)");
  assert.ok(ht.includes("wd-th-ico"), "列头类型语义图标(§3.3)");
  assert.ok(ht.includes("甲&lt;script&gt;"), "单元格值转义(XSS 不注入)");
  assert.ok(ht.includes('data-selected="1"'), "选中行标记");
  assert.ok(renderTableEditor({ rows: [], selected: [], schema: { columns: [{ key: "name", type: "text", label: "名" }] } })
    .includes("wd-skeleton"), "空态:表头 + 骨架行(§3.3)");

  const sk = { entries: [{ key: "a", value: "1" }, { key: "a", value: "2" }] };
  const hk = renderKvEditor(sk);
  assert.equal(hk, renderKvEditor(sk), "kv render 纯");
  assert.ok(hk.includes("wd-kv-warn"), "重复 key 行警示类(§2.4)");
  assert.equal(sk.entries.length, 2, "render 不改 entries");
  // (W-form/W-list 的 render 纯函数断言在下方 W3 区——import 分批,避免 TDZ)
}

{
  // W-table:列型渲染/增删移/键盘移行/DnD envelope/空态/change 上行
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const columns = [
    { key: "name", type: "text", label: "名", required: true },
    { key: "n", type: "number", label: "数" },
    { key: "ok", type: "boolean", label: "好" },
    { key: "kind", type: "enum", label: "类", options: ["a", "b"] },
  ];
  const changes = [];
  const w = mountTableEditor(host, { columns, rows: [{ name: "甲", n: 1 }], path: "/t/table" });
  w.on("change", (p) => changes.push(p.rows.length));
  const html = () => host.innerHTML;
  assert.ok(html().includes("wd-req"), "required 星标");
  assert.ok(html().includes('role="grid"'), "role=grid");
  assert.ok(html().includes('type="checkbox"'), "boolean 列编辑器(始终交互)");
  assert.ok(html().includes("wd-chip"), "enum 展示态 = chip(§3.3)");
  // 单元格点击进内联编辑(§3.3):enum 出 select,text 出 32px input
  const cellEl = new StubEl("span");
  cellEl.dataset.cell = `${w.state.rows[0].id}:kind`;
  cellEl.closest = (sel) => (sel === "[data-cell]" ? cellEl : null);
  host.trigger("click", { target: cellEl });
  assert.ok(html().includes("<select"), "enum 单元格点击出 select 编辑器(§3.3)");
  const cellName = new StubEl("span");
  cellName.dataset.cell = `${w.state.rows[0].id}:name`;
  cellName.closest = (sel) => (sel === "[data-cell]" ? cellName : null);
  host.trigger("click", { target: cellName });
  assert.ok(html().includes("wd-cell-in"), "text 单元格点击出内联 input(§3.3)");
  // 增行(骨架按列型)
  w.add_row();
  assert.equal(w.state.rows.length, 2);
  assert.equal(w.state.rows[1].cells.n, 0, "新增行骨架(number=0)");
  assert.equal(w.state.rows[1].cells.kind, "a", "enum 骨架取首项");
  // 删行
  w.remove_row(w.state.rows[0].id);
  assert.equal(w.state.rows.length, 1);
  // 移行(move_row before)
  w.add_row({ name: "乙" });
  w.add_row({ name: "丙" });
  const ids = () => w.state.rows.map((r) => r.cells.name);
  w.move_row(w.state.rows[2].id, w.state.rows[0].id);
  assert.deepEqual(ids(), ["丙", "", "乙"], "move_row 重排(before 插入)");
  assert.ok(changes.length >= 3, "change 事件上行");
  // Alt+↑ 键盘移行
  const rowEl = new StubEl("tr");
  rowEl.dataset.row = w.state.rows[1].id;
  rowEl.closest = (sel) => (sel === "[data-row]" ? rowEl : null);
  const before = ids();
  host.trigger("keydown", { target: rowEl, key: "ArrowUp", altKey: true });
  assert.notDeepEqual(ids(), before, "Alt+↑ 移行");
  // DnD envelope(§15 合规)
  let setDataArgs = null;
  const rowEl2 = new StubEl("tr");
  rowEl2.dataset.row = w.state.rows[0].id;
  rowEl2.closest = (sel) => (sel === "[data-row]" ? rowEl2 : null);
  const dt = { types: ["application/x-agent-os-widget"], setData: (m, v) => { setDataArgs = [m, v]; }, getData: () => "" };
  host.trigger("dragstart", { target: rowEl2, dataTransfer: dt });
  const env = JSON.parse(setDataArgs[1]);
  assert.equal(env.source_kind, "table-row", "envelope source_kind");
  assert.ok(env.source.startsWith("/t/table/row/"), "envelope source = §14 路径");
  assert.ok("position" in env, "position 键在(强制最小集)");
  // drop 重排(落到空区 = 移到末尾;accept 校验:非 table-row 源不动)
  const sourceName = w.state.rows[0].cells.name;
  const dtDrop = { types: ["application/x-agent-os-widget"], getData: () => JSON.stringify({ source: env.source, source_kind: "table-row", position: {} }) };
  host.trigger("drop", { target: host, dataTransfer: dtDrop, preventDefault: () => {} });
  assert.equal(ids().at(-1), sourceName, "落到空区 = 移到末尾(before 缺省)");
  const order1 = ids();
  const dtBad = { types: ["application/x-agent-os-widget"], getData: () => JSON.stringify({ source: "/x", source_kind: "evil" }) };
  host.trigger("drop", { target: host, dataTransfer: dtBad, preventDefault: () => {} });
  assert.deepEqual(ids(), order1, "未知 kind 源被拒(不静默)");
  // 空态(§3.3:表头 + 骨架行 + 「添加第一行」主操作)
  const host2 = doc.createElement("div");
  doc.body.appendChild(host2);
  mountTableEditor(host2, { columns, rows: [] });
  assert.ok(host2.innerHTML.includes("添加第一行"), "空态主操作");
  assert.ok(host2.innerHTML.includes("wd-skeleton"), "空态骨架行");
}

{
  // W-kv:重复 key 警示(非硬拦)/序列化往返
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountKvEditor(host, { entries: [{ key: "a", value: "1" }, { key: "a", value: "2" }] });
  assert.deepEqual(dupKeys(w.state.entries), ["a"], "重复 key 检出");
  assert.ok(host.innerHTML.includes("重复") || host.innerHTML.includes("重"), "警示上屏(警告态)");
  w.add({ key: "b", value: "3" });
  assert.deepEqual(w.serialize(), { a: "2", b: "3" }, "序列化(后者覆盖,与 JSON 语义一致)");
  assert.deepEqual(entriesToObject(objectToEntries({ x: "1", y: "2" })), { x: "1", y: "2" }, "往返");
}

{
  // W-bubble:开气泡/种子消息(既有边注兼容)/submit 带 cascade 三级/
  // 回复渲染/apply 只发事件/多条并存/Esc
  const doc = makeDocument();
  globalThis.document = doc;
  const anchor = { member: "lab.d", kind: "span", path: "/lab/iterate/member/lab.d/span/prompt/span/0", span: { start: 0 } };
  const providers = [
    { prefix: "/lab/iterate/member/lab.d", scope: "widget", fn: () => ({ span: { start: 0 }, paragraph: "段", full_text: "全" }) },
    { prefix: "/lab/iterate", scope: "app", fn: () => ({ draft: "lab.d", tier: "none" }) },
  ];
  const submissions = [];
  const applies = [];
  const mk = (a) => {
    const host = doc.createElement("div");
    doc.body.appendChild(host);
    const b = mountBubble(host, {
      anchor: a, seedMessages: [{ role: "user", text: "旧边注" }], cascadeProviders: providers,
    });
    b.on("submit", (p) => submissions.push(p));
    b.on("apply", (p) => applies.push(p));
    return { host, b };
  };
  const { host, b } = mk(anchor);
  assert.ok(host.innerHTML.includes("旧边注"), "既有边注作种子消息(数据兼容)");
  assert.ok(host.innerHTML.includes('role="dialog"'), "role=dialog");
  assert.ok(host.innerHTML.includes('role="log"'), "role=log(消息区)");
  assert.ok(host.innerHTML.includes("lab.d"), "锚点引用行");
  // submit:消息+骨架+事件(信封三级:span/全文/app 状态都在)
  const input = new StubEl("input"); // region 元素不带 dataset,合成驱动(dom-stub 面)
  input.dataset.bubbleDraft = "";
  input.parentNode = host;
  input.value = "这段精简点";
  host.trigger("input", { target: input });
  host.trigger("keydown", { target: input, key: "Enter" });
  assert.equal(submissions.length, 1, "Enter 提交");
  assert.deepEqual(
    submissions[0].cascade.cascade.map((f) => f.scope),
    ["widget", "app"],
    "级联三级内容都在(span/全文/app)",
  );
  assert.equal(submissions[0].cascade.cascade[0].data.full_text, "全");
  assert.ok(host.innerHTML.includes("pf-skel"), "busy 骨架");
  // 回复渲染 + apply 只发事件(气泡不越权)
  b.receiveReply("建议:删第二句");
  assert.ok(host.innerHTML.includes("建议:删第二句"), "回复渲染");
  const applyBtn = new StubEl("button");
  applyBtn.dataset.apply = "2"; // assistant 消息索引(seed + submit + reply)
  applyBtn.parentNode = host;
  host.trigger("click", { target: applyBtn });
  assert.deepEqual(applies.map((p) => p.text), ["建议:删第二句"], "apply_reply 只发事件");
  assert.ok(!("notes" in b), "气泡不持有批注写面(不越权)");
  // 多条并存:另一锚点独立
  const { b: b2 } = mk({ ...anchor, member: "lab.e", path: "/lab/iterate/member/lab.e/span/prompt" });
  b2.receiveReply("另一条");
  assert.equal(b.state.messages.length, 3, "本锚点消息流完整(seed+问+答)");
  assert.equal(b2.state.messages.length, 2, "另一锚点自己的 seed+答");
  assert.ok(!b.state.messages.some((m) => m.text === "另一条"), "两条气泡互不串");
  // Esc 关闭(不提交)
  const before = submissions.length;
  host.trigger("keydown", { target: input, key: "Escape" });
  assert.equal(submissions.length, before, "Esc 关闭不产生提交");
}

/* ── W3:W-form / W-list / W-tree / W-date ─────────────────────── */

const { mountFormEditor, validateValues, mountSelectList, mountNsTreeWidget,
  mountDatePicker, parseIso, quickRange, rangeInverted, monthGridHtml,
  renderFormEditor, renderSelectList, visibleItems } =
  await import("../js/widgets/index.js");

{
  // W5.2:W-form/W-list render 纯函数(同 state 同 html、不改 state、语义类)
  const sf = { values: { city: "" }, errors: { city: "必填" },
    schema: { required: ["city"],
      properties: { city: { type: "string" }, addr: { type: "object", properties: { zip: { type: "string" } } } } } };
  const hf = renderFormEditor(sf);
  assert.equal(hf, renderFormEditor(sf), "form render 纯(同 state 同 html)");
  assert.ok(hf.includes("wd-field-err"), "错误字段红边类(§2.5)");
  assert.ok(hf.includes("<fieldset"), "嵌套分组(§2.5)");

  const sl = { items: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }],
    selected: "a", filter: "", focus: 1, multi: false };
  const hl = renderSelectList(sl);
  assert.equal(hl, renderSelectList(sl), "list render 纯");
  assert.ok(hl.includes('aria-selected="true"'), "选中行(左色条走 CSS)");
  assert.ok(hl.includes('data-focus="1"'), "焦点行标记");
  assert.deepEqual(visibleItems({ items: sl.items, filter: "alp" }).map((i) => i.id), ["a"], "visibleItems 纯过滤");
}

{
  // W-form:六类型生成/required 星标/嵌套/数组项/默认值与 skeletonFromSchema 一致
  const doc = makeDocument();
  globalThis.document = doc;
  const schema = {
    type: "object",
    required: ["city"],
    properties: {
      city: { type: "string" },
      n: { type: "integer", minimum: 1, maximum: 10 },
      ratio: { type: "number" },
      ok: { type: "boolean" },
      kind: { type: "string", enum: ["a", "b"] },
      addr: { type: "object", properties: { zip: { type: "string" } } },
      tags: { type: "array", items: { type: "string" } },
    },
  };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountFormEditor(host, { schema });
  const html = () => host.innerHTML;
  assert.ok(html().includes("wd-req"), "required 星标");
  assert.ok(html().includes('type="number"'), "integer/number 列型(stepper)");
  assert.ok(html().includes('role="switch"'), "boolean = switch(§3.5)");
  assert.ok(html().includes("wd-chips"), "enum = chips 单选组(§3.5)");
  assert.ok(html().includes("<fieldset"), "嵌套 object 组");
  assert.deepEqual(w.values(), {
    city: "", n: 1, ratio: 1, ok: false, kind: "", addr: { zip: "" }, tags: [],
  }, "默认值与 skeletonFromSchema 一致");
  // set_field + validate(required/integer min)
  w.set_field("n", 0);
  assert.equal(w.validate(), false, "minimum 约束拦");
  assert.ok(w.state.errors.n.includes("不能小于 1"), "min 错误文案");
  w.set_field("n", 5);
  w.set_field("city", "北京");
  assert.equal(w.validate(), true, "修完通过");
  assert.deepEqual(w.state.errors, {}, "通过即清错");
  // 数组项:增/删
  const addBtn = new StubEl("button");
  addBtn.dataset.fAdd = "tags";
  addBtn.parentNode = host;
  host.trigger("click", { target: addBtn });
  assert.deepEqual(w.values().tags, [""], "数组项添加");
  const delBtn = new StubEl("button");
  delBtn.dataset.fDel = "tags/0";
  delBtn.parentNode = host;
  host.trigger("click", { target: delBtn });
  assert.deepEqual(w.values().tags, [], "数组项删除");
  // reset 回默认
  w.reset();
  assert.equal(w.values().city, "", "reset 回骨架");
  // validateValues 纯函数:required/type/min-max
  assert.ok(validateValues({ n: 0 }, { required: ["city"], properties: { n: { type: "integer", minimum: 1 } } }).city);
  // 边界:空 schema / 畸形
  const host2 = doc.createElement("div");
  doc.body.appendChild(host2);
  assert.doesNotThrow(() => mountFormEditor(host2, { schema: {} }), "空 schema 不炸");
  assert.doesNotThrow(() => mountFormEditor(host2, { schema: { properties: { x: { type: "magic" } } } }), "未知列型降级 text");
}

{
  // W-list:过滤/单选/多选/键盘导航/空态
  const doc = makeDocument();
  globalThis.document = doc;
  const items = [
    { id: "a", label: "Alpha" },
    { id: "b", label: "Beta" },
    { id: "g", label: "Gamma" },
  ];
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const events = [];
  const w = mountSelectList(host, { items });
  w.on("select", (p) => events.push(p));
  assert.ok(host.innerHTML.includes('role="listbox"'), "role=listbox");
  // 过滤(平列表子串;§3.6:命中子串 --live 文字色高亮,mark 不打断阅读)
  w.filter("alp");
  assert.ok(host.innerHTML.includes('<mark class="wd-hit">Alp</mark>ha') && !host.innerHTML.includes("Beta"),
    "过滤命中 + 高亮(文字色非底色)");
  w.filter("");
  // 单选切换(再点取消)
  w.select("a");
  assert.equal(w.state.selected, "a");
  w.select("a");
  assert.equal(w.state.selected, null, "单选再点取消");
  // 键盘:↓ + Enter = activate
  const acts = [];
  w.on("activate", (p) => acts.push(p.id));
  host.trigger("keydown", { target: host, key: "ArrowDown" });
  host.trigger("keydown", { target: host, key: "Enter" });
  assert.deepEqual(acts, ["b"], "键盘导航 ↓+Enter 激活第二项");
  // 多选
  const host2 = doc.createElement("div");
  doc.body.appendChild(host2);
  const w2 = mountSelectList(host2, { items, multi: true });
  w2.select("a");
  w2.select("g");
  assert.deepEqual(w2.state.selected, ["a", "g"], "多选");
  // 空态
  const host3 = doc.createElement("div");
  doc.body.appendChild(host3);
  mountSelectList(host3, { items: [] });
  assert.ok(host3.innerHTML.includes("没有可选项"), "空态");
}

{
  // W-tree:ns-tree 薄封装(原件语义:折叠/默认展开/过滤/选中)
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const items = [{ name: "weather.query" }, { name: "weather.forecast" }, { name: "ops.janitor" }];
  const sels = [];
  const w = mountNsTreeWidget(host, { items });
  w.on("select", (p) => sels.push(p.id));
  const html = () => host.innerHTML;
  assert.ok(html().includes("weather.query"), "叶子渲染(ns-tree 原件路径)");
  // toggle:折叠 weather 命名空间
  const hasToggle = html().includes("data-ns-toggle");
  if (hasToggle) {
    w.toggle("weather");
    assert.ok(!html().includes("weather.query") || !html().includes("weather.forecast"), "折叠后叶子藏起");
    w.toggle("weather");
    assert.ok(html().includes("weather.query"), "再展开");
  }
  // 过滤(§3.7 设计终稿:命中高亮 + 祖先链展开 + **非命中 50% 调光,不剪枝**)
  w.filter("janitor");
  assert.ok(html().includes('<mark class="wd-hit">janitor</mark>'), "过滤命中高亮(wd-hit)");
  assert.ok(html().includes("weather.query") && html().includes("wd-dim"), "非命中调光不剪枝(§3.7)");
  w.filter("");
  // 选中
  const leaf = new StubEl("div");
  leaf.dataset.wtLeaf = "weather.query";
  leaf.parentNode = host;
  host.trigger("click", { target: leaf });
  assert.deepEqual(sels, ["weather.query"], "选中事件上行");
}

{
  // W-date:输入校验/倒置警示/快捷项/翻页键盘/点选纠序
  const doc = makeDocument();
  globalThis.document = doc;
  // 纯函数:ISO 校验
  assert.ok(parseIso("2026-08-03"), "date 合法");
  assert.equal(parseIso("2026-13-99"), null, "非法日期");
  assert.equal(parseIso("not-a-date"), null, "非 ISO");
  assert.ok(parseIso("2026-08-03T10:30", "datetime"), "datetime 合法");
  // 快捷项(固定锚点)
  const anchor = new Date(2026, 7, 5); // 2026-08-05 周三
  assert.deepEqual(quickRange("today", anchor), { start: "2026-08-05", end: "2026-08-05" });
  assert.deepEqual(quickRange("yesterday", anchor), { start: "2026-08-04", end: "2026-08-04" });
  assert.deepEqual(quickRange("week", anchor), { start: "2026-08-03", end: "2026-08-05" }, "本周(周一起)");
  assert.deepEqual(quickRange("lastweek", anchor), { start: "2026-07-27", end: "2026-08-02" }, "上周");
  assert.ok(rangeInverted({ start: "2026-08-10", end: "2026-08-01" }), "倒置检出");
  assert.ok(!rangeInverted({ start: "2026-08-01", end: "2026-08-10" }), "正序不警示");
  assert.ok(monthGridHtml(2026, 7).includes('data-day="2026-08-01"'), "月历网格");
  // 控件:range 倒置**自动纠序 + 闪提示**(§3.8 设计终稿批准的行为变更:
  // 输入通道与点选同律纠序,不再只警示)+ 非法手输行内提示 + ←→ 翻页
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountDatePicker(host, { mode: "range" });
  w.set("start", "2026-08-10");
  w.set("end", "2026-08-01");
  assert.deepEqual(w.state.value, { start: "2026-08-01", end: "2026-08-10" }, "输入倒置自动纠序(§3.8)");
  assert.equal(w.state.notice, "fix", "纠序置闪提示标记");
  assert.ok(host.innerHTML.includes("已自动调整顺序"), "纠序提示上屏(不静默改值)");
  // 手输非法:红边 + 行内提示,值保留不丢
  w.set("start", "not-a-date");
  assert.ok(w.state.invalid, "非法手输标记(state.invalid)");
  assert.ok(host.innerHTML.includes("is-invalid") && host.innerHTML.includes("无法识别"), "红边 + 行内提示(§3.8)");
  w.set("start", "2026-08-01");
  assert.equal(w.state.invalid, null, "修正即清");
  w.pick("2026-08-03");
  w.pick("2026-08-01");
  assert.deepEqual(w.state.value, { start: "2026-08-01", end: "2026-08-03" }, "点选自动纠序");
  const month0 = host.innerHTML.match(/data-ym="(\d+-\d+)"/)?.[1];
  host.trigger("keydown", { target: host, key: "ArrowRight" });
  const month1 = host.innerHTML.match(/data-ym="(\d+-\d+)"/)?.[1];
  assert.notEqual(month1, month0, "→ 翻下一月(键盘可达)");
  host.trigger("keydown", { target: host, key: "ArrowLeft" });
  host.trigger("keydown", { target: host, key: "ArrowLeft" });
  const month2 = host.innerHTML.match(/data-ym="(\d+-\d+)"/)?.[1];
  assert.notEqual(month2, month1, "← 翻上一月");
  w.quick("today");
  assert.equal(w.state.invalid, null, "快捷项清非法态");
}

/* ── W4:W-diff / W-md / W-log / W-chart ───────────────────────── */

const { mountDiffViewer, diffBodyHtml, mdToHtml, looksMarkdown, mountLogViewer,
  mountChart, chartSvg, chartTableHtml, downsample, niceTicks, mountMarkdownViewer } =
  await import("../js/widgets/index.js");
const { diffCard } = await import("../../../web_platform/static/cards.js");

{
  // W-diff:split 与 cards.js diffCard 一致性(提取不改语义);三态渲染;模式切换;折叠
  const diff = { has_changes: true, members: [{
    member: "lab.d", status: "changed",
    fields: [{ kind: "changed", path: "description", old: "旧", new: "新" }],
    prompt_diff: [
      { kind: "del", text: "旧句" },
      { kind: "same", text: "同句1" },
      { kind: "same", text: "同句2" },
      { kind: "add", text: "新句" },
    ],
    tests: { added: [], removed: [] } }] };
  assert.equal(
    diffBodyHtml(diff, { mode: "split" }),
    diffCard({ data: { name: "lab.d", diff } }).replace(/<div class="pf-dim">.*$/, "") || diffBodyHtml(diff, { mode: "split" }),
    "split 与 diffCard 同构(提取一致性)",
  );
  // 直接逐字节对(diffCard 现在就是委托 diffBodyHtml)
  assert.equal(diffCard({ data: { name: "lab.d", diff } }), diffBodyHtml(diff, { mode: "split" }),
    "diffCard = diffBodyHtml(split),逐字节");
  assert.ok(diffBodyHtml(diff).includes('data-kind="changed"'), "字段两列(红绿语义)");
  assert.ok(diffBodyHtml(diff).includes('data-kind="add"'), "红绿行 add");
  assert.ok(diffBodyHtml(diff).includes('data-kind="del"'), "红绿行 del");
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountDiffViewer(host, { diff });
  assert.ok(host.innerHTML.includes('data-on="1"'), "split 默认激活");
  // unified:same 折叠上下文(+2 行未变)
  w.set_mode("unified");
  assert.ok(host.innerHTML.includes("+2 行未变"), "unified 折叠上下文");
  assert.ok(host.innerHTML.includes("wd-diff-old") && host.innerHTML.includes("wd-diff-new"), "新旧堆叠");
  // 展开折叠
  const foldBtn = new StubEl("button");
  foldBtn.dataset.fold = "lab.d";
  foldBtn.parentNode = host;
  host.trigger("click", { target: foldBtn });
  assert.ok(host.innerHTML.includes("同句1"), "展开后 same 行可见");
  assert.ok(!diffBodyHtml({ members: [] }), "空 diff 空串(调用方给空态)");
}

{
  // W-md:XSS 全转义/白名单渲染/代码块 mono/非法链接剥壳
  const xss = mdToHtml('<script>alert(1)</script>');
  assert.ok(!xss.includes("<script"), "<script> 转义");
  assert.ok(xss.includes("&lt;script&gt;"), "转义为文本可见");
  const attr = mdToHtml('<img src=x onerror=alert(1)>');
  assert.ok(!attr.includes("<img"), "HTML 标签不存活(整体先转义)");
  assert.ok(attr.includes("&lt;img"), "转义为文本可见(属性永远只是文本)");
  const evil = mdToHtml("[点我](javascript:alert(1))");
  assert.ok(!evil.includes('href="javascript:'), "javascript: 链接剥壳");
  assert.ok(evil.includes("javascript:alert(1)"), "剥壳后原文可见(纯文本)");
  const ok = mdToHtml("[文档](https://example.com/a) 和 [站内](/#/lab)");
  assert.ok(ok.includes('href="https://example.com/a"'), "https 链接白名单");
  assert.ok(ok.includes('href="/#/lab"'), "站内相对链接白名单");
  const md = mdToHtml("# 标题\n\n- 甲\n- 乙\n\n```\ncode <b>\n```\n\n**粗** 和 `行内`\n\n| a | b |\n|---|---|\n| 1 | 2 |");
  assert.ok(md.includes("<h4>"), "标题(h4 起,页面语义层)");
  assert.ok(md.includes("<li>"), "列表");
  assert.ok(md.includes('class="mono wd-md-code"'), "代码块 mono");
  assert.ok(!md.includes("<b>code"), "代码块内不再加工(先转义)");
  assert.ok(md.includes("<b>粗</b>"), "粗体");
  assert.ok(md.includes("<table"), "表格");
  // looksMarkdown:结构才启用(普通文本不误伤)
  assert.ok(looksMarkdown("# 标题"), "标题结构检出");
  assert.ok(looksMarkdown("普通一句含 **重点**"), "粗体结构检出");
  assert.ok(!looksMarkdown("就是一句普通的话,没有结构"), "普通文本不启用");
}

{
  // W-log:跟随/上滚暂停/回到底部/截断/复制/kind 着色
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountLogViewer(host, { lines: [{ kind: "run.start", text: "起" }], maxLines: 5 });
  assert.ok(host.innerHTML.includes('data-kind="run.start"'), "kind 着色槽");
  assert.ok(host.innerHTML.includes('role="log"'), "role=log");
  // append + 跟随滚底(render 内 scrollTop=scrollHeight;dom-stub 每次渲染换
  // region、scrollHeight 恒 0——断言赋值语义,数值行为在真实 DOM 生效)
  w.append([{ kind: "info", text: "一" }, { kind: "error", text: "错" }]);
  const box = host.querySelector("[data-wlog-box]");
  assert.equal(box.scrollTop, box.scrollHeight, "跟随模式自动滚底(scrollTop=scrollHeight)");
  // 上滚暂停跟随(垫 scrollHeight 模拟长日志;scrollTop=0 = 滚到顶)
  w.state.follow = true;
  box.scrollHeight = 800;
  box.scrollTop = 0;
  host.trigger("scroll", { target: box });
  assert.equal(w.state.follow, false, "上滚即暂停跟随");
  assert.ok(host.innerHTML.includes("回到底部"), "回到底部钮出现");
  // 回到底部恢复(合成按钮:region dataset 为空,dom-stub 面)
  const bottomBtn = new StubEl("button");
  bottomBtn.dataset.wlogBottom = "1";
  bottomBtn.parentNode = host;
  host.trigger("click", { target: bottomBtn });
  assert.equal(w.state.follow, true, "点回底部恢复跟随");
  // 截断保尾部
  w.append([6, 7, 8, 9, 10].map((n) => ({ kind: "info", text: `行${n}` })));
  assert.equal(w.state.lines.length, 5, "截断到上限");
  assert.equal(w.state.lines.at(-1).text, "行10", "保留尾部");
  // 复制全部(事件降级)
  let copied = "";
  w.on("copy", (p) => { copied = p.text; });
  assert.equal(w.copy_all(), copied, "copy_all 文本与事件一致");
  assert.ok(copied.includes("行10"), "复制含尾部行");
  // 过滤
  w.filter("错");
  assert.ok(host.innerHTML.includes("错") && !host.innerHTML.includes("行9"), "过滤");
}

{
  // W-chart:三型/空态/抽稀/刻度/hover title/表格视图等价/series 显隐
  assert.ok(chartSvg([]).includes("还没有数据"), "空态");
  assert.equal(downsample([...Array(1000)].map((_, i) => ({ x: i, y: i }))).length, 500, ">500 抽稀到上限");
  const ds = downsample([...Array(1000)].map((_, i) => ({ x: i, y: i })));
  assert.equal(ds.at(-1).x, 999, "抽稀保尾点");
  assert.ok(niceTicks(0, 100).includes(100) || niceTicks(0, 100).length >= 2, "刻度自动");
  const series = [{ name: "cost", points: [{ x: 1, y: 2 }, { x: 2, y: 5 }, { x: 3, y: 3 }] }];
  const line = chartSvg(series, { type: "line", label: "费用" });
  assert.ok(line.includes("<polyline"), "line 型");
  assert.ok(line.includes('role="img"'), "role=img");
  assert.ok(line.includes('aria-label="费用"'), "aria-label 摘要");
  assert.ok(line.includes("2: 5"), "hover 读值(title)");
  assert.ok(line.includes("wd-chart-grid"), "网格");
  const bar = chartSvg(series, { type: "bar" });
  assert.ok(bar.includes("<rect"), "bar 型");
  const spark = chartSvg(series, { type: "spark" });
  assert.ok(spark.includes("wd-chart-spark") && !spark.includes("wd-chart-grid"), "spark 无轴迷你");
  // 表格视图:等价数据(硬规则)
  const table = chartTableHtml(series);
  assert.ok(table.includes("<td>2</td><td>5</td>"), "等价数据表同行数据");
  assert.ok(table.includes("cost"), "series 名在表");
  // 控件:toggle 视图 + series 显隐(多序列)
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountChart(host, {
    series: [series[0], { name: "tokens", points: [{ x: 1, y: 10 }] }],
    type: "line", label: "用量",
  });
  assert.ok(host.innerHTML.includes("wd-chart"), "图表视图默认");
  const toggle = new StubEl("button");
  toggle.dataset.chartToggle = "";
  toggle.parentNode = host;
  host.trigger("click", { target: toggle });
  assert.ok(host.innerHTML.includes("wd-chart-table"), "切表格视图");
  const s1 = new StubEl("button");
  s1.dataset.chartSeries = "tokens";
  s1.parentNode = host;
  host.trigger("click", { target: toggle }); // 回图表
  host.trigger("click", { target: s1 });
  assert.deepEqual(w.state.hidden, ["tokens"], "多序列显隐");
}

/* ── W5.3:七控件 render 纯函数 + 新行为(Esc 收层/过滤自动展开/当前项/复制钮)── */

const { renderTreeWidget, renderDatePicker, renderLogViewer, renderDiffViewer,
  renderMarkdownViewer, renderChart, renderBubble } =
  await import("../js/widgets/index.js");

{
  // render 纯函数三要素:同 state 同 html、不改 state、转义
  const sTree = { nodes: [{ name: "a.b.c" }], expanded: [], selected: null, filter: "" };
  assert.equal(renderTreeWidget(sTree), renderTreeWidget(sTree), "tree render 纯");
  assert.deepEqual(sTree.expanded, [], "tree render 不改 state");
  const sDate = { value: { start: "2026-08-01", end: "2026-08-05" }, mode: "range", inverted: false,
    open: true, cursor: { year: 2026, month: 7 } };
  assert.equal(renderDatePicker(sDate), renderDatePicker(sDate), "date render 纯");
  assert.ok(renderDatePicker(sDate).includes("wd-grid"), "弹层开 → 月历在");
  assert.ok(!renderDatePicker({ ...sDate, open: false }).includes("wd-grid"), "弹层关 → 月历不在(§2.8)");
  const sLog = { lines: [{ kind: "error", text: "x<b>" }], follow: false, filter: "" };
  const hl = renderLogViewer(sLog);
  assert.equal(hl, renderLogViewer(sLog), "log render 纯");
  assert.ok(hl.includes("x&lt;b&gt;"), "log 行转义");
  assert.ok(hl.includes("wd-log-bottom"), "暂停跟随 → 回到底部钮");
  const sDiff = { left: { members: [{ member: "m", status: "changed", fields: [], prompt_diff: [{ kind: "add", text: "+1" }] }] },
    mode: "unified", expanded: [] };
  assert.equal(renderDiffViewer(sDiff), renderDiffViewer(sDiff), "diff render 纯");
  const sChart = { series: [{ name: "s", points: [{ x: 1, y: 2 }] }], type: "line", view: "chart", hidden: [] };
  assert.equal(renderChart(sChart, { label: "L" }), renderChart(sChart, { label: "L" }), "chart render 纯");
  const sBub = { anchor: { member: "m", path: "p" }, messages: [{ role: "assistant", text: "答" }], busy: false, draft: "" };
  assert.equal(renderBubble(sBub), renderBubble(sBub), "bubble render 纯");
  assert.ok(renderBubble(sBub).includes("w-bubble-anchor"), "气泡卡锚点引用行");
}

{
  // W-tree:过滤命中自动展开祖先链(深层默认折叠)+ 当前项浅底标记
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  // top.a 有 8 个叶子且 top 有分叉(不触发单层链合并)→ top.a 深层默认折叠
  const items = [...Array(8)].map((_, i) => ({ name: `top.a.x${i + 1}` })).concat([{ name: "top.b.y1" }]);
  const w = mountNsTreeWidget(host, { items });
  assert.ok(!host.innerHTML.includes("top.a.x1"), "深层叶子默认折叠(≥阈值藏起)");
  w.filter("x1");
  assert.ok(host.innerHTML.includes("top.a.x1"), "过滤命中 → 祖先链自动展开(§2.7)");
  w.filter("");
  w.select("top.b.y1");
  assert.ok(host.innerHTML.includes('data-current="1"'), "当前项浅底标记");
}

{
  // W-date:Esc 收层 / 输入区点击重开
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  mountDatePicker(host, { mode: "range" });
  assert.ok(host.innerHTML.includes("wd-grid"), "初始层开");
  host.trigger("keydown", { target: host, key: "Escape" });
  assert.ok(!host.innerHTML.includes("wd-grid"), "Esc 收层(§2.8)");
  const inputEl = new StubEl("input");
  inputEl.closest = (sel) => (sel === ".wd-date-in" ? inputEl : null);
  inputEl.parentNode = host;
  host.trigger("click", { target: inputEl });
  assert.ok(host.innerHTML.includes("wd-grid"), "点输入区重开层");
}

{
  // W-md:代码块复制钮(render 产出 + 点击复制事件带块文本)
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountMarkdownViewer(host, { source: "# t\n\n```\nlet a = 1;\n```\n" });
  assert.ok(host.innerHTML.includes('data-md-copy="0"'), "代码块带复制钮(§2.12)");
  const copied = [];
  w.on("copy", (p) => copied.push(p.text));
  const btn = new StubEl("button");
  btn.dataset.mdCopy = "0";
  btn.parentNode = host;
  host.trigger("click", { target: btn });
  assert.deepEqual(copied, ["let a = 1;"], "复制 = 代码块原文");
}

/* ── W5.6:双形态(card/tab;docs/WIDGET-ARCH.md §1.4)─────────────────
   同一实例 state 两种渲染:card = 只读摘要 + 整卡 open 入口;tab = 完整交互。
   本区钉:协议面(双 surface + open 事件声明)、card render 纯函数三要素 +
   摘要关键内容 + 零编辑控件 + 转义、缺省 opts = tab(既有行为不回退)、
   mount 面(update 在 card 形态工作、check() 局部刷新钩子两种形态共用)。 */

{
  // 协议面:13 控件 def 全声明 card+tab,且都声明 open 事件(card 唯一交互)
  // (本文件前文注册过 t-probe 等探针——无 render 面,不计入产品控件面)
  const defs = listWidgetKinds().map((k) => getWidgetDef(k)).filter((d) => typeof d.render === "function");
  assert.equal(defs.length, 13, "13 种自渲染控件");
  for (const def of defs) {
    assert.deepEqual([...(def.surfaces ?? [])].sort(), ["card", "tab"], `${def.kind} 双 surface 声明`);
    assert.ok(def.events.includes("open"), `${def.kind} 声明 open 事件(card 整卡点击)`);
  }
}

{
  // card render 面:纯(同 state 同 html/不改 state)、根带 data-surface + wd-card、
  // 无 textarea/input/button/select、摘要关键内容在、XSS 转义;缺省 = tab
  const NO_EDIT = /<(textarea|input|button|select)\b/i;
  const today = quickRange("today"); // 快捷徽标确定性锚点(用真实"今天")
  const cases = [
    ["text-editor", renderTextEditor,
      { value: "一<script>\n二\n三\n四", dirty: true, mono: true, rows: 4, field: "p", label: "p" }, {},
      (h) => h.includes("wd-card-name") && h.includes("wd-card-dot") && h.includes("wd-card-go") &&
        h.includes("wd-card-meta") && h.includes("4 行") && h.includes("wd-fade") &&
        h.includes("一&lt;script&gt;") && !h.includes("四"),
      "标题行(名称+dirty 点+打开→)+ 前 3 行预览(末行渐隐,第 4 行不进卡)+ meta 行;值转义"],
    ["json-editor", renderJsonEditor,
      { value: '{"a<": 1}', mono: true, rows: 6, field: "j", label: "j", error: null }, {},
      (h) => h.includes("wd-json-ok") && h.includes('{&quot;a&lt;&quot;: 1}') && !h.includes("wd-format"),
      "合法绿勾 + 首行预览;无 format 钮;key 转义"],
    ["json-editor(错误)", renderJsonEditor,
      { value: '{\n  "a": bad\n}', mono: true, field: "j", label: "j", error: { line: 2, message: "bad token" } }, {},
      (h) => h.includes('data-wd-errbar="1">') && h.includes("第 2 行") && h.includes("bad token") &&
        h.includes('wd-json-ok" hidden'),
      "错误红条(行级)+ 绿勾隐"],
    ["table-editor", renderTableEditor,
      { rows: [{ id: "r1", cells: { name: "甲<b>", n: 1, ok: true, kind: "a" } }, { id: "r2", cells: { name: "乙", n: 2 } },
          { id: "r3", cells: { name: "丙", n: 3 } }],
        selected: [], schema: { columns: [
          { key: "name", type: "text", label: "名" }, { key: "n", type: "number", label: "数" },
          { key: "ok", type: "boolean", label: "好" }, { key: "kind", type: "enum", label: "类", options: ["a"] }] } }, {},
      (h) => h.includes("+1") && h.includes("3 行") && h.includes("wd-card-cols") && h.includes("甲&lt;b&gt;") &&
        h.includes("乙") && !h.includes("丙") && !h.includes("⠿") && h.includes("查看全部"),
      "列摘要 4→3 溢出 +1;行数徽标;前 2 行只读(第 3 行不进卡);无拖柄;查看全部 →;转义"],
    ["kv-editor", renderKvEditor,
      { entries: [{ key: "a", value: "1" }, { key: "a", value: "2" }, { key: "b<x>", value: "3" }, { key: "c", value: "4" }] }, {},
      (h) => h.includes("4 键值") && h.includes("重复") && h.includes("b&lt;x&gt;") &&
        !h.includes(">c<") && !h.includes("data-kv-x"),
      "计数徽标 + 重复 key 警示;前 3 条(第 4 条不进卡);无 ✕;转义"],
    ["schema-form", renderFormEditor,
      { values: { city: "北京" }, errors: {}, schema: { required: ["city", "zip"],
          properties: { city: { type: "string" }, zip: { type: "string" }, note: { type: "string" } } } }, {},
      (h) => h.includes("必填 1/2") && h.includes("wd-progress") && h.includes(">zip<") && !h.includes("lab-field"),
      "必填完成度 1/2 + 进度条 + 缺失字段 chip;无字段控件"],
    ["select-list", renderSelectList,
      { items: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta<b>", hint: "第二项" }, { id: "g", label: "Gamma" }],
        selected: "b", filter: "", focus: 0, multi: false }, {},
      (h) => h.includes("共 3 项") && h.includes("Beta&lt;b&gt;") && h.includes("第二项") &&
        h.includes("更换") && !h.includes("wd-list-filter") && !h.includes("Alpha"),
      "当前选中项(名称+元信息)+ 共 N 项 + 更换 →;无过滤框;未选项不进卡;转义"],
    ["select-list(多选)", renderSelectList,
      { items: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }, { id: "g", label: "Gamma" }],
        selected: ["a", "b", "g"], filter: "", focus: 0, multi: true }, {},
      (h) => h.includes("Alpha") && h.includes("+2"), "多选:首选项 + 溢出 +N"],
    ["ns-tree", renderTreeWidget,
      { nodes: [{ name: "weather.query" }, { name: "weather.forecast" }, { name: "ops.janitor" }],
        expanded: [], selected: "weather.<query>", filter: "" }, {},
      (h) => h.includes("weather") && h.includes("&lt;query&gt;") && h.includes("3 叶子") &&
        h.includes("命名空间") && h.includes(" / ") && !h.includes("wd-tree-filter"),
      "面包屑当前路径(段间 /)+ 叶子/命名空间计数;无过滤框;转义"],
    ["date-picker(range)", renderDatePicker,
      { value: { start: "2026-08-01", end: "2026-08-04" }, mode: "range", inverted: false,
        open: true, cursor: { year: 2026, month: 7 } }, {},
      (h) => h.includes("2026-08-01 → 2026-08-04") && !h.includes("wd-grid"),
      "区间 起 → 止;不开日历层"],
    ["date-picker(快捷命中)", renderDatePicker,
      { value: { ...today }, mode: "range", inverted: false, open: false, cursor: { year: 2026, month: 7 } }, {},
      (h) => h.includes(copy("w.date.today")), "值命中快捷项 → 快捷标签徽标"],
    ["chart", renderChart,
      { series: [{ name: "cost", points: [{ x: 1, y: 2 }, { x: 2, y: 5 }, { x: 3, y: 3 }] },
          { name: "tok", points: [{ x: 1, y: 9 }] }], type: "line", view: "chart", hidden: [] },
      { label: "费用<x>" },
      (h) => h.includes("wd-chart-mini") && h.includes("2 序列") && h.includes("wd-card-num") &&
        h.includes("<polyline") && !h.includes("wd-chart-tick") && h.includes('aria-label="费用&lt;x&gt;"'),
      "迷你图(无坐标轴文字)+ 最新值读数 + 图例压成计数;label 转义"],
    ["log-viewer", renderLogViewer,
      { lines: [{ kind: "info", text: "首行" }, { kind: "warn", text: "二" }, { kind: "error", text: "三<b>" },
          { kind: "info", text: "四" }], follow: true, filter: "" }, {},
      (h) => h.includes("4 行") && h.includes('data-kind="error"') && h.includes("三&lt;b&gt;") &&
        h.includes("四") && !h.includes("首行") && !h.includes("wd-log-filter") && !h.includes("wd-log-bottom"),
      "总行数徽标 + 最近 3 行(kind 色条,首行不进卡);无过滤框/回到底部;转义"],
    ["diff-viewer", renderDiffViewer,
      { left: { members: [{ member: "m", status: "changed", fields: [],
            prompt_diff: [{ kind: "del", text: "旧<b>" }, { kind: "same", text: "同" }, { kind: "add", text: "新" }] }] },
        mode: "split", expanded: [] }, {},
      (h) => h.includes('data-kind="add">+1<') && h.includes('data-kind="del">-1<') &&
        h.includes("- 旧&lt;b&gt;") && h.includes("+ 新") && !h.includes("同") && !h.includes("wd-mode"),
      "+add/-del 计数徽标 + 首个 hunk 2 行预览(same 不进卡);无模式切换;转义"],
    ["md-viewer", renderMarkdownViewer,
      { source: "# 标题<script>\n\n首段摘录。\n\n第二段不进卡\n\n```\ncode\n```" }, {},
      (h) => h.includes("标题&lt;script&gt;") && h.includes("首段摘录。") && !h.includes("第二段") &&
        !h.includes("data-md-copy"),
      "首个标题 + 首段摘录(二段/代码块不进卡);无复制钮;转义"],
    ["chat-bubble", renderBubble,
      { anchor: { member: "m", path: "p" }, messages: [{ role: "user", text: "问" },
          { role: "assistant", text: "答<b>" }], busy: false, draft: "", unread: 2 }, {},
      (h) => h.includes("2 条") && h.includes("2 未读") && h.includes("答&lt;b&gt;") && !h.includes("问") &&
        !h.includes("data-bubble-draft"),
      "消息计数 + 未读徽标 + 最后一条摘录;无输入框;转义"],
  ];
  for (const [name, fn, state, opts, check, desc] of cases) {
    const snapshot = JSON.stringify(state);
    const card = fn(state, { ...opts, surface: "card" });
    assert.equal(card, fn(state, { ...opts, surface: "card" }), `${name}:card render 纯(同 state 同 html)`);
    assert.equal(JSON.stringify(state), snapshot, `${name}:card render 不改 state`);
    assert.ok(card.includes('data-surface="card"'), `${name}:card 根带 data-surface="card"`);
    assert.ok(card.includes("wd-card"), `${name}:card 根带紧凑类 wd-card`);
    assert.ok(!NO_EDIT.test(card), `${name}:card 无编辑控件(textarea/input/button/select)`);
    assert.ok(!card.includes("<script"), `${name}:card 无未转义注入`);
    assert.ok(check(card), `${name}:card 摘要关键内容(${desc})`);
    assert.equal(fn(state, opts), fn(state, { ...opts, surface: "tab" }), `${name}:缺省 opts = tab(不回退)`);
    assert.ok(fn(state, opts).length > 0, `${name}:tab 渲染非空`);
  }
}

{
  // mount 面:card 形态宿主委托只挂 open;update() 在 card 形态维持(state → card 重渲)
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  host.dataset.field = "prompt";
  doc.body.appendChild(host);
  const w = mountTextEditor(host, { value: "一\n二", surface: "card", path: "/t/card-text" });
  assert.ok(host.innerHTML.includes('data-surface="card"'), "text card 首渲");
  assert.equal(host.querySelector("textarea"), null, "card 不产出 textarea");
  const opens = [];
  w.on("open", (p) => opens.push(p));
  host.trigger("click", { target: host });
  assert.equal(opens.length, 1, "整卡点击 → open");
  assert.equal(opens[0].path, "/t/card-text", "open 负载带 §14 路径");
  host.trigger("keydown", { target: host, key: "Enter" });
  assert.equal(opens.length, 2, "Enter 也发 open(键盘可达)");
  w.update({ value: "一\n二\n三" }); // update() 有无维持现状:state 合并 → 按 card 面重渲
  assert.ok(host.innerHTML.includes('data-surface="card"'), "update 后仍是 card 面");
  assert.ok(host.innerHTML.includes("3 行"), "update 后微标随 state 刷新(串内文本)");
  w.destroy();
  assert.equal(host.innerHTML, "", "destroy 清空(自渲染件纪律)");
}

{
  // json card:check() 的局部刷新钩子(.wd-errbar/.wd-json-ok)两种形态共用——
  // mount 末的初值校验把 card 状态行同步到位(与 tab 同纪律,不重渲)
  const doc = makeDocument();
  globalThis.document = doc;
  const bad = doc.createElement("div");
  doc.body.appendChild(bad);
  mountJsonEditor(bad, { value: '{\n  "a": 1', field: "j", label: "j", surface: "card" });
  const bar = bad.querySelector(".wd-errbar");
  assert.ok(bar && !bar.hidden, "非法 JSON:card 错误条显(check() 同步)");
  assert.ok(bar.textContent.includes("第 2 行"), "行级定位进 card 状态行");
  assert.ok(bad.querySelector(".wd-json-ok").hidden, "非法 → 绿勾隐");
  const ok = doc.createElement("div");
  doc.body.appendChild(ok);
  mountJsonEditor(ok, { value: '{"a": 1}', field: "j", label: "j", surface: "card" });
  assert.ok(!ok.querySelector(".wd-json-ok").hidden, "合法 JSON:card 绿勾显");
  assert.ok(ok.querySelector(".wd-errbar").hidden, "合法 → 错误条隐");
}

{
  // bubble card:open 负载沿用本控件语义({anchor});tab 行为不回退(输入框还在)
  const doc = makeDocument();
  globalThis.document = doc;
  const anchor = { member: "lab.d", path: "/x/y" };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountBubble(host, { anchor, seedMessages: [{ role: "user", text: "旧" }], surface: "card", unread: 3 });
  assert.ok(host.innerHTML.includes("3 未读"), "未读徽标(mount 选项进 state)");
  const opens = [];
  w.on("open", (p) => opens.push(p));
  host.trigger("click", { target: host });
  assert.deepEqual(opens, [{ anchor }], "card open 负载 = {anchor}(与 tab open 事件同语义)");
  const host2 = doc.createElement("div");
  doc.body.appendChild(host2);
  mountBubble(host2, { anchor, seedMessages: [] });
  assert.ok(host2.innerHTML.includes("data-bubble-draft"), "tab 输入框不回退");
}

console.log("widgets.test.mjs: W5.6 dual-surface assertions passed");

/* ── W6.0/W6.1:设计终稿(docs/WIDGET-DESIGN.md §1/§3.1/§3.2)──────────
   基建:--log-bg 六主题契约、widgets.css 共享层在位、零硬编码色值;
   W-text/W-json 视觉结构(关键类名/数据属性)+ 着色/括号/键数纯函数。 */

const { jsonHighlightHtml, jsonKeyCount, matchBrace, relTime } =
  await import("../js/widgets/index.js");

{
  // W6.0 基建:--log-bg 进 tokens + 六主题 css + CONTRACT_TOKENS
  const tokensCss = readFileSync(join(import.meta.dirname, "../css/tokens.css"), "utf-8");
  assert.ok(tokensCss.includes("--log-bg: #0c1018"), "tokens.css 有 --log-bg(效果图值)");
  for (const t of ["classic", "moe", "terminal", "blueprint", "ink", "pixel"]) {
    const css = readFileSync(join(import.meta.dirname, `../css/themes/${t}.css`), "utf-8");
    assert.match(css, /--log-bg:\s*#\w+/, `${t} 定义 --log-bg(压暗映射)`);
  }
  const { CONTRACT_TOKENS } = await import("../js/themes.js");
  assert.ok(CONTRACT_TOKENS.includes("--log-bg"), "--log-bg 进契约清单(themes-contract 盯)");

  // widgets.css:共享层在位 + 零硬编码色值(契约 token 红线进测试)
  const wcss = readFileSync(join(import.meta.dirname, "../css/widgets.css"), "utf-8");
  const noComments = wcss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.ok(!/#[0-9a-f]{3,8}\b/i.test(noComments), "widgets.css 零硬编码色值");
  for (const sel of [".wd-chip", ".wd-badge", ".wd-focus-ring", ".wd-skeleton", ".wd-empty-box",
    ".wd-btn-primary", ".wd-btn-ghost", ".wd-a-fade", ".wd-a-lift"]) {
    assert.ok(wcss.includes(sel), `共享层 ${sel} 在位`);
  }
  assert.ok(wcss.includes("[data-tone=\"ok\"]") && wcss.includes("[data-tone=\"danger\"]"),
    "badge/chip 四 tone 齐(warn/live/ok/danger)");
  assert.ok(wcss.includes("@keyframes wd-shimmer"), "skeleton shimmer 动效");
  assert.ok(tokensCss.includes("prefers-reduced-motion"), "reduced-motion 全局降级(tokens.css 基线)");
}

{
  // W6.1 W-text tab 视觉结构(§3.1):头部/行号槽逐行/当前行槽/微标胶囊/只读锁/占位
  const h = renderTextEditor({ value: "a\nb", dirty: true, mono: true, rows: 4,
    field: "prompt", label: "prompt", lang: "markdown" });
  assert.ok(h.includes("wd-text-head") && h.includes("prompt · markdown"), "头部 field · lang");
  assert.ok(h.includes('class="wd-gl" data-line="1">1<') && h.includes('data-line="2">2<'), "行号槽逐行槽位");
  assert.ok(h.includes("wd-curline"), "当前行高亮槽在(逻辑面定位)");
  assert.ok(h.includes("wd-micro-box") && h.includes("wd-micro-dot") && h.includes("wd-micro-tip"),
    "微标胶囊:dirty 圆点 + 行列 tip 槽");
  assert.ok(!h.includes('wd-micro-dot" aria-hidden="true" hidden'), "dirty → 圆点显");
  assert.ok(h.includes('wrap="off"'), "mono 不软换行(行号/着色对齐前提)");
  assert.ok(h.includes(`placeholder="${copy("w.text.ph")}"`), "占位文案(copy 键)");
  const clean = renderTextEditor({ value: "a", dirty: false, mono: true, field: "f", label: "f" });
  assert.ok(clean.includes('wd-micro-dot" aria-hidden="true" hidden'), "无 dirty → 圆点隐");
  const ro = renderTextEditor({ value: "a", readonly: true, mono: true, field: "f", label: "f" });
  assert.ok(ro.includes("wd-lock") && ro.includes("🔒"), "readonly 锁图标(§3.1)");
  assert.ok(ro.includes("is-readonly"), "readonly 类(无光标/无脏条走 CSS)");
  const plain = renderTextEditor({ value: "a", mono: false, field: "f", label: "f" });
  assert.ok(!plain.includes("wd-gutter") && !plain.includes("wd-curline"), "plain 无行号槽/当前行槽");
  assert.ok(renderTextEditor({ value: "a", mono: true, field: "f<x>", label: "f<x>" }).includes("f&lt;x&gt; · "),
    "头部 field 转义");
}

{
  // W6.1 W-text card(§3.1):标题行/预览渐隐/meta 相对时间
  const now = Date.now() / 1000;
  const h = renderTextEditor({ value: "一\n二\n三\n四", mono: false, field: "prompt", label: "晚餐助手",
    updated_at: now - 180 }, { surface: "card" });
  assert.ok(h.includes("wd-doc-ico") && h.includes("wd-card-name"), "标题行:图标位 + 名称");
  assert.ok(h.includes("晚餐助手"), "名称 = label");
  assert.ok(h.includes("wd-card-go"), "「打开 →」槽(hover 渐显走 CSS)");
  assert.ok(h.includes("wd-fade"), "预览末行渐隐");
  assert.ok(h.includes("wd-card-meta") && h.includes("4 行") && h.includes("3 分钟前"), "meta:行数·字数·相对时间");
  // relTime 纯函数:刚刚/分钟/小时/天
  assert.equal(relTime(now - 10, now), copy("w.time.now"), "relTime 刚刚");
  assert.equal(relTime(now - 300, now), copy("w.time.min").replace("{n}", "5"), "relTime 分钟");
  assert.equal(relTime(now - 7200, now), copy("w.time.hour").replace("{n}", "2"), "relTime 小时");
  assert.equal(relTime(now - 3 * 86400, now), copy("w.time.day").replace("{n}", "3"), "relTime 天");
}

{
  // W6.1 W-json 着色/键数/括号纯函数(§3.2:着色仅渲染层,不进 state)
  const hl = jsonHighlightHtml('{\n  "name": "bot<x>", "n": 80, "ok": true\n}');
  assert.ok(hl.includes('wd-tk-key">&quot;name&quot;'), "key = --live 槽");
  assert.ok(hl.includes('wd-tk-str">&quot;bot&lt;x&gt;&quot;'), "string = --ok 槽 + 转义");
  assert.ok(hl.includes('wd-tk-num">80') && hl.includes('wd-tk-num">true'), "number/bool = --warn 槽");
  assert.ok(hl.includes('wd-tk-pn">{'), "标点弱色槽");
  assert.ok(hl.includes('wd-hl-line" data-line="1"'), "逐行槽(行号/波浪对齐面)");
  const hlErr = jsonHighlightHtml("{\nbad\n}", { errorLine: 2 });
  assert.ok(hlErr.includes('wd-hl-line is-err" data-line="2"'), "错误行红波浪槽");
  const hlMatch = jsonHighlightHtml("{}", { matches: [0, 1] });
  assert.equal((hlMatch.match(/wd-brace/g) ?? []).length, 2, "括号匹配浅底(mark 注入)");
  assert.equal(jsonKeyCount('{"a":1,"b":{"c":2},"d":[1,2]}'), 4, "键数递归统计(嵌套计入)");
  assert.equal(jsonKeyCount("{bad"), null, "非法 JSON 键数 → null");
  assert.deepEqual(matchBrace('{"a":[1,2]}', 1), [0, 10], "前向配对");
  assert.deepEqual(matchBrace('{"a":[1,2]}', 11), [10, 0], "后向配对(光标在闭括号后)");
  assert.equal(matchBrace('{"a":"}"]}', 7), null, "串内括号不算");
  assert.equal(matchBrace("abc", 1), null, "不在括号旁 → null");
}

{
  // W6.1 W-json tab 结构:着色层/✓ 胶囊(带键数)/format 禁用/错误条行
  const ok = renderJsonEditor({ value: '{"a": 1}', mono: true, field: "inputsText",
    label: "inputsText", error: null });
  assert.ok(ok.includes("<pre class=\"wd-hl\""), "着色层 overlay 在");
  assert.ok(ok.includes("wd-json-ok") && ok.includes("✓ JSON 合法 · 1 键"), "✓ 绿徽标带键数(§3.2)");
  assert.ok(ok.includes("wd-text-head") && ok.includes("inputsText · json"), "头部 field · lang");
  const err = renderJsonEditor({ value: "{bad", mono: true, field: "f", label: "f",
    error: { line: 1, message: "Unexpected end" } });
  assert.ok(err.includes('wd-hl-line is-err" data-line="1"'), "错误行红波浪进首渲");
  assert.ok(err.includes('wd-format" data-wd-format="1" disabled'), "非法 → format 禁用 + title 说明");
  assert.ok(err.includes(copy("w.json.fmt_dis")), "禁用说明文案(copy 键)");
  assert.ok(err.includes("wd-err-hint"), "跳转提示槽在");
  // card:错误态左边条 + meta(大小 · 键数)
  const cardErr = renderJsonEditor({ value: "{bad", field: "f", label: "f",
    error: { line: 1, message: "x" } }, { surface: "card" });
  assert.ok(cardErr.includes("is-err"), "card 错误态 .is-err(左边条 --danger)");
  assert.ok(cardErr.includes("wd-card-meta") && cardErr.includes(" B"), "card meta 带大小");
  const cardOk = renderJsonEditor({ value: '{"a": 1}', field: "f", label: "f" }, { surface: "card" });
  assert.ok(cardOk.includes("✓ JSON 合法 · 1 键") && cardOk.includes("1 键"), "card 合法胶囊 + meta 键数");
  // mount:check() 初值同步——format 禁用/着色层已填(局部刷新路径)
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  host.dataset.field = "inputsText";
  doc.body.appendChild(host);
  mountJsonEditor(host, { value: "{bad" });
  assert.ok(host.querySelector(".wd-format").disabled, "mount 初值非法 → format 禁用(check 局部刷新)");
  assert.ok(host.querySelector(".wd-hl").innerHTML.includes("wd-tk-key") ||
    host.querySelector(".wd-hl").innerHTML.includes("wd-tk-pn"), "mount 后着色层已填(syncHl)");
}

console.log("widgets.test.mjs: W6.0/W6.1 design assertions passed");

/* ── W6.2:W-table/W-kv/W-form(docs/WIDGET-DESIGN.md §3.3/§3.4/§3.5)────
   视觉结构断言(类名/数据属性)+ 新行为(内联编辑/末行回车加行/switch/
   chips/stepper/dirty 操作行/submit 校验聚焦/数组拖序)。 */

{
  // W-table tab 结构(§3.3):面板头计数/sticky 列头/类型图标/hover 槽/虚线添加
  const cols = [
    { key: "name", type: "text", label: "名称", required: true },
    { key: "n", type: "number", label: "优先级" },
    { key: "kind", type: "enum", label: "状态", options: ["待办", "进行中"] },
    { key: "due", type: "date", label: "截止" },
  ];
  const ht = renderTableEditor({ rows: [{ id: "r1", cells: { name: "故宫门票", n: 1, kind: "进行中" } }],
    selected: [], editing: "", title: "出行任务表", schema: { columns: cols } });
  assert.ok(ht.includes("wd-pane-head") && ht.includes("出行任务表 · 1 行 × 4 列"), "面板头:title · N 行 × M 列");
  assert.ok(ht.includes("wd-table-scroll"), "sticky 列头滚动容器");
  assert.ok(ht.includes('wd-th-ico'), "类型语义图标槽");
  assert.ok(ht.includes(">Aa<") && ht.includes(">#<") && ht.includes(">≡<") && ht.includes(">📅<"), "Aa/#/≡/📅 图标");
  assert.ok(ht.includes("wd-th-act"), "hover 显排序/⋯槽");
  assert.ok(ht.includes('class="wd-add" data-wd-add'), "整宽虚线添加行");
  assert.ok(ht.includes("wd-chip wd-cell"), "enum 展示态 chip");
  // 空态:表头 + 骨架行 + 主操作(§3.3/§1.5)
  const he = renderTableEditor({ rows: [], selected: [], schema: { columns: cols }, title: "" });
  assert.ok(he.includes("<thead") && he.includes("wd-skeleton") && he.includes("wd-btn-primary"),
    "空态三件套(表头/骨架/主操作)");
  assert.ok(he.includes(copy("w.table.addfirst")), "「添加第一行」copy 键");
}

{
  // W-table 行为:editing 退出(focusout 离表/Enter);DnD 视觉反馈类
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountTableEditor(host, { columns: [{ key: "name", type: "text", label: "名" }], rows: [{ name: "甲" }], title: "T" });
  const cell = new StubEl("span");
  cell.dataset.cell = `${w.state.rows[0].id}:name`;
  cell.closest = (sel) => (sel === "[data-cell]" ? cell : null);
  host.trigger("click", { target: cell });
  assert.equal(w.state.editing, `${w.state.rows[0].id}:name`, "点击进编辑");
  const ed = new StubEl("input"); // 编辑态 input(region 无 dataset,合成驱动)
  ed.closest = (sel) => (sel === "[data-cell]" ? ed : null);
  host.trigger("keydown", { target: ed, key: "Enter" });
  assert.equal(w.state.editing, "", "Enter 退出编辑(§3.3)");
  // dragstart 浮起类(视觉反馈,§3.3)
  const rowEl = new StubEl("tr");
  rowEl.dataset.row = w.state.rows[0].id;
  rowEl.closest = (sel) => (sel === "[data-row]" ? rowEl : null);
  const dt = { types: ["application/x-agent-os-widget"], setData: () => {}, getData: () => "" };
  host.trigger("dragstart", { target: rowEl, dataTransfer: dt });
  assert.ok(rowEl.classList.contains("wd-dragging"), "拖动中行浮起类(wd-dragging)");
  host.trigger("dragend", { target: rowEl });
  assert.ok(true, "dragend 清理不炸");
}

{
  // W-kv tab 结构(§3.4):列头/⚠ tooltip/虚线添加/空态
  const hk = renderKvEditor({ entries: [{ key: "Accept", value: "a" }, { key: "Accept", value: "b" }], title: "headers" });
  assert.ok(hk.includes("wd-kv-cols") && hk.includes(">KEY<") && hk.includes(">VALUE<"), "KEY/VALUE 列头");
  assert.ok(hk.includes("wd-kv-warn-ico") && hk.includes(`data-tip="${copy("w.kv.dup")}"`), "⚠ + tooltip(§3.4)");
  assert.ok((hk.match(/wd-kv-warn"/g) ?? []).length === 2, "重复 key 两行都警示");
  assert.ok(hk.includes('class="wd-add" data-kv-add'), "虚线添加行");
  const he = renderKvEditor({ entries: [] });
  assert.ok(he.includes("wd-empty-ico") && he.includes(copy("w.kv.empty")) && he.includes(copy("w.kv.addfirst")),
    "空态:图标 + 引导 + 主操作(§1.5)");
  const hc = renderKvEditor({ entries: [{ key: "a", value: "1" }, { key: "a", value: "2" }] }, { surface: "card" });
  assert.ok(hc.includes("⚠ 重复 ×1"), "card 警示徽标与 tab 同源(同 dupKeys)");
  assert.ok(hc.includes("共 2 条 · 1 处重复"), "card meta 合计行");
}

{
  // W-kv 行为:末行 value 回车自动加行(§3.4)
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountKvEditor(host, { entries: [{ key: "a", value: "1" }] });
  const val = new StubEl("input");
  val.dataset.kvValue = "0"; // 末行
  val.closest = (sel) => (sel === "[data-kv-value]" ? val : null);
  host.trigger("keydown", { target: val, key: "Enter" });
  assert.equal(w.state.entries.length, 2, "末行回车 → 自动加行");
  const val2 = new StubEl("input");
  val2.dataset.kvValue = "0"; // 加行后 0 不再是末行
  val2.closest = (sel) => (sel === "[data-kv-value]" ? val2 : null);
  host.trigger("keydown", { target: val2, key: "Enter" });
  assert.equal(w.state.entries.length, 2, "非末行回车不加行");
}

{
  // W-form tab 结构(§3.5):switch/chips/stepper/help/嵌套描述/操作行
  const schema = {
    type: "object", required: ["city"],
    properties: {
      city: { type: "string", description: "显示在列表标题" },
      n: { type: "integer", minimum: 1, maximum: 10 },
      ok: { type: "boolean" },
      kind: { type: "string", enum: ["a", "b"] },
      addr: { type: "object", description: "alertmanager 接收方", properties: { zip: { type: "string" } } },
    },
  };
  const hf = renderFormEditor({ values: { city: "北京", n: 5, ok: true, kind: "b", addr: { zip: "" } },
    errors: {}, schema, dirty: true, title: "告警规则" });
  assert.ok(hf.includes("wd-pane-head") && hf.includes("告警规则 · form"), "面板头");
  assert.ok(hf.includes('role="switch" aria-checked="true"'), "switch 开态(aria 双编码)");
  assert.ok(hf.includes("wd-switch-knob"), "switch 拨钮");
  assert.ok(hf.includes('role="radiogroup"') && hf.includes('data-on="1"'), "chips 单选组选中态");
  assert.ok(hf.includes("wd-stepwrap") && hf.includes("wd-step"), "number stepper");
  assert.ok(hf.includes("wd-help") && hf.includes("显示在列表标题"), "帮助文字 11px 弱色位");
  assert.ok(hf.includes("alertmanager 接收方"), "嵌套组描述");
  assert.ok(hf.includes("wd-form-foot") && hf.includes("data-f-submit"), "操作行:主操作在");
  assert.ok(!hf.includes("data-f-reset=\"1\" disabled"), "dirty → reset 可用");
  assert.ok(hf.includes("wd-form-dirty") && !hf.includes("wd-form-dirty\" hidden"), "dirty 提示显");
  const hfClean = renderFormEditor({ values: {}, errors: {}, schema, dirty: false });
  assert.ok(hfClean.includes('data-f-reset="1" disabled'), "干净 → reset 禁用(§3.5)");
  // card:进度条 + chips + meta
  const hc = renderFormEditor({ values: { city: "北京" }, errors: {}, schema, title: "告警规则" }, { surface: "card" });
  assert.ok(hc.includes("wd-progress") && hc.includes("wd-progress-in"), "完成度进度条(§3.5)");
  assert.ok(hc.includes("告警规则 · form") && hc.includes("schema · 5 字段 · 1 嵌套组"), "card 标题 + meta");
}

{
  // W-form 行为:switch/chips/stepper/dirty 操作行/submit 聚焦/数组拖序
  const doc = makeDocument();
  globalThis.document = doc;
  const schema = {
    type: "object", required: ["city"],
    properties: {
      city: { type: "string" }, n: { type: "integer", minimum: 1, maximum: 10 },
      ok: { type: "boolean" }, kind: { type: "string", enum: ["a", "b"] },
      tags: { type: "array", items: { type: "string" } },
    },
  };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountFormEditor(host, { schema });
  // switch 点击翻转(局部 aria,不重渲)
  const sw = new StubEl("button");
  sw.dataset.f = "ok";
  sw.closest = (sel) => (sel === ".wd-switch" ? sw : null);
  host.trigger("click", { target: sw });
  assert.equal(w.values().ok, true, "switch 点击 → set_field 翻转");
  assert.equal(sw.getAttribute("aria-checked"), "true", "aria-checked 局部翻转");
  // chips 单选
  const chip = new StubEl("button");
  chip.dataset.fChip = "kind:b";
  chip.closest = (sel) => (sel === "[data-f-chip]" ? chip : null);
  host.trigger("click", { target: chip });
  assert.equal(w.values().kind, "b", "chip 点击 → 单选");
  // stepper:+ 步进;最小值钳制
  const stepUp = new StubEl("button");
  stepUp.dataset.fStep = "n:1";
  stepUp.closest = (sel) => (sel === "[data-f-step]" ? stepUp : null);
  host.trigger("click", { target: stepUp });
  assert.equal(w.values().n, 2, "stepper + 步进");
  const stepDown = new StubEl("button");
  stepDown.dataset.fStep = "n:-1";
  stepDown.closest = (sel) => (sel === "[data-f-step]" ? stepDown : null);
  host.trigger("click", { target: stepDown });
  host.trigger("click", { target: stepDown });
  host.trigger("click", { target: stepDown });
  assert.equal(w.values().n, 1, "stepper 钳 minimum(§3.5)");
  // dirty → 操作行局部刷新(state 面;reset 钮 disabled 的 DOM 面在结构块断言)
  assert.ok(w.state.dirty, "改动 → dirty");
  // submit:非法 → 不发事件 + 错误行内;合法 → emit submit
  const submissions = [];
  w.on("submit", (p) => submissions.push(p));
  const submitBtn = new StubEl("button");
  submitBtn.closest = (sel) => (sel === "[data-f-submit]" ? submitBtn : null);
  host.trigger("click", { target: submitBtn });
  assert.equal(submissions.length, 0, "必填缺失 → submit 不发");
  assert.ok(host.innerHTML.includes("wd-field-err"), "错误行内呈现(不弹窗)");
  w.set_field("city", "北京");
  host.trigger("click", { target: submitBtn });
  assert.equal(submissions.length, 1, "校验通过 → submit 上行");
  assert.equal(submissions[0].values.city, "北京", "submit 载荷 = values");
  // 数组拖序(§3.5;§15 envelope)
  w.set_field("tags", ["a", "b", "c"]);
  w.validate(); // 触发一次 render(values 进串)
  const src = new StubEl("div");
  src.dataset.fArr = "tags";
  src.dataset.idx = "0";
  src.closest = (sel) => (sel === "[data-f-arr]" ? src : null);
  const dt = { types: ["application/x-agent-os-widget"], setData(m, v) { this._v = v; }, getData() { return this._v; } };
  host.trigger("dragstart", { target: src, dataTransfer: dt });
  const tgt = new StubEl("div");
  tgt.dataset.fArr = "tags";
  tgt.dataset.idx = "2";
  tgt.closest = (sel) => (sel === "[data-f-arr]" ? tgt : null);
  host.trigger("drop", { target: tgt, dataTransfer: dt, preventDefault: () => {} });
  assert.deepEqual(w.values().tags, ["b", "c", "a"], "数组项拖序重排");
  // reset:回骨架 + dirty 清
  w.reset();
  assert.equal(w.state.dirty, false, "reset 清 dirty");
}

console.log("widgets.test.mjs: W6.2 design assertions passed");

/* ── W6.3:W-list/W-tree/W-date(docs/WIDGET-DESIGN.md §3.6/§3.7/§3.8)── */

const { renderTreeWidget: _rtw63, renderDatePicker: _rdp63 } = await import("../js/widgets/index.js");

{
  // W-list 结构(§3.6):搜索框/命中文字色/选中 ✓/多选浮条/过滤空态
  const items = [
    { id: "a", label: "net.http_fetch", hint: "v2.1 · 工具" },
    { id: "b", label: "net.http_server", hint: "v1.4 · 工具" },
  ];
  const hl = renderSelectList({ items, selected: "a", filter: "server", focus: 0, multi: false, title: "mcp.tools" });
  assert.ok(hl.includes("wd-list-search") && hl.includes("🔍") && hl.includes("wd-list-esc"), "搜索框三件套(§3.6)");
  assert.ok(hl.includes("wd-pane-head"), "面板头");
  assert.ok(hl.includes('<mark class="wd-hit">server</mark>'), "命中子串 mark(文字色非底色)");
  assert.ok(!hl.includes("net.http_fetch"), "过滤只留命中项");
  const sel = renderSelectList({ items, selected: "a", filter: "", focus: 0, multi: false });
  assert.ok(sel.includes("wd-list-check"), "选中行右侧 ✓(§3.6)");
  const multi = renderSelectList({ items, selected: ["a", "b"], filter: "", focus: 0, multi: true });
  assert.ok(multi.includes("wd-list-bar") && multi.includes("已选 2"), "多选浮条「已选 N · 清除」");
  const no = renderSelectList({ items, selected: null, filter: "zzz", focus: 0, multi: false });
  assert.ok(no.includes("没有匹配『zzz』的项") && no.includes("data-wl-clear"), "过滤空态 + 清除搜索钮(§3.6)");
}

{
  // W-list 行为:Esc 清空 / 清除搜索钮 / 浮条清除
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountSelectList(host, { items: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }], multi: true });
  w.filter("alp");
  host.trigger("keydown", { target: host, key: "Escape" });
  assert.equal(w.state.filter, "", "Esc 清空过滤(§3.6)");
  w.select("a");
  w.select("b");
  assert.ok(host.innerHTML.includes("已选 2"), "浮条随选中出现");
  const clr = new StubEl("button");
  clr.closest = (sel) => (sel === "[data-wl-clear-sel]" ? clr : null);
  host.trigger("click", { target: clr });
  assert.deepEqual(w.state.selected, [], "浮条清除 → 清空选择");
}

{
  // W-tree 结构(§3.7):计数胶囊/父链转正色/调光过滤/面包屑 card
  const nodes = [{ name: "system.file.read" }, { name: "system.net.http_fetch" }, { name: "weather.query" }];
  const ht = _rtw63({ nodes, expanded: ["system", "system.file", "system.net", "weather"],
    selected: "system.net.http_fetch", filter: "", title: "skills" });
  assert.ok(ht.includes("ns-count"), "目录右侧叶子数胶囊(§3.7)");
  assert.ok(ht.includes('data-chain="1"'), "父链标记(data-chain → 名称转正色)");
  assert.ok(ht.includes('data-current="1"'), "当前项标记");
  const hf = _rtw63({ nodes, expanded: [], selected: null, filter: "http" });
  assert.ok(hf.includes('<mark class="wd-hit">http</mark>'), "过滤命中叶子高亮");
  assert.ok(hf.includes('data-dim="1"'), "非命中命名空间调光(data-dim)");
  assert.ok(hf.includes("system.net.http_fetch"), "调光不剪枝(非命中叶子仍在,50% 透明走 CSS)");
  const hc = _rtw63({ nodes, expanded: [], selected: "system.net.http_fetch", filter: "" }, { surface: "card" });
  assert.ok(hc.includes("system") && hc.includes(" / ") && hc.includes("http_fetch"), "card 面包屑(§3.7)");
  assert.ok(hc.includes("3 叶子") && hc.includes("命名空间"), "card 双计数徽标");
}

{
  // W-tree 行为:键盘 ←→ 折叠展开 / ↑↓ 移动 / Enter 选中(§3.7)
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountNsTreeWidget(host, { items: [{ name: "ops.janitor" }, { name: "weather.query" }, { name: "weather.forecast" }] });
  host.trigger("keydown", { target: host, key: "ArrowDown" });
  assert.equal(w.state.focusKey, "ns:ops", "↓ 聚焦首行(命名空间 ops)");
  host.trigger("keydown", { target: host, key: "ArrowDown" });
  assert.equal(w.state.focusKey, "leaf:ops.janitor", "↓ 移动到叶子");
  host.trigger("keydown", { target: host, key: "Enter" });
  assert.equal(w.state.selected, "ops.janitor", "Enter 选中焦点叶子");
  host.trigger("keydown", { target: host, key: "ArrowLeft" });
  assert.equal(w.state.focusKey, "ns:ops", "← 回父命名空间");
  host.trigger("keydown", { target: host, key: "ArrowLeft" });
  assert.ok(!w.state.expanded.includes("ops"), "← 折叠已展开命名空间");
  host.trigger("keydown", { target: host, key: "ArrowRight" });
  assert.ok(w.state.expanded.includes("ops"), "→ 展开折叠命名空间");
}

{
  // W-date 结构(§3.8):双月/星期头/邻月日/色带/端点/chips 选中/提示
  const s = { value: { start: "2026-08-01", end: "2026-08-04" }, mode: "range", inverted: false,
    open: true, cursor: { year: 2026, month: 7 }, invalid: null, notice: "", title: "travel.dates" };
  const h = _rdp63(s);
  assert.ok(h.includes("wd-months two"), "range 双月并排(§3.8)");
  assert.ok((h.match(/wd-monthblk/g) ?? []).length === 2, "双月块");
  assert.ok(h.includes("data-ym=\"2026-08\"") && h.includes("data-ym=\"2026-09\""), "8 月 + 9 月");
  assert.ok(h.includes("wd-week"), "星期头(11px 弱)");
  assert.ok(h.includes("is-out"), "非当月日 35% 透明槽");
  assert.ok(h.includes("is-range"), "区间连续色带槽");
  assert.ok((h.match(/is-end/g) ?? []).length >= 2, "两端点实底");
  assert.ok(h.includes("wd-date-ico") && h.includes("📅"), "输入框 📅 图标");
  assert.ok(h.includes("wd-date-hint") && h.includes("ISO 8601"), "手输提示");
  const wk = quickRange("week"); // 快捷命中锚定真实今天(不依赖环境日期)
  const wkDate = parseIso(wk.start);
  const hw = _rdp63({ ...s, value: { ...wk }, cursor: { year: wkDate.getFullYear(), month: wkDate.getMonth() } });
  assert.ok(hw.includes('data-wd-quick="week" data-on="1"'), "快捷 chip 选中态(值命中本周)");
  const single = _rdp63({ value: "2026-08-04", mode: "date", open: true, cursor: { year: 2026, month: 7 } });
  assert.ok(!single.includes("wd-months two") && (single.match(/wd-monthblk/g) ?? []).length === 1, "单月单历");
  const bad = _rdp63({ value: "", mode: "date", open: false, cursor: { year: 2026, month: 7 },
    invalid: { which: "value", text: "not-a-date" } });
  assert.ok(bad.includes("is-invalid") && bad.includes("无法识别"), "非法手输:红边 + 行内提示");
  const fix = _rdp63({ ...s, notice: "fix" });
  assert.ok(fix.includes("wd-date-fix") && fix.includes("已自动调整顺序"), "纠序闪提示上屏");
  // card:meta 带共 N 天
  const hc = _rdp63(s, { surface: "card" });
  assert.ok(hc.includes("range · 共 4 天"), "card meta(range · 共 N 天)");
  assert.ok(hc.includes("wd-card-all") && hc.includes(copy("w.card.go")), "card 打开入口");
}

{
  // W-date 行为:点外收层 / 快捷 chip 选中 / 日格方向键翻月接管
  const doc = makeDocument();
  globalThis.document = doc;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountDatePicker(host, { mode: "range", value: { ...quickRange("week") } });
  assert.ok(host.innerHTML.includes('data-wd-quick="week" data-on="1"'), "mount 后 chip 选中态");
  const outside = doc.createElement("div");
  doc.body.appendChild(outside);
  doc.trigger("click", { target: outside });
  assert.equal(w.state.open, false, "点外收层(§3.8)");
  w.destroy();
  doc.trigger("click", { target: outside });
  assert.equal(w.state.open, false, "destroy 后监听已摘(不重开)");
  // 日格方向键:目标格不在当前层 → 翻月接管(stub 面无 data-day= 值区域,走翻月路径)
  const host2 = doc.createElement("div");
  doc.body.appendChild(host2);
  const w2 = mountDatePicker(host2, { mode: "date", value: "2026-08-04" });
  const m0 = w2.state.cursor.month;
  const dayEl = new StubEl("button");
  dayEl.dataset.day = "2026-08-31";
  dayEl.closest = (sel) => (sel === "[data-day]" ? dayEl : null);
  host2.trigger("keydown", { target: dayEl, key: "ArrowRight" });
  assert.notEqual(w2.state.cursor.month, m0, "日格 → 越界自动翻月(§3.8 键盘走格)");
}

console.log("widgets.test.mjs: W6.3 design assertions passed");

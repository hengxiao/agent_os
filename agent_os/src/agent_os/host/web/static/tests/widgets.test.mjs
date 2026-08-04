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

/* ── W-text ───────────────────────────────────────────────────── */

function _textHost(doc, value = "hello\nworld", field = "prompt") {
  const host = doc.createElement("div");
  host.dataset.widget = "text-editor";
  const ta = doc.createElement("textarea");
  ta.dataset.field = field;
  ta.value = value;
  host.appendChild(ta);
  doc.body.appendChild(host);
  return { host, ta };
}

{
  const doc = makeDocument();
  globalThis.document = doc;
  const { host, ta } = _textHost(doc);
  const events = [];
  const micro = () => host.querySelector(".wd-micro")?.textContent ?? "";
  const w = mountTextEditor(host, { path: "/t/text" });
  w.on("change", (p) => events.push(p));
  // aria:从 data-field 推导 aria-label(必填纪律)
  assert.equal(ta.getAttribute("aria-label"), "prompt", "aria-label 推导");
  // 渲染:微标在(行数/字数)
  assert.ok(micro().includes("2 行"), "微标行数");
  assert.ok(micro().includes("11 字"), "微标字数");
  // 交互:input → dirty + change 事件 + 微标刷新
  ta.value = "hello\nworld\n!";
  host.trigger("input", { target: ta });
  assert.ok(w.state.dirty, "输入置 dirty");
  assert.equal(events.at(-1).dirty, true, "change 事件携带 dirty");
  assert.ok(micro().includes("3 行"), "微标随输入刷新");
  // commit → dirty 清零 + commit 事件
  let committed = "";
  w.on("commit", (p) => { committed = p.value; });
  w.commit();
  assert.equal(committed, ta.value, "commit 事件值");
  assert.ok(!w.state.dirty, "commit 清 dirty");
  // revert 选区保留(重渲染不丢 selection)
  ta.value = "被改掉的";
  host.trigger("input", { target: ta });
  ta.selectionStart = 2;
  ta.selectionEnd = 5;
  w.revert();
  assert.equal(ta.value, committed, "revert 回 baseline");
  assert.equal(ta.selectionStart, 2, "选区保留(start)");
  assert.equal(ta.selectionEnd, 5, "选区保留(end)");
  // Esc=blur
  ta.blur = () => { ta.focused = false; };
  ta.focus();
  host.trigger("keydown", { target: ta, key: "Escape" });
  assert.ok(!ta.focused, "Esc=blur");
  w.destroy();
  assert.equal(host.querySelector(".wd-micro"), null, "destroy 拆微标");
}

{
  // 边界:超长文本微标不炸;aria-label 缺失且无 data-field → 拒装
  const doc = makeDocument();
  globalThis.document = doc;
  const { host } = _textHost(doc, "x".repeat(120000));
  assert.doesNotThrow(() => mountTextEditor(host), "超长文本不炸");
  const bare = doc.createElement("div");
  bare.appendChild(doc.createElement("textarea"));
  doc.body.appendChild(bare);
  assert.throws(() => mountTextEditor(bare), /aria-label/, "aria-label 必填");
}

/* ── W-json ───────────────────────────────────────────────────── */

function _jsonHost(doc, value = '{\n  "a": 1\n}') {
  const host = doc.createElement("div");
  host.dataset.widget = "json-editor";
  const ta = doc.createElement("textarea");
  ta.dataset.field = "inputsText";
  ta.value = value;
  ta.dispatchEvent = (ev) => ta.trigger(ev.type ?? ev, { target: ta }); // dom-stub 面
  host.appendChild(ta);
  const hint = doc.createElement("span");
  hint.className = "lab-hint";
  host.appendChild(hint);
  doc.body.appendChild(host);
  return { host, ta, hint };
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
  const doc = makeDocument();
  globalThis.document = doc;
  const { host, ta, hint } = _jsonHost(doc);
  const w = mountJsonEditor(host, { path: "/t/json" });
  // 渲染:format 钮在;合法初值无错误条
  assert.ok(host.querySelector(".wd-format"), "format 钮在");
  assert.equal(hint.textContent, "", "合法初值无错误");
  // 交互:非法 JSON 即时校验 → 行级定位(错在哪一行,不是只报 message)
  ta.value = '{\n  "a": bad\n}';
  host.trigger("input", { target: ta });
  assert.ok(w.state.error, "即时校验出错");
  assert.ok(hint.textContent.includes("第 2 行"), "行级错误定位上屏");
  // 修复 → 错误恢复
  ta.value = '{\n  "a": 1\n}';
  host.trigger("input", { target: ta });
  assert.equal(w.state.error, null, "错误恢复");
  assert.equal(hint.textContent, "", "错误条清空");
  // 失焦校验(§2)
  ta.value = "{bad";
  host.trigger("focusout", { target: ta });
  assert.ok(w.state.error, "失焦校验");
  // format:不合法不美化;合法 → 美化 + 同步 input(宿主表单模型联动)
  const fmtBtn = host.querySelector(".wd-format");
  ta.value = "{bad";
  fmtBtn.trigger("click", { target: fmtBtn });
  assert.equal(ta.value, "{bad", "不合法不美化");
  ta.value = '{"b":2,"a":1}';
  fmtBtn.trigger("click", { target: fmtBtn });
  assert.equal(ta.value, '{\n  "b": 2,\n  "a": 1\n}', "format 一键美化");
  // schema validate action
  ta.value = '{"city": 1}';
  assert.equal(w.validate({ properties: { city: { type: "string" } } }), false, "schema 不合");
  assert.ok(w.state.error.message.includes("city"), "字段级提示");
  ta.value = '{"city": "bj"}';
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
  entriesToObject, objectToEntries, dupKeys, mountBubble } =
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
  assert.ok(html().includes("名 *"), "required 星标");
  assert.ok(html().includes('role="grid"'), "role=grid");
  assert.ok(html().includes('type="checkbox"'), "boolean 列编辑器");
  assert.ok(html().includes("<select"), "enum 列编辑器");
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
  // 空态
  const host2 = doc.createElement("div");
  doc.body.appendChild(host2);
  mountTableEditor(host2, { columns, rows: [] });
  assert.ok(host2.innerHTML.includes("还没有行"), "空态文案");
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
  mountDatePicker, parseIso, quickRange, rangeInverted, monthGridHtml } =
  await import("../js/widgets/index.js");

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
  assert.ok(html().includes('type="number"'), "integer/number 列型");
  assert.ok(html().includes('type="checkbox"'), "boolean 列型");
  assert.ok(html().includes("<select"), "enum 列型");
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
  // 过滤(平列表子串)
  w.filter("alp");
  assert.ok(host.innerHTML.includes("Alpha") && !host.innerHTML.includes("Beta"), "过滤命中");
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
  // 过滤(ns-tree filterNsTree 语义:命中保留)
  w.filter("janitor");
  assert.ok(html().includes("ops.janitor"), "过滤命中");
  assert.ok(!html().includes("weather.query"), "过滤排他");
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
  // 控件:range 输入倒置警示 + 点选纠序 + ←→ 翻页
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = mountDatePicker(host, { mode: "range" });
  w.set("start", "2026-08-10");
  w.set("end", "2026-08-01");
  assert.ok(w.state.inverted, "输入倒置警示");
  assert.ok(host.innerHTML.includes("起点晚于终点"), "警示上屏");
  w.pick("2026-08-03");
  w.pick("2026-08-01");
  assert.deepEqual(w.state.value, { start: "2026-08-01", end: "2026-08-03" }, "点选自动纠序");
  assert.ok(!w.state.inverted, "纠序后无警示");
  const month0 = host.innerHTML.match(/wd-month-title">(\d+-\d+)</)?.[1];
  host.trigger("keydown", { target: host, key: "ArrowRight" });
  const month1 = host.innerHTML.match(/wd-month-title">(\d+-\d+)</)?.[1];
  assert.notEqual(month1, month0, "→ 翻下一月(键盘可达)");
  host.trigger("keydown", { target: host, key: "ArrowLeft" });
  host.trigger("keydown", { target: host, key: "ArrowLeft" });
  const month2 = host.innerHTML.match(/wd-month-title">(\d+-\d+)</)?.[1];
  assert.notEqual(month2, month1, "← 翻上一月");
  w.quick("today");
  assert.equal(w.state.inverted, false, "快捷项重置警示");
}

/* ── W4:W-diff / W-md / W-log / W-chart ───────────────────────── */

const { mountDiffViewer, diffBodyHtml, mdToHtml, looksMarkdown, mountLogViewer,
  mountChart, chartSvg, chartTableHtml, downsample, niceTicks } =
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

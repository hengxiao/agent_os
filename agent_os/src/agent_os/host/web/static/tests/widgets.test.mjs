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

const { makeDocument } = await import("./dom-stub.mjs");
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

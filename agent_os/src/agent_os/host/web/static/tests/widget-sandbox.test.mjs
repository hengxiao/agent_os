/* 控件沙盒测试(docs/WIDGET-ARCH.md 沙盒节):
   - samples.js 覆盖 listWidgetKinds() 全部 kind(无缺漏);
   - 每个样例在 stub host 上 mount 不抛异常、产出非空渲染、可订阅全部声明事件;
   - parseSandboxUrl/buildSandboxUrl 纯函数:合法/缺省/未知 kind/坏 JSON/往返;
   - widget.html smoke:引用 widget-sandbox.js / widgets.css / 六主题 css。
   运行:node static/tests/widget-sandbox.test.mjs */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url);

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const widgets = await import("../js/widgets/index.js");
const { SAMPLES } = await import("../js/widgets/samples.js");
const { parseSandboxUrl, buildSandboxUrl } = await import("../js/widget-sandbox.js");

{
  // 覆盖:listWidgetKinds() 全 13 种,样例表无缺漏(多一个少一个都报)
  const kinds = widgets.listWidgetKinds();
  assert.equal(kinds.length, 13, "13 种控件");
  assert.deepEqual(Object.keys(SAMPLES).sort(), kinds.sort(), "样例表与注册表一一对应");
  for (const k of kinds) {
    assert.ok(SAMPLES[k].samples.length >= 2, `${k} 至少两个样例(空态/典型/边界)`);
    assert.equal(typeof widgets[SAMPLES[k].mount], "function", `${k} 的 mount 函数存在`);
  }
}

{
  // 逐样例 mount:不抛异常、产出非空渲染、声明事件可订阅、触发一个动作有事件上行
  const doc = makeDocument();
  globalThis.document = doc;
  for (const [kind, entry] of Object.entries(SAMPLES)) {
    entry.samples.forEach((sample, i) => {
      const host = doc.createElement("div");
      doc.body.appendChild(host);
      const w = widgets[entry.mount](host, { ...sample.options });
      assert.ok(w, `${kind}#${i}(${sample.name})mount 返回实例`);
      assert.ok(host.innerHTML.length > 0, `${kind}#${i}(${sample.name})产出非空渲染`);
      const def = widgets.getWidgetDef(kind);
      const seen = [];
      for (const ev of def?.events ?? []) w.on(ev, (p) => seen.push(ev));
      w.destroy?.();
    });
  }
  // 事件上行实测:select-list 选一项 → select 事件携带 id
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const w = widgets.mountSelectList(host, { items: [{ id: "a", label: "Alpha" }] });
  const got = [];
  for (const ev of widgets.getWidgetDef("select-list").events) w.on(ev, (p) => got.push([ev, p]));
  w.select("a");
  assert.ok(got.some(([ev, p]) => ev === "select" && p.id === "a"), "事件订阅面(def.events)工作");
}

{
  // parseSandboxUrl:合法 / 缺省 / 未知 kind / 坏 JSON
  const ok = parseSandboxUrl("?kind=w-text&theme=moe&sample=1", "");
  assert.equal(ok.theme, "moe");
  assert.equal(ok.sample, 1);
  assert.equal(ok.kind, null, "kind=w-text 不是注册 kind(沙盒用注册名)→ null");
  const ok2 = parseSandboxUrl("?kind=text-editor&sample=2", "");
  assert.equal(ok2.kind, "text-editor", "注册 kind 命中");
  const dft = parseSandboxUrl("", "");
  assert.equal(dft.kind, null);
  assert.equal(dft.sample, 0, "缺省 sample=0");
  assert.equal(parseSandboxUrl("?kind=evil", "").kind, null, "未知 kind → null(不崩)");
  assert.equal(parseSandboxUrl("?kind=text-editor&sample=-3", "").sample, 0, "负数 sample → 0");
  const opts = parseSandboxUrl("?kind=kv-editor", `#options=${encodeURIComponent('{"entries":[]}')}`);
  assert.deepEqual(opts.options, { entries: [] }, "options 覆盖解析");
  const bad = parseSandboxUrl("?kind=kv-editor", "#options={bad");
  assert.equal(bad.options, null);
  assert.ok(bad.optionsError, "坏 JSON → optionsError 标记(不崩)");
}

{
  // buildSandboxUrl:序列化 + 往返
  const url = buildSandboxUrl({ kind: "kv-editor", theme: "moe", sample: 1, options: { entries: [] } });
  assert.ok(url.startsWith("?"), "query 起手");
  assert.ok(url.includes("kind=kv-editor") && url.includes("theme=moe") && url.includes("sample=1"), "三参数在");
  assert.ok(url.includes("#options="), "options 进 hash");
  const back = parseSandboxUrl(url.split("#")[0], `#${url.split("#")[1]}`);
  assert.equal(back.kind, "kv-editor");
  assert.deepEqual(back.options, { entries: [] }, "往返一致");
  const bare = buildSandboxUrl({ kind: "chart", theme: "", sample: 0 });
  assert.ok(!bare.includes("#"), "无 options 不带 hash");
}

{
  // widget.html smoke:引用沙盒逻辑/widgets.css/六主题 css
  const html = readFileSync(new URL("../widget.html", import.meta.url), "utf8");
  assert.ok(html.includes("/static/js/widget-sandbox.js"), "引用沙盒逻辑");
  assert.ok(html.includes("/static/css/widgets.css"), "引用 widgets.css");
  for (const t of ["classic", "moe", "terminal", "blueprint", "ink", "pixel"]) {
    assert.ok(html.includes(`/static/css/themes/${t}.css`), `主题 css: ${t}`);
  }
}

console.log("widget-sandbox.test.mjs: all assertions passed");

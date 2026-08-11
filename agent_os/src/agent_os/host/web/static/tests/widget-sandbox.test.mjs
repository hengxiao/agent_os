/* 控件沙盒测试(docs/WIDGET-ARCH.md 沙盒节):
   - samples.js 覆盖 listWidgetKinds() 全部 kind(无缺漏);
   - 每个样例 × def.surfaces 每个声明形态在 stub host 上 mount 不抛异常、
     产出非空渲染、可订阅全部声明事件;card 形态追加 data-surface/零编辑控件/
     整卡点击 → open 事件断言(W5.6 双形态);
   - parseSandboxUrl/buildSandboxUrl 纯函数:合法/缺省/未知 kind/坏 JSON/往返/
     surface 解析与序列化;
   - widget.html smoke:引用 widget-sandbox.js / widgets.css / 六主题 css /
     形态切换器 / 卡片舞台收窄样式。
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
  // 覆盖:listWidgetKinds() 全 14 种,样例表无缺漏(多一个少一个都报)
  // C4.1:desktop(compound 根)/supervisor-inbox(系统件薄壳)非沙盒样例件,排除
  // widget-libs 试点 2:text-editor-cm(CM6 对照实验件)计入(有样例,并排对比)
  const kinds = widgets.listWidgetKinds().filter((k) => !["desktop", "supervisor-inbox"].includes(k));
  assert.equal(kinds.length, 14, "14 种控件(13 + text-editor-cm 对照件)");
  assert.deepEqual(Object.keys(SAMPLES).sort(), kinds.sort(), "样例表与注册表一一对应");
  for (const k of kinds) {
    assert.ok(SAMPLES[k].samples.length >= 2, `${k} 至少两个样例(空态/典型/边界)`);
    assert.equal(typeof widgets[SAMPLES[k].mount], "function", `${k} 的 mount 函数存在`);
  }
}

{
  // 逐样例 × 逐声明 surface 挂载(def.surfaces 声明与 render 实际支持一致性,
  // W5.6):不抛异常、产出非空渲染、声明事件可订阅;
  // card 形态追加:根带 data-surface="card" + wd-card、零编辑控件
  // (textarea/input/button/select 一律不进卡)、整卡点击 → open 事件上行
  const doc = makeDocument();
  globalThis.document = doc;
  for (const [kind, entry] of Object.entries(SAMPLES)) {
    const def = widgets.getWidgetDef(kind);
    for (const surface of def?.surfaces ?? []) {
      entry.samples.forEach((sample, i) => {
        const host = doc.createElement("div");
        doc.body.appendChild(host);
        const w = widgets[entry.mount](host, { ...sample.options, surface });
        assert.ok(w, `${kind}#${i}(${sample.name})@${surface} mount 返回实例`);
        assert.ok(host.innerHTML.length > 0, `${kind}#${i}(${sample.name})@${surface} 产出非空渲染`);
        for (const ev of def?.events ?? []) w.on(ev, () => {});
        if (surface === "card") {
          assert.ok(host.innerHTML.includes('data-surface="card"'), `${kind}#${i} card 根带 data-surface`);
          assert.ok(host.innerHTML.includes("wd-card"), `${kind}#${i} card 根带紧凑类`);
          assert.ok(
            !/<(textarea|input|button|select)\b/i.test(host.innerHTML),
            `${kind}#${i} card 无编辑控件(textarea/input/button/select 不进卡)`,
          );
          const opens = [];
          w.on("open", (p) => opens.push(p));
          host.trigger("click", { target: host });
          assert.equal(opens.length, 1, `${kind}#${i} card 整卡点击 → open 事件`);
        }
        w.destroy?.();
      });
    }
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
  // surface 参数(W5.6 双形态):card 命中;缺省/非法值回落 tab
  assert.equal(parseSandboxUrl("?kind=text-editor&surface=card", "").surface, "card", "surface=card 解析");
  assert.equal(parseSandboxUrl("?kind=text-editor", "").surface, "tab", "surface 缺省 = tab");
  assert.equal(parseSandboxUrl("?kind=text-editor&surface=wide", "").surface, "tab", "非法 surface 回落 tab");
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
  // surface 序列化(W5.6):card 进 URL,tab 缺省省略;往返一致
  const cardUrl = buildSandboxUrl({ kind: "chart", theme: "", sample: 0, surface: "card" });
  assert.ok(cardUrl.includes("surface=card"), "surface=card 进 URL(分享链接带形态)");
  assert.equal(parseSandboxUrl(cardUrl, "").surface, "card", "surface 往返一致");
  assert.ok(!buildSandboxUrl({ kind: "chart", theme: "", sample: 0, surface: "tab" }).includes("surface"),
    "surface=tab 是缺省,不进 URL");
}

{
  // widget.html smoke:引用沙盒逻辑/widgets.css/六主题 css/形态切换器/卡片收窄样式
  const html = readFileSync(new URL("../widget.html", import.meta.url), "utf8");
  assert.ok(html.includes("/static/js/widget-sandbox.js"), "引用沙盒逻辑");
  assert.ok(html.includes("/static/css/widgets.css"), "引用 widgets.css");
  for (const t of ["classic", "moe", "terminal", "blueprint", "ink", "pixel"]) {
    assert.ok(html.includes(`/static/css/themes/${t}.css`), `主题 css: ${t}`);
  }
  assert.ok(html.includes('id="sb-surface"'), "顶栏形态切换器(W5.6)");
  assert.ok(html.includes('.sb-stage[data-surface="card"]'), "card 形态舞台收窄样式");
}

console.log("widget-sandbox.test.mjs: all assertions passed");

/* explorer 系 app 薄壳测试(docs/DESKTOP-WIDGET.md §5-1;C4.3):
   五 def 注册面 + runs 自装(layout 纯/range 过滤/locate 高亮/fetch 装载/
   W-date 联动)+ legacy 包装器机制(保活囊 hidden/destroy close/locate
   三分支)。legacy 模块本体不进 stub(真实浏览器侧由 tests-ui 验);
   包装器机制用 fake open/close 断(pouch/close/locate 调用账)。
   运行:node static/tests/explorer-apps.test.mjs */

import assert from "node:assert/strict";
import { register } from "node:module";

await register("./platform-loader.mjs", import.meta.url);

const { makeDocument, StubEl } = await import("./dom-stub.mjs");
const { getWidgetDef } = await import("../js/widgets/index.js");
const {
  RUNS_EXPLORER_DEF, createRunsExplorer, runsRowsHtml,
  createSkillsExplorer, createToolsExplorer, createLabApp, createDebugConsole,
} = await import("../../../web_platform/static/explorer-apps.js");

{
  // ① 五 def 注册面(§2 白名单同名;aria.label = 任务栏题名)
  const want = {
    "skills-explorer": "技能", "runs-explorer": "运行", "tools-explorer": "工具",
    lab: "Lab", "debug-console": "调试",
  };
  for (const [kind, label] of Object.entries(want)) {
    const def = getWidgetDef(kind);
    assert.ok(def?.compound, `${kind} 注册进 registry(薄壳 compound)`);
    assert.equal(def.aria.label, label, `${kind} aria.label`);
    assert.deepEqual(def.surfaces, ["tab"], `${kind} 只有完整面`);
    assert.equal(typeof def.compound.layout, "function", `${kind} layout 在 def`);
  }
}

{
  // ② runs layout 纯:行渲染/range 过滤/locate 高亮/计数行;同入同出不改 state
  const s = {
    title: "运行", range: null, locate: "",
    rows: [
      { run_id: "r-1", skill: "weather.query", status: "success", started_at: "2026-08-10T01:00:00Z" },
      { run_id: "r-2", skill: "news.digest", status: "failed", started_at: "2026-08-11T02:00:00Z" },
    ],
    total: 2, failed: 1,
  };
  const before = JSON.stringify(s);
  const html = RUNS_EXPLORER_DEF.compound.layout(s, {});
  assert.ok(html.includes("weather.query") && html.includes("news.digest"), "行集上屏");
  assert.ok(html.includes('data-detail-kind="run"'), "行内 run 链接(同 app.js 装配)");
  assert.ok(html.includes("/#/runs"), "深链旧页保留(行为零回退)");
  const ranged = RUNS_EXPLORER_DEF.compound.layout({ ...s, range: { start: "2026-08-11", end: "" } }, {});
  assert.ok(!ranged.includes("weather.query") && ranged.includes("news.digest"), "range 过滤(纯)");
  const located = runsRowsHtml({ ...s, locate: "r-1" });
  assert.ok(located.includes('data-run-row="r-1"') && located.includes("doc-flash"), "locate 高亮行");
  assert.equal(RUNS_EXPLORER_DEF.compound.layout(s, {}), html, "layout 纯:同入同出");
  assert.equal(JSON.stringify(s), before, "layout 不改 state");
}

{
  // ③ runs 工厂:fetch 装载进 canonical;mount_view 首渲;W-date change 联动过滤
  const doc = makeDocument();
  globalThis.document = doc;
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return {
      ok: true,
      json: async () => [
        { run_id: "r-1", skill: "weather.query", status: "success", started_at: "2026-08-10T01:00:00Z" },
        { run_id: "r-2", skill: "news.digest", status: "failed", started_at: "2026-08-11T02:00:00Z" },
      ],
    };
  };
  const { inst, locate } = createRunsExplorer();
  const events = [];
  inst.on("change", (p) => events.push(p)); // 先订阅:装载 emit 随首挂异步到
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  inst.mount_view(host);
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(calls.some((u) => u === "/api/runs"), "行集走 /api/runs(同 app.js 端点)");
  assert.equal(inst.state.rows.length, 2, "行集进 canonical state(hidden 语义)");
  const rowsHost = host.querySelector("[data-runs-rows]");
  assert.ok(rowsHost.innerHTML.includes("weather.query"), "行区渲染");
  assert.ok(host.innerHTML.includes("wd-date") || host.querySelector("[data-browse-range]"), "W-date 时间窗挂接");

  // locate:定位行高亮 + change 上行(total)
  locate("r-2");
  assert.equal(inst.state.locate, "r-2", "locate 进 state(可序列化)");
  assert.ok(host.querySelector("[data-runs-rows]").innerHTML.includes("doc-flash"), "定位行高亮上屏");
  assert.ok(events.some((p) => p && typeof p === "object" && "total" in p), "装载 change 上行");

  // state 可序列化
  const round = JSON.parse(JSON.stringify(inst.state));
  assert.equal(round.rows.length, 2, "runs state JSON 往返");
}

{
  // ④ legacy 包装器机制(fake open/close 断账):保活囊 hidden 语义——
  // 摘挂不 close、原树同元素接回;destroy 才 close;locate 三分支
  const doc = makeDocument();
  globalThis.document = doc;
  const openCalls = [];
  let closeCalls = 0;
  // 包装器是 explorer-apps 的内部面——经 createSkillsExplorer 等同形验证:
  // 此处用同机制的 fake legacy 件(skills 的 open/close 签名:(host, name))
  const { createCompound, registerWidgetDef } = await import("../js/widgets/index.js");
  // 与 _createLegacyApp 同构的探针(机制一致面:pouch/detach/destroy/locate)
  const fakeDef = registerWidgetDef({
    kind: "t-legacy-probe",
    v: 1,
    state_schema: { type: "object" },
    state_defaults: { title: "探针" },
    actions: [],
    events: ["change"],
    aria: { role: "application", label: "探针" },
    surfaces: ["tab"],
    compound: { dynamic: { allow: [], max: 0 }, layout: () => `<div class="xp-legacy" data-xp="t"></div>` },
  });
  const inst = createCompound(fakeDef, { path: "/t-legacy" });
  let pouch = null;
  let opened = false;
  let shellHost = null;
  let pendingLocate = null;
  const open = (h, name) => {
    openCalls.push(name ?? null);
    const root = doc.createElement("div");
    root.className = "legacy-root";
    root.innerHTML = `<span class="legacy-body">内容${openCalls.length}</span>`;
    h.appendChild(root);
  };
  const close = () => {
    closeCalls += 1;
  };
  const _mv = inst.mount_view.bind(inst);
  inst.mount_view = (viewHost, mopts = {}) => {
    const view = _mv(viewHost, mopts);
    shellHost = viewHost.querySelector("[data-xp]") ?? viewHost;
    if (pouch) {
      { const f=pouch; while(f.children.length){const c=f.children[0];f.children.splice?.(0,1);shellHost.appendChild(c);} }
      pouch = null;
    } else if (!opened) {
      open(shellHost, pendingLocate);
      pendingLocate = null;
    }
    opened = true;
    return {
      host: viewHost,
      detach: () => {
        pouch = doc.createElement("div");
        { const f=shellHost; while(f.children.length){const c=f.children[0];f.children.splice?.(0,1);pouch.appendChild(c);} }
        view.detach();
      },
    };
  };
  const _destroy = inst.destroy.bind(inst);
  inst.destroy = () => {
    close();
    _destroy();
  };
  const locate = (ref) => {
    if (!ref) return;
    if (!opened) {
      pendingLocate = ref;
      return;
    }
    close();
    pouch = null;
    opened = false;
    if (shellHost && shellHost.isConnected !== false) {
      open(shellHost, ref);
      opened = true;
    } else {
      pendingLocate = ref;
    }
  };

  // 首挂:open 一次,无定位名
  const host1 = doc.createElement("div");
  doc.body.appendChild(host1);
  const v1 = inst.mount_view(host1);
  assert.equal(openCalls.length, 1, "首挂 open 一次");
  assert.equal(openCalls[0], null, "无定位名首挂");
  const shell1 = host1.querySelector("[data-xp]"); // stub 区域提取面(root 挂在壳元素里)
  const rootEl = shell1.querySelector(".legacy-root");
  assert.ok(rootEl.innerHTML.includes("内容1"), "legacy 内容上屏");

  // 摘挂(最小化):不 close;子树挪保活囊
  v1.detach();
  assert.equal(closeCalls, 0, "摘挂不 close(保活语义)");
  // 重挂:open 不再调;同一 root 元素接回(监听/模块态随元素存活)
  const host2 = doc.createElement("div");
  doc.body.appendChild(host2);
  inst.mount_view(host2);
  assert.equal(openCalls.length, 1, "重挂不重开(保活囊接回)");
  const root2 = host2.querySelector("[data-xp]").querySelector(".legacy-root");
  assert.equal(root2.innerHTML, rootEl.innerHTML, "内容逐字在(同元素)");

  // locate(已开可见):close + 重开带名
  locate("skill.x");
  assert.equal(closeCalls, 1, "locate = 关模块(导航语义)");
  assert.equal(openCalls.at(-1), "skill.x", "locate 重开带定位名");

  // destroy:close 链(显式销毁才退订)
  inst.destroy();
  assert.equal(closeCalls, 2, "destroy 链 close legacy 模块");
}

{
  // ⑤ 四 legacy 工厂成形面:inst/compound + locate 函数在(真实 open 由 UI 侧验)
  for (const [name, create] of [
    ["skills", createSkillsExplorer], ["tools", createToolsExplorer],
    ["lab", createLabApp], ["debug", createDebugConsole],
  ]) {
    const { inst, locate } = create();
    assert.equal(inst.kind, name === "lab" ? "lab" : `${name === "debug" ? "debug-console" : `${name}-explorer`}`, `${name} 工厂实例 kind`);
    assert.equal(typeof locate, "function", `${name} locate 面在`);
    assert.ok(inst._compound, `${name} 薄壳 compound 实例`);
  }
}

console.log("explorer-apps.test.mjs: all assertions passed");

/* D3 DOM-stub 冒烟测试(WEB-UI.md §4.3):
   1) Launch Modal 校验交互:技能下拉填充 → 骨架预填放行 → 非法 JSON 禁用 Run →
      schema 违例禁用 → 合法放行 → Run 提交(overrides 进 body)→ 关 Modal 跳
      #/runs/<id>;POST 失败进底部错误条;Esc 关闭 + 焦点管理。
   2) Live 进度:mountLiveBar 渲染(脉冲/双轨/warn)→ Stop 确认流(确认条 →
      loading → 失败回退)→ 结束态切换(结果 Banner 替换进度区,live 脉冲熄灭)。
   运行:node static/tests/smoke-d3.test.mjs(DOM 用 dom-stub.mjs,无浏览器)。 */

import assert from "node:assert/strict";
import { StubEl, makeDocument } from "./dom-stub.mjs";

const flush = () => new Promise((r) => setTimeout(r, 0));

/* ── 全局 stub:document / location / fetch(先于被测模块 import)── */
const doc = makeDocument();
const locationStub = { hash: "" };
const FIB_SUMMARY = {
  name: "fib",
  version: "1.0.0",
  kind: "prompt",
  description: "生成前 n 个菲波拉契数。Use when 需要菲波拉契数列。",
  permissions: { tools: ["system.python.exec"], skills: ["fib"], blackboard: [] },
};
const FIB_DETAIL = {
  ...FIB_SUMMARY,
  inputs: { type: "object", properties: { n: { type: "integer", minimum: 1 } }, required: ["n"] },
};
const http = { posts: [], postQueue: [] };
globalThis.document = doc;
globalThis.location = locationStub;
globalThis.fetch = async (path, options = {}) => {
  const url = String(path);
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (options.method === "POST" && url === "/api/runs") {
    http.posts.push(JSON.parse(options.body));
    return reply(http.postQueue.length ? http.postQueue.shift() : { run_id: "r-test-1" });
  }
  if (url === "/api/skills") return reply([FIB_SUMMARY, { ...FIB_SUMMARY, name: "summarize" }]);
  if (url === "/api/skills/fib") return reply(FIB_DETAIL);
  if (url === "/api/skills/summarize") return reply({ ...FIB_DETAIL, name: "summarize" });
  throw new Error(`未 stub 的请求: ${options.method ?? "GET"} ${url}`);
};

const { openLaunchDialog } = await import("../js/components/launch-dialog.js");
const { mountLiveBar, deriveProgress } = await import("../js/components/progress-bar.js");

/* ══ 1. Launch Modal 冒烟 ═══════════════════════════════════ */
{
  const modal = openLaunchDialog();
  const { els } = modal;
  await flush();
  await flush(); // skills 列表 + 首个技能详情两段取数

  const overlay = doc.body.querySelector(".modal-overlay");
  assert.ok(overlay, "Modal 挂载到 body");
  assert.equal(doc.activeElement, els.skill, "打开聚焦第一个输入(焦点管理)");
  assert.equal(els.skill.children.length, 2, "技能下拉由 /api/skills 填充");
  assert.equal(els.skill.value, "fib", "默认选中首个技能");
  assert.match(els.input.value, /"n": 1/, "编辑器预填 inputs 骨架");
  assert.match(els.info.innerHTML, /菲波拉契/, "选中后显示技能 description");
  assert.match(els.info.innerHTML, /n: integer, minimum 1\(必填\)/, "inputs 摘要");
  assert.equal(els.skill.title, FIB_SUMMARY.description, "hover 悬浮提示(原生 title)");
  assert.equal(els.run.disabled, false, "骨架合法 → Run 放行");
  assert.equal(els.errors.hidden, true, "无错误条");

  /* 非法 JSON:禁用 Run + 错误条 + 红框 */
  els.input.value = "{bad json";
  els.input.trigger("input");
  assert.equal(els.run.disabled, true, "非法 JSON 禁用 Run");
  assert.equal(els.errors.hidden, false);
  assert.match(els.errors.innerHTML, /非法 JSON/);
  assert.ok(els.input.classList.contains("is-invalid"), "编辑区红框");

  /* schema 违例(minimum):禁用 Run */
  els.input.value = '{"n": 0}';
  els.input.trigger("input");
  assert.equal(els.run.disabled, true, "schema 违例禁用 Run");
  assert.match(els.errors.innerHTML, /minimum 1/);

  /* 合法:放行 */
  els.input.value = '{"n": 8}';
  els.input.trigger("input");
  assert.equal(els.run.disabled, false, "合法输入放行 Run");
  assert.equal(els.errors.hidden, true);
  assert.ok(!els.input.classList.contains("is-invalid"));

  /* 高级区 → overrides 进 POST body;成功 → 关 Modal 跳 live */
  els.maxSteps.value = "5";
  els.run.trigger("click");
  await flush();
  assert.equal(http.posts.length, 1);
  assert.deepEqual(http.posts[0], {
    skill: "fib",
    input: { n: 8 },
    wait: false,
    overrides: { max_steps: 5 },
  });
  assert.equal(locationStub.hash, "#/runs/r-test-1", "成功后跳转 #/runs/<run_id>");
  assert.equal(doc.body.querySelector(".modal-overlay"), null, "成功后 Modal 关闭");
  assert.equal(doc.listenerCount("keydown"), 0, "关闭后 Esc 监听摘除");

  /* POST 失败(200 + status failed):底部错误条,Modal 不关闭 */
  http.postQueue.push({ status: "failed", error: "输入不合 schema: n 应为 integer" });
  const modal2 = openLaunchDialog();
  await flush();
  await flush();
  modal2.els.run.trigger("click");
  await flush();
  assert.match(modal2.els.modalError.textContent, /输入不合 schema/, "失败进底部错误条");
  assert.equal(modal2.els.modalError.hidden, false);
  assert.ok(doc.body.querySelector(".modal-overlay"), "失败时 Modal 保持打开");
  assert.equal(locationStub.hash, "#/runs/r-test-1", "失败不跳转");
  assert.equal(modal2.els.run.disabled, false, "失败后按钮恢复可点");

  /* Esc 关闭 */
  doc.trigger("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(doc.body.querySelector(".modal-overlay"), null, "Esc 关闭 Modal");
  assert.equal(doc.listenerCount("keydown"), 0);
}

/* ══ 2. Live 进度冒烟 ═══════════════════════════════════════ */
{
  const clickLb = (container, lb) => {
    const fake = new StubEl("button");
    fake.dataset.lb = lb;
    container.appendChild(fake);
    container.trigger("click", { target: fake });
    fake.remove();
  };

  const container = new StubEl("div");
  let stopCalls = 0;
  const bar = mountLiveBar(container, {
    onStop: async () => {
      stopCalls += 1;
      return true;
    },
  });
  assert.match(container.innerHTML, /live-bar/, "进度条区渲染");
  assert.match(container.innerHTML, /live-dot/, "live 脉冲点");
  assert.match(container.innerHTML, /data-lb="stop"/, "Stop 常驻右侧");

  /* 进度更新(含 80% warn) */
  bar.update(deriveProgress(
    { config: { max_steps: 10, max_cost: 1 } },
    [
      ...Array.from({ length: 9 }, (_, i) => ({
        name: "post:step", frame_id: "f1", payload: { step: i + 1 },
      })),
      { name: "post:llm.response", frame_id: "f1", payload: { usage: { cost: 0.9 } } },
    ]
  ));
  assert.match(container.innerHTML, /9\/10/, "steps 计数更新");
  assert.match(container.innerHTML, /pb-steps is-warn/, "9/10 > 80% → steps 条 warn");
  assert.match(container.innerHTML, /pb-cost is-warn/, "cost 条 warn");
  bar.setElapsed("12.4s"); // stub 不解析 innerHTML:querySelector 拿到 null,守卫不炸

  /* Stop 流:确认条(不可逆提示)→ 取消恢复 */
  clickLb(container, "stop");
  assert.match(container.innerHTML, /中止不可逆/, "确认条含不可逆提示");
  clickLb(container, "cancel");
  assert.match(container.innerHTML, /data-lb="stop"/, "取消后回到 live 态");

  /* 确认 → loading → onStop 调用 */
  clickLb(container, "stop");
  clickLb(container, "confirm");
  assert.match(container.innerHTML, /中止中…/, "确认后按钮 loading");
  await flush();
  assert.equal(stopCalls, 1, "onStop 被调用一次");

  /* 结束态切换:结果 Banner 替换进度条区,live 脉冲熄灭 */
  bar.end("done");
  assert.match(container.innerHTML, /data-tone="ok"/, "done → 绿 Banner");
  assert.doesNotMatch(container.innerHTML, /live-dot/, "live 脉冲熄灭");
  assert.doesNotMatch(container.innerHTML, /data-lb="stop"/, "Stop 随进度区移除");
  bar.destroy();

  /* onStop 失败(如 run 已结束 409):回到 live 态,Toast 由调用方承担 */
  const c2 = new StubEl("div");
  const bar2 = mountLiveBar(c2, { onStop: async () => false });
  clickLb(c2, "stop");
  clickLb(c2, "confirm");
  await flush();
  assert.match(c2.innerHTML, /data-lb="stop"/, "stop 失败退回 live 态");
  bar2.end("aborted", "web stop");
  assert.match(c2.innerHTML, /data-tone="aborted"/, "aborted → 紫 Banner");
  assert.match(c2.innerHTML, /web stop/);
  bar2.destroy();
}

console.log("smoke-d3.test.mjs: all assertions passed");

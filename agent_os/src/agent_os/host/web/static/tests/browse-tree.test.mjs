/* Skills/Tools 浏览器树化 DOM-stub 冒烟(L4.5):
   树渲染(命名空间行 + 计数 + 叶子行末段名)、点叶子进详情、搜索过滤
   (祖先链自动展开)、折叠切换(点击 ns-toggle 后子层消失/复现)。
   运行:node static/tests/browse-tree.test.mjs */

import assert from "node:assert/strict";
import { makeDocument, StubEl } from "./dom-stub.mjs";

const doc = makeDocument();
const toastStack = new StubEl("div");
toastStack.setAttribute("id", "toastStack");
doc.body.appendChild(toastStack);
doc.querySelector = (sel) => doc.body.querySelector(sel);
globalThis.document = doc;
globalThis.location = { hash: "" };

const SKILLS = [
  { name: "system.file.read", version: "1.0.0", kind: "prompt", permissions: { tools: [], skills: [] } },
  { name: "system.file.write", version: "1.0.0", kind: "prompt", permissions: { tools: [], skills: [] } },
  { name: "ops.scan.workspace", version: "0.1.0", kind: "prompt", permissions: { tools: [], skills: [] } },
  { name: "fib", version: "1.0.0", kind: "prompt", permissions: { tools: [], skills: ["fib"] } },
];
const TOOLS = [
  { name: "system.file.read", permission: "READ", description: "读" },
  { name: "system.file.write", permission: "WRITE", description: "写" },
  { name: "system.shell.exec", permission: "EXEC", description: "执行" },
  { name: "system.time.now", permission: "READ", description: "现在" },
];

globalThis.fetch = async (path) => {
  const url = String(path);
  const reply = (data) => ({ ok: true, status: 200, json: async () => data });
  if (url === "/api/skills") return reply(SKILLS.map((s) => ({ ...s, lint: [] })));
  if (url.startsWith("/api/skills/")) {
    const name = decodeURIComponent(url.slice("/api/skills/".length));
    return reply(SKILLS.find((s) => s.name === name) ?? { name, lint: [] });
  }
  if (url === "/api/tools") return reply(TOOLS);
  throw new Error(`未 stub 的请求: ${url}`);
};

const { store } = await import("../js/store.js");
const { openSkillsView, closeSkillsView } = await import("../js/components/skills-view.js");
const { openToolsView, closeToolsView } = await import("../js/components/tools-view.js");

/* ── Skills 视图:树渲染 / 点叶子 / 搜索 / 折叠 ───────────────── */
{
  const main = doc.createElement("main");
  doc.body.appendChild(main);
  store.set({ skillsets: [], skillSet: null });
  const view = openSkillsView(main);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const listHtml = () => view.els.list.innerHTML;
  assert.ok(listHtml().includes('data-ns-toggle="system.file"'), "命名空间行(单层链折叠)");
  assert.ok(listHtml().includes('data-ns-toggle="ops.scan"'), "ops.scan 折叠行");
  assert.ok(listHtml().includes(">read<"), "叶子行末段名(非全名)");
  assert.ok(listHtml().includes('title="system.file.read"'), "全名进 title");
  assert.ok(listHtml().includes("v1.0.0"), "version chip 保留");

  // 点叶子 → 选中进详情(hash 深链接形态不变)
  const leaf = new StubEl("div");
  leaf.className = "brw-item";
  leaf.dataset.name = "ops.scan.workspace";
  leaf.parentNode = view.root;
  view.root.trigger("click", { target: leaf });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(globalThis.location.hash, "#/skills/ops.scan.workspace", "叶子点击进深链接");
  assert.ok(view.els.detail.innerHTML.includes("ops.scan.workspace"), "详情渲染");

  // 搜索过滤:命中叶子保留 + 祖先链自动展开(折叠态被忽略)
  view.els.search.value = "extract";
  view.root.trigger("input", { target: view.els.search });
  // 无命中:skills 里没有 extract → 空态
  assert.ok(listHtml().includes("无匹配") || !listHtml().includes("system.file.read"), "无命中收敛");
  view.els.search.value = "scan";
  view.root.trigger("input", { target: view.els.search });
  assert.ok(listHtml().includes("ops.scan"), "命中祖先链保留");
  assert.ok(listHtml().includes('title="ops.scan.workspace"'), "命中叶子在(过滤态自动展开)");

  // 折叠切换:清空搜索 → ops.scan 默认展开 → 点击 toggle 后叶子消失,再点复现
  view.els.search.value = "";
  view.root.trigger("input", { target: view.els.search });
  assert.ok(listHtml().includes('title="ops.scan.workspace"'), "默认展开可见叶子");
  const toggle = new StubEl("button");
  toggle.dataset.nsToggle = "ops.scan";
  toggle.parentNode = view.root;
  view.root.trigger("click", { target: toggle });
  assert.ok(!listHtml().includes('title="ops.scan.workspace"'), "折叠后叶子隐藏");
  view.root.trigger("click", { target: toggle });
  assert.ok(listHtml().includes('title="ops.scan.workspace"'), "再点复现");
  closeSkillsView();
}

/* ── Tools 视图:树渲染 / perm 徽标保留 / 搜索 ─────────────────── */
{
  const main = doc.createElement("main");
  doc.body.appendChild(main);
  const view = openToolsView(main);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  const listHtml = () => view.els.list.innerHTML;
  assert.ok(listHtml().includes('data-ns-toggle="system.file"'), "命名空间行");
  assert.ok(listHtml().includes(">exec<"), "叶子末段名");
  assert.ok(listHtml().includes('data-perm="EXEC"'), "perm 徽标保留");

  view.els.search.value = "now";
  view.root.trigger("input", { target: view.els.search });
  assert.ok(listHtml().includes('title="system.time.now"'), "搜索命中(过滤态自动展开)");
  assert.ok(!listHtml().includes('title="system.file.read"'), "未命中剪枝");
  closeToolsView();
}

console.log("browse-tree.test.mjs: all assertions passed");

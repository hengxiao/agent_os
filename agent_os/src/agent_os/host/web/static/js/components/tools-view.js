/* Tools 浏览器页面(WEB-UI.md §4.7):左列表(搜索 + 权限筛选 chips:全部/READ/WRITE/
   NET/EXEC;每项 name + PermBadge)+ 右详情(头部 name + 大 PermBadge,EXEC 级附
   "危险操作,受 sidecar/人工闸门约束"提示行 / Spec description 路由规则渲染 /
   Parameters SchemaView / 执行属性 chips:timeout·idempotent·concurrency_safe·
   cacheable·untrusted_source / Examples mono 卡片 / 来源 builtin)。
   数据源:GET /api/tools(注册表全量 ToolSpec,单端点,详情从列表数据直接渲染)。
   深链接 #/tools 与 #/tools/<name>;三态齐全。**权限等级是首要视觉信息**(§4.7)。

   非目标(§4.7):不从 UI 试运行工具(写操作风险面,后续经 HumanApproval 单独立项)。

   DOM 约定同 skills-view:骨架 createElement 直持引用,内容 innerHTML,事件委托 root。 */

import { getJson } from "../api.js";
import { emptyBlock, esc, routeDescHtml } from "../util.js";
import { normalizePerm, permBadge } from "./perm-badge.js";
import { schemaView } from "./schema-view.js";

const PERMS = ["READ", "WRITE", "NET", "EXEC"];

/* ── 页面私有状态(离开页面 closeToolsView 整体复位)────────────── */

const tv = {
  main: null,
  root: null,
  els: null, // { search, chips, list, detail }
  mounted: false,
  status: "idle", // idle | loading | ready | error
  tools: [], // GET /api/tools 全量 ToolSpec 摘要
  error: null,
  selected: null,
  search: "",
  permFilter: null, // null = 全部;否则 READ|WRITE|NET|EXEC(单选)
};

/* ── 骨架 ───────────────────────────────────────────────────── */

function mountShell() {
  const doc = tv.main.ownerDocument ?? document;
  const el = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  tv.main.innerHTML = ""; // 清掉上一个页面的内容(与 workbench 的 innerHTML 覆盖同旨)
  const root = el("div", "brw tools");

  const toolbar = el("div", "brw-toolbar");
  const search = el("input", "search-input brw-search");
  search.setAttribute("type", "search");
  search.setAttribute("placeholder", "搜索工具…");
  search.setAttribute("aria-label", "搜索工具");
  search.value = tv.search;
  const chips = el("div", "chips");
  chips.setAttribute("role", "group");
  chips.setAttribute("aria-label", "权限筛选");
  const allChip = el("button", "chip", "全部");
  allChip.dataset.permFilter = "all";
  chips.appendChild(allChip);
  for (const p of PERMS) {
    const c = el("button", "chip", p);
    c.dataset.permFilter = p;
    c.dataset.perm = p; // 选中态按 --perm-* 着色(app.css)
    chips.appendChild(c);
  }
  toolbar.appendChild(search);
  toolbar.appendChild(chips);

  const cols = el("div", "brw-cols");
  const list = el("div", "brw-list");
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "工具列表");
  const detail = el("div", "brw-detail");
  cols.appendChild(list);
  cols.appendChild(detail);

  root.appendChild(toolbar);
  root.appendChild(cols);
  tv.main.appendChild(root);

  root.addEventListener("click", onClick);
  root.addEventListener("input", onInput);
  root.addEventListener("keydown", onKeydown);

  tv.root = root;
  tv.els = { search, chips, list, detail };
  renderChips();
}

/* ── 事件(委托 root)──────────────────────────────────────────── */

function onClick(e) {
  const chip = e.target.closest?.(".chip");
  if (chip && tv.els?.chips?.contains(chip)) {
    const f = chip.dataset.permFilter;
    tv.permFilter = !f || f === "all" ? null : f;
    renderChips();
    renderList();
    return;
  }
  if (e.target.closest?.("[data-tools]") && tv.root.contains(e.target.closest("[data-tools]"))) {
    loadList(); // 面板内重试
    return;
  }
  const item = e.target.closest?.(".brw-item");
  if (item && tv.root.contains(item) && item.dataset.name) {
    location.hash = `#/tools/${encodeURIComponent(item.dataset.name)}`;
    select(item.dataset.name); // 立即响应;hashchange 回来是同名下幂等
  }
}

function onInput(e) {
  if (e.target === tv.els?.search) {
    tv.search = e.target.value;
    renderList();
  }
}

function onKeydown(e) {
  if (e.key !== "Enter") return;
  const item = e.target.closest?.(".brw-item");
  if (item && tv.root.contains(item) && item.dataset.name) {
    location.hash = `#/tools/${encodeURIComponent(item.dataset.name)}`;
    select(item.dataset.name);
  }
}

/* ── 取数:GET /api/tools(全量 ToolSpec,详情直接从中渲染)────────── */

async function loadList() {
  tv.status = "loading";
  tv.error = null;
  renderList();
  try {
    const tools = await getJson("/api/tools");
    if (!tv.mounted) return;
    tv.tools = Array.isArray(tools) ? tools : [];
    tv.status = "ready";
  } catch (err) {
    if (!tv.mounted) return;
    tv.tools = [];
    tv.error = err.message ?? "加载失败";
    tv.status = "error";
  }
  renderList();
  renderDetail(); // 详情数据源同列表:到位后重渲选中项
}

/* ── 渲染:筛选 chips / 左列表(三态)────────────────────────────── */

function renderChips() {
  const box = tv.els?.chips;
  if (!box) return;
  for (const c of box.children) {
    const f = c.dataset.permFilter;
    c.classList.toggle("is-active", f === "all" ? tv.permFilter === null : tv.permFilter === f);
  }
}

const skeletonRows = (n) =>
  Array.from({ length: n }, () =>
    `<div class="skeleton-row">` +
    `<span class="skeleton skeleton-line w-70"></span>` +
    `<span class="skeleton skeleton-line w-40"></span>` +
    `</div>`).join("");

function visibleTools() {
  const q = tv.search.trim().toLowerCase();
  return tv.tools.filter((t) => {
    if (tv.permFilter && normalizePerm(t.permission) !== tv.permFilter) return false;
    if (q && !(t.name || "").toLowerCase().includes(q)) return false;
    return true;
  });
}

function toolItemHtml(t) {
  const sel = t.name === tv.selected;
  return (
    `<div class="brw-item" data-name="${esc(t.name)}" role="option" tabindex="0"` +
    ` aria-selected="${sel}" title="${esc(t.name)}">` +
    `<div class="brw-item-row">` +
    `<span class="brw-item-name mono">${esc(t.name)}</span>` +
    permBadge(t.permission) +
    `</div>` +
    `</div>`
  );
}

function renderList() {
  const box = tv.els?.list;
  if (!box) return;
  if (tv.status === "loading" || tv.status === "idle") {
    box.innerHTML = skeletonRows(6);
    return;
  }
  if (tv.status === "error") {
    box.innerHTML =
      `<div class="panel-error">` +
      `<span class="error-msg">加载工具列表失败:${esc(tv.error)}</span>` +
      `<button class="btn" data-tools="retry">重试</button>` +
      `</div>`;
    return;
  }
  const tools = visibleTools();
  if (!tools.length) {
    box.innerHTML =
      tv.tools.length === 0
        ? emptyBlock("注册表为空", "内核未装配任何工具(检查 [tools] 配置)")
        : emptyBlock("无匹配的工具", "调整搜索关键词或权限筛选");
    return;
  }
  box.innerHTML = tools.map(toolItemHtml).join("");
}

/* ── 渲染:右详情(§4.7 分区)──────────────────────────────────────── */

const dsec = (title, bodyHtml) =>
  `<section class="dsec"><h3 class="dsec-title">${esc(title)}</h3>${bodyHtml}</section>`;

const kv = (label, valueHtml) =>
  `<div class="kv"><span class="kv-label">${esc(label)}</span>` +
  `<span class="kv-value">${valueHtml}</span></div>`;

/* 执行属性 chips(§4.7):timeout/idempotent/concurrency_safe/cacheable/untrusted_source */
function propChips(t) {
  const props = [
    ["timeout", `${t.timeout ?? "—"}s`],
    ["idempotent", Boolean(t.idempotent)],
    ["concurrency_safe", Boolean(t.concurrency_safe)],
    ["cacheable", Boolean(t.cacheable)],
    ["untrusted_source", Boolean(t.untrusted_source)],
  ];
  return (
    `<div class="prop-chips">` +
    props
      .map(
        ([k, v]) =>
          `<span class="prop-chip" title="${esc(k)}">${esc(k)}: <b>${esc(String(v))}</b></span>`,
      )
      .join("") +
    `</div>`
  );
}

function detailHtml(t) {
  const perm = normalizePerm(t.permission);
  const head =
    `<div class="brw-detail-head">` +
    `<span class="brw-title mono">${esc(t.name)}</span>` +
    permBadge(perm, { large: true }) +
    `</div>` +
    // §4.7:EXEC 级附提示行(内核三层权限模型在 UI 上的投影)
    (perm === "EXEC"
      ? `<div class="exec-warning">⚠ 危险操作,受 sidecar/人工闸门约束</div>`
      : "");

  const spec = dsec("Spec", `<div class="desc">${routeDescHtml(t.description)}</div>`);
  const params = dsec("Parameters", schemaView(t.parameters));
  const props = dsec("执行属性", propChips(t));

  const examples = Array.isArray(t.examples) && t.examples.length
    ? dsec(
        "Examples",
        t.examples
          .map((ex) => `<pre class="example-card" tabindex="0">${esc(JSON.stringify(ex, null, 2))}</pre>`)
          .join(""),
      )
    : "";

  const source = dsec("来源", kv("registry", "builtin"));

  return head + spec + params + props + examples + source;
}

function renderDetail() {
  const box = tv.els?.detail;
  if (!box) return;
  const name = tv.selected;
  if (!name) {
    box.innerHTML = emptyBlock("选择一个工具", "从左侧列表选择工具查看 spec 与权限等级");
    return;
  }
  if (tv.status === "loading" || tv.status === "idle") {
    box.innerHTML = `<div class="skeleton-pad">${skeletonRows(8)}</div>`;
    return;
  }
  if (tv.status === "error") {
    box.innerHTML =
      `<div class="panel-error">` +
      `<span class="error-msg">加载工具列表失败:${esc(tv.error)}</span>` +
      `<button class="btn" data-tools="retry">重试</button>` +
      `</div>`;
    return;
  }
  const tool = tv.tools.find((t) => t.name === name);
  if (!tool) {
    box.innerHTML = emptyBlock(`工具 ${name} 不存在`, "注册表中没有该工具(可能已变更)");
    return;
  }
  box.innerHTML = detailHtml(tool);
}

/* ── 选中(路由 itemName 与列表点击共用;同名下幂等)────────────────── */

function select(name) {
  if (tv.selected === name) return;
  tv.selected = name;
  renderList(); // aria-selected 跟随
  renderDetail();
}

/* ── 入口(app.js 路由分发;幂等)────────────────────────────────── */

export function openToolsView(main, name = null) {
  tv.main = main;
  if (!tv.mounted) {
    tv.mounted = true;
    mountShell();
    renderList();
    renderDetail();
    loadList();
  }
  select(name);
  return { root: tv.root, els: tv.els }; // 句柄供测试/调用方探查(app.js 忽略)
}

export function closeToolsView() {
  tv.main = null;
  tv.root = null; // 随 #main 内容替换一并丢弃,监听器附着在 root 上随之失效
  tv.els = null;
  tv.mounted = false;
  tv.status = "idle";
  tv.tools = [];
  tv.error = null;
  tv.selected = null;
  tv.search = "";
  tv.permFilter = null;
}

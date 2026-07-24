/* Skills 浏览器页面(WEB-UI.md §4.6):左列表(搜索过滤 / name+version+kind chip /
   循环依赖 --warn 警示)+ 右详情(Meta / description 路由规则渲染 / Inputs·Outputs
   SchemaView / Permissions 三组(skills 可点击跳链)/ Model & Limits / Prompt 模板
   mono 只读+复制)+ lint 警告横幅(--warn,技能作者自检入口)+ 操作(↻ Reload →
   Toast reloaded true/false;Run ▶ → 带该技能预填打开 Launch Modal)。
   深链接 #/skills 与 #/skills/<name>;三态(loading/empty"在 agent-os.toml 配置
   skills.path"/error+面板内重试)齐全。

   纯函数(不碰 DOM,node 单测可载):
     findCycles(manifests)  permissions.skills 依赖图 → 循环依赖技能名 Set
                            (Tarjan SCC ≥2;**自引用是合法递归,不算循环**)

   DOM 约定(与 workbench 不同,为 DOM-stub 冒烟可测):骨架用 createElement 搭
   (引用直持,不经 querySelector),内容渲染走 innerHTML,事件全部委托 root。 */

import { getJson, postJson } from "../api.js";
import { COPY_SVG, emptyBlock, esc, routeDescHtml, toast } from "../util.js";
import { banner } from "./banner.js";
import { openLaunchDialog } from "./launch-dialog.js";
import { schemaView } from "./schema-view.js";

/* ── findCycles:permissions.skills 依赖图的循环依赖(Tarjan SCC)────
   判定口径(§4.6 + D4 任务书):
   - 自引用(fib 的 skills:[fib])是**合法递归**,先剔边,不算循环;
   - 经他人回边(a→b→a)才是循环,环上全部技能都标注;
   - 指向未知技能的边不参与(无出边,不可能成环)。 */
export function findCycles(manifests) {
  const names = new Set((manifests ?? []).map((m) => m?.name).filter(Boolean));
  const adj = new Map();
  for (const m of manifests ?? []) {
    if (!m?.name) continue;
    const deps = (m?.permissions?.skills ?? []).filter((d) => d !== m.name && names.has(d));
    adj.set(m.name, deps);
  }
  const index = new Map();
  const low = new Map();
  const onStack = new Set();
  const stack = [];
  const cyclic = new Set();
  let counter = 0;
  const strongconnect = (v) => {
    index.set(v, counter);
    low.set(v, counter);
    counter += 1;
    stack.push(v);
    onStack.add(v);
    for (const w of adj.get(v) ?? []) {
      if (!index.has(w)) {
        strongconnect(w);
        low.set(v, Math.min(low.get(v), low.get(w)));
      } else if (onStack.has(w)) {
        low.set(v, Math.min(low.get(v), index.get(w)));
      }
    }
    if (low.get(v) === index.get(v)) {
      const scc = [];
      let w;
      do {
        w = stack.pop();
        onStack.delete(w);
        scc.push(w);
      } while (w !== v);
      if (scc.length >= 2) scc.forEach((n) => cyclic.add(n)); // SCC≥2 = 循环
    }
  };
  for (const v of adj.keys()) if (!index.has(v)) strongconnect(v);
  return cyclic;
}

/* ── 页面私有状态(离开页面 closeSkillsView 整体复位)────────────── */

const sv = {
  main: null,
  root: null, // .brw 根(事件委托挂点)
  els: null, // { search, reload, list, detail } 结构引用(createElement 直持)
  mounted: false,
  status: "idle", // idle | loading | ready | error
  skills: [], // GET /api/skills 摘要列表
  error: null,
  cycles: new Set(), // findCycles 结果(列表项 --warn 标注)
  selected: null, // 当前选中技能名(路由 itemName)
  details: new Map(), // name -> { status, data?, error? }(详情缓存)
  search: "",
  reloadBusy: false,
};

/* ── 骨架:createElement 搭结构(引用直持),内容区留空 ────────────── */

function mountShell() {
  const doc = sv.main.ownerDocument ?? document;
  const el = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  sv.main.innerHTML = ""; // 清掉上一个页面的内容(与 workbench 的 innerHTML 覆盖同旨)
  const root = el("div", "brw skls");

  const toolbar = el("div", "brw-toolbar");
  const search = el("input", "search-input brw-search");
  search.setAttribute("type", "search");
  search.setAttribute("placeholder", "搜索技能…");
  search.setAttribute("aria-label", "搜索技能");
  search.value = sv.search;
  const spacer = el("span", "spacer");
  const reload = el("button", "btn", "↻ Reload");
  reload.dataset.skls = "reload";
  reload.title = "热重载 skills 文件(只影响后续新建的 run)";
  toolbar.appendChild(search);
  toolbar.appendChild(spacer);
  toolbar.appendChild(reload);

  const cols = el("div", "brw-cols");
  const list = el("div", "brw-list");
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "技能列表");
  const detail = el("div", "brw-detail");
  cols.appendChild(list);
  cols.appendChild(detail);

  root.appendChild(toolbar);
  root.appendChild(cols);
  sv.main.appendChild(root);

  root.addEventListener("click", onClick);
  root.addEventListener("input", onInput);
  root.addEventListener("keydown", onKeydown);

  sv.root = root;
  sv.els = { search, reload, list, detail };
}

/* ── 事件(委托 root;不用 data-action——app.js 全局代理只认它自己的动作)── */

function onClick(e) {
  const actBtn = e.target.closest?.("[data-skls]");
  if (actBtn && sv.root.contains(actBtn)) {
    const act = actBtn.dataset.skls;
    if (act === "reload") doReload();
    else if (act === "retry-list") loadList();
    else if (act === "retry-detail") sv.selected && ensureDetail(sv.selected, true);
    else if (act === "run") openLaunchDialog({ presetSkill: sv.selected });
    return;
  }
  const item = e.target.closest?.(".brw-item");
  if (item && sv.root.contains(item) && item.dataset.name) {
    location.hash = `#/skills/${encodeURIComponent(item.dataset.name)}`;
    select(item.dataset.name); // 立即响应;hashchange 回来是同名下幂等
  }
}

function onInput(e) {
  if (e.target === sv.els?.search) {
    sv.search = e.target.value;
    renderList();
  }
}

function onKeydown(e) {
  if (e.key !== "Enter") return;
  const item = e.target.closest?.(".brw-item");
  if (item && sv.root.contains(item) && item.dataset.name) {
    location.hash = `#/skills/${encodeURIComponent(item.dataset.name)}`;
    select(item.dataset.name);
  }
}

/* ── 取数:列表(摘要 + 循环标注)───────────────────────────────── */

async function loadList() {
  sv.status = "loading";
  sv.error = null;
  renderList();
  try {
    const skills = await getJson("/api/skills");
    if (!sv.mounted) return;
    sv.skills = Array.isArray(skills) ? skills : [];
    sv.cycles = findCycles(sv.skills);
    sv.status = "ready";
  } catch (err) {
    if (!sv.mounted) return;
    sv.skills = [];
    sv.error = err.message ?? "加载失败";
    sv.status = "error";
  }
  renderList();
}

/* ── 取数:详情(全量 manifest,含 prompt 与 lint;Map 缓存)────────── */

async function ensureDetail(name, force = false) {
  const cached = sv.details.get(name);
  if (!force && cached && cached.status !== "error") return;
  sv.details.set(name, { status: "loading" });
  if (sv.selected === name) renderDetail();
  try {
    const data = await getJson(`/api/skills/${encodeURIComponent(name)}`);
    sv.details.set(name, { status: "ready", data });
  } catch (err) {
    sv.details.set(name, { status: "error", error: err.message ?? "加载失败" });
  }
  if (sv.mounted && sv.selected === name) renderDetail();
}

/* ── 操作:↻ Reload(按钮 loading → Toast reloaded true/false)─────── */

function renderReload() {
  const btn = sv.els?.reload;
  if (!btn) return;
  btn.disabled = sv.reloadBusy;
  btn.textContent = sv.reloadBusy ? "重载中…" : "↻ Reload";
}

async function doReload() {
  if (sv.reloadBusy) return;
  sv.reloadBusy = true;
  renderReload();
  try {
    const res = await postJson("/api/skills/reload");
    const reloaded = Boolean(res?.reloaded);
    toast(
      reloaded ? "skills 已重载(影响后续新建的 run)" : "reloaded:false(mtime 未变,跳过重载)",
      reloaded ? "success" : "info",
    );
    sv.details.clear(); // manifest 可能已变:详情缓存整体失效
    await loadList();
    if (sv.mounted && sv.selected) ensureDetail(sv.selected);
  } catch (err) {
    toast(`Reload 失败:${err.message}`, "error");
  } finally {
    sv.reloadBusy = false;
    if (sv.mounted) renderReload();
  }
}

/* ── 渲染:左列表(三态 + 搜索过滤 + 循环 --warn 警示)──────────────── */

const skeletonRows = (n) =>
  Array.from({ length: n }, () =>
    `<div class="skeleton-row">` +
    `<span class="skeleton skeleton-line w-70"></span>` +
    `<span class="skeleton skeleton-line w-40"></span>` +
    `</div>`).join("");

function visibleSkills() {
  const q = sv.search.trim().toLowerCase();
  return sv.skills.filter((s) => !q || (s.name || "").toLowerCase().includes(q));
}

function skillItemHtml(s) {
  const sel = s.name === sv.selected;
  const cyclic = sv.cycles.has(s.name);
  return (
    `<div class="brw-item" data-name="${esc(s.name)}" role="option" tabindex="0"` +
    ` aria-selected="${sel}" title="${esc(s.name)}">` +
    `<div class="brw-item-row">` +
    `<span class="brw-item-name">${esc(s.name)}</span>` +
    (cyclic
      ? `<span class="cycle-warn" title="循环依赖:permissions.skills 形成环" aria-label="循环依赖">⚠</span>`
      : "") +
    `</div>` +
    `<div class="brw-item-meta">` +
    `<span class="mono">v${esc(s.version || "—")}</span>` +
    (s.kind ? `<span class="kind-chip">${esc(s.kind)}</span>` : "") +
    `</div>` +
    `</div>`
  );
}

function renderList() {
  const box = sv.els?.list;
  if (!box) return;
  if (sv.status === "loading" || sv.status === "idle") {
    box.innerHTML = skeletonRows(6);
    return;
  }
  if (sv.status === "error") {
    box.innerHTML =
      `<div class="panel-error">` +
      `<span class="error-msg">加载技能列表失败:${esc(sv.error)}</span>` +
      `<button class="btn" data-skls="retry-list">重试</button>` +
      `</div>`;
    return;
  }
  const skills = visibleSkills();
  if (!skills.length) {
    box.innerHTML =
      sv.skills.length === 0
        ? emptyBlock("还没有技能", "在 agent-os.toml 配置 skills.path")
        : emptyBlock("无匹配的技能", "调整搜索关键词");
    return;
  }
  box.innerHTML = skills.map(skillItemHtml).join("");
}

/* ── 渲染:右详情(§4.6 分区)──────────────────────────────────────── */

const dsec = (title, bodyHtml) =>
  `<section class="dsec"><h3 class="dsec-title">${esc(title)}</h3>${bodyHtml}</section>`;

const kv = (label, valueHtml) =>
  `<div class="kv"><span class="kv-label">${esc(label)}</span>` +
  `<span class="kv-value">${valueHtml}</span></div>`;

const chipList = (names) =>
  (names ?? []).length
    ? names.map((n) => `<span class="kind-chip mono">${esc(n)}</span>`).join(" ")
    : "—";

const skillLinks = (names) =>
  (names ?? []).length
    ? names
        .map(
          (n) =>
            `<a class="skill-link mono" href="#/skills/${encodeURIComponent(n)}"` +
            ` title="跳到技能 ${esc(n)}">${esc(n)}</a>`,
        )
        .join(" ")
    : "—";

function detailHtml(d) {
  const perms = d.permissions ?? {};
  const lint = Array.isArray(d.lint) ? d.lint : [];
  const manifestJson = esc(JSON.stringify(d, null, 2));

  const head =
    `<div class="brw-detail-head">` +
    `<span class="brw-title">${esc(d.name)}</span>` +
    (d.kind ? `<span class="kind-chip">${esc(d.kind)}</span>` : "") +
    `<span class="kind-chip mono">v${esc(d.version || "—")}</span>` +
    `<span class="spacer"></span>` +
    `<button class="copy-btn" data-action="copy" data-copy="${manifestJson}"` +
    ` data-copy-label="manifest 已复制" title="复制 manifest JSON" aria-label="复制 manifest JSON">${COPY_SVG}</button>` +
    `<button class="btn btn-primary" data-skls="run">Run ▶</button>` +
    `</div>`;

  // §4.6:description lint 结果在详情顶部 --warn 横幅(技能作者自检入口)
  const lintBanner = lint.length
    ? banner(
        "warn",
        `description lint:${lint.length} 条自检警告`,
        `<ul class="lint-list">${lint.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`,
      )
    : "";

  const meta = dsec(
    "Meta",
    kv("kind", esc(d.kind ?? "—")) +
      kv("version", esc(d.version || "—")) +
      kv("namespace", "local(LocalFile registry)") +
      (d.entry ? kv("entry", `<span class="mono">${esc(d.entry)}</span>`) : "") +
      (d.handler ? kv("handler", `<span class="mono">${esc(d.handler)}</span>`) : ""),
  );

  const desc = dsec("Description(路由规则)", `<div class="desc">${routeDescHtml(d.description)}</div>`);

  const inputs = dsec("Inputs", schemaView(d.inputs));
  const outputs = dsec("Outputs", schemaView(d.outputs));

  const permissions = dsec(
    "Permissions",
    kv("tools", chipList(perms.tools)) +
      kv("skills", skillLinks(perms.skills)) + // §4.6:可点击链接跳 #/skills/<name>
      kv("blackboard", chipList(perms.blackboard)),
  );

  const modelRows =
    (d.model?.prefer?.length ? kv("prefer", `<span class="mono">${esc(d.model.prefer.join(", "))}</span>`) : "") +
    (d.model?.temperature != null ? kv("temperature", esc(String(d.model.temperature))) : "") +
    (d.limits?.max_steps != null ? kv("max_steps", esc(String(d.limits.max_steps))) : "") +
    (d.limits?.timeout != null ? kv("timeout", `${esc(String(d.limits.timeout))}s`) : "") +
    (d.limits?.retry != null ? kv("retry", esc(String(d.limits.retry))) : "");
  const model = dsec("Model & Limits", modelRows || `<div class="fg-2">(未配置,继承全局)</div>`);

  // §4.6:Prompt 模板 mono 只读 + 复制(prompt 技能才带;code 技能见 Meta 的 entry/handler)
  const prompt = d.prompt
    ? dsec(
        "Prompt 模板",
        `<div class="prompt-wrap">` +
          `<pre class="prompt-block" tabindex="0">${esc(d.prompt)}</pre>` +
          `<button class="copy-btn prompt-copy" data-action="copy" data-copy="${esc(d.prompt)}"` +
          ` data-copy-label="Prompt 已复制" title="复制 Prompt 模板" aria-label="复制 Prompt 模板">${COPY_SVG}</button>` +
          `</div>`,
      )
    : "";

  return head + lintBanner + meta + desc + inputs + outputs + permissions + model + prompt;
}

function renderDetail() {
  const box = sv.els?.detail;
  if (!box) return;
  const name = sv.selected;
  if (!name) {
    box.innerHTML = emptyBlock("选择一个技能", "从左侧列表选择技能查看详情,或点 Run ▶ 发起运行");
    return;
  }
  const cached = sv.details.get(name);
  if (!cached || cached.status === "loading") {
    box.innerHTML = `<div class="skeleton-pad">${skeletonRows(8)}</div>`;
    return;
  }
  if (cached.status === "error") {
    box.innerHTML =
      `<div class="panel-error">` +
      `<span class="error-msg">加载技能 ${esc(name)} 失败:${esc(cached.error)}</span>` +
      `<button class="btn" data-skls="retry-detail">重试</button>` +
      `</div>`;
    return;
  }
  box.innerHTML = detailHtml(cached.data);
}

/* ── 选中(路由 itemName 与列表点击共用;同名下幂等)────────────────── */

function select(name) {
  if (sv.selected === name) return;
  sv.selected = name;
  renderList(); // aria-selected 跟随
  renderDetail();
  if (name) ensureDetail(name);
}

/* ── 入口(app.js 路由分发;幂等)────────────────────────────────── */

export function openSkillsView(main, name = null) {
  sv.main = main;
  if (!sv.mounted) {
    sv.mounted = true;
    mountShell();
    renderReload();
    renderList();
    renderDetail();
    loadList();
  }
  select(name);
  return { root: sv.root, els: sv.els }; // 句柄供测试/调用方探查(app.js 忽略)
}

export function closeSkillsView() {
  sv.main = null;
  sv.root = null; // 随 #main 内容替换一并丢弃,监听器附着在 root 上随之失效
  sv.els = null;
  sv.mounted = false;
  sv.status = "idle";
  sv.skills = [];
  sv.error = null;
  sv.cycles = new Set();
  sv.selected = null;
  sv.details = new Map();
  sv.search = "";
  sv.reloadBusy = false;
}

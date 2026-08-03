/* 主题系统 T1(docs/DEBUG-UI-THEMES.md §2):三层契约的注册表与切换层。
   职责:
   · 契约 token 清单(CONTRACT_TOKENS,§2.1 全集;css/themes/<id>.css 必须全量定义);
   · 声明式主题注册表 {id, name, css, copy, motion, mascot, scope}(§2.5);
   · 注册时校验:主题 css 已在文档加载且契约变量完整,缺变量/未加载 → 不注册 + console.warn
     (防半成品主题上线;node 测试环境无 styleSheets 时跳过校验,完整性由
      tests/themes-contract.test.mjs 直接解析 css 断言);
   · 切换与持久化(§2.5):applyTheme 切 <html data-theme> + localStorage(agent-os.theme)
     + hash ?theme= 同步;启动按 URL > localStorage > classic 解析;
   · scope 回落:主题声明验收过的页面(scope),未验收页面强制回落 classic——
     宁可回落,不半成品(§5)。
   组件零分支:只消费语义 token / copy(key) / mascot 层,差异全部收敛在本层。 */

import { COPY as classicCopy } from "./copy/classic.js";
import { COPY as moeCopy } from "./copy/moe.js";
import { COPY as terminalCopy } from "./copy/terminal.js";
import { COPY as blueprintCopy } from "./copy/blueprint.js";
import { COPY as inkCopy } from "./copy/ink.js";
import { COPY as pixelCopy } from "./copy/pixel.js";

export const DEFAULT_THEME = "classic";
export const THEME_STORAGE_KEY = "agent-os.theme";

/* 契约 token 清单(§2.1 全集 = docs/WEB-UI.md §3 既有语义变量,一个不能少) */
export const CONTRACT_TOKENS = [
  // 基底
  "--bg-0", "--bg-1", "--bg-2", "--bg-3", "--line", "--line-strong",
  "--fg-0", "--fg-1", "--fg-2",
  // 状态
  "--ok", "--warn", "--danger", "--aborted", "--live",
  // 信号
  "--sig-llm", "--sig-tool", "--sig-sidecar", "--sig-compress", "--sig-budget", "--sig-frame",
  // 权限
  "--perm-read", "--perm-write", "--perm-net", "--perm-exec",
  // 排版
  "--font-ui", "--font-mono",
  "--text-2xs", "--text-xs", "--text-sm", "--text-md", "--text-lg", "--text-xl",
  "--s1", "--s1-5", "--s2", "--s3", "--s4", "--s6", "--s8",
  "--r-sm", "--r-conn", "--r-md", "--r-lg",
];

/* 文案 key 契约(§2.2;以 classic 表为准,各主题表必须同 key 覆盖) */
export const COPY_KEYS = Object.keys(classicCopy);

/* ── 声明式主题清单:新增主题 = 加一个 css/copy + 一行注册 ──────────────
   motion(§2.3):具名动效 → 档位 full/subtle/instant;T1 不动效,全 subtle。
   scope(§5):"app-wide" 或已验收页面名数组(= body[data-route] 值)。 */
const REGISTRY = [
  {
    id: "classic",
    name: "Classic · 严肃工程",
    css: "css/themes/classic.css",
    copy: classicCopy,
    motion: { "bp-hit": "subtle", step: "subtle", resume: "subtle", "run-done": "subtle", intervene: "subtle" },
    mascot: null,
    scope: "app-wide",
  },
  {
    id: "moe",
    name: "Moe · 萌系",
    css: "css/themes/moe.css",
    copy: moeCopy,
    motion: { "bp-hit": "subtle", step: "subtle", resume: "subtle", "run-done": "subtle", intervene: "subtle" },
    mascot: "mochi",
    scope: "app-wide", // T1.1 全站开放(组件零分支:全站消费同一语义 token,观感走查随用随修)
  },
  /* T2/T3 四主题(§3.3-3.6):同 moe 的 T1.1 依据,token 全集过契约即 app-wide */
  {
    id: "terminal",
    name: "Terminal · 终端极客",
    css: "css/themes/terminal.css",
    copy: terminalCopy,
    motion: { "bp-hit": "subtle", step: "subtle", resume: "subtle", "run-done": "subtle", intervene: "subtle" },
    mascot: null,
    scope: "app-wide",
  },
  {
    id: "blueprint",
    name: "Blueprint · 蓝图",
    css: "css/themes/blueprint.css",
    copy: blueprintCopy,
    motion: { "bp-hit": "subtle", step: "subtle", resume: "subtle", "run-done": "subtle", intervene: "subtle" },
    mascot: null,
    scope: "app-wide",
  },
  {
    id: "ink",
    name: "Ink · 水墨",
    css: "css/themes/ink.css",
    copy: inkCopy,
    motion: { "bp-hit": "subtle", step: "subtle", resume: "subtle", "run-done": "subtle", intervene: "subtle" },
    mascot: null,
    scope: "app-wide",
  },
  {
    id: "pixel",
    name: "Pixel · 像素复古",
    css: "css/themes/pixel.css",
    copy: pixelCopy,
    motion: { "bp-hit": "subtle", step: "subtle", resume: "subtle", "run-done": "subtle", intervene: "subtle" },
    mascot: "sprite8", // mascot 抽象第二实例(§3.6:与 mochi 共用 MascotLayer 接口)
    scope: "app-wide",
  },
];

/* ── 注册时校验:从已加载样式表提取 [data-theme="<id>"] 规则的声明表 ──
   返回:null = 无法校验(node/无 styleSheets API);{} = 表已加载但无主题规则。 */
function sheetTokens(theme) {
  if (typeof document === "undefined" || !document.styleSheets) return null;
  const file = theme.css.split("/").pop();
  for (const sheet of document.styleSheets) {
    const href = sheet.href ?? "";
    if (!href.endsWith(file)) continue;
    let rules = [];
    try {
      rules = [...(sheet.cssRules ?? [])];
    } catch {
      continue; // 跨域等不可读表:跳过(本应用不存在,防御)
    }
    const out = {};
    for (const rule of rules) {
      if (rule.selectorText !== `[data-theme="${theme.id}"]`) continue;
      const text = rule.style?.cssText ?? "";
      for (const m of text.matchAll(/(--[\w-]+)\s*:\s*([^;]+)/g)) out[m[1]] = m[2].trim();
    }
    return out;
  }
  return undefined; // 样式表清单可枚举,但该主题 css 未加载
}

const THEMES = new Map();

export function registerTheme(theme) {
  const tokens = sheetTokens(theme);
  if (tokens === undefined) {
    console.warn(`[themes] 主题 ${theme.id} 的 css 未加载(${theme.css}),不注册`);
    return false;
  }
  if (tokens) {
    const missing = CONTRACT_TOKENS.filter((t) => !tokens[t]);
    if (missing.length) {
      console.warn(`[themes] 主题 ${theme.id} 契约变量缺失,不注册:${missing.join(", ")}`);
      return false;
    }
  }
  // 文案表覆盖校验(§2.2):缺 key 不阻塞注册(运行期回落 classic 文案),但告警
  const missingKeys = COPY_KEYS.filter((k) => !(k in (theme.copy ?? {})));
  if (missingKeys.length) {
    console.warn(`[themes] 主题 ${theme.id} 文案 key 缺失(将回落 classic):${missingKeys.join(", ")}`);
  }
  THEMES.set(theme.id, theme);
  return true;
}

for (const t of REGISTRY) registerTheme(t);

export const listThemes = () => [...THEMES.values()];

/* ── 切换与持久化(§2.5)────────────────────────────────────────── */

let requestedId = DEFAULT_THEME; // 用户选择(持久化/URL 同步的对象)
let effectiveId = DEFAULT_THEME; // 当前页实际生效(scope 回落后)

const pageOf = () =>
  (typeof document !== "undefined" && document.body?.dataset?.route) || "runs";

/* scope 回落(§5):未验收页面强制 classic */
export function resolveEffective(page = pageOf()) {
  const t = THEMES.get(requestedId);
  const ok =
    t && (t.scope === "app-wide" || (Array.isArray(t.scope) && t.scope.includes(page)));
  return ok ? requestedId : DEFAULT_THEME;
}

/* 重解析有效主题并切 <html data-theme>(启动/路由变化时调用) */
export function syncTheme() {
  effectiveId = resolveEffective();
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.dataset.theme = effectiveId;
  }
  return effectiveId;
}

/* hash ?theme= 同步(replaceState,不触发路由;classic 为默认,省略参数) */
function syncThemeUrl() {
  if (typeof location === "undefined" || typeof history === "undefined") return;
  const raw = (location.hash || "#/runs").replace(/^#/, "");
  const [path, qs] = raw.split("?");
  const q = new URLSearchParams(qs ?? "");
  if (requestedId !== DEFAULT_THEME) q.set("theme", requestedId);
  else q.delete("theme");
  const s = q.toString();
  history.replaceState(null, "", `#${path}${s ? `?${s}` : ""}`);
}

function themeFromUrl() {
  if (typeof location === "undefined") return null;
  // 两种携带形态都受理:#/lab?theme=moe(hash 内,主形态)与 /?theme=moe#...
  // (hash 外;UX 评审第三轮实测 ?theme=classic 不生效即后者被忽略所致)
  const hashQs = (location.hash || "").replace(/^#/, "").split("?")[1];
  const id =
    new URLSearchParams(hashQs ?? "").get("theme") ??
    new URLSearchParams(location.search ?? "").get("theme");
  return id && THEMES.has(id) ? id : null;
}

/* 用户切换:持久化 + URL 同步 + 按当前页解析生效 */
export function applyTheme(id, { persist = true, syncUrl = true } = {}) {
  if (THEMES.has(id)) {
    requestedId = id;
  } else {
    console.warn(`[themes] 未知主题 ${id},回落 ${DEFAULT_THEME}`);
    requestedId = DEFAULT_THEME;
  }
  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, requestedId);
    } catch {
      /* 隐私模式等:不持久化,仅本次会话生效 */
    }
  }
  if (syncUrl) syncThemeUrl();
  return syncTheme();
}

/* 启动解析:URL(深链接)> localStorage > classic;URL 命中的同时写入持久化 */
export function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    stored = null;
  }
  const id = themeFromUrl() ?? (stored && THEMES.has(stored) ? stored : DEFAULT_THEME);
  return applyTheme(id);
}

/* ── 组件消费面(零分支:读当前有效主题的契约产物)───────────────── */

export function currentTheme() {
  return THEMES.get(effectiveId) ?? THEMES.get(DEFAULT_THEME) ?? null;
}

export function currentThemeId() {
  return effectiveId;
}

/* 文案键查询:有效主题 → classic 回落 → key 原文(防半成品文案) */
export function copy(key) {
  return (
    currentTheme()?.copy?.[key] ??
    THEMES.get(DEFAULT_THEME)?.copy?.[key] ??
    key
  );
}

/* 切换器 swatch 三色预览:从已加载主题 css 读 [基底/文本/主色] */
export function themeSwatch(id) {
  const t = THEMES.get(id);
  if (!t) return null;
  const tokens = sheetTokens(t);
  if (!tokens) return null; // 无法解析(node):切换器省略色板
  return [tokens["--bg-0"], tokens["--fg-0"], tokens["--live"]].filter(Boolean);
}

/* ── TopBar 主题切换器(§2.5:下拉,每项带三色 swatch 预览)────────── */

export function mountThemePicker(container) {
  if (!container) return;
  const doc = container.ownerDocument ?? document;
  container.classList.add("theme-picker");

  const btn = doc.createElement("button");
  btn.className = "btn theme-picker-btn";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-label", "主题选择");
  const menu = doc.createElement("div");
  menu.className = "theme-menu";
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", "主题列表");
  menu.hidden = true;
  container.appendChild(btn);
  container.appendChild(menu);

  const swatchEl = (id) => {
    const wrap = doc.createElement("span");
    wrap.className = "swatch";
    wrap.setAttribute("aria-hidden", "true");
    for (const c of themeSwatch(id) ?? []) {
      const d = doc.createElement("span");
      d.className = "sw-dot";
      d.style.background = c;
      wrap.appendChild(d);
    }
    return wrap;
  };

  const items = listThemes().map((t) => {
    const item = doc.createElement("button");
    item.className = "theme-item";
    item.setAttribute("role", "option");
    item.dataset.themeId = t.id;
    item.appendChild(swatchEl(t.id));
    const name = doc.createElement("span");
    name.className = "theme-item-name";
    name.textContent = t.name;
    item.appendChild(name);
    item.addEventListener("click", () => {
      applyTheme(t.id);
      renderBtn();
      menu.hidden = true;
    });
    menu.appendChild(item);
    return { t, item };
  });

  function renderBtn() {
    const t = THEMES.get(requestedId) ?? THEMES.get(DEFAULT_THEME);
    btn.innerHTML = ""; // 重建 swatch + 名称(选择变化)
    btn.appendChild(swatchEl(t.id));
    const label = doc.createElement("span");
    label.className = "theme-picker-label";
    label.textContent = t.name;
    btn.appendChild(label);
    for (const { t: it, item } of items) {
      item.setAttribute("aria-selected", String(it.id === requestedId));
    }
  }

  btn.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
  });
  renderBtn();
}

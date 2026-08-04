/* Agent OS · 对话中枢(docs/WEB-PLATFORM.md §10):tab 条布局 + 对话流。

   左栏 = 竖排 tab 条:conversation(会话,固定首 tab,不可关闭)+ detail
   (详情,可关闭,✕);底部会话下拉(切换/新建)。卡上"查看详情"链接 →
   开/聚焦 detail tab(同一 ref 去重)。主区:conversation tab = 对话流 +
   意图输入;detail tab = 详情视图(输入区隐藏)。
   不引用旧 web 的 app.js;只复用宿主无关模块(themes/util/trace 纯函数)。 */

import { copy, initTheme, applyTheme, listThemes, currentThemeId } from "/static/js/themes.js";
import { esc, toast } from "/static/js/util.js";
import { mountDatePicker, mountFormEditor, mountLogViewer } from "/static/js/widgets/index.js";
import { looksMarkdown, mdToHtml } from "/static/js/widgets/w-md.js";
import { mountDocEditor } from "./doc-editor.js";
import { renderCardSurface } from "./cards.js";
import { renderTabSurface } from "./details.js";

const $ = (sel) => document.querySelector(sel);

/* ── tab 模型(纯函数;测试可载)────────────────────────────── */

/* 开 tab:同 kind+ref 去重聚焦(gate/pack 可能同 ref——同一草稿的
   报告与包是两个详情);返回 {tabs, active, opened} */
export function openTab(tabs, tab) {
  const existing = (tabs ?? []).find(
    (t) => t.kind !== "conversation" && t.kind === tab.kind && t.ref === tab.ref
  );
  if (existing) return { tabs, active: existing.id, opened: false };
  const next = [...(tabs ?? []), tab];
  return { tabs: next, active: tab.id, opened: true };
}

/* 关 tab(M2 关闭≠销毁,docs/APP-MODEL.md §6):关闭只是隐藏——返回
   {tabs, active, closed},closed 交给"最近关闭"列表(重开回同 instance);
   关闭后回退到 conversation(或剩余最后一个) */
export function closeTab(tabs, id, fallbackId = "conv") {
  const closed = (tabs ?? []).find((t) => t.id === id) ?? null;
  const next = (tabs ?? []).filter((t) => t.id !== id);
  const active = next.some((t) => t.id === fallbackId) ? fallbackId : (next[0]?.id ?? fallbackId);
  return { tabs: next, active, closed };
}

/* 最近关闭列表(重开入口;去重按 id,新关在前,上限 3) */
export function pushClosed(closed, tab, cap = 3) {
  const next = [tab, ...(closed ?? []).filter((t) => t.id !== tab.id)];
  return next.slice(0, cap);
}

const state = {
  sessions: [],
  current: null, // 当前会话 id(conversation tab 的内容源)
  messages: [],
  busy: false,
  tabs: [{ id: "conv", kind: "conversation", title: "", ref: "conv" }],
  closedTabs: [], // M2:最近关闭(重开入口;销毁是显式动作,本期不做)
  active: "conv",
  detail: null, // {kind, ref, loading, error, html}
  shell: null, // M5:shell 根 app 的 instance(唯一事实源;本地 tabs/active 是它的镜像)
  useShell: false, // /api/shell 不可达时回落本地 tab 模型(M1-M4 行为,降级面)
  longPressTab: null, // 触屏降级:长按出"移到最左/最右"(§15.4 a11y)
  // M5 增补(桌面化 root widget):桌面态 = active 为 ""(无激活 tab;tab 全保留)
  desktop: { pinned: [], wallpaper: true }, // shell.state.desktop 的镜像
  startOpen: false, // 开始菜单开合(纯 UI 态,不进 shell.state)
};

/* ── shell 镜像(docs/APP-MODEL.md §13;M5)───────────────────────
   shell.state 是唯一事实源:前端 tabs/active 只是它的渲染镜像;
   一切 tab/布局/主题操作转发为 shell action(管道),响应回镜。 */

function _mirrorShell(shellState) {
  state.tabs = (shellState.tabs ?? []).map((t) => ({
    id: t.id,
    kind: t.kind,
    title: t.title,
    ref: t.ref,
    instance: t.instance_id || undefined,
  }));
  state.active = shellState.active_tab ?? "conv";
  document.body.dataset.iconMode = shellState.layout?.icon_mode ? "1" : "0";
  // M5 增补(桌面化):desktop 镜像(旧持久化无此键 → 缺省壁纸开)
  state.desktop = {
    pinned: shellState.desktop?.pinned ?? [],
    wallpaper: shellState.desktop?.wallpaper !== false,
  };
  document.body.dataset.wallpaper = state.desktop.wallpaper ? "1" : "0";
}

async function loadShell() {
  try {
    const doc = await (await fetch("/platform/api/shell")).json();
    state.shell = doc;
    state.useShell = true;
    _mirrorShell(doc.state ?? {});
  } catch {
    state.useShell = false; // 回落本地 tab 模型(降级面,不阻断启动)
  }
}

/* shell action 转发(事件 → 管道 → 回镜;失败静默回落本地语义) */
async function shellAction(actionId, args = {}) {
  if (!state.useShell) return null;
  try {
    const res = await fetch(
      `/platform/api/apps/${encodeURIComponent(state.shell.id)}/actions/${encodeURIComponent(actionId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ surface: "tab", args }),
      }
    );
    if (!res.ok) return null;
    const result = await res.json();
    if (result.instance?.state) _mirrorShell(result.instance.state);
    return result;
  } catch {
    return null;
  }
}

/* ── 主题(与正式系统同一契约:initTheme 解析 URL/localStorage)────── */

function mountThemes() {
  const host = $("#themes");
  const current = initTheme();
  for (const btn of host.querySelectorAll("button")) {
    btn.dataset.on = btn.dataset.t === current ? "1" : "0";
    btn.addEventListener("click", async () => {
      const { applyTheme } = await import("/static/js/themes.js");
      applyTheme(btn.dataset.t);
      shellAction("shell.theme.set", { theme: btn.dataset.t }); // M5:主题偏好持久化(§13.1 endpoint)
      host.querySelectorAll("button").forEach((b) => {
        b.dataset.on = b.dataset.t === btn.dataset.t ? "1" : "0";
      });
    });
  }
}

/* ── 渲染:tab 条 + 会话下拉 ─────────────────────────────────── */

function renderTabs() {
  const host = $("#tabs");
  host.innerHTML = state.tabs
    .map((t) => {
      const active = t.id === state.active;
      const close = `<button class="pf-tab-x" data-tab-x="${esc(t.id)}" aria-label="${esc(copy("platform.tab.close"))}">✕</button>`;
      const label = t.kind === "conversation" ? copy("platform.tab.chat") : t.title;
      // 触屏降级(§15.4):长按出的"移到最左/最右"菜单(与 DnD 同一 move_tab action)
      const lp =
        state.longPressTab === t.id
          ? `<span class="pf-tab-lp">` +
            `<button data-move-start="${esc(t.id)}">${esc(copy("platform.move.start"))}</button>` +
            `<button data-move-end="${esc(t.id)}">${esc(copy("platform.move.end"))}</button></span>`
          : "";
      return (
        `<div class="pf-tab" data-tab="${esc(t.id)}" role="tab" tabindex="0" aria-selected="${active}"` +
        ` title="${esc(label)}"` + // 窄屏图标列时悬停给全文(M2)
        ` data-reg-path="/shell/tab/${esc(t.id)}"` + // §14 widget 寻址(与注册一致)
        (t.kind !== "conversation" ? ` draggable="true"` : "") + // §15 DnD v1:tab 可拖
        `>` +
        `<span class="pf-tab-ico" aria-hidden="true">${esc((label || "?").trim().charAt(0))}</span>` +
        `<span class="pf-tab-label">${esc(label)}</span>${lp}${close}</div>`
      );
    })
    .join("");
  // M2 关闭≠销毁:最近关闭小列表(重开回同 instance;销毁是显式动作,本期不做)
  host.innerHTML += state.closedTabs.length
    ? `<div class="pf-recent"><div class="pf-recent-title">${esc(copy("platform.tabs.recent"))}</div>` +
      state.closedTabs
        .map(
          (t) =>
            `<button class="pf-recent-item" data-reopen="${esc(t.id)}" title="${esc(t.title)}">` +
            `${esc(t.title)}</button>`
        )
        .join("") +
      `</div>`
    : "";
  const sel = $("#sessionSel");
  sel.innerHTML =
    state.sessions
      .map(
        (s) =>
          `<option value="${esc(s.id)}"${s.id === state.current ? " selected" : ""}>${esc(s.title)}</option>`
      )
      .join("") || `<option value="">${esc(copy("platform.no.sessions"))}</option>`;
  renderTray(); // 任务栏一体:tab 重渲时托盘同步(live/主题/收件箱计数)
  renderStartMenu(); // 开始菜单数据源与桌面图标同源(closedTabs 会变)
}

/* ── M5 增补:桌面化 root widget(Windows 桌面式;红线:不做自由排布/浮动
   窗口——窗口仍是单激活最大化 tab,桌面是"无 tab 激活时的主屏")──────────
   三面同源:桌面图标/开始菜单/tab 条只读 shell.state(镜像),点击一律转发
   既有 shell action,无独立代码路径。 */

/* 桌面图标数据源(数据驱动;开始菜单同一份):对话恒首 + legacy 五应用 +
   最近关闭前 3(M2 closedTabs,关闭≠销毁的重开入口) */
function desktopIcons() {
  const icons = [{ id: "conv", label: copy("platform.tab.chat") }];
  for (const [k, c] of _LEGACY_PAGES) icons.push({ id: k, label: copy(c) });
  for (const t of state.closedTabs.slice(0, 3)) {
    icons.push({ id: t.id, label: t.title, recent: true });
  }
  return icons;
}

/* 大图标(首字符 glyph,与 tab 条图标列同手法——零新资产) */
function _deskIconHtml(icon) {
  return (
    `<button class="pf-desk-ico" data-desk-open="${esc(icon.id)}">` +
    `<span class="pf-desk-glyph" aria-hidden="true">${esc((icon.label || "?").trim().charAt(0))}</span>` +
    `<span class="pf-desk-name">${esc(icon.label)}</span></button>`
  );
}

/* 桌面主区(无激活 tab 时):图标网格 + 最近关闭组 + 壁纸开关;
   壁纸 = 主题 body 背景图案透出(桌面区透明底,已有资产零新增) */
function renderDesktop() {
  const host = $("#desktop");
  if (!host) return;
  const icons = desktopIcons();
  const apps = icons.filter((i) => !i.recent);
  const recent = icons.filter((i) => i.recent);
  host.innerHTML =
    `<div class="pf-desk-grid">${apps.map(_deskIconHtml).join("")}</div>` +
    (recent.length
      ? `<div class="pf-desk-recent"><div class="pf-recent-title">${esc(copy("platform.tabs.recent"))}</div>` +
        `<div class="pf-desk-grid">${recent.map(_deskIconHtml).join("")}</div></div>`
      : "") +
    `<button class="pf-desk-wall" data-desk-wallpaper aria-pressed="${state.desktop.wallpaper}">` +
    `${esc(copy("platform.desktop.wallpaper"))}</button>`;
}

/* 桌面图标点击 = tab 条/launcher/最近关闭的同一入口(同源断言):
   对话 → activateTab(同 tab 条点击,shell.tab.focus);
   最近关闭 → reopenTab(同"最近关闭"列表,shell.tab.open);
   legacy 应用 → openDetail(同 launcher,shell.tab.open) */
function openDesktopIcon(id) {
  state.startOpen = false; // 开始菜单项同源此口,点完即收
  if (id === "conv") return openConversation(); // conv 可关:不在时按恒首语义补回
  if (state.closedTabs.some((t) => t.id === id)) return reopenTab(id);
  return openDetail(id, id, {});
}

/* 开始菜单:点开应用菜单(与桌面图标同数据源,项同 data-desk-open 同源点击) */
function renderStartMenu() {
  const btn = $("#startBtn");
  const menu = $("#startMenu");
  if (!btn || !menu) return;
  menu.hidden = !state.startOpen;
  btn.setAttribute("aria-expanded", state.startOpen ? "true" : "false");
  if (!state.startOpen) return;
  menu.innerHTML = desktopIcons()
    .map(
      (i) =>
        `<button class="pf-start-item" role="menuitem" data-desk-open="${esc(i.id)}">` +
        `<span class="pf-desk-glyph" aria-hidden="true">${esc((i.label || "?").trim().charAt(0))}</span>` +
        `${esc(i.label)}</button>`
    )
    .join("");
}

/* 系统托盘:live 指示(连接心跳)/ 主题切换 / 收件箱(decisions 计数);
   托盘项一律 role=button + aria-label;live 色点 + 状态原文双编码 */
function renderTray() {
  const host = $("#tray");
  if (!host) return;
  const live = _es ? "sse" : _pollTimer ? "poll" : "off"; // 连接态(技术原文豁免,直读)
  const pending = state.messages.reduce(
    (n, m) => n + (m.cards ?? []).filter((c) => c.type === "escalation" && !c.data?.resolved).length,
    0
  );
  host.innerHTML =
    `<button class="pf-tray-item" data-tray-live role="button" ` +
    `aria-label="${esc(copy("platform.tray.live"))}: ${live}">` +
    `<span class="pf-tray-dot" data-live="${live}" aria-hidden="true"></span>` +
    `<span class="pf-tray-label">${live}</span></button>` +
    `<button class="pf-tray-item" data-tray-theme role="button" ` +
    `aria-label="${esc(copy("platform.tray.theme"))}">` +
    `<span class="pf-tray-label">${esc(currentThemeId())}</span></button>` +
    `<button class="pf-tray-item" data-tray-inbox role="button" ` +
    `aria-label="${esc(copy("platform.tray.inbox"))}: ${pending}">` +
    `<span aria-hidden="true">✉</span> <span class="pf-tray-label">${pending}</span></button>`;
}

/* 托盘 live 项点击 = 重连(降级态尝试升回 SSE;与启动同一 connectStream) */
function reconnectStream() {
  try {
    _es?.close();
  } catch {
    /* 忽略 */
  }
  _es = null;
  connectStream();
  renderTray();
}

/* 托盘主题项点击 = 循环下一主题(注册表顺序,无主题 id 硬编码;
   与顶栏切换同一通道:applyTheme + shell.theme.set) */
function cycleTheme() {
  const ids = listThemes().map((t) => t.id);
  const next = ids[(ids.indexOf(currentThemeId()) + 1) % ids.length] ?? ids[0];
  applyTheme(next);
  shellAction("shell.theme.set", { theme: next });
  document.querySelector("#themes")?.querySelectorAll("button").forEach((b) => {
    b.dataset.on = b.dataset.t === next ? "1" : "0";
  });
  renderTray();
}

/* 窗口标题栏(激活 app 最大化时):图标 + 标题 + 最小化(回桌面,tab 保留)
   + 关闭(✕,走 M5 关闭≠销毁进最近关闭;有桌面后 conversation 同样可关) */
function renderTitlebar() {
  const host = $("#titlebar");
  if (!host) return;
  const tab = state.tabs.find((t) => t.id === state.active);
  host.hidden = !tab;
  if (!tab) {
    host.innerHTML = "";
    return;
  }
  const label = tab.kind === "conversation" ? copy("platform.tab.chat") : tab.title;
  const close = `<button class="pf-win-x" data-win-close aria-label="${esc(copy("platform.tab.close"))}">✕</button>`;
  host.innerHTML =
    `<span class="pf-win-ico" aria-hidden="true">${esc((label || "?").trim().charAt(0))}</span>` +
    `<span class="pf-win-title">${esc(label)}</span>` +
    `<span class="pf-spacer"></span>` +
    `<button class="pf-win-min" data-win-min aria-label="${esc(copy("platform.win.min"))}">—</button>${close}`;
}

/* 最小化 = 回桌面(无激活 tab;tab 保留在任务栏)。shell 不可达时本地生效(降级面) */
function minimizeTab() {
  state.active = ""; // 乐观先渲(shell 回镜校正——同值)
  shellAction("shell.tab.minimize"); // M5 增补:最小化 = shell action(local)
  renderTabs();
  renderMain();
}

/* 壁纸开关 = shell.desktop.set(local;持久化进 shell.state.desktop) */
async function toggleWallpaper() {
  const next = !state.desktop.wallpaper;
  const result = await shellAction("shell.desktop.set", { wallpaper: next });
  if (!result) {
    // 降级面:无 shell 本地生效
    state.desktop.wallpaper = next;
    document.body.dataset.wallpaper = next ? "1" : "0";
  }
  renderDesktop();
}



/* ── DnD v1(docs/APP-MODEL.md §15):envelope 产出/消费 + 触屏降级 ── */

const _DND_MIME = "application/x-agent-os-widget";
/* 卡型 → 详情 kind(卡面拖进 tab 条 = 打开对应详情;与 _APP_KIND 逆映射) */
const _CARD_TO_DETAIL = {
  gate_report: "gate", skill_pack: "pack", publish: "plan",
  diff: "diff", escalation: "esc", plan: "decompose", table: "run",
};

function bindDnd() {
  const strip = $("#tabs");
  document.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.(".pf-card[data-reg-path]");
    const tab = e.target.closest?.("[data-tab]");
    if (card) {
      // envelope(§15.1):source/source_kind 必填,ref 为扩展键(目标不识可忽略)
      e.dataTransfer?.setData(_DND_MIME, JSON.stringify({
        source: card.dataset.regPath, source_kind: card.dataset.card,
        ref: card.dataset.detailRef ?? "", position: {},
      }));
    } else if (tab && tab.dataset.tab !== "conv") {
      e.dataTransfer?.setData(_DND_MIME, JSON.stringify({
        source: `/shell/tab/${tab.dataset.tab}`, source_kind: "shell-tab", position: {},
      }));
    }
  });
  strip.addEventListener("dragover", (e) => {
    // accept 校验(§15.2):只认我们的 envelope 类型——非法落点不高亮不接收
    if ([...(e.dataTransfer?.types ?? [])].includes(_DND_MIME)) {
      e.preventDefault();
      strip.classList.add("pf-drop-ok");
    }
  });
  strip.addEventListener("dragleave", () => strip.classList.remove("pf-drop-ok"));
  strip.addEventListener("drop", (e) => {
    strip.classList.remove("pf-drop-ok");
    let env = null;
    try {
      env = JSON.parse(e.dataTransfer?.getData(_DND_MIME) ?? "null");
    } catch {
      env = null;
    }
    if (!env || typeof env.source !== "string" || typeof env.source_kind !== "string") {
      return; // envelope 三键是强制最小集(§15.4),不合不静默吞(无高亮亦无动作)
    }
    e.preventDefault();
    const before = e.target.closest?.("[data-tab]")?.dataset.tab ?? "";
    if (env.source_kind === "shell-tab") {
      // tab → tab 条 = 重排(§15.3 第一对;shell.layout.move_tab,local)
      const sourceId = env.source.split("/").pop();
      if (sourceId && sourceId !== before) {
        shellAction("shell.layout.move_tab", { tab: sourceId, before }).then(() => renderTabs());
      }
      return;
    }
    // 卡面 → tab 条 = 打开(§15.3 第二对;与点"打开"同一 openDetail 路径,同源断言)
    const kind = _CARD_TO_DETAIL[env.source_kind];
    if (kind && env.ref) openDetail(kind, env.ref, {});
  });
}

/* 触屏降级(§15.4 a11y):tab 长按 600ms 出"移到最左/最右"(与 DnD 同一 action) */
function bindLongPress() {
  let timer = null;
  document.addEventListener("pointerdown", (e) => {
    const tab = e.target.closest?.("[data-tab]");
    if (!tab || tab.dataset.tab === "conv") return;
    timer = setTimeout(() => {
      state.longPressTab = tab.dataset.tab;
      renderTabs();
    }, 600);
  });
  const cancel = () => clearTimeout(timer);
  document.addEventListener("pointerup", cancel);
  document.addEventListener("pointercancel", cancel);
}

/* ── 渲染:主区(conversation = 对话流;detail = 详情)──────────── */

function renderMain() {
  const isDesktop = !state.active; // M5 增补:无激活 tab = 桌面主屏(窗口仍是单激活最大化)
  const isConv = state.active === "conv";
  document.body.dataset.desktop = isDesktop ? "1" : "0"; // 壁纸透出的样式钩子
  // legacy 视图切换/离开/最小化时先收编(close 退订 store,防复活写;M4b)
  if (_legacyClose && (isConv || isDesktop || state.detail?.mount !== _legacyMountedFor)) {
    try {
      _legacyClose();
    } catch {
      /* close 失败不阻断切换 */
    }
    _legacyClose = null;
    _legacyMountedFor = null;
  }
  const desktop = $("#desktop");
  if (desktop) desktop.hidden = !isDesktop;
  $("#log").hidden = !isConv;
  $("#inputBar").hidden = !isConv;
  $("#detailHost").hidden = isConv || isDesktop;
  if (isDesktop) renderDesktop();
  else if (isConv) renderLog();
  else renderDetail();
  renderTitlebar();
}

function msgHtml(m, index) {
  const role = m.role === "user" ? "user" : "agent";
  // W4(W-md 装配点):含 markdown 结构的消息走白名单安全渲染;
  // 普通纯文本保持 esc 原文(保守启用,不误伤)
  const text = m.text
    ? `<div class="pf-bubble-text">${looksMarkdown(m.text) ? mdToHtml(m.text) : esc(m.text)}</div>`
    : "";
  // N7(O7):LLM 路由凭证降级 → 人话系统提示(copy 六主题;不静默,不裸错)
  const degrade =
    m.meta?.route === "rule" && m.meta?.reason === "llm_unavailable"
      ? `<div class="pf-bubble-note">${esc(copy("platform.route.degrade"))}</div>`
      : "";
  // §14 widget 寻址:卡面运行时路径(/conv/<sid>/msg/<n>/card/<k>,序号属纯序列)
  const cards = (m.cards ?? [])
    .map((c, k) => renderCardSurface(c, 1, _regOf(c, index, k), _refOf(c)))
    .join("");
  return `<div class="pf-msg" data-role="${role}"><div class="pf-bubble">${degrade}${text}${cards}</div></div>`;
}

/* 卡的 widget 路径(§14)与业务锚(DnD envelope 的 ref 扩展键,§15.4) */
function _regOf(card, msgIndex, cardIndex) {
  return `/conv/${state.current}/msg/${msgIndex}/card/${cardIndex}`;
}

function _refOf(card) {
  const d = card?.data ?? {};
  switch (card?.type) {
    case "plan": return (d.create ?? [])[0]?.name ?? "";
    case "skill_pack": case "diff": return d.name ?? "";
    case "gate_report": return d.draft ?? "";
    case "publish": return d.plan_id ?? "";
    case "escalation": return d.question_id ?? "";
    case "table": return d.ref?.id ?? "";
    default: return "";
  }
}

function renderLog() {
  const log = $("#log");
  if (!state.messages.length && !state.busy) {
    log.innerHTML =
      `<div class="pf-empty">` +
      `<div class="pf-empty-title">${esc(copy("platform.empty.title"))}</div>` +
      ["platform.empty.1", "platform.empty.2", "platform.empty.3"]
        .map((k) => `<button class="pf-example" data-example="${esc(copy(k))}">${esc(copy(k))}</button>`)
        .join("") +
      `</div>`;
    return;
  }
  log.innerHTML =
    state.messages.map(msgHtml).join("") +
    (state.busy
      ? `<div class="pf-msg" data-role="agent"><div class="pf-bubble pf-skel">` +
        `<span class="pf-skel-line"></span><span class="pf-skel-line w60"></span></div></div>`
      : "");
  log.scrollTop = log.scrollHeight;
}

let _legacyClose = null; // 当前挂载的 legacy 视图 close 句柄(M4b;防订阅泄漏)
let _legacyMountedFor = null;

async function renderDetail() {
  const host = $("#detailHost");
  const d = state.detail;
  if (!d) {
    host.innerHTML = "";
    return;
  }
  if (d.loading) {
    host.innerHTML = `<div class="pf-wait">${esc(copy("platform.detail.loading"))}</div>`;
    return;
  }
  if (d.error) {
    host.innerHTML =
      `<div class="pf-wait pf-errline">${esc(d.error)}</div>` +
      `<button class="btn" data-it-retry>${esc(copy("platform.detail.retry"))}</button>`;
    return;
  }
  if (d.mount && !d.html) {
    // legacy 整页挂载(自有内容,M4b)
    if (_legacyMountedFor !== d.mount) {
      host.innerHTML = "";
      try {
        _legacyClose = await _mountLegacy(host, d.mount);
        _legacyMountedFor = d.mount;
      } catch (e) {
        host.innerHTML = `<div class="pf-wait pf-errline">${esc(copy("platform.detail.error"))}: ${esc(e.message ?? e)}</div>`;
      }
    }
    return;
  }
  host.innerHTML = d.html ?? "";
  if (d.mount) await d.mount(host); // html 之上的控件挂载(W3:run.launch 表单/browse 时间窗)
}

/* ── 数据:会话装载与选择(刷新恢复)──────────────────────────── */

async function loadSessions(selectId = null) {
  state.sessions = await (await fetch("/platform/api/sessions")).json();
  const target = selectId ?? (state.sessions.length ? state.sessions[0].id : null);
  if (target) await selectSession(target);
  else {
    state.current = null;
    state.messages = [];
  }
  renderTabs();
  renderMain();
}

async function selectSession(id) {
  state.current = id;
  const session = await (await fetch(`/platform/api/sessions/${id}`)).json();
  state.messages = session.messages ?? [];
  renderTabs();
  renderMain();
}

async function newSession() {
  // M5:新会话 = shell.session.create(endpoint 态,与 POST /api/sessions 同源);
  // shell 不可达时回落直调(降级面)
  const result = await shellAction("shell.session.create");
  const session = result?.session ?? await (await fetch("/platform/api/sessions", { method: "POST" })).json();
  await loadSessions(session.id);
}

/* ── 发送意图(同前:用户气泡 → 骨架 → agent 消息)────────────── */

async function send() {
  const input = $("#intent");
  const text = input.value.trim();
  if (!text || state.busy) return;
  if (!state.current) await newSession();
  state.busy = true;
  state.messages.push({ role: "user", text, cards: [] });
  input.value = "";
  renderMain();
  try {
    const res = await fetch(`/platform/api/sessions/${state.current}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
    const msg = await res.json();
    state.messages.push(msg);
  } catch (e) {
    state.messages.push({ role: "agent", text: `${copy("platform.error")}: ${e.message ?? e}`, cards: [] });
  } finally {
    state.busy = false;
    renderMain();
  }
}

/* ── 卡片动作(同前;结果追加进会话)──────────────────────────── */

async function cardAction(btn) {
  const actionId = btn.dataset.appAction || btn.dataset.cardAct;
  const instId = btn.dataset.appInst; // M1:有 instance 走新 action 管道(§4)
  const payload = JSON.parse(btn.dataset.payload ?? "{}");
  const card = btn.closest(".pf-card");
  const ack = card?.querySelector("[data-ack]");
  if (ack && !ack.checked) {
    toast(copy("platform.warnings.ack"), "info");
    return;
  }
  btn.disabled = true;
  try {
    let result;
    if (instId) {
      // 新管道:服务端按 manifest 绑定参数,前端只交事件参数(防越权构造)
      const res = await fetch(`/platform/api/apps/${encodeURIComponent(instId)}/actions/${encodeURIComponent(actionId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          surface: "card",
          args: ack ? { warnings_ack: true } : {},
          session_id: state.current,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      result = await res.json();
    } else {
      // 旧管道(过渡兼容:M1 前持久化的卡没有 instance;M3 退役)
      if (ack) payload.warnings_ack = true;
      const res = await fetch("/platform/api/cards/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: actionId, payload, session_id: state.current }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      result = await res.json();
    }
    state.messages.push({ role: "agent", text: result.text ?? "", cards: result.cards ?? [] });
    if (instId && result.run_instance?.ref) {
      // run 真通道(M4a):卡面动作产出 run app —— 直接进 run tab
      await openDetail("run", result.run_instance.ref, null);
      return;
    }
    renderMain();
  } catch (e) {
    state.messages.push({ role: "agent", text: `${copy("platform.error")}: ${e.message ?? e}`, cards: [] });
    renderMain();
  } finally {
    btn.disabled = false;
  }
}

/* ── 详情 tab(开/聚焦/关闭/加载;loading/error 可重试)────────── */

const _DETAIL_META = {
  gate: { title: copy("platform.detail.gate") },
  pack: { title: copy("platform.detail.pack") },
  plan: { title: copy("platform.detail.plan") },
  run: { title: copy("platform.detail.run") },
  diff: { title: copy("platform.detail.diff") },
  esc: { title: copy("platform.detail.esc") },
  decompose: { title: copy("platform.detail.decompose") },
  debug: { title: copy("platform.detail.debug") },
  draft: { title: copy("platform.detail.draft") },
  doc: { title: copy("platform.detail.doc") },
  skills: { title: copy("platform.app.skills") },
  runs: { title: copy("platform.app.runs") },
  tools: { title: copy("platform.app.tools") },
  lab: { title: copy("platform.app.lab") },
  debugold: { title: copy("platform.app.debugold") },
};

/* 详情 kind → app kind(M3 全解开,docs/APP-MODEL.md §8:run/debug/lab-draft;
   M4b:legacy 五页 skills/runs/tools/lab/debug-old) */
const _APP_KIND = {
  gate: "gate_report",
  pack: "skill_pack",
  plan: "publish",
  diff: "diff",
  esc: "escalation",
  decompose: "plan",
  run: "run",
  debug: "debug",
  draft: "lab-draft",
  doc: "doc", // D1(docs/DOC-EDITOR.md §2:doc app kind 接入)
  skills: "skills",
  runs: "runs",
  tools: "tools",
  lab: "lab",
  debugold: "debug-old",
};

/* M4b legacy 页(§8 迁移地图末行):能挂 ES module 的直接挂载(同 document,
   零隔离);runs 列表在旧 app.js 里无独立装配口 → 深链 + 摘要。
   挂载经 globalThis.__legacyMounts 可注入(node 测试的替代装配口)。 */
const _LEGACY_PAGES = [
  ["skills", "platform.app.skills"],
  ["runs", "platform.app.runs"],
  ["tools", "platform.app.tools"],
  ["lab", "platform.app.lab"],
  ["debugold", "platform.app.debugold"],
];
const _LEGACY_MODULE = {
  skills: ["/static/js/components/skills-view.js", "openSkillsView", "closeSkillsView"],
  tools: ["/static/js/components/tools-view.js", "openToolsView", "closeToolsView"],
  lab: ["/static/js/components/lab.js", "openLab", "closeLab"],
  debugold: ["/static/js/components/debug-home.js", "openDebugHome", "closeDebugHome"],
};

function renderLauncher() {
  const host = $("#launcher");
  if (!host) return;
  host.innerHTML =
    `<div class="pf-recent-title">${esc(copy("platform.apps.label"))}</div>` +
    _LEGACY_PAGES
      .map(([k, c]) => `<button class="pf-recent-item" data-open-legacy="${k}">${esc(copy(c))}</button>`)
      .join("") +
    // M5 §13.1:图标列开关 = shell.layout.set(local;持久化进 shell.state.layout)
    `<button class="pf-recent-item" data-icon-toggle>${esc(copy("platform.layout.iconmode"))}</button>`;
}

/* legacy 挂载:返回 close 函数(切走/关闭时调用,防 store 订阅泄漏) */
async function _mountLegacy(host, kind) {
  const injected = globalThis.__legacyMounts?.[kind]; // node 测试替代装配口
  if (injected) return injected(host);
  const [url, openName, closeName] = _LEGACY_MODULE[kind];
  const mod = await import(url);
  mod[openName](host);
  return mod[closeName] ?? null;
}

/* W3:skill inputs schema(launch 表单数据源;拿不到 → null,发起面保持 textarea) */
async function _skillInputs(skill) {
  try {
    const res = await fetch(`/api/skills/${encodeURIComponent(skill)}`);
    if (!res.ok) return null;
    const doc = await res.json();
    return doc?.inputs && typeof doc.inputs === "object" && Object.keys(doc.inputs.properties ?? {}).length
      ? doc.inputs
      : null;
  } catch {
    return null;
  }
}

/* runs legacy tab:摘要 + 深链 + 行内 run tab 直达(旧页无装配口的落法);
   W3:browse 时间窗 = W-date range(过滤行内 run;quick 快捷项本地算)。
   首屏行内渲染(区域提取可见);时间窗变更时走 region 重渲(真实 DOM 活性面) */
function _runsRowsHtml(rows) {
  return rows
    .slice(0, 8)
    .map(
      (r) =>
        `<div class="pf-ln"><button class="pf-detail-link" data-detail-kind="run" ` +
        `data-detail-ref="${esc(r.run_id)}" data-detail='{}'>${esc(r.skill ?? r.run_id)}</button> ` +
        `<span class="pf-dim">${esc(r.status ?? "")}</span></div>`
    )
    .join("");
}

async function _legacyRunsHtml() {
  const runs = await (await fetch("/api/runs")).json();
  state._runsAll = runs ?? [];
  const failed = state._runsAll.filter((r) => r.status === "failed").length;
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head">${esc(copy("platform.app.runs"))}</div>` +
    `<div data-browse-range="1"></div>` +
    `<div class="pf-card-sub" data-runs-count="1">${esc(copy("platform.legacy.runs.line"))
      .replace("{n}", String(state._runsAll.length))
      .replace("{f}", String(failed))}</div>` +
    `<div class="pf-card-actions"><a class="btn" href="/#/runs">${esc(copy("platform.legacy.open"))}</a></div>` +
    `<div data-runs-rows="1">${_runsRowsHtml(state._runsAll)}</div>` +
    `</div>`
  );
}

/* runs legacy tab 行渲染(时间窗过滤后;W-date change 驱动) */
function _renderRunsRows(host, range) {
  const all = state._runsAll ?? [];
  const inRange = (r) => {
    const day = String(r.started_at ?? "").slice(0, 10);
    if (range?.start && day < range.start) return false;
    if (range?.end && day > range.end) return false;
    return true;
  };
  const rows = all.filter(inRange);
  const failed = rows.filter((r) => r.status === "failed").length;
  const count = host.querySelector("[data-runs-count]");
  if (count) {
    count.textContent = copy("platform.legacy.runs.line")
      .replace("{n}", String(rows.length))
      .replace("{f}", String(failed));
  }
  const rowsHost = host.querySelector("[data-runs-rows]");
  if (rowsHost) rowsHost.innerHTML = _runsRowsHtml(rows);
}

/* spawn 的 state 归一(M3):args_from 的参数源——run 要 run_id、debug 要
   session_id、lab-draft 要 name/root(绑定便利键,与 app.py _register_cards 同哲学) */
function _spawnState(tab, data) {
  if (tab.kind === "run") return { run_id: tab.ref, status: "" };
  if (tab.kind === "debug") return { session_id: tab.ref, run_id: data?.run_id ?? "" };
  if (tab.kind === "draft") {
    const name = data?.name ?? tab.ref;
    return { ...(data ?? {}), name, root: name };
  }
  // D1:doc spawn state(args_from state.name 的绑定源;view/dirty 进 meta.set 面)
  if (tab.kind === "doc") {
    return { name: tab.ref, text: "", dirty: false, savedAt: 0, view: "split", versions: [], bubbles: [] };
  }
  return data ?? {};
}

/* spawn(M2):详情 tab 从"页面"升格为 app 的 Tab Surface——开 tab 时在服务端
   登记/解析 instance(失败不阻断展示:数据面增强,不是依赖) */
async function _spawnForTab(tab, data) {
  const appKind = _APP_KIND[tab.kind];
  if (!appKind) return;
  try {
    const res = await fetch("/platform/api/apps/spawn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: appKind,
        ref: tab.ref,
        title: tab.title,
        state: _spawnState(tab, data),
        created_by: state.current ?? "",
      }),
    });
    if (res.ok) tab.instance = (await res.json()).instance.id;
  } catch {
    /* spawn 失败静默:tab 渲染不受影响 */
  }
}

function activateTab(id) {
  state.active = id; // 乐观先渲( shell 回镜会校正——同值 )
  shellAction("shell.tab.focus", { tab: id }); // M5:焦点 = shell action(§13.1)
  renderTabs();
  renderMain();
}

async function openDetail(kind, ref, data) {
  state._launchForm = null; // 换 tab 即弃旧表单(W3 表单态跟 tab 生命周期)
  const tab = { id: `d:${kind}:${ref}`, kind, title: _DETAIL_META[kind]?.title ?? ref, ref };
  const { tabs, active, opened } = openTab(state.tabs, tab);
  state.tabs = tabs;
  if (!opened) {
    // 同 ref 已开:聚焦并直接重渲(数据可能已更新——详情是活面)
    state.active = active;
    await shellAction("shell.tab.focus", { tab: active });
    state.detail = await _loadDetail(kind, ref, data);
    renderTabs();
    renderMain();
    return;
  }
  state.active = active;
  state.detail = { kind, ref, loading: true };
  renderTabs();
  renderMain();
  await _spawnForTab(tab, data); // spawn 先行:tab.instance 确定后再渲染(M4a fallback 依赖)
  // M5:开 tab = shell.tab.open(本地 openTab 与 shell mutator 同语义,回镜收敛)
  await shellAction("shell.tab.open", {
    id: tab.id, instance_id: tab.instance ?? "", kind, ref, title: tab.title,
  });
  state.detail = await _loadDetail(kind, ref, data);
  renderTabs();
  renderMain();
  // widget 注册(§14 注册制:Surface 渲染即登记;摘要 = tab 标题,人话)
  _widgetCall("/platform/api/widgets/register", {
    path: `/shell/tab/${tab.id}/surface/tab`, kind, summary_hint: tab.title,
  });
}

/* widget 端点调用(M5 §14;fire-and-forget,失败静默——注册面不阻断渲染) */
async function _widgetCall(url, body) {
  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    /* 静默 */
  }
}

/* agent 动词 focus 的前端执行面(§14.3):POST 裁决 → 滚动 + 高亮脉冲 */
async function widgetFocus(path) {
  try {
    const res = await fetch("/platform/api/widgets/focus", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) return;
  } catch {
    return;
  }
  const el = document.querySelector(`[data-reg-path="${path}"]`);
  if (el) {
    el.scrollIntoView?.();
    el.classList.add("pf-pulse");
    setTimeout(() => el.classList.remove("pf-pulse"), 900);
  }
}

async function _loadDetail(kind, ref, data) {
  try {
    // gate/plan/diff/esc/decompose:数据在卡内不拉取;pack/run 拉取(同一 Tab Surface 分发)
    if (["gate", "plan", "diff", "esc", "decompose"].includes(kind)) {
      return { kind, ref, html: renderTabSurface(kind, data) };
    }
    if (kind === "pack") {
      const closure = await (
        await fetch(`/api/lab/packages/${encodeURIComponent(ref)}/closure?mode=runtime`)
      ).json();
      return { kind, ref, html: renderTabSurface(kind, closure) };
    }
    if (kind === "run") {
      const [dRes, sRes] = await Promise.all([
        fetch(`/api/runs/${encodeURIComponent(ref)}`),
        fetch(`/api/runs/${encodeURIComponent(ref)}/signals`),
      ]);
      if (dRes.ok) {
        const [detail, signals] = await Promise.all([dRes.json(), sRes.json()]);
        // W3:launch schema 已知时发起面升级 W-form(未知/ad-hoc 保持 textarea)
        const launchSchema = detail?.skill ? await _skillInputs(detail.skill) : null;
        const mount = (host) => {
          if (launchSchema) {
            state._launchForm = mountFormEditor(host.querySelector("[data-launch-form]"), {
              schema: launchSchema,
            });
          }
          // W4:原始信号区挂 W-log(trace 主视图不动;kind 着色/跟随/复制)
          const rawLog = host.querySelector("[data-raw-log]");
          if (rawLog) {
            mountLogViewer(rawLog, {
              lines: (signals ?? []).map((s) => ({
                kind: s.type ?? s.name ?? "signal",
                text: JSON.stringify(s.payload ?? s),
              })),
            });
          }
        };
        return { kind, ref, html: renderTabSurface(kind, { detail, signals, launchSchema }), mount };
      }
      // M4a:ad-hoc run(iterate 等不走产物面)回落 instance state——
      // running 态 = 持 run_id 且未终态,不发明新标志位(v0.2 §4)
      const tab = state.tabs.find((t) => t.id === `d:run:${ref}`);
      if (tab?.instance) {
        const inst = await (await fetch(`/platform/api/apps/${encodeURIComponent(tab.instance)}`)).json();
        if (inst?.state?.status) {
          return {
            kind, ref,
            html: renderTabSurface(kind, {
              detail: {
                run_id: ref, skill: inst.state.skill ?? "",
                status: inst.state.status, result: inst.state.result ?? null, error: "",
              },
              signals: [],
            }),
          };
        }
      }
      throw new Error(`HTTP ${dRes.status}`);
    }
    if (kind === "doc") {
      // D1(docs/DOC-EDITOR.md §2):读面直给;写动作全走 tabAction 管道(§3)
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(ref)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const doc = await res.json();
      // D2:气泡种子(开关不丢;读面直给,与 doc 全文同一请求面)
      let bubblesData = [];
      try {
        bubblesData = await (await fetch(`/platform/api/docs/${encodeURIComponent(ref)}/bubbles`)).json();
      } catch {
        bubblesData = []; // 气泡面故障不挡编辑器(降级为空种子)
      }
      const mount = (host) => {
        state._docEditor = mountDocEditor(host, doc, {
          seedFlows: bubblesData,
          getTabInstance: () => state.tabs.find((t) => t.id === state.active),
          reload: async () => {
            state.detail = await _loadDetail("doc", ref, null);
            renderMain();
          },
          onViewChange: (view) => {
            const tab = state.tabs.find((t) => t.id === state.active);
            if (tab?.instance) {
              // meta.set = local(§3):视图偏好持久化进 instance.state(尽力面)
              fetch(`/platform/api/apps/${encodeURIComponent(tab.instance)}/actions/meta.set`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ surface: "tab", args: { view } }),
              }).catch(() => {});
            }
          },
        });
      };
      return { kind, ref, html: renderTabSurface(kind, doc), mount };
    }
    if (kind === "debug") {
      // M3:简化调试台(快照 = 旧 web 调试端点同形)
      const doc = await (await fetch(`/api/debug/sessions/${encodeURIComponent(ref)}`)).json();
      return { kind, ref, html: renderTabSurface(kind, doc) };
    }
    if (kind === "draft") {
      const doc = await (await fetch(`/api/lab/drafts/${encodeURIComponent(ref)}`)).json();
      return { kind, ref, html: renderTabSurface(kind, doc) };
    }
    // M4b legacy:runs = 深链摘要 + W-date 时间窗(W3);其余四页 = ES module 挂载
    if (kind === "runs") {
      const html = await _legacyRunsHtml();
      const mount = (host) => {
        const picker = mountDatePicker(host.querySelector("[data-browse-range]"), { mode: "range" });
        picker.on("change", ({ value }) => _renderRunsRows(host, value));
        _renderRunsRows(host, null);
      };
      return { kind, ref, html, mount };
    }
    if (_LEGACY_MODULE[kind]) return { kind, ref, mount: kind };
    return { kind, ref, error: `unknown detail kind: ${kind}` };
  } catch (e) {
    return { kind, ref, error: `${copy("platform.detail.error")}: ${e.message ?? e}` };
  }
}

function closeDetail(id) {
  const wasActive = state.active === id;
  // 关闭回退:普通 tab 回 conv;conv 自己可关(有桌面)——回下一个 tab 或桌面("");
  // 兜底:closeTab 的 fallback 可能指向刚被删的 conv,必须钳制到存在的 tab(或桌面)
  const { tabs, active, closed } = closeTab(state.tabs, id, id === "conv" ? "" : "conv");
  state.tabs = tabs;
  state.active = tabs.some((t) => t.id === active) ? active : (tabs[0]?.id ?? "");
  if (closed) state.closedTabs = pushClosed(state.closedTabs, closed); // M2:关闭≠销毁
  shellAction("shell.tab.close", { tab: id }); // M5:关闭 = shell action(回镜收敛)
  _widgetCall("/platform/api/widgets/unregister", { path: `/shell/tab/${id}/surface/tab` });
  if (wasActive || state.active === "") state.detail = null; // 关的是激活 tab(或回桌面)→ 详情作废
  renderTabs();
  renderMain();
}

/* 打开/回到对话 tab(conv 也可关:不在 tab 条时先按 conv 恒首语义补回) */
function openConversation() {
  if (!state.tabs.some((t) => t.id === "conv")) {
    state.tabs.unshift({ id: "conv", kind: "conversation", title: "", ref: "conv" });
    // conv 恒首 + 去重由服务端同一语义收敛(M5 增补:conv 可关后可再开)
    shellAction("shell.tab.open", { kind: "conversation", ref: "conv", id: "conv", title: copy("platform.tab.chat") });
  }
  return activateTab("conv");
}

/* 重开(M2):从最近关闭回到 tab 条并聚焦(同一 tab id/ref → 同 instance) */
function reopenTab(id) {
  const tab = state.closedTabs.find((t) => t.id === id);
  if (!tab) return;
  state.closedTabs = state.closedTabs.filter((t) => t.id !== id);
  const { tabs, active } = openTab(state.tabs, tab);
  state.tabs = tabs;
  state.active = active;
  shellAction("shell.tab.open", {
    id: tab.id, instance_id: tab.instance ?? "", kind: tab.kind, ref: tab.ref, title: tab.title,
  });
  renderTabs();
  renderMain();
}

/* ── 升权决策(W2):就地作答 + 轮询汇聚(系统主动开口)───────────── */

/* 把某 question_id 的卡标记为已决(改 state 里的卡数据,重渲即置灰) */
function _markResolved(questionId, resolved) {
  for (const m of state.messages) {
    for (const card of m.cards ?? []) {
      if (card.type === "escalation" && card.data?.question_id === questionId) {
        card.data.resolved = resolved;
      }
    }
  }
}

async function answerDecision(btn) {
  const qid = btn.dataset.decision;
  const answer = btn.dataset.answer;
  const instId = btn.dataset.appInst; // M1:有 instance 走新 action 管道(作答 = action id)
  btn.disabled = true;
  try {
    const res = instId
      ? await fetch(`/platform/api/apps/${encodeURIComponent(instId)}/actions/${encodeURIComponent(answer)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ surface: "card" }),
        })
      : await fetch(`/platform/api/decisions/${encodeURIComponent(qid)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answer }),
        });
    if (!res.ok) {
      // 404 = 已被别处处理(旧收件箱/另一标签页);400 = 答案不合(协议串没变,按已处理提示)
      _markResolved(qid, "gone");
    } else {
      _markResolved(qid, answer);
    }
  } catch (e) {
    toast(`${copy("platform.error")}: ${e.message ?? e}`, "error");
    btn.disabled = false; // 网络失败不算已决,允许重试
    return;
  }
  renderMain();
}

/* 轮询汇聚:新 pending 以 agent 消息 + escalation 卡进当前会话(服务端持久化);
   拉取失败静默——决策通道永远不能打断对话 */
async function pollDecisions() {
  if (!state.current) return;
  try {
    const res = await fetch(`/platform/api/sessions/${state.current}/decisions/present`, {
      method: "POST",
    });
    if (!res.ok) return;
    const { presented } = await res.json();
    if (presented?.length) {
      state.messages.push(...presented);
      renderMain();
    }
  } catch {
    /* 静默:下一周期再试 */
  }
}

/* 主动汇报(M4b):本会话发起的 run 到终态 → agent 消息进会话(SSE run.finished
   或轮询兜底触发;幂等,游标在服务端) */
async function presentRuns() {
  if (!state.current) return;
  try {
    const res = await fetch(`/platform/api/sessions/${state.current}/runs/present`, {
      method: "POST",
    });
    if (!res.ok) return;
    const { presented } = await res.json();
    if (presented?.length) {
      state.messages.push(...presented);
      renderMain();
    }
  } catch {
    /* 静默:下一周期再试 */
  }
}

/* SSE transport(M4b):decision.new/run.finished 即时推进;断线回落 5s 轮询
   (与 workbench 同哲学);EventSource 缺席(node 测试/老浏览器)直接轮询 */
let _es = null;
let _pollTimer = null;

function _startPolling() {
  if (_pollTimer) return;
  _pollTimer = setInterval(() => {
    pollDecisions();
    presentRuns();
  }, 5000);
  _pollTimer.unref?.();
  renderTray(); // M5 增补:live 指示降级态同步
}

function _stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
    renderTray(); // M5 增补:SSE 复活,live 指示回升
  }
}

function connectStream() {
  if (typeof EventSource === "undefined") {
    _startPolling();
    renderTray(); // M5 增补:EventSource 缺席(node/老浏览器)= poll 态
    return;
  }
  try {
    _es = new EventSource("/platform/api/stream");
  } catch {
    _startPolling();
    renderTray();
    return;
  }
  _es.addEventListener("decision.new", () => pollDecisions());
  _es.addEventListener("run.finished", () => presentRuns());
  _es.onopen = () => {
    _stopPolling(); // SSE 活了即停轮询(替代,不双轨)
    renderTray();
  };
  _es.onerror = () => {
    _es?.close();
    _es = null;
    _startPolling(); // 断线回落轮询
    renderTray();
  };
}

/* tab 面动作(M3,docs/APP-MODEL.md §4):全面动作与卡面同一管道——
   instance 取当前激活 tab(spawn 登记),surface="tab";动作后重渲当前 tab(活面) */
async function tabAction(btn) {
  const tab = state.tabs.find((t) => t.id === state.active);
  if (!tab?.instance) {
    toast(copy("platform.detail.loading"), "info"); // spawn 竞态:稍等再点
    return;
  }
  btn.disabled = true;
  try {
    // M4a:run.launch 的 input 来自发起面;W3:W-form 优先(逐字段校验),
    // 无表单时回 textarea JSON("高级:JSON" 或 schema 未知的面)
    let args = {};
    if (btn.dataset.tabAct === "run.launch") {
      if (state._launchForm) {
        if (!state._launchForm.validate()) {
          toast(copy("w.form.invalid"), "error");
          btn.disabled = false;
          return;
        }
        args = { input: state._launchForm.values() };
      } else {
        const raw = $("#detailHost")?.querySelector("[data-launch-input]")?.value?.trim();
        if (raw) {
          try {
            args = { input: JSON.parse(raw) };
          } catch {
            toast(copy("platform.run.launch.badjson"), "error");
            btn.disabled = false;
            return;
          }
        }
      }
    }
    // D1:doc 写动作的参数收集(§3 args_input 声明面)
    if (btn.dataset.tabAct === "doc.save") {
      args = { text: $("#detailHost")?.querySelector("[data-doc-text]")?.value ?? "" };
    }
    if (btn.dataset.tabAct === "doc.rewind") {
      args = { version: $("#detailHost")?.querySelector("[data-rewind-version]")?.value ?? "" };
    }
    const res = await fetch(
      `/platform/api/apps/${encodeURIComponent(tab.instance)}/actions/${encodeURIComponent(btn.dataset.tabAct)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ surface: "tab", args, session_id: state.current }),
      }
    );
    if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
    const result = await res.json();
    if (result.text) {
      state.messages.push({ role: "agent", text: result.text, cards: result.cards ?? [] });
    }
    if (result.run_instance?.ref) {
      // run 真通道(v0.2 §4):动作产出 run app —— 直接进它的 tab 看进展
      await openDetail("run", result.run_instance.ref, null);
      return;
    }
    state.detail = await _loadDetail(tab.kind, tab.ref, null);
    renderMain();
  } catch (e) {
    state.messages.push({ role: "agent", text: `${copy("platform.error")}: ${e.message ?? e}`, cards: [] });
    renderMain();
  } finally {
    btn.disabled = false;
  }
}

/* 开调试(M3 闭环:run tab → replay 调试会话 → debug tab;复用旧 web debug 端点) */
async function openDebug(runId) {
  try {
    const res = await fetch("/api/debug/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ replay_run_id: runId }),
    });
    if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
    const doc = await res.json();
    await openDetail("debug", doc.session_id, { run_id: doc.run_id ?? runId });
  } catch (e) {
    toast(`${copy("platform.error")}: ${e.message ?? e}`, "error");
  }
}

/* ── 事件 ─────────────────────────────────────────────────────── */

function bind() {
  $("#send").addEventListener("click", send);
  $("#intent").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  $("#newSession").addEventListener("click", newSession);
  $("#sessionSel").addEventListener("change", (e) => {
    if (e.target.value) selectSession(e.target.value);
  });
  document.addEventListener("click", (e) => {
    // ── M5 增补(桌面化):开始按钮/桌面图标/壁纸/标题栏/托盘 ──
    if (e.target.closest("#startBtn")) {
      state.startOpen = !state.startOpen;
      return renderStartMenu();
    }
    const deskOpen = e.target.closest("[data-desk-open]");
    if (deskOpen) return openDesktopIcon(deskOpen.dataset.deskOpen); // 桌面/开始菜单同源
    if (e.target.closest("[data-desk-wallpaper]")) return toggleWallpaper();
    if (state.startOpen && !e.target.closest("#startMenu")) {
      state.startOpen = false; // 点菜单外收起
      renderStartMenu();
    }
    if (e.target.closest("[data-win-min]")) return minimizeTab(); // — 回桌面(tab 保留)
    if (e.target.closest("[data-win-close]")) return closeDetail(state.active); // ✕ 关闭≠销毁
    if (e.target.closest("[data-tray-live]")) return reconnectStream();
    if (e.target.closest("[data-tray-theme]")) return cycleTheme();
    if (e.target.closest("[data-tray-inbox]")) return openConversation(); // 决策在对话里处理(conv 可关,补回再激活)
    const tabX = e.target.closest("[data-tab-x]");
    if (tabX) {
      e.stopPropagation(); // ✕ 不触发 tab 激活
      return closeDetail(tabX.dataset.tabX);
    }
    const reopen = e.target.closest("[data-reopen]");
    if (reopen) return reopenTab(reopen.dataset.reopen);
    const legacy = e.target.closest("[data-open-legacy]");
    if (legacy) return openDetail(legacy.dataset.openLegacy, legacy.dataset.openLegacy, {});
    const moveStart = e.target.closest("[data-move-start]");
    if (moveStart) {
      state.longPressTab = null;
      return shellAction("shell.layout.move_tab", { tab: moveStart.dataset.moveStart, before: "__start__" })
        .then(() => renderTabs());
    }
    const moveEnd = e.target.closest("[data-move-end]");
    if (moveEnd) {
      state.longPressTab = null;
      return shellAction("shell.layout.move_tab", { tab: moveEnd.dataset.moveEnd })
        .then(() => renderTabs());
    }
    if (e.target.closest("[data-icon-toggle]")) {
      return shellAction("shell.layout.set", { icon_mode: document.body.dataset.iconMode !== "1" });
    }
    const tab = e.target.closest("[data-tab]");
    if (tab) return activateTab(tab.dataset.tab);
    const link = e.target.closest("[data-detail-kind]");
    if (link) {
      const data = JSON.parse(link.dataset.detail ?? "{}");
      return openDetail(link.dataset.detailKind, link.dataset.detailRef, data);
    }
    if (e.target.closest("[data-it-retry]") && state.detail) {
      return openDetail(state.detail.kind, state.detail.ref, null);
    }
    const decision = e.target.closest("[data-decision]");
    if (decision) return answerDecision(decision);
    const dbg = e.target.closest("[data-debug-run]");
    if (dbg) return openDebug(dbg.dataset.debugRun);
    // D3 lab NOTES.md 接点:draft tab "编辑文档" → 建/开 notes.<draft> 的 doc tab
    const notes = e.target.closest("[data-open-notes]");
    if (notes) {
      return (async () => {
        const tab = state.tabs.find((t) => t.id === state.active);
        const draft = tab?.ref ?? "";
        if (!draft) return;
        const name = `notes.${draft}`;
        // 首开建文档(空种子;已存在 409 即直接开——只读+另存模式,不碰生产)
        await fetch("/platform/api/docs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, title: `${draft} 笔记`, text: `# ${draft} 笔记\n` }),
        }).catch(() => {});
        await openDetail("doc", name, {});
      })();
    }
    const tAct = e.target.closest("[data-tab-act]");
    if (tAct) return tabAction(tAct);
    const act = e.target.closest("[data-card-act]");
    if (act) return cardAction(act);
    const example = e.target.closest("[data-example]");
    if (example) {
      $("#intent").value = example.dataset.example;
      $("#intent").focus();
    }
  });
  document.addEventListener("keydown", (e) => {
    // tab 条键盘可达(Enter 激活;widget 标准)
    if (e.key === "Enter" && e.target.closest?.("[data-tab]")) {
      activateTab(e.target.closest("[data-tab]").dataset.tab);
    }
  });
}

/* ── 启动 ─────────────────────────────────────────────────────── */

function renderStaticCopy() {
  const sub = document.querySelector("[data-i18n='sub']");
  if (sub) sub.textContent = copy("platform.sub");
  $("#intent").placeholder = copy("platform.input.ph");
  $("#send").textContent = copy("platform.send");
  $("#startBtn")?.setAttribute("aria-label", copy("platform.start")); // M5 增补:开始按钮
}

mountThemes();
bind();
bindDnd(); // M5 §15:DnD v1(envelope 产出/消费)
bindLongPress(); // M5 §15.4:触屏降级(长按 = 非手势触发同一 action)
renderStaticCopy();
renderLauncher();
renderTabs();
renderMain();
// M5:shell 先行(tab 条消费 shell.state;不可达回落本地 tab 模型,降级面)
loadShell().finally(() => {
  renderTabs();
  renderMain();
  loadSessions().catch((e) => toast(e.message ?? String(e), "error"));
});
connectStream(); // M4b:SSE 主通道(断线/缺席自动回落轮询)

// 测试探针(node 冒烟用;浏览器无副作用)
if (typeof globalThis !== "undefined") {
  globalThis.__platform = {
    state, renderTabs, renderMain, loadSessions, openDetail, closeDetail, reopenTab,
    pollDecisions, presentRuns, connectStream, shellAction, widgetFocus, loadShell,
    // M5 增补(桌面化):桌面/开始菜单/标题栏/托盘的操作面
    desktopIcons, openDesktopIcon, minimizeTab, toggleWallpaper, cycleTheme,
    renderDesktop, renderTitlebar, renderTray,
    stream: () => _es,
    polling: () => Boolean(_pollTimer),
  };
}

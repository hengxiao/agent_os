/* Desktop widget(docs/DESKTOP-WIDGET.md §2/§3/§4;C4.1)——层级体系的根。

   本文件两个 def:
   - supervisor-inbox:系统件薄壳(预定义 slot;C4.1 包装 pending decisions
     展示,state.pending 由宿主适配层喂;完整升权交互留 C4.2+);
   - desktop:根 compound。layout 三分支(state 驱动,纯函数):
     桌面(active == null:壁纸 + 图标栅格)/ 单窗(active = id:窗口 chrome
     + 激活子 slot,其余子件 view 摘下 = §5 hidden 语义)/ 任务栏恒在底部
     (children 摘要行:glyph + 名称 + badge + 激活态;inbox 系统件渲染在
     托盘位,不进任务行)。
   行为层在宿主页面(activate/minimize/close/reorder 都是 local UI 操作,
   改 state + relayout;desktop 的 activate/close 事件由宿主适配层代发)。
   铁律:layout 纯函数;零 fetch;slotRefs 只读元信息(path/kind/surface/badge)。 */

import { getWidgetDef, registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { copy } from "../themes.js";

/* ── supervisor-inbox(系统件薄壳,C4.1)────────────────────────────── */

/* 渲染(纯):card = ✉ + 待办数 + 最近 3 条;tab = 全量清单。 */
export function renderSupervisorInbox(state, { surface = "card" } = {}) {
  const items = state.pending ?? [];
  const n = items.length;
  const head =
    `<div class="w-inbox-head"><span class="w-inbox-glyph" aria-hidden="true">✉</span>` +
    `<span class="w-inbox-title">${esc(copy("platform.tray.inbox"))}</span>` +
    `<span class="w-inbox-n" data-inbox-n="1">${n}</span></div>`;
  const shown = surface === "card" ? items.slice(0, 3) : items;
  const body = shown.length
    ? shown
        .map(
          (p) =>
            `<div class="w-inbox-item" data-inbox-item="${esc(p.id ?? "")}">` +
            `<span class="w-inbox-text">${esc(p.text ?? p.summary ?? "")}</span></div>`
        )
        .join("") +
      (surface === "card" && n > 3
        ? `<div class="w-inbox-more">… +${n - 3}</div>`
        : "")
    : ""; // 空态:头部 0 已足(C4.1 薄壳不新立 copy 键;空清单文案随 C4.2 完整功能)
  return `<div class="w-inbox" data-surface="${esc(surface)}">${head}${body}</div>`;
}

export const SUPERVISOR_INBOX_DEF = registerWidgetDef({
  kind: "supervisor-inbox",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { pending: [] },
  actions: [],
  events: ["change", "open"], // open = card 整卡点击(§1.4);change 负载可带 badge(§7 补丁)
  aria: { role: "complementary", label: "收件箱" },
  surfaces: ["card", "tab"],
  render: renderSupervisorInbox,
  mount: mountSupervisorInbox,
});

/* 挂进宿主(C4.1 薄壳:pending 由宿主适配层喂 state;badge = pending 数,
   由宿主 emit change {badge} 记账——真实升权流 C4.2 接管)。 */
export function mountSupervisorInbox(host, { pending = [], surface = "card", path = "" } = {}) {
  const widget = createWidget(SUPERVISOR_INBOX_DEF, { path, state: { pending } });
  const render = () => {
    host.innerHTML = renderSupervisorInbox(widget.state, { surface });
  };
  widget.update = () => render();
  if (surface === "card") bindCardOpen(host, widget);
  render();
  widget.register("");
  return widget;
}

/* ── desktop(根 compound)─────────────────────────────────────────── */

/* 顺序解析(纯;layout 与宿主 reorder 共用):state.*_order 先(过滤已删
   子件),新增子件按注册序补尾。 */
export function orderedIds(ids, order) {
  const o = (order ?? []).filter((id) => ids.includes(id));
  return [...o, ...ids.filter((id) => !o.includes(id))];
}

/* 子件标题/图标位(纯):aria.label 为标题,首字为 glyph(与 platform 壳
   tab 同源惯例);图标 = 图标位非 card 面缩略(DESKTOP-WIDGET §3-1 允许)。 */
function _meta(kind) {
  const label = String(getWidgetDef(kind)?.aria?.label ?? kind ?? "?");
  return { label, glyph: label.trim().charAt(0) || "?" };
}

function _badgeHtml(n, attr = "") {
  return n ? `<span class="dt-badge"${attr}>${Number(n)}</span>` : "";
}

/* layout(§3 三分支,纯函数):
   1) active == null → 桌面:壁纸 + 图标栅格(每个动态子件一图标);
   2) active = id → 单窗:标题栏(glyph/标题/—/✕)+ 激活子 slot;
   3) 任务栏恒在:摘要行(读 slotRefs + badge)+ 托盘 inbox 系统件。 */
export function renderDesktopLayout(state, slotRefs) {
  const ids = Object.keys(slotRefs ?? {}).filter((id) => id !== "inbox");
  const taskIds = orderedIds(ids, state?.taskbar_order);
  const iconIds = orderedIds(ids, state?.icon_order);
  const active = state?.active && slotRefs[state.active] ? state.active : null;

  const taskbarRows = taskIds
    .map((id) => {
      const r = slotRefs[id];
      const m = _meta(r.kind);
      return (
        `<div class="dt-task" role="button" tabindex="0" data-desk-task="${esc(id)}" ` +
        `data-active="${active === id ? "1" : "0"}" aria-label="${esc(m.label)}">` +
        `<span class="dt-glyph" aria-hidden="true">${esc(m.glyph)}</span>` +
        `<span class="dt-name">${esc(m.label)}</span>` +
        _badgeHtml(r.badge, ` data-desk-task-badge="${esc(id)}"`) +
        `<button class="dt-x" data-desk-close="${esc(id)}" aria-label="关闭 ${esc(m.label)}" ` +
        `title="再点一次确认关闭">✕</button></div>`
      );
    })
    .join("");

  const inboxBadge = slotRefs.inbox?.badge ?? null;
  const tray =
    `<div class="dt-tray"><div class="dt-tray-inbox">` +
    `<div data-slot="inbox"></div>` +
    _badgeHtml(inboxBadge, ' data-desk-inbox-badge="1"') +
    `</div></div>`;
  const taskbar =
    `<div class="dt-taskbar"><div class="dt-tasks">${taskbarRows}</div>${tray}</div>`;

  let main;
  if (active) {
    const r = slotRefs[active];
    const m = _meta(r.kind);
    main =
      `<div class="dt-win">` +
      `<div class="dt-titlebar">` +
      `<span class="dt-glyph" aria-hidden="true">${esc(m.glyph)}</span>` +
      `<span class="dt-title">${esc(m.label)}</span>` +
      `<span class="dt-spacer"></span>` +
      `<button class="dt-btn" data-desk-min="${esc(active)}" aria-label="最小化">—</button>` +
      `<button class="dt-btn" data-desk-close="${esc(active)}" aria-label="关闭 ${esc(m.label)}" ` +
      `title="再点一次确认关闭">✕</button>` +
      `</div>` +
      `<div class="dt-win-body"><div data-slot="${esc(active)}"></div></div>` +
      `</div>`;
  } else {
    const icons = iconIds
      .map((id) => {
        const r = slotRefs[id];
        const m = _meta(r.kind);
        return (
          `<button class="dt-icon" data-desk-open="${esc(id)}" aria-label="打开 ${esc(m.label)}">` +
          `<span class="dt-glyph" aria-hidden="true">${esc(m.glyph)}</span>` +
          `<span class="dt-name">${esc(m.label)}</span>` +
          _badgeHtml(r.badge) +
          `</button>`
        );
      })
      .join("");
    main =
      `<div class="dt-desk" data-wallpaper="${esc(state?.wallpaper ?? "default")}">` +
      `<div class="dt-icons">${icons}</div>` +
      `</div>`;
  }

  return `<div class="dt-root" data-active="${esc(active ?? "")}">${main}${taskbar}</div>`;
}

export const DESKTOP_DEF = registerWidgetDef({
  kind: "desktop",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { wallpaper: "default", active: null, icon_order: [], taskbar_order: [], badges: {} },
  actions: [], // activate/close/reorder = local UI 操作,宿主适配层直改 state(红线:action 三态不进 widget)
  events: ["activate", "close"], // child_event/reparent 由基座自动补(§2)
  aria: { role: "application", label: "桌面" },
  surfaces: ["tab"], // desktop 只有完整面(它就是根)
  compound: {
    slots: [
      { id: "inbox", kind: "supervisor-inbox", surface: "card" }, // 预定义系统件(托盘)
    ],
    dynamic: {
      allow: ["conversation", "doc-editor", "skills-explorer", "runs-explorer",
        "tools-explorer", "lab", "debug-console"],
      max: 30,
    },
    layout: renderDesktopLayout,
  },
});

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

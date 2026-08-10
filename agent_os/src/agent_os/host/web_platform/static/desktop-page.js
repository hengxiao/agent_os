/* Desktop page 驱动(docs/DESKTOP-WIDGET.md §6 C4.1;开发验证页,与旧壳并存)。

   职责(compound 语义对照 §1):
   - createCompound(DESKTOP_DEF, path "/root") + mount_view——窗口/任务栏/
     图标 chrome 全由 desktop layout 产出(三分支,state 驱动);
   - 行为层 = local UI 操作:activate/minimize/close/reorder 直改 state +
     relayout(红线:action 三态不进 widget;desktop 的 activate/close 事件
     由本适配层代发);
   - 演示子件 = 薄壳占位 app(conversation/runs-explorer;真实 app 迁移是
     C4.2/C4.3);inbox 系统件由本层喂 pending + emit badge(真实升权流
     C4.2 接管)。 */

import { initTheme } from "/static/js/themes.js";
import { BUILD } from "/static/js/widget-sandbox.js";
import {
  createCompound, createWidget, registerWidgetDef, orderedIds, DESKTOP_DEF,
} from "/static/js/widgets/index.js";

/* ── 占位 app(C4.1 演示子件;薄壳:一个可输入的状态面,证明 hidden 语义)── */

function _renderConv(state) {
  return (
    `<div class="w-app" data-app="conversation">` +
    `<div class="w-app-log" data-conv-log="1">` +
    (state.log ?? [])
      .map((m) => `<div class="w-app-msg" data-role="${esc(m.role)}">${esc(m.text)}</div>`)
      .join("") +
    `</div>` +
    `<input class="input" data-conv-draft="1" value="${esc(state.draft ?? "")}" ` +
    `placeholder="说点什么,Enter 发送(最小化重开不丢)">` +
    `</div>`
  );
}

function mountConversation(host, { path = "" } = {}) {
  const widget = createWidget(CONVERSATION_DEF, { path });
  const render = () => {
    host.innerHTML = _renderConv(widget.state);
  };
  widget.update = () => render();
  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-conv-draft]")) widget.state.draft = e.target.value;
  });
  host.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target.closest("[data-conv-draft]")) return;
    e.preventDefault?.();
    const text = String(widget.state.draft ?? "").trim();
    if (!text) return;
    widget.state.log = [...(widget.state.log ?? []), { role: "user", text }];
    widget.state.draft = "";
    render();
    widget.emit("change", { lines: widget.state.log.length });
  });
  render();
  return widget;
}

const CONVERSATION_DEF = registerWidgetDef({
  kind: "conversation",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { log: [], draft: "" },
  actions: [],
  events: ["change"],
  aria: { role: "application", label: "对话" },
  surfaces: ["tab"],
  mount: mountConversation,
});

function _renderRuns(state) {
  const rows = (state.runs ?? []).filter((r) => !state.filter || r.name.includes(state.filter));
  return (
    `<div class="w-app" data-app="runs-explorer">` +
    `<input class="input" data-runs-filter="1" value="${esc(state.filter ?? "")}" placeholder="筛选运行(Enter 生效)">` +
    `<div class="w-app-lines" data-runs-lines="1">` +
    rows
      .map((r) => `<div class="w-app-line" data-kind="${esc(r.kind)}">${esc(r.name)}</div>`)
      .join("") +
    `</div></div>`
  );
}

function mountRunsExplorer(host, { path = "" } = {}) {
  const widget = createWidget(RUNS_EXPLORER_DEF, { path });
  const render = () => {
    host.innerHTML = _renderRuns(widget.state);
  };
  widget.update = () => render();
  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-runs-filter]")) widget.state.filter = e.target.value;
  });
  host.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target.closest("[data-runs-filter]")) return;
    e.preventDefault?.();
    render();
    widget.emit("change", { filter: widget.state.filter });
  });
  render();
  return widget;
}

const RUNS_EXPLORER_DEF = registerWidgetDef({
  kind: "runs-explorer",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: {
    filter: "",
    runs: [
      { name: "weather.query · 成功 · 2m", kind: "ok" },
      { name: "news.digest · 失败 · 1h", kind: "err" },
      { name: "doc.review · 成功 · 3h", kind: "ok" },
    ],
  },
  actions: [],
  events: ["change"],
  aria: { role: "application", label: "运行" },
  surfaces: ["tab"],
  mount: mountRunsExplorer,
});

const APPS = [
  { kind: "conversation", label: "对话" },
  { kind: "runs-explorer", label: "运行" },
];

/* ── 启动 ────────────────────────────────────────────────────────── */

export function bootDesktop() {
  const $ = (sel) => document.querySelector(sel);
  initTheme();
  $("#dt-build").textContent = `build ${BUILD}`;

  const inst = createCompound(DESKTOP_DEF, { path: "/root" }); // 全树寻址自此唯一
  const host = $("#dt-root");
  inst.mount_view(host);
  globalThis.__desktop = inst; // 调试/测试钩(开发验证页)

  const log = [];
  const _log = (line) => {
    log.unshift(`${new Date().toISOString().slice(11, 19)}  ${line}`);
    $("#dt-events").textContent = log.slice(0, 6).join("\n");
  };

  /* 行为层(local;§3/§4):activate = state.active + relayout;最小化 =
     激活位清空(子 view 摘下,§5 hidden:instance/state/context 照旧) */
  const activate = (id) => {
    if (id && !inst.child(id)) return;
    inst.state.active = id ?? null;
    inst.relayout();
    inst.emit("activate", { id: id ?? null });
    _log(`activate ${id ?? "(桌面)"}`);
  };

  /* 关闭 = remove_child(destroy);两段确认(再点一次执行) */
  let armed = null;
  let armedTimer = null;
  const close = (id, btn) => {
    if (armed !== id) {
      armed = id;
      btn?.classList?.add("dt-arm");
      clearTimeout(armedTimer);
      armedTimer = setTimeout(() => {
        armed = null;
        btn?.classList?.remove("dt-arm");
      }, 2500);
      return;
    }
    armed = null;
    clearTimeout(armedTimer);
    inst.state.taskbar_order = (inst.state.taskbar_order ?? []).filter((x) => x !== id);
    inst.state.icon_order = (inst.state.icon_order ?? []).filter((x) => x !== id);
    if (inst.state.active === id) inst.state.active = null;
    inst.remove_child(id, { destroy: true }); // 内部 relayout(§4)
    inst.emit("close", { id });
    _log(`close ${id}`);
  };

  /* 重排 = taskbar_order state 变更 → relayout(持久于 state,可序列化) */
  const reorder = (dragId, targetId, before) => {
    const ids = orderedIds(
      inst.children_snapshot().map((s) => s.id).filter((id) => id !== "inbox"),
      inst.state.taskbar_order
    );
    ids.splice(ids.indexOf(dragId), 1);
    const at = ids.indexOf(targetId);
    ids.splice(before ? at : at + 1, 0, dragId);
    inst.state.taskbar_order = ids;
    inst.relayout();
    _log(`reorder ${ids.join(" → ")}`);
  };

  const openApp = (kind) => {
    if (inst.child(kind)) return activate(kind); // 已开 = 聚焦(同 id 不再生)
    inst.add_child(kind, { slot: kind }); // id = kind(演示期唯一实例)
    activate(kind);
  };

  /* 事件委托(chrome 全部 data-desk-*;layout 重渲不伤) */
  let suppressClick = false;
  host.addEventListener("click", (e) => {
    const x = e.target.closest("[data-desk-close]");
    if (x) return close(x.dataset.deskClose, x);
    if (e.target.closest("[data-desk-min]")) return activate(null);
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    const open = e.target.closest("[data-desk-open],[data-desk-task]");
    if (open) activate(open.dataset.deskOpen || open.dataset.deskTask);
  });

  /* 任务栏拖拽重排(pointer 阈值 8px;落点过中线判前后) */
  let drag = null;
  host.addEventListener("pointerdown", (e) => {
    const row = e.target.closest("[data-desk-task]");
    if (!row || e.target.closest("[data-desk-close]")) return;
    drag = { id: row.dataset.deskTask, x: e.clientX, on: false };
  });
  host.addEventListener("pointermove", (e) => {
    if (drag && !drag.on && Math.abs(e.clientX - drag.x) > 8) drag.on = true;
  });
  host.addEventListener("pointerup", (e) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (!d.on) return; // 小位移 = 点击(走 click 激活)
    suppressClick = true;
    // 拖拽收尾若派生 click(同任务序列 mouseup→click)须吞;下一拍清零,
    // 否则未派生 click 时旗标滞留会误吞后续真实点击(tests-ui 抓出)
    setTimeout(() => { suppressClick = false; }, 0);
    const over = e.target.closest("[data-desk-task]");
    if (!over || over.dataset.deskTask === d.id) return;
    const r = over.getBoundingClientRect();
    reorder(d.id, over.dataset.deskTask, e.clientX < r.x + r.width / 2);
  });

  /* 发起面(演示):顶栏选 app 打开;已开 = 聚焦 */
  const sel = $("#dt-kind");
  sel.innerHTML = APPS.map((a) => `<option value="${a.kind}">${a.label}</option>`).join("");
  $("#dt-open").addEventListener("click", () => openApp(sel.value));

  /* inbox 系统件:薄壳喂 pending;badge = pending 数(§7 补丁,闸门记账);
     「模拟升权」推一条并记 badge——升权到达有徽标(§7 验收) */
  const inbox = inst.child("inbox");
  inbox.state.pending = [
    { id: "d-1", text: "批准发布 weather.query v3" },
    { id: "d-2", text: "确认删除草稿 notes.old" },
  ];
  inbox.emit("change", { badge: inbox.state.pending.length });
  $("#dt-escalate").addEventListener("click", () => {
    const n = inbox.state.pending.length + 1;
    inbox.state.pending = [...inbox.state.pending, { id: `d-${n}`, text: `升权请求 #${n}(演示)` }];
    inbox.emit("change", { badge: inbox.state.pending.length });
  });

  inst.on("child_event", (p) => {
    if (p.payload && typeof p.payload === "object" && "badge" in p.payload) {
      _log(`badge ${p.child} = ${p.payload.badge}`);
    }
  });

  /* 种子:两个演示 app 以最小化态进桌面(图标栅格 + 任务栏行) */
  inst.add_child("conversation", { slot: "conversation" });
  inst.add_child("runs-explorer", { slot: "runs-explorer" });
  _log("desktop boot /root");
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

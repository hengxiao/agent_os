/* Desktop page 驱动(docs/DESKTOP-WIDGET.md §6;C4.2:真实 app 接入)。

   职责(compound 语义对照 §1):
   - createCompound(DESKTOP_DEF, path "/root") + mount_view——窗口/任务栏/
     图标 chrome 全由 desktop layout 产出(三分支,state 驱动);
   - 行为层 = local UI 操作:activate/minimize/close/reorder 直改 state +
     relayout(红线:action 三态不进 widget;desktop 的 activate/close 事件
     由本适配层代发);
   - app 接入(§5 两种来源):
     conversation = 薄壳真实 app(conversation-app.js 工厂,一会话一实例;
       顶栏「+ 新对话」= 新会话新实例);
     doc-editor 直进(§5-2):对话流文档卡「打开详情」→ 工厂建实例 →
       attach_existing 进窗口区(同名聚焦不重复);窗口区 tab 面 = 完整编辑器,
       对话流 doc 卡 = card 面活视图(link_view 重挂,hard link 同一实例);
     runs-explorer 仍是占位(C4.3 换);
   - inbox 真实化:/platform/api/decisions 轮询 + SSE decision.new 扇入,
     pending 行进 state,badge = pending 数(§7 补丁;C4.1 模拟升权退役);
   - SSE 单源(/platform/api/stream):decision.new/run.finished 扇给各
     conversation 实例的轮询汇聚 + inbox 刷新;断线回落 5s 轮询(同 app.js)。 */

import { initTheme } from "/static/js/themes.js";
import { BUILD } from "/static/js/widget-sandbox.js";
import {
  createCompound, createWidget, registerWidgetDef, orderedIds, DESKTOP_DEF,
} from "/static/js/widgets/index.js";
import { createConversation } from "./conversation-app.js";
import { createDocEditor } from "./doc-editor.js";

/* ── 占位 app(runs-explorer;C4.3 换真实件)────────────────────────── */

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

  /* ── 行为层(C4.1 不动;local;§3/§4)── */
  const activate = (id) => {
    if (id && !inst.child(id)) return;
    inst.state.active = id ?? null;
    inst.relayout();
    inst.emit("activate", { id: id ?? null });
    _hangLiveDocCards(); // 挂接重出后活卡按连接态补挂(log 未重渲也得回来)
    _log(`activate ${id ?? "(桌面)"}`);
  };

  const openDocs = new Map(); // 文档名 → {inst, ed, liveView, liveHost}(hard link 账)
  const convs = new Map(); // id → conversation api(SSE/轮询扇入面;close 清)
  let convSeq = 0; // 新对话 id 序号(唯一,不看集合大小)

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
    // doc 窗口:活卡 view 摘下,hard link 账清(C4.2)
    const docRec = openDocs.get(id);
    if (docRec) {
      docRec.liveView?.detach?.();
      openDocs.delete(id);
    }
    convs.delete(id); // conversation 关闭 → 轮询扇入面清(实例已 destroy)
    inst.remove_child(id, { destroy: true }); // 内部 relayout(§4)
    inst.emit("close", { id });
    _log(`close ${id}`);
  };

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

  /* ── doc-editor 直进(§5-2):对话流文档卡「打开详情」→ 窗口区 ── */
  async function openDocWindow(name) {
    if (!name) return;
    if (inst.child(name)) {
      activate(name); // 同名聚焦(同一 instance,不重复开)
      return;
    }
    try {
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const doc = await res.json();
      let bubbles = [];
      try {
        bubbles = await (await fetch(`/platform/api/docs/${encodeURIComponent(name)}/bubbles`)).json();
      } catch {
        bubbles = []; // 气泡面故障不挡编辑器(同 app.js 降级)
      }
      const ed = createDocEditor(doc, {
        seedFlows: bubbles,
        getTabInstance: () => null, // 写动作平台管道 C4.3 接(见 §9 偏差注)
        reload: async () => {
          const fresh = await (await fetch(`/platform/api/docs/${encodeURIComponent(name)}`)).json();
          ed.api.setText(fresh.text ?? "");
        },
      });
      ed.compound._compoundId = name; // 身份即文档名(attach 后 id = slot = 文档名)
      inst.attach_existing(ed.compound, { slot: name, surface: "tab" });
      openDocs.set(name, { inst: ed.compound, ed, liveView: null, liveHost: null });
      activate(name);
      _hangLiveDocCards(); // 对话流 doc 卡 → card 面活视图(hard link)
      _log(`open-doc ${name}`);
    } catch (err) {
      _log(`open-doc ${name} 失败:${err.message ?? err}`);
    }
  }

  /* doc 活卡重挂(hard link §5):对话流里的文档卡,已开窗口的挂上同一
     instance 的 card 面活视图;log 重渲 wiping 后按连接态补挂。 */
  function _hangLiveDocCards() {
    const root = $("#dt-root");
    if (!root) return;
    for (const [name, rec] of openDocs) {
      if (rec.liveHost && !rec.liveHost.isConnected) {
        rec.liveView?.detach?.(); // 旧 host 已被 log 重渲摘掉,view 同步清
        rec.liveView = null;
        rec.liveHost = null;
      }
      if (rec.liveView) continue;
      const link = root.querySelector(`[data-detail-kind="doc"][data-detail-ref="${name}"]`);
      const card = link?.closest(".pf-card");
      if (!card) continue;
      const live = document.createElement("div");
      live.className = "cv-doc-live";
      card.appendChild(live);
      rec.liveView = rec.inst.link_view(live, { surface: "card" }); // card 面 = 内容预览(§5)
      rec.liveHost = live;
    }
  }

  /* ── conversation 接入(§5-1;薄壳真实 app)── */
  async function openConversation(load) {
    const conv = await createConversation({ load, onOpenDoc: openDocWindow });
    conv.inst._compoundId = load === "new" ? `conv-${++convSeq}` : "conversation";
    conv.api.onLogRendered = _hangLiveDocCards;
    convs.set(conv.inst._compoundId, conv.api);
    inst.attach_existing(conv.inst, { surface: "tab" });
    return conv;
  }

  /* ── inbox 真实化(C4.2):/api/decisions 读面;badge = pending 数 ── */
  const inbox = inst.child("inbox");
  async function refreshInbox() {
    try {
      const rows = await (await fetch("/platform/api/decisions")).json();
      inbox.state.pending = (rows ?? []).map((r) => ({ id: r.question_id, ...r }));
      inbox.emit("change", { badge: inbox.state.pending.length }); // §7 补丁:闸门记账
    } catch {
      /* 读面故障不挡桌面(fail-safe,同 app.js 决策轮询静默) */
    }
  }

  /* ── SSE 单源(同 app.js):扇入 inbox 刷新 + 各 conversation 轮询汇聚 ── */
  let pollTimer = null;
  const _fanPoll = () => {
    refreshInbox();
    for (const c of convs.values()) c.poll();
  };
  const _startPoll = () => {
    if (pollTimer) return;
    pollTimer = setInterval(_fanPoll, 5000);
    pollTimer.unref?.();
  };
  const connectStream = () => {
    if (typeof EventSource === "undefined") return _startPoll();
    try {
      const es = new EventSource("/platform/api/stream");
      es.addEventListener("decision.new", _fanPoll);
      es.addEventListener("run.finished", _fanPoll);
      es.onerror = () => {
        es.close();
        _startPoll(); // 断线回落轮询(不双轨)
      };
    } catch {
      _startPoll();
    }
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

  /* 任务栏拖拽重排(C4.1 不动) */
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
    if (!d.on) return;
    suppressClick = true;
    // 拖拽收尾若派生 click(同任务序列)须吞;下一拍清零防误吞后续真实点击
    setTimeout(() => { suppressClick = false; }, 0);
    const over = e.target.closest("[data-desk-task]");
    if (!over || over.dataset.deskTask === d.id) return;
    const r = over.getBoundingClientRect();
    reorder(d.id, over.dataset.deskTask, e.clientX < r.x + r.width / 2);
  });

  /* 发起面:顶栏「+ 新对话」(新会话新实例)/占位 app 打开 */
  $("#dt-newconv").addEventListener("click", async () => {
    const conv = await openConversation("new");
    activate(conv.inst._compoundId);
  });
  $("#dt-open").addEventListener("click", () => {
    if (inst.child("runs-explorer")) return activate("runs-explorer");
    inst.add_child("runs-explorer", { slot: "runs-explorer" });
    activate("runs-explorer");
  });

  /* desktop 事件面:inbox 整卡 open → 回对话(决策在对话里处理,同旧托盘) */
  inst.on("child_event", (p) => {
    if (p.child === "inbox" && p.event === "open") {
      const convId = inst.child("conversation") ? "conversation" : null;
      if (convId) activate(convId);
    }
  });

  /* 种子(异步;会话/决策读面):boot conversation(最新会话)+ 占位运行 +
     inbox 真实 pending + SSE 单源 */
  (async () => {
    await openConversation("latest");
    inst.add_child("runs-explorer", { slot: "runs-explorer" });
    await refreshInbox();
    connectStream();
    _log("desktop boot /root");
  })().catch((err) => _log(`boot 失败:${err.message ?? err}`));
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

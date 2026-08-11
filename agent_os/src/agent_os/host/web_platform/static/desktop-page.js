/* Desktop page 驱动(docs/DESKTOP-WIDGET.md §6;C4.3:五 explorer 薄壳接入)。

   职责(compound 语义对照 §1):
   - createCompound(DESKTOP_DEF, path "/root") + mount_view——窗口/任务栏/
     图标 chrome 全由 desktop layout 产出(三分支,state 驱动);
   - 行为层 = local UI 操作:activate/minimize/close/reorder 直改 state +
     relayout(红线:action 三态不进 widget;desktop 的 activate/close 事件
     由本适配层代发);
   - app 接入(§5):conversation/doc-editor(C4.2 接线不动);
     五 explorer 薄壳(explorer-apps.js 工厂,C4.3):skills/runs/tools/lab/
     debug-console——顶栏选件打开,detail 链接按 kind 路由(已开 activate+
     locate,未开工厂 + attach_existing 再 locate);
   - 卡 DnD(APP-MODEL §15,envelope 不变):对话卡拖入任务栏 = 按卡型路由
     (同 detail 链接);拖放协议三键(source/source_kind/ref)照旧;
   - conversation 未读 badge(C4.3 §7 小注):arrived 经闸门按可见性记账——
     激活不记(改写 badge:null)/最小化累记;激活时清账;
   - inbox 真实化 + SSE 单源(C4.2 不动)。 */

import { initTheme } from "/static/js/themes.js";
import { BUILD } from "/static/js/widget-sandbox.js";
import { createCompound, orderedIds, DESKTOP_DEF } from "/static/js/widgets/index.js";
import { contextCascade } from "/static/js/widgets/cascade.js";
import { createConversation } from "./conversation-app.js";
import { createDocEditor } from "./doc-editor.js";
import {
  createSkillsExplorer, createRunsExplorer, createToolsExplorer,
  createLabApp, createDebugConsole,
} from "./explorer-apps.js";

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

  const openDocs = new Map(); // 文档名 → {inst, ed, liveView, liveHost}(hard link 账)
  const convs = new Map(); // id → conversation api(SSE/轮询扇入面;close 清)
  const apps = new Map(); // kind → explorer 工厂产物 {inst, locate}(close 清)
  const unseen = new Map(); // conversation id → 未读计数(未读 badge 的父侧账,§7 小注)
  let convSeq = 0;

  /* ── 未读 badge 闸门(C4.3 §7 小注:可见性在父不在子)──
     conversation 的 change 带 arrived(agent 新消息数):激活子不记
     (改写 badge:null 摘徽),最小化子累记;activate 时清账。 */
  DESKTOP_DEF.compound.on_child_event = (childInst, event, payload) => {
    const id = childInst?._compoundId ?? "";
    if (event === "change" && payload && typeof payload === "object" && "arrived" in payload) {
      if (inst.state.active === id) {
        unseen.delete(id);
        return { payload: { ...payload, badge: null } }; // 激活不记(摘徽)
      }
      const n = (unseen.get(id) ?? 0) + Number(payload.arrived ?? 0);
      unseen.set(id, n);
      return { payload: { ...payload, badge: n } }; // 最小化累记
    }
    return true; // 其余事件照放(含 inbox open)
  };

  /* ── 行为层(C4.1 不动;local;§3/§4)── */
  const activate = (id) => {
    if (id && !inst.child(id)) return;
    unseen.delete(id); // 激活清未读账(§7 小注:可见性在父)
    if (id && inst.state.badges?.[id]) delete inst.state.badges[id];
    inst.state.active = id ?? null;
    inst.relayout();
    inst.emit("activate", { id: id ?? null });
    _hangLiveDocCards();
    _log(`activate ${id ?? "(桌面)"}`);
  };

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
    const docRec = openDocs.get(id);
    if (docRec) {
      docRec.liveView?.detach?.();
      openDocs.delete(id);
    }
    convs.delete(id);
    apps.delete(id); // explorer 实例随 remove_child destroy(工厂 destroy 链 close 模块)
    unseen.delete(id);
    inst.remove_child(id, { destroy: true });
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

  /* ── doc-editor 直进(§5-2,C4.2)+ 写动作管道(C4.4 偏差清零)──
     开窗即 spawn doc app 实例(与旧壳 _spawnForTab 同参):snapshot/rewind/
     export/apply 四动作走同一 app action 管道(三态 exec 不动) */
  async function openDocWindow(name) {
    if (!name) return;
    if (inst.child(name)) {
      activate(name);
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
        bubbles = [];
      }
      const rec = { spawnId: null, ed: null, inst: null, liveView: null, liveHost: null };
      const ed = createDocEditor(doc, {
        seedFlows: bubbles,
        getTabInstance: () => ({ instance: rec.spawnId }), // 写动作实例面(spawn 后回填)
        reload: async () => {
          const fresh = await (await fetch(`/platform/api/docs/${encodeURIComponent(name)}`)).json();
          ed.api.setText(fresh.text ?? "");
        },
      });
      rec.ed = ed;
      rec.inst = ed.compound;
      ed.compound._compoundId = name;
      inst.attach_existing(ed.compound, { slot: name, surface: "tab", title: name });
      ed.api._rebindAppProvider?.(`/root/${name}`); // 级联改址随 reparent(§6;app 级 provider 在基座外)
      openDocs.set(name, rec);
      // spawn(C4.4;失败不阻断编辑面——数据面增强,不是依赖,同旧壳语义)
      try {
        const sp = await fetch("/platform/api/apps/spawn", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kind: "doc", ref: name, title: name,
            state: { name, text: "", dirty: false, savedAt: 0, view: "split", versions: [], bubbles: [] },
            created_by: convs.get("conversation")?.compound.state.session?.id ?? "",
          }),
        });
        if (sp.ok) rec.spawnId = (await sp.json()).instance?.id ?? null;
      } catch {
        rec.spawnId = null;
      }
      activate(name);
      _hangLiveDocCards();
      _log(`open-doc ${name}`);
    } catch (err) {
      _log(`open-doc ${name} 失败:${err.message ?? err}`);
    }
  }

  /* doc 写动作(C4.4):snapshot/rewind 按钮(data-tab-act,docTabHtml 工具条)
     与 export/apply(doc-editor 内部 getTabInstance)同管道——POST app action,
     结果以 agent 消息进 boot conversation(与旧壳 tabAction 同语义);
     rewind 后重拉全文(版本回滚 → 内容变) */
  async function docTabAction(btn, name, rec) {
    const actId = btn.dataset.tabAct;
    btn.disabled = true;
    try {
      const args = {};
      if (actId === "doc.rewind") {
        args.version = btn.closest("[data-slot]")?.querySelector("[data-rewind-version]")?.value ?? "";
      }
      const res = await fetch(
        `/platform/api/apps/${encodeURIComponent(rec.spawnId)}/actions/${encodeURIComponent(actId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            surface: "tab",
            args,
            session_id: convs.get("conversation")?.compound.state.session?.id ?? "",
            cascade: contextCascade(`/doc/${name}`).cascade, // §17.7-3 级联信封(同旧壳)
          }),
        }
      );
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const result = await res.json();
      if (result.text) {
        const conv = convs.get("conversation");
        if (conv) {
          conv.compound.state.messages = [
            ...conv.compound.state.messages,
            { role: "agent", text: result.text, cards: result.cards ?? [] },
          ];
          conv.refresh();
        }
      }
      if (actId === "doc.rewind") {
        const fresh = await (await fetch(`/platform/api/docs/${encodeURIComponent(name)}`)).json();
        rec.ed.api.setText(fresh.text ?? ""); // 回滚后全文重拉(同旧壳 tabAction 重渲)
      }
      _log(`${actId} ${name} ✓`);
    } catch (err) {
      _log(`${actId} ${name} 失败:${err.message ?? err}`);
    } finally {
      btn.disabled = false;
    }
  }

  function _hangLiveDocCards() {
    const root = $("#dt-root");
    if (!root) return;
    for (const [name, rec] of openDocs) {
      if (rec.liveHost && !rec.liveHost.isConnected) {
        rec.liveView?.detach?.();
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
      rec.liveView = rec.inst.link_view(live, { surface: "card" });
      rec.liveHost = live;
    }
  }

  /* ── explorer 接入(C4.3):已开 activate + locate,未开工厂 + attach + locate ── */
  const EXPLORERS = {
    "skills-explorer": createSkillsExplorer,
    "runs-explorer": createRunsExplorer,
    "tools-explorer": createToolsExplorer,
    lab: createLabApp,
    "debug-console": createDebugConsole,
  };
  async function openExplorer(kind, ref = "") {
    let app = apps.get(kind);
    if (!app) {
      app = await EXPLORERS[kind]?.();
      if (!app) return null;
      app.inst._compoundId = kind;
      apps.set(kind, app);
      inst.attach_existing(app.inst, { slot: kind, surface: "tab" });
    }
    activate(kind);
    if (ref) app.locate?.(ref); // 「打开详情」定位面(§5;导航语义)
    return app;
  }

  /* detail 链接路由(C4.3 全 kind;对话卡点击与 runs 行内链接同口) */
  const _DETAIL_ROUTE = {
    doc: (ref) => openDocWindow(ref),
    run: (ref) => openExplorer("runs-explorer", ref),
    pack: (ref) => openExplorer("skills-explorer", ref),
    decompose: (ref) => openExplorer("skills-explorer", ref),
    publish: (ref) => openExplorer("skills-explorer", ref),
    tool: (ref) => openExplorer("tools-explorer", ref),
    gate: (ref) => openExplorer("lab", ref),
    diff: (ref) => openExplorer("lab", ref),
    draft: (ref) => openExplorer("lab", ref),
    debug: (ref) => openExplorer("debug-console", ref),
  };
  function routeDetail(kind, ref) {
    const route = _DETAIL_ROUTE[kind];
    if (route) return route(ref);
    _log(`detail ${kind}:${ref}(未接 kind,esc 在对话处理)`); // esc/未知:不接
  }

  /* ── conversation 接入(C4.2 不动;onOpenDetail 升全 kind)── */
  async function openConversation(load) {
    const conv = await createConversation({ load, onOpenDetail: routeDetail });
    conv.inst._compoundId = load && typeof load === "object" && load.session
      ? `conv-${load.session}`
      : load === "new"
        ? `conv-${++convSeq}`
        : "conversation";
    // 会话去重(「app 即会话」:同会话已有窗 = 聚焦,不重复开)
    for (const [id, api] of convs) {
      if (api.compound.state.session?.id === conv.inst.state.session?.id) {
        activate(id);
        return convs.get(id) === conv.api ? conv : { inst: inst.child(id), api: convs.get(id) };
      }
    }
    conv.api.onLogRendered = _hangLiveDocCards;
    convs.set(conv.inst._compoundId, conv.api);
    // C4.4 per-instance 题名:会话标题(缺省 对话·sid 前 6;slotRefs.title 元信息)
    const sid = conv.inst.state.session?.id ?? "";
    const title = conv.inst.state.session?.title || `对话 · ${sid.slice(0, 6)}`;
    inst.attach_existing(conv.inst, { surface: "tab", title });
    return conv;
  }

  /* ── inbox 真实化(C4.2 不动)── */
  const inbox = inst.child("inbox");
  async function refreshInbox() {
    try {
      const rows = await (await fetch("/platform/api/decisions")).json();
      inbox.state.pending = (rows ?? []).map((r) => ({ id: r.question_id, ...r }));
      inbox.emit("change", { badge: inbox.state.pending.length });
    } catch {
      /* 读面故障不挡桌面 */
    }
  }

  /* ── SSE 单源(C4.2 不动)── */
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
        _startPoll();
      };
    } catch {
      _startPoll();
    }
  };

  /* ── 卡 DnD(APP-MODEL §15;envelope 三键不变)── */
  const _DND_MIME = "application/x-agent-os-widget";
  const _CARD_ROUTE = { // 卡型 → detail 路由 kind(与 app.js _CARD_TO_DETAIL 同映射,接 C4.3 路由)
    gate_report: "gate", skill_pack: "pack", publish: "publish",
    diff: "diff", plan: "decompose", table: "run", doc: "doc", doc_list: "doc",
  };
  host.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.(".pf-card[data-reg-path]");
    if (!card) return;
    e.dataTransfer?.setData(_DND_MIME, JSON.stringify({
      source: card.dataset.regPath, source_kind: card.dataset.card,
      ref: card.dataset.detailRef ?? "", position: {},
    }));
  });
  const tasks = () => host.querySelector(".dt-tasks");
  host.addEventListener("dragover", (e) => {
    if (!e.target.closest?.(".dt-tasks")) return;
    if ([...(e.dataTransfer?.types ?? [])].includes(_DND_MIME)) {
      e.preventDefault();
      tasks()?.classList.add("pf-drop-ok");
    }
  });
  host.addEventListener("dragleave", () => tasks()?.classList.remove("pf-drop-ok"));
  host.addEventListener("drop", (e) => {
    if (!e.target.closest?.(".dt-tasks")) return;
    tasks()?.classList.remove("pf-drop-ok");
    let env = null;
    try {
      env = JSON.parse(e.dataTransfer?.getData(_DND_MIME) ?? "null");
    } catch {
      env = null;
    }
    if (!env || typeof env.source !== "string" || typeof env.source_kind !== "string") return;
    e.preventDefault();
    const kind = _CARD_ROUTE[env.source_kind];
    if (kind && env.ref) routeDetail(kind, env.ref); // 卡面 → 任务栏 = 打开+定位(同 detail 链接)
  });

  /* 事件委托(chrome 全部 data-desk-*;layout 重渲不伤) */
  let suppressClick = false;
  host.addEventListener("click", (e) => {
    const x = e.target.closest("[data-desk-close]");
    if (x) return close(x.dataset.deskClose, x);
    if (e.target.closest("[data-desk-min]")) return activate(null);
    // doc 写动作按钮(docTabHtml 工具条 data-tab-act;C4.4 接通 app 管道)
    const tAct = e.target.closest("[data-tab-act]");
    if (tAct) {
      const slotEl = tAct.closest("[data-slot]");
      const rec = slotEl?.dataset.slot ? openDocs.get(slotEl.dataset.slot) : null;
      if (rec?.spawnId) return docTabAction(tAct, slotEl.dataset.slot, rec);
      return; // 无 spawn 实例 = 数据面未备(静默,同旧壳 spawn 失败降级)
    }
    // detail 链接全 kind 路由(conversation 外的链接:runs 行内等;
    // conversation 内由其 wireView 经 onOpenDetail 走同一路由,跳过防双路由)
    const link = e.target.closest("[data-detail-kind]");
    if (link && !e.target.closest("[data-cv-log]")) {
      return routeDetail(link.dataset.detailKind, link.dataset.detailRef ?? "");
    }
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

  /* 发起面:「+ 新建」菜单(会话/app 两路收拢;点外/点项即收) */
  const newBtn = $("#dt-newbtn");
  const newMenu = $("#dt-newMenu");
  const _closeNew = () => {
    newMenu.hidden = true;
    newBtn.setAttribute("aria-expanded", "false");
  };
  newBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    newMenu.hidden = !newMenu.hidden;
    newBtn.setAttribute("aria-expanded", newMenu.hidden ? "false" : "true");
  });
  document.addEventListener("click", (e) => {
    if (!newMenu.hidden && !e.target.closest(".dt-new")) _closeNew();
  });
  $("#dt-newconv").addEventListener("click", async () => {
    _closeNew();
    const conv = await openConversation("new");
    activate(conv.inst._compoundId);
  });
  const _reloadSessions = async () => {
    try {
      const sessions = await (await fetch("/platform/api/sessions")).json();
      $("#dt-sessions").innerHTML = (sessions ?? [])
        .map((s) => `<option value="${s.id}">${s.title ?? s.id}</option>`)
        .join("");
    } catch {
      /* 列表故障留空 */
    }
  };
  $("#dt-openconv").addEventListener("click", async () => {
    const sid = $("#dt-sessions").value;
    if (!sid) return;
    _closeNew();
    const conv = await openConversation({ session: sid });
    activate(conv.inst._compoundId);
  });
  $("#dt-open").addEventListener("click", () => {
    _closeNew();
    openExplorer($("#dt-kind").value);
  });

  /* desktop 事件面:inbox 整卡 open → 回对话(决策在对话里处理,同旧托盘) */
  inst.on("child_event", (p) => {
    if (p.child === "inbox" && p.event === "open") {
      const convId = inst.child("conversation") ? "conversation" : null;
      if (convId) activate(convId);
    }
  });

  /* 种子(异步):boot conversation(最新会话)+ runs-explorer + inbox + SSE + 会话列表 */
  (async () => {
    await openConversation("latest");
    await openExplorer("runs-explorer");
    activate(null); // 回桌面(图标栅格;boot 后无主窗)
    await refreshInbox();
    connectStream();
    await _reloadSessions();
    _log("desktop boot /root");
  })().catch((err) => _log(`boot 失败:${err.message ?? err}`));
}

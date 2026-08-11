/* conversation app 薄壳(docs/DESKTOP-WIDGET.md §5-1;C4.2):
   platform 对话(会话流/消息渲染/输入栏/orchestrator 交互)包成
   `conversation` def 的薄壳 compound——行为契约与 app.js 同(同端点、
   同卡片渲染、同 SSE/轮询面),壳把页面逻辑装进 def 的 layout/mount。

   结构:
   - CONVERSATION_DEF = compound(layout 纯函数:对话流 chrome——消息流 +
     busy 骨架 + composer;卡面复用 cards.js renderCardSurface,零新渲染套);
   - createConversation({load, onOpenDoc}) 工厂 = 实例级适配层(会话装载/
     发送/卡动作/升权作答/轮询汇聚全部一份,随实例生灭——不是随 view);
   - mount_view 重包:layout 首渲 + 按 view 绑委托(输入/发送/示例/卡动作/
     文档卡「打开详情」);消息增量只刷 log 区(不重排 layout,输入不丢焦点)。

   出海纪律与 app.js 同:全部经 /platform/api 既有端点(orchestrator 意图、
   cards/action、decisions answer、decisions|runs/present);widget 本体不出海。
   C4.2 边界(记 DESKTOP-WIDGET §9):detail 链接只接 doc(其余 kind C4.3);
   会话切换下拉未迁(一会话一实例;旧壳 sessionSel 不动);卡 DnD 未迁。 */

import { copy } from "/static/js/themes.js";
import { esc, toast } from "/static/js/util.js";
import { createCompound, registerWidgetDef } from "/static/js/widgets/index.js";
import { looksMarkdown, mdToHtml } from "/static/js/widgets/w-md.js";
import { renderCardSurface } from "./cards.js";

/* ── 渲染(纯;与 app.js 的对话流同构)───────────────────────────── */

/* 单条消息(纯):role/降级提示/markdown 白名单渲染/卡面(renderCardSurface) */
function _msgHtml(m, index, sessionId) {
  const role = m.role === "user" ? "user" : "agent";
  const text = m.text
    ? `<div class="pf-bubble-text">${looksMarkdown(m.text) ? mdToHtml(m.text) : esc(m.text)}</div>`
    : "";
  const degrade =
    m.meta?.route === "rule" && m.meta?.reason === "llm_unavailable"
      ? `<div class="pf-bubble-note">${esc(copy("platform.route.degrade"))}</div>`
      : "";
  const cards = (m.cards ?? [])
    .map((c, k) => renderCardSurface(c, 1, `/conv/${sessionId}/msg/${index}/card/${k}`, _refOf(c)))
    .join("");
  return `<div class="pf-msg" data-role="${role}"><div class="pf-bubble">${degrade}${text}${cards}</div></div>`;
}

/* 卡的业务锚(与 app.js _refOf 同契约;doc 卡/文档列表卡经此取文档名) */
function _refOf(card) {
  const d = card?.data ?? {};
  switch (card?.type) {
    case "plan": return (d.create ?? [])[0]?.name ?? "";
    case "skill_pack": case "diff": return d.name ?? "";
    case "gate_report": return d.draft ?? "";
    case "publish": return d.plan_id ?? "";
    case "escalation": return d.question_id ?? "";
    case "table": return d.ref?.id ?? "";
    case "doc": case "doc_list": return d.name ?? "";
    default: return "";
  }
}

/* log 区(纯;layout 首渲与增量刷同一产出——两处不漂) */
export function conversationLogHtml(state) {
  const msgs = state.messages ?? [];
  if (!msgs.length && !state.busy) {
    return (
      `<div class="pf-empty">` +
      `<div class="pf-empty-title">${esc(copy("platform.empty.title"))}</div>` +
      ["platform.empty.1", "platform.empty.2", "platform.empty.3"]
        .map((k) => `<button class="pf-example" data-example="${esc(copy(k))}">${esc(copy(k))}</button>`)
        .join("") +
      `</div>`
    );
  }
  return (
    msgs.map((m, i) => _msgHtml(m, i, state.session?.id ?? "")).join("") +
    (state.busy
      ? `<div class="pf-msg" data-role="agent"><div class="pf-bubble pf-skel">` +
        `<span class="pf-skel-line"></span><span class="pf-skel-line w60"></span></div></div>`
      : "")
  );
}

/* layout(纯;§3):对话流 chrome——log 区 + composer(draft 在 state,可序列化) */
export function renderConversation(state) {
  return (
    `<div class="cv-root">` +
    `<div class="cv-log" data-cv-log="1" role="log" aria-live="polite">${conversationLogHtml(state)}</div>` +
    `<div class="cv-composer">` +
    `<textarea class="input" data-cv-input="1" rows="2" aria-label="${esc(copy("platform.input.ph"))}" ` +
    `placeholder="${esc(copy("platform.input.ph"))}">${esc(state.draft ?? "")}</textarea>` +
    `<button class="btn btn-primary" data-cv-send="1">${esc(copy("platform.send"))}</button>` +
    `</div></div>`
  );
}

export const CONVERSATION_DEF = registerWidgetDef({
  kind: "conversation",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { session: null, messages: [], busy: false, draft: "" },
  actions: [],
  // open-detail:对话流详情卡「打开详情」上行(desktop 按 kind 路由:
  // doc 进窗口区,run/skill/lab/debug 进对应 explorer 并定位,§5-2/C4.3)
  events: ["change", "open-detail"],
  aria: { role: "application", label: "对话" },
  surfaces: ["tab"],
  compound: {
    dynamic: { allow: [], max: 0 }, // C4.2 无子件;薄壳 compound 结构面(§5-1)
    layout: renderConversation,
  },
});

/* ── 实例工厂(适配层;每实例一份,与 view 生灭无关)────────────────── */

/* load: "latest" = 最新会话(旧壳同语义,无则新建);"new" = 强制新会话;
   {session} = 指定会话(C4.3 发起面会话列表;一会话一实例,「app 即会话」)。
   onOpenDetail(kind, ref):详情卡「打开详情」回调(desktop 驱动注入路由)。 */
export async function createConversation({ load = "latest", onOpenDetail = null } = {}) {
  const inst = createCompound(CONVERSATION_DEF, {
    path: "/conv",
    state: { session: null, messages: [], busy: false, draft: "" },
  });

  /* 会话装载(与 app.js loadSessions/newSession 同端点同语义) */
  if (load === "new") {
    const s = await (await fetch("/platform/api/sessions", { method: "POST" })).json();
    inst.state.session = { id: s.id, title: s.title ?? "" };
    inst.state.messages = [];
  } else if (load && typeof load === "object" && load.session) {
    // C4.3 发起面会话列表:按 id 开会话(一会话一实例,窗随会话)
    const s = await (await fetch(`/platform/api/sessions/${load.session}`)).json();
    inst.state.session = { id: s.id ?? load.session, title: s.title ?? "" };
    inst.state.messages = s.messages ?? [];
  } else {
    const sessions = await (await fetch("/platform/api/sessions")).json();
    if (sessions.length) {
      const s = sessions[0];
      inst.state.session = { id: s.id, title: s.title ?? "" };
      const doc = await (await fetch(`/platform/api/sessions/${s.id}`)).json();
      inst.state.messages = doc.messages ?? [];
    } else {
      const s = await (await fetch("/platform/api/sessions", { method: "POST" })).json();
      inst.state.session = { id: s.id, title: s.title ?? "" };
      inst.state.messages = [];
    }
  }
  inst.path = `/conv/${inst.state.session.id}`; // §1 寻址:会话即路径段

  /* ── 视图面:mount_view 重包(layout 首渲 + 按 view 绑委托)── */
  const viewHosts = new Set(); // 挂着的 view host(消息增量只刷各 log 区)
  const _renderLogs = () => {
    for (const host of viewHosts) {
      const el = host.querySelector("[data-cv-log]");
      if (!el) continue;
      el.innerHTML = conversationLogHtml(inst.state);
      el.scrollTop = el.scrollHeight;
    }
    api.onLogRendered?.(); // 驱动钩:doc 活卡重挂(C4.2 hard link)
  };
  const _renderComposers = () => {
    for (const host of viewHosts) {
      const ta = host.querySelector("[data-cv-input]");
      if (ta && ta.value !== (inst.state.draft ?? "")) ta.value = inst.state.draft ?? "";
    }
  };

  const _baseMountView = inst.mount_view.bind(inst);
  inst.mount_view = (viewHost, mopts = {}) => {
    const view = _baseMountView(viewHost, mopts);
    viewHosts.add(viewHost);
    _wireView(viewHost);
    return {
      host: viewHost,
      detach: () => {
        viewHosts.delete(viewHost);
        view.detach();
      },
    };
  };

  function _wireView(viewHost) {
    viewHost.addEventListener("input", (e) => {
      if (e.target.closest("[data-cv-input]")) inst.state.draft = e.target.value;
    });
    viewHost.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && e.target.closest("[data-cv-input]")) {
        e.preventDefault?.();
        send();
      }
    });
    viewHost.addEventListener("click", (e) => {
      if (e.target.closest("[data-cv-send]")) return send();
      const example = e.target.closest("[data-example]");
      if (example) {
        inst.state.draft = example.dataset.example ?? "";
        _renderComposers();
        viewHost.querySelector("[data-cv-input]")?.focus?.();
        return;
      }
      // 详情卡「打开详情」→ 驱动路由(C4.3 全 kind:doc 进窗口区,其余进 explorer 定位)
      const link = e.target.closest("[data-detail-kind]");
      if (link) {
        const kind = link.dataset.detailKind ?? "";
        const ref = link.dataset.detailRef ?? "";
        onOpenDetail?.(kind, ref);
        inst.emit("open-detail", { kind, ref });
        return;
      }
      // D4 对话卡片:doc_list 卡「新建文档」→ 唯一名起稿 → 开窗口(同 app.js)
      if (e.target.closest("[data-doc-create]")) {
        return (async () => {
          let name = "doc.untitled";
          for (let i = 2; ; i++) {
            const res = await fetch("/platform/api/docs", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name, title: name, text: "" }),
            }).catch(() => null);
            if (res?.ok) break;
            if (res?.status === 409) {
              name = `doc.untitled${i}`;
              continue;
            }
            break;
          }
          onOpenDetail?.("doc", name);
          inst.emit("open-detail", { kind: "doc", ref: name });
        })();
      }
      const decision = e.target.closest("[data-decision]");
      if (decision) return answerDecision(decision);
      const act = e.target.closest("[data-card-act],[data-app-action]");
      if (act) return cardAction(act);
    });
  }

  /* ── 行为契约(与 app.js 同端点)── */

  /* 发送意图(同 app.js send:用户气泡 → busy 骨架 → agent 消息) */
  async function send() {
    const text = String(inst.state.draft ?? "").trim();
    if (!text || inst.state.busy) return;
    inst.state.busy = true;
    inst.state.messages = [...inst.state.messages, { role: "user", text, cards: [] }];
    inst.state.draft = "";
    _renderComposers();
    _renderLogs();
    try {
      const res = await fetch(`/platform/api/sessions/${inst.state.session.id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const msg = await res.json();
      inst.state.messages = [...inst.state.messages, msg];
    } catch (err) {
      inst.state.messages = [
        ...inst.state.messages,
        { role: "agent", text: `${copy("platform.error")}: ${err.message ?? err}`, cards: [] },
      ];
    } finally {
      inst.state.busy = false;
      _renderLogs();
    }
    // arrived = 本轮 agent 新消息数(未读 badge 的闸门记账原料,C4.3 §7 小注)
    inst.emit("change", { messages: inst.state.messages.length, arrived: 1 });
  }

  /* 卡面动作(同 app.js cardAction:新管道 app instance 优先,旧管道过渡) */
  async function cardAction(btn) {
    const actionId = btn.dataset.appAction || btn.dataset.cardAct;
    const instId = btn.dataset.appInst;
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
        const res = await fetch(`/platform/api/apps/${encodeURIComponent(instId)}/actions/${encodeURIComponent(actionId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            surface: "card",
            args: ack ? { warnings_ack: true } : {},
            session_id: inst.state.session.id,
          }),
        });
        if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
        result = await res.json();
      } else {
        if (ack) payload.warnings_ack = true;
        const res = await fetch("/platform/api/cards/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action_id: actionId, payload, session_id: inst.state.session.id }),
        });
        if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
        result = await res.json();
      }
      inst.state.messages = [
        ...inst.state.messages,
        { role: "agent", text: result.text ?? "", cards: result.cards ?? [] },
      ];
      _renderLogs();
    } catch (err) {
      inst.state.messages = [
        ...inst.state.messages,
        { role: "agent", text: `${copy("platform.error")}: ${err.message ?? err}`, cards: [] },
      ];
      _renderLogs();
    } finally {
      btn.disabled = false;
    }
  }

  /* 升权作答(同 app.js answerDecision:一律 action 管道;无 instance 先 spawn) */
  function _markResolved(questionId, resolved) {
    for (const m of inst.state.messages) {
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
    btn.disabled = true;
    try {
      let instId = btn.dataset.appInst;
      if (!instId) {
        const sp = await fetch("/platform/api/apps/spawn", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: "escalation", ref: qid, state: { question_id: qid, skill: "" } }),
        });
        if (!sp.ok) throw new Error((await sp.json()).detail ?? `HTTP ${sp.status}`);
        instId = (await sp.json()).instance?.id;
      }
      const res = await fetch(`/platform/api/apps/${encodeURIComponent(instId)}/actions/${encodeURIComponent(answer)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ surface: "card", session_id: inst.state.session.id }),
      });
      if (!res.ok) {
        _markResolved(qid, "gone"); // 已被别处处理(404/400 按已处理提示)
      } else {
        _markResolved(qid, answer);
      }
    } catch (err) {
      toast(`${copy("platform.error")}: ${err.message ?? err}`, "error");
      btn.disabled = false;
      return;
    }
    _renderLogs();
  }

  /* 轮询汇聚(同 app.js;SSE decision.new/run.finished 或 5s 兜底由驱动扇入) */
  async function pollDecisions() {
    try {
      const res = await fetch(`/platform/api/sessions/${inst.state.session.id}/decisions/present`, {
        method: "POST",
      });
      if (!res.ok) return;
      const { presented } = await res.json();
      if (presented?.length) {
        inst.state.messages = [...inst.state.messages, ...presented];
        _renderLogs();
        inst.emit("change", { messages: inst.state.messages.length, arrived: presented.length });
      }
    } catch {
      /* 静默:下一周期再试 */
    }
  }

  async function presentRuns() {
    try {
      const res = await fetch(`/platform/api/sessions/${inst.state.session.id}/runs/present`, {
        method: "POST",
      });
      if (!res.ok) return;
      const { presented } = await res.json();
      if (presented?.length) {
        inst.state.messages = [...inst.state.messages, ...presented];
        _renderLogs();
        inst.emit("change", { messages: inst.state.messages.length, arrived: presented.length });
      }
    } catch {
      /* 静默 */
    }
  }

  const api = {
    send,
    poll: () => Promise.all([pollDecisions(), presentRuns()]),
    refresh: _renderLogs,
    onLogRendered: null, // 驱动钩(doc 活卡重挂)
    compound: inst,
  };
  return { inst, api };
}

export { CONVERSATION_DEF as def };

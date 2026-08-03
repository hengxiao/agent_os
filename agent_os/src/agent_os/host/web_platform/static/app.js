/* Agent OS · 对话中枢(docs/WEB-PLATFORM.md §10):tab 条布局 + 对话流。

   左栏 = 竖排 tab 条:conversation(会话,固定首 tab,不可关闭)+ detail
   (详情,可关闭,✕);底部会话下拉(切换/新建)。卡上"查看详情"链接 →
   开/聚焦 detail tab(同一 ref 去重)。主区:conversation tab = 对话流 +
   意图输入;detail tab = 详情视图(输入区隐藏)。
   不引用旧 web 的 app.js;只复用宿主无关模块(themes/util/trace 纯函数)。 */

import { copy, initTheme } from "/static/js/themes.js";
import { esc, toast } from "/static/js/util.js";
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

/* 关 tab:关闭后回退到 conversation(或剩余最后一个) */
export function closeTab(tabs, id, fallbackId = "conv") {
  const next = (tabs ?? []).filter((t) => t.id !== id);
  const active = next.some((t) => t.id === fallbackId) ? fallbackId : (next[0]?.id ?? fallbackId);
  return { tabs: next, active };
}

const state = {
  sessions: [],
  current: null, // 当前会话 id(conversation tab 的内容源)
  messages: [],
  busy: false,
  tabs: [{ id: "conv", kind: "conversation", title: "", ref: "conv" }],
  active: "conv",
  detail: null, // {kind, ref, loading, error, html}
};

/* ── 主题(与正式系统同一契约:initTheme 解析 URL/localStorage)────── */

function mountThemes() {
  const host = $("#themes");
  const current = initTheme();
  for (const btn of host.querySelectorAll("button")) {
    btn.dataset.on = btn.dataset.t === current ? "1" : "0";
    btn.addEventListener("click", async () => {
      const { applyTheme } = await import("/static/js/themes.js");
      applyTheme(btn.dataset.t);
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
      const close =
        t.kind !== "conversation"
          ? `<button class="pf-tab-x" data-tab-x="${esc(t.id)}" aria-label="${esc(copy("platform.tab.close"))}">✕</button>`
          : "";
      const label = t.kind === "conversation" ? copy("platform.tab.chat") : t.title;
      return (
        `<div class="pf-tab" data-tab="${esc(t.id)}" role="tab" tabindex="0" aria-selected="${active}">` +
        `<span class="pf-tab-label">${esc(label)}</span>${close}</div>`
      );
    })
    .join("");
  const sel = $("#sessionSel");
  sel.innerHTML =
    state.sessions
      .map(
        (s) =>
          `<option value="${esc(s.id)}"${s.id === state.current ? " selected" : ""}>${esc(s.title)}</option>`
      )
      .join("") || `<option value="">${esc(copy("platform.no.sessions"))}</option>`;
}

/* ── 渲染:主区(conversation = 对话流;detail = 详情)──────────── */

function renderMain() {
  const isConv = state.active === "conv";
  $("#log").hidden = !isConv;
  $("#inputBar").hidden = !isConv;
  $("#detailHost").hidden = isConv;
  if (isConv) renderLog();
  else renderDetail();
}

function msgHtml(m) {
  const role = m.role === "user" ? "user" : "agent";
  const text = m.text ? `<div class="pf-bubble-text">${esc(m.text)}</div>` : "";
  // N7(O7):LLM 路由凭证降级 → 人话系统提示(copy 六主题;不静默,不裸错)
  const degrade =
    m.meta?.route === "rule" && m.meta?.reason === "llm_unavailable"
      ? `<div class="pf-bubble-note">${esc(copy("platform.route.degrade"))}</div>`
      : "";
  const cards = (m.cards ?? []).map(renderCardSurface).join(""); // 对话流 = Card Surface(摘要层)
  return `<div class="pf-msg" data-role="${role}"><div class="pf-bubble">${degrade}${text}${cards}</div></div>`;
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

function renderDetail() {
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
  host.innerHTML = d.html ?? "";
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
  const session = await (await fetch("/platform/api/sessions", { method: "POST" })).json();
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
};

function activateTab(id) {
  state.active = id;
  renderTabs();
  renderMain();
}

async function openDetail(kind, ref, data) {
  const tab = { id: `d:${kind}:${ref}`, kind, title: _DETAIL_META[kind]?.title ?? ref, ref };
  const { tabs, active, opened } = openTab(state.tabs, tab);
  state.tabs = tabs;
  if (!opened) {
    // 同 ref 已开:聚焦并直接重渲(数据可能已更新——详情是活面)
    state.active = active;
    state.detail = await _loadDetail(kind, ref, data);
    renderTabs();
    renderMain();
    return;
  }
  state.active = active;
  state.detail = { kind, ref, loading: true };
  renderTabs();
  renderMain();
  state.detail = await _loadDetail(kind, ref, data);
  renderMain();
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
      const [detail, signals] = await Promise.all([
        (await fetch(`/api/runs/${encodeURIComponent(ref)}`)).json(),
        (await fetch(`/api/runs/${encodeURIComponent(ref)}/signals`)).json(),
      ]);
      return { kind, ref, html: renderTabSurface(kind, { detail, signals }) };
    }
    return { kind, ref, error: `unknown detail kind: ${kind}` };
  } catch (e) {
    return { kind, ref, error: `${copy("platform.detail.error")}: ${e.message ?? e}` };
  }
}

function closeDetail(id) {
  const { tabs, active } = closeTab(state.tabs, id, "conv");
  state.tabs = tabs;
  state.active = active;
  if (state.active === "conv") state.detail = null;
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
    const tabX = e.target.closest("[data-tab-x]");
    if (tabX) {
      e.stopPropagation(); // ✕ 不触发 tab 激活
      return closeDetail(tabX.dataset.tabX);
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
}

mountThemes();
bind();
renderStaticCopy();
renderTabs();
renderMain();
loadSessions().catch((e) => toast(e.message ?? String(e), "error"));
// 升权决策轮询(W2;unref 让 node 测试进程可退出,浏览器无此方法)
const _decisionTimer = setInterval(pollDecisions, 5000);
_decisionTimer.unref?.();

// 测试探针(node 冒烟用;浏览器无副作用)
if (typeof globalThis !== "undefined") {
  globalThis.__platform = { state, renderTabs, renderMain, loadSessions, openDetail, closeDetail, pollDecisions };
}

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
        `<div class="pf-tab" data-tab="${esc(t.id)}" role="tab" tabindex="0" aria-selected="${active}"` +
        ` title="${esc(label)}">` + // 窄屏图标列时悬停给全文(M2)
        `<span class="pf-tab-ico" aria-hidden="true">${esc((label || "?").trim().charAt(0))}</span>` +
        `<span class="pf-tab-label">${esc(label)}</span>${close}</div>`
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
  debug: { title: copy("platform.detail.debug") },
  draft: { title: copy("platform.detail.draft") },
};

/* 详情 kind → app kind(M3 全解开,docs/APP-MODEL.md §8:run/debug/lab-draft) */
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
};

/* spawn 的 state 归一(M3):args_from 的参数源——run 要 run_id、debug 要
   session_id、lab-draft 要 name/root(绑定便利键,与 app.py _register_cards 同哲学) */
function _spawnState(tab, data) {
  if (tab.kind === "run") return { run_id: tab.ref, status: "" };
  if (tab.kind === "debug") return { session_id: tab.ref, run_id: data?.run_id ?? "" };
  if (tab.kind === "draft") {
    const name = data?.name ?? tab.ref;
    return { ...(data ?? {}), name, root: name };
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
  _spawnForTab(tab, data); // M2:登记 app instance(fire-and-forget,不阻断渲染)
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
    if (kind === "debug") {
      // M3:简化调试台(快照 = 旧 web 调试端点同形)
      const doc = await (await fetch(`/api/debug/sessions/${encodeURIComponent(ref)}`)).json();
      return { kind, ref, html: renderTabSurface(kind, doc) };
    }
    if (kind === "draft") {
      const doc = await (await fetch(`/api/lab/drafts/${encodeURIComponent(ref)}`)).json();
      return { kind, ref, html: renderTabSurface(kind, doc) };
    }
    return { kind, ref, error: `unknown detail kind: ${kind}` };
  } catch (e) {
    return { kind, ref, error: `${copy("platform.detail.error")}: ${e.message ?? e}` };
  }
}

function closeDetail(id) {
  const { tabs, active, closed } = closeTab(state.tabs, id, "conv");
  state.tabs = tabs;
  state.active = active;
  if (closed) state.closedTabs = pushClosed(state.closedTabs, closed); // M2:关闭≠销毁
  if (state.active === "conv") state.detail = null;
  renderTabs();
  renderMain();
}

/* 重开(M2):从最近关闭回到 tab 条并聚焦(同一 tab id/ref → 同 instance) */
function reopenTab(id) {
  const tab = state.closedTabs.find((t) => t.id === id);
  if (!tab) return;
  state.closedTabs = state.closedTabs.filter((t) => t.id !== id);
  const { tabs, active } = openTab(state.tabs, tab);
  state.tabs = tabs;
  state.active = active;
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
    const res = await fetch(
      `/platform/api/apps/${encodeURIComponent(tab.instance)}/actions/${encodeURIComponent(btn.dataset.tabAct)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ surface: "tab", args: {}, session_id: state.current }),
      }
    );
    if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
    const result = await res.json();
    if (result.text) {
      state.messages.push({ role: "agent", text: result.text, cards: result.cards ?? [] });
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
    const tabX = e.target.closest("[data-tab-x]");
    if (tabX) {
      e.stopPropagation(); // ✕ 不触发 tab 激活
      return closeDetail(tabX.dataset.tabX);
    }
    const reopen = e.target.closest("[data-reopen]");
    if (reopen) return reopenTab(reopen.dataset.reopen);
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
  globalThis.__platform = { state, renderTabs, renderMain, loadSessions, openDetail, closeDetail, reopenTab, pollDecisions };
}

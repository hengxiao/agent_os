/* Agent OS · 对话中枢(docs/WEB-PLATFORM.md §10):页面骨架与对话流。

   布局:左栏会话索引(摘要 + 新建)/ 主区对话流(消息气泡 + 产物卡)/
   底部意图输入(Enter 发送,Shift+Enter 换行)。空态 = help 卡引导。
   刷新恢复:会话列表重载 + 选中会话重载(持久化在服务端,§3)。
   不引用旧 web 的 app.js;只复用宿主无关模块(themes.js 的 initTheme/copy、
   util.js 的 esc/toast)。 */

import { copy, initTheme } from "/static/js/themes.js";
import { esc, toast } from "/static/js/util.js";
import { cardHtml } from "./cards.js";

const $ = (sel) => document.querySelector(sel);

const state = {
  sessions: [], // 摘要列表
  current: null, // 当前会话 id
  messages: [], // 当前会话消息
  busy: false, // 发送中(骨架 loading)
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

/* ── 渲染:会话列表(左栏索引,§3)────────────────────────────── */

function renderSessions() {
  const box = $("#sessions");
  if (!state.sessions.length) {
    box.innerHTML = `<div class="pf-dim">${esc(copy("platform.no.sessions"))}</div>`;
    return;
  }
  box.innerHTML = state.sessions
    .map(
      (s) =>
        `<div class="pf-session" data-sid="${esc(s.id)}" role="option" tabindex="0"` +
        ` aria-selected="${s.id === state.current}">` +
        `<div class="pf-session-title">${esc(s.title)}</div>` +
        `<div class="pf-dim">${s.messages} 条消息 · ${s.cards} 张卡</div></div>`
    )
    .join("");
}

/* ── 渲染:对话流(气泡 + 卡;骨架 loading 与错误态)────────────── */

function msgHtml(m) {
  const role = m.role === "user" ? "user" : "agent";
  const text = m.text ? `<div class="pf-bubble-text">${esc(m.text)}</div>` : "";
  const cards = (m.cards ?? []).map(cardHtml).join("");
  return `<div class="pf-msg" data-role="${role}"><div class="pf-bubble">${text}${cards}</div></div>`;
}

function renderLog() {
  const log = $("#log");
  if (!state.messages.length && !state.busy) {
    // 空态:help 卡引导(三句示例意图,§2.2 新手再造)
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
  log.scrollTop = log.scrollHeight; // 焦点管理:新消息滚动到底(§widget 标准)
}

/* ── 数据:会话装载与选择(刷新恢复)──────────────────────────── */

async function loadSessions(selectId = null) {
  state.sessions = await (await fetch("/platform/api/sessions")).json();
  const target =
    selectId ?? (state.sessions.length ? state.sessions[0].id : null);
  if (target) await selectSession(target);
  else {
    state.current = null;
    state.messages = [];
  }
  renderSessions();
  renderLog();
}

async function selectSession(id) {
  state.current = id;
  const session = await (await fetch(`/platform/api/sessions/${id}`)).json();
  state.messages = session.messages ?? [];
  renderSessions();
  renderLog();
}

async function newSession() {
  const session = await (
    await fetch("/platform/api/sessions", { method: "POST" })
  ).json();
  await loadSessions(session.id);
}

/* ── 发送意图:用户气泡 → 骨架 → agent 消息(失败以错误气泡呈现)── */

async function send() {
  const input = $("#intent");
  const text = input.value.trim();
  if (!text || state.busy) return;
  if (!state.current) await newSession();
  state.busy = true;
  state.messages.push({ role: "user", text, cards: [] });
  input.value = "";
  renderLog();
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
    state.messages.push({
      role: "agent",
      text: `${copy("platform.error")}: ${e.message ?? e}`,
      cards: [],
      error: true,
    });
  } finally {
    state.busy = false;
    renderLog();
  }
}

/* ── 卡片动作:统一经 cards/action 转发(结果追加进会话)────────── */

async function cardAction(btn) {
  const actionId = btn.dataset.cardAct;
  const payload = JSON.parse(btn.dataset.payload ?? "{}");
  // publish 卡的 warnings 勾选门(§4):未勾选不发
  const card = btn.closest(".pf-card");
  const ack = card?.querySelector("[data-ack]");
  if (ack && !ack.checked) {
    toast(copy("platform.warnings.ack"), "info");
    return;
  }
  if (ack) payload.warnings_ack = true;
  btn.disabled = true;
  try {
    const res = await fetch("/platform/api/cards/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: actionId, payload, session_id: state.current }),
    });
    if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
    const result = await res.json();
    // 结果以 agent 消息呈现(返回新卡则渲染;§6 动作即对话)
    state.messages.push({
      role: "agent",
      text: result.text ?? "",
      cards: result.cards ?? [],
    });
    renderLog();
  } catch (e) {
    state.messages.push({
      role: "agent",
      text: `${copy("platform.error")}: ${e.message ?? e}`,
      cards: [],
      error: true,
    });
    renderLog();
  } finally {
    btn.disabled = false;
  }
}

/* ── 事件 ─────────────────────────────────────────────────────── */

function bind() {
  $("#send").addEventListener("click", send);
  $("#intent").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); // Enter 发送,Shift+Enter 换行(§骨架)
      send();
    }
  });
  $("#newSession").addEventListener("click", newSession);
  document.addEventListener("click", (e) => {
    const session = e.target.closest(".pf-session");
    if (session) return selectSession(session.dataset.sid);
    const act = e.target.closest("[data-card-act]");
    if (act) return cardAction(act);
    const example = e.target.closest("[data-example]");
    if (example) {
      $("#intent").value = example.dataset.example;
      $("#intent").focus();
    }
  });
}

/* ── 启动 ─────────────────────────────────────────────────────── */

function renderStaticCopy() {
  // index.html 的静态文案接 copy(文案契约:全走 copy,六主题同步;节点缺失防御)
  const sub = document.querySelector("[data-i18n='sub']");
  if (sub) sub.textContent = copy("platform.sub");
  const side = document.querySelector("[data-i18n='sessions']");
  if (side) side.textContent = copy("platform.sessions");
  $("#intent").placeholder = copy("platform.input.ph");
  $("#send").textContent = copy("platform.send");
}

mountThemes();
bind();
renderStaticCopy();
renderLog();
loadSessions().catch((e) => toast(e.message ?? String(e), "error"));

// 测试探针(node 冒烟用;浏览器无副作用)
if (typeof globalThis !== "undefined") globalThis.__platform = { state, renderLog, loadSessions };

/* Supervisor 收件箱(SUPERVISOR.md v2 §5;S3):TopBar 图标 + 待答计数徽标 +
   右侧抽屉视图。数据 GET /api/supervisor/pending(app.js 5s 轮询调 pollInbox 写
   store.inboxPending);抽屉打开时订阅 store 增量重绘(保留展开的 context / 输入中
   的回答 / 滚动位置)。

   问题卡片:urgency 左色条(high=--danger,high 在前——后端已排序,UI 防御再排)
   + "高优" chip(§3.1 双编码)、question、context 可展开、options 按钮组(无 options
   则文本输入)、run/帧深链接(#/runs/<id> 与 ?frame=<fid>,点击跳转并关抽屉)。
   提交:options 按钮点击即提交,文本输入回车(或"提交"钮)提交;成功 → 卡片消失
   + Toast;被拒(400/404)→ 卡片显示错误条(§3 previous_error 语义:格式错误
   返回调用方重答,服务端重问时 previous_error 也经同一条呈现)。

   纯函数(不碰 DOM,node 单测可载):
     sortedPending(rows)          防御排序:high 在前,其余先问先排(同后端语义)
     badgeModel(rows)             TopBar 徽标视图模型 { count, hasHigh }
     questionCardHtml(q, err)     问题卡片 HTML(err = 本地提交被拒错误条文本);
                                  kind == "escalation" 转升权卡片
     escalationCardHtml(q, err)   升权卡片(ESCALATION.md §3;E2):档位徽标 + skill 名
                                  + reason_hint + params JSON + 权限集 + 选项按钮 */

import { getJson, postJson } from "../api.js";
import { store } from "../store.js";
import { copy } from "../themes.js";
import { absTs, emptyBlock, esc, relTime, shortId, toast } from "../util.js";

/* ── 纯函数 ─────────────────────────────────────────────────── */

/* 防御排序:urgency=high 在前,其余按 asked_at 先问先排(与后端 InboxChannel 同语义)。 */
export function sortedPending(rows) {
  const list = [...(Array.isArray(rows) ? rows : [])];
  list.sort(
    (a, b) =>
      ((a?.urgency === "high" ? 0 : 1) - (b?.urgency === "high" ? 0 : 1)) ||
      ((Number(a?.asked_at) || 0) - (Number(b?.asked_at) || 0)));
  return list;
}

/* TopBar 徽标视图模型:count = 待答数(0 → 徽标隐藏);hasHigh → 徽标变色(--danger)。 */
export function badgeModel(rows) {
  const list = Array.isArray(rows) ? rows : [];
  return { count: list.length, hasHigh: list.some((q) => q?.urgency === "high") };
}

/* 问题卡片 HTML:urgency 色条(data-urgency 驱动)+ question + run/帧链接 +
   context 可展开 + 错误条(previous_error / 本地提交被拒)+ 作答区(options 按钮组
   或文本输入)。err 为本地提交被拒错误条文本(优先于 previous_error 呈现)。
   kind === "escalation" 转升权卡片(escalationCardHtml),普通问答行为不变。 */
export function questionCardHtml(q, err = null) {
  if (q?.kind === "escalation") return escalationCardHtml(q, err);
  const qid = String(q?.question_id ?? "");
  const urgency = q?.urgency === "high" ? "high" : "normal";
  const runId = String(q?.run_id ?? "");
  const frameId = String(q?.frame_id ?? "");
  const ctx = q?.context && Object.keys(q.context).length ? q.context : null;
  const options = Array.isArray(q?.options) && q.options.length ? q.options : null;
  const errText = err || q?.previous_error || null;
  const askedAt = Number(q?.asked_at);
  const timeHtml = Number.isFinite(askedAt) && askedAt > 0
    ? `<span class="sup-time" title="${esc(absTs(askedAt))}">` +
      `${esc(relTime(new Date(askedAt * 1000).toISOString()))}</span>`
    : "";
  return (
    `<div class="sup-card" data-urgency="${urgency}" data-qid="${esc(qid)}">` +
    `<div class="sup-card-top">` +
    (urgency === "high"
      ? `<span class="sup-urg" title="urgency: high">高优</span>`
      : "") +
    `<span class="sup-q">${esc(q?.question ?? "")}</span>` +
    `</div>` +
    `<div class="sup-meta">` +
    (runId
      ? `<a class="sup-link" href="#/runs/${encodeURIComponent(runId)}"` +
        ` title="查看 run ${esc(runId)}">run ${esc(shortId(runId))}</a>`
      : "") +
    (runId && frameId
      ? `<a class="sup-link" href="#/runs/${encodeURIComponent(runId)}` +
        `?frame=${encodeURIComponent(frameId)}" title="查看提问帧 ${esc(frameId)}">` +
        `帧 f-${esc(shortId(frameId))}</a>`
      : "") +
    timeHtml +
    `</div>` +
    (ctx
      ? `<details class="sup-ctx"><summary>context</summary>` +
        `<pre class="mono">${esc(JSON.stringify(ctx, null, 2))}</pre></details>`
      : "") +
    (errText ? `<div class="sup-error" role="alert">${esc(errText)}</div>` : "") +
    (options
      ? `<div class="sup-actions">` +
        options
          .map((o) => `<button class="btn" data-answer="${esc(o)}">${esc(o)}</button>`)
          .join("") +
        `</div>`
      : `<div class="sup-actions">` +
        `<input class="input sup-input" type="text" placeholder="输入回答,回车提交"` +
        ` aria-label="回答">` +
        `<button class="btn btn-primary" data-submit>提交</button>` +
        `</div>`) +
    `</div>`
  );
}

/* 档位 → perm 色板槽位(与 Permission 缺省推导同一梯度:L1↔READ,L2↔WRITE,L3↔EXEC)。
   颜色走 --perm-* 契约 token;档名/参数/权限名是技术文本,直渲不进 copy 表。 */
const TIER_PERM = { none: "READ", reversible: "WRITE", irreversible: "EXEC" };

/* 升权卡片(ESCALATION.md §3;E2):档位徽标(perm-badge 风格)+ skill 名 + question +
   reason_hint + params JSON(可折叠)+ requested 权限集 chips + 选项按钮。
   选项枚数由后端 options 决定(L2 三枚/L3 两枚),UI 不自判;作答走同一 data-answer 通道。 */
export function escalationCardHtml(q, err = null) {
  const qid = String(q?.question_id ?? "");
  const urgency = q?.urgency === "high" ? "high" : "normal";
  const runId = String(q?.run_id ?? "");
  const frameId = String(q?.frame_id ?? "");
  const ctx = q?.context && typeof q.context === "object" ? q.context : {};
  const tier = Object.hasOwn(TIER_PERM, ctx.tier) ? String(ctx.tier) : "none";
  const skill = String(ctx.skill ?? "");
  const reason = String(ctx.reason_hint ?? "");
  const params = ctx.params && typeof ctx.params === "object" ? ctx.params : null;
  const req = ctx.requested && typeof ctx.requested === "object" ? ctx.requested : {};
  const reqTools = Array.isArray(req.tools) ? req.tools : [];
  const reqSkills = Array.isArray(req.skills) ? req.skills : [];
  const options = Array.isArray(q?.options) && q.options.length ? q.options : null;
  const errText = err || q?.previous_error || null;
  const askedAt = Number(q?.asked_at);
  const timeHtml = Number.isFinite(askedAt) && askedAt > 0
    ? `<span class="sup-time" title="${esc(absTs(askedAt))}">` +
      `${esc(relTime(new Date(askedAt * 1000).toISOString()))}</span>`
    : "";
  return (
    `<div class="sup-card" data-urgency="${urgency}" data-qid="${esc(qid)}" data-kind="escalation">` +
    `<div class="sup-card-top">` +
    `<span class="perm-badge" data-perm="${TIER_PERM[tier]}" title="tier: ${esc(tier)}">` +
    `<span class="perm-dot" aria-hidden="true"></span>${esc(tier)}</span>` +
    `<span class="sup-q mono">${esc(skill)}</span>` +
    (urgency === "high"
      ? `<span class="sup-urg" title="urgency: high">高优</span>`
      : "") +
    `</div>` +
    `<div class="sup-esc-q">${esc(q?.question ?? "")}</div>` +
    `<div class="sup-meta">` +
    (runId
      ? `<a class="sup-link" href="#/runs/${encodeURIComponent(runId)}"` +
        ` title="查看 run ${esc(runId)}">run ${esc(shortId(runId))}</a>`
      : "") +
    (runId && frameId
      ? `<a class="sup-link" href="#/runs/${encodeURIComponent(runId)}` +
        `?frame=${encodeURIComponent(frameId)}" title="查看提问帧 ${esc(frameId)}">` +
        `帧 f-${esc(shortId(frameId))}</a>`
      : "") +
    timeHtml +
    `</div>` +
    (reason ? `<div class="sup-reason mono">${esc(reason)}</div>` : "") +
    (params
      ? `<details class="sup-ctx"><summary>${esc(copy("escalation.params"))}</summary>` +
        `<pre class="mono">${esc(JSON.stringify(params, null, 2))}</pre></details>`
      : "") +
    (reqTools.length || reqSkills.length
      ? `<div class="sup-req">` +
        `<span class="sup-req-label">${esc(copy("escalation.requested"))}</span>` +
        reqTools.map((t) => `<span class="chip mono">${esc(t)}</span>`).join("") +
        reqSkills.map((s) => `<span class="chip mono">skill:${esc(s)}</span>`).join("") +
        `</div>`
      : "") +
    (errText ? `<div class="sup-error" role="alert">${esc(errText)}</div>` : "") +
    (options
      ? `<div class="sup-actions">` +
        options
          .map((o) => `<button class="btn" data-answer="${esc(o)}">${esc(o)}</button>`)
          .join("") +
        `</div>`
      : `<div class="sup-actions">` +
        `<input class="input sup-input" type="text" placeholder="输入回答,回车提交"` +
        ` aria-label="回答">` +
        `<button class="btn btn-primary" data-submit>提交</button>` +
        `</div>`) +
    `</div>`
  );
}

/* ── 抽屉(DOM)─────────────────────────────────────────────── */

let drawer = null; // 当前打开的抽屉根(.inbox-overlay);null = 关闭
let unsub = null; // store 订阅句柄(打开时挂,关闭时退)
/* 本地提交被拒错误条:qid → 文本(下次成功提交或问题离开收件箱时清除) */
const localErrors = new Map();

export const isInboxOpen = () => drawer !== null;

/* 轮询入口(app.js 5s 周期 + 打开抽屉时调用):失败静默,徽标保持旧值。 */
export async function pollInbox() {
  try {
    const rows = await getJson("/api/supervisor/pending");
    store.set({ inboxPending: Array.isArray(rows) ? rows : [] });
  } catch {
    /* 单次轮询失败静默:下个周期重试(API 健康 Toast 由 app 轮询承担) */
  }
}

/* TopBar 徽标(store.inboxPending 订阅驱动):>0 显示计数;含 high 变色(§3.1 双编码) */
export function renderInboxBadge() {
  const el = document.querySelector("#inboxBadge");
  const btn = document.querySelector("#inboxBtn");
  if (!el || !btn) return;
  const { count, hasHigh } = badgeModel(store.get("inboxPending"));
  el.hidden = count === 0;
  el.textContent = count > 99 ? "99+" : String(count);
  el.dataset.high = String(hasHigh);
  btn.title = count ? `Supervisor 收件箱(${count} 个待答)` : "Supervisor 收件箱";
}

/* 作答提交:成功 → 乐观移出 store(卡片消失)+ Toast;被拒 → 错误条留在卡片上。 */
async function submitAnswer(qid, answer) {
  try {
    await postJson(`/api/supervisor/${encodeURIComponent(qid)}/answer`, { answer });
  } catch (e) {
    localErrors.set(qid, e.message ?? "提交失败");
    renderCards();
    return;
  }
  localErrors.delete(qid);
  store.set({
    inboxPending: (store.get("inboxPending") ?? []).filter((q) => q?.question_id !== qid),
  });
  toast("已提交回答", "success");
}

/* 重绘保留:展开的 context / 输入中的回答 / 滚动位置(5s 轮询重绘不打扰作答) */
function snapshotCards(body) {
  return {
    open: new Set(
      [...body.querySelectorAll(".sup-ctx[open]")]
        .map((d) => d.closest(".sup-card")?.dataset.qid)
        .filter(Boolean)),
    inputs: new Map(
      [...body.querySelectorAll(".sup-input")]
        .map((i) => [i.closest(".sup-card")?.dataset.qid, i.value])
        .filter(([k]) => Boolean(k))),
    scroll: body.scrollTop,
  };
}

function renderCards() {
  const body = drawer?.querySelector(".inbox-body");
  if (!body) return;
  const snap = snapshotCards(body);
  const rows = sortedPending(store.get("inboxPending"));
  // 已离开收件箱的问题清除本地错误条
  for (const qid of [...localErrors.keys()]) {
    if (!rows.some((q) => q?.question_id === qid)) localErrors.delete(qid);
  }
  body.innerHTML = rows.length
    ? rows.map((q) => questionCardHtml(q, localErrors.get(q?.question_id) ?? null)).join("")
    : emptyBlock("没有待答的裁决请求", "run 里 ask_supervisor 的提问会出现在这里", "inbox");
  for (const d of body.querySelectorAll(".sup-ctx")) {
    if (snap.open.has(d.closest(".sup-card")?.dataset.qid)) d.open = true;
  }
  for (const i of body.querySelectorAll(".sup-input")) {
    const v = snap.inputs.get(i.closest(".sup-card")?.dataset.qid);
    if (v) i.value = v;
  }
  body.scrollTop = snap.scroll;
  const count = drawer.querySelector(".inbox-count");
  if (count) count.textContent = rows.length ? `${rows.length} 个待答` : "";
}

export function openInbox() {
  if (drawer) {
    renderCards();
    return;
  }
  drawer = document.createElement("div");
  drawer.className = "inbox-overlay";
  drawer.innerHTML =
    `<aside class="inbox-drawer" role="dialog" aria-modal="true" aria-label="Supervisor 收件箱">` +
    `<div class="inbox-head">` +
    `<span class="inbox-title">Supervisor 收件箱</span>` +
    `<span class="inbox-count"></span>` +
    `<button class="icon-btn inbox-close" aria-label="关闭">✕</button>` +
    `</div>` +
    `<div class="inbox-body"></div>` +
    `</aside>`;
  document.body.appendChild(drawer);
  unsub = store.subscribe((state, patch) => {
    if ("inboxPending" in patch) renderCards();
  });
  // 事件(委托在抽屉根上):关闭 / 作答 / 链接跳转
  drawer.querySelector(".inbox-close").addEventListener("click", closeInbox);
  drawer.addEventListener("click", (e) => {
    if (e.target === drawer) return closeInbox(); // 遮罩点击关闭
    const link = e.target.closest(".sup-link");
    if (link) return closeInbox(); // 跳 run/帧深链接:关抽屉,hash 导航照常
    const card = e.target.closest(".sup-card");
    if (!card) return;
    const qid = card.dataset.qid;
    const optBtn = e.target.closest("[data-answer]");
    if (optBtn) return submitAnswer(qid, optBtn.dataset.answer);
    if (e.target.closest("[data-submit]")) {
      return submitAnswer(qid, card.querySelector(".sup-input")?.value ?? "");
    }
  });
  drawer.addEventListener("keydown", (e) => {
    if (e.key === "Escape") return closeInbox();
    if (e.key === "Enter" && e.target.classList?.contains("sup-input")) {
      const card = e.target.closest(".sup-card");
      if (card) submitAnswer(card.dataset.qid, e.target.value);
    }
  });
  renderCards();
  pollInbox(); // 打开即刷新一次(不等下个轮询周期)
}

export function closeInbox() {
  if (!drawer) return;
  unsub?.();
  unsub = null;
  drawer.remove();
  drawer = null;
}

export function toggleInbox() {
  if (isInboxOpen()) closeInbox();
  else openInbox();
}

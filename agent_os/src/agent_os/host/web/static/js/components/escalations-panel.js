/* 升权审计面板(docs/ESCALATION.md §5;WS2):run 详情底部折叠栏——
   汇总行(裁决/批准/拒绝/Grant 命中)+ 事件表(时间/技能/档位迁移/决定/批准人/
   scope/参数折叠)+ Grant 台账段(approve-once 不登记的说明随段头)。

   纯函数(不碰 DOM,node 单测可载):
     decisionBadgeHtml(ev)       决定徽标(approve-once/approve-run/grant-run/deny/待裁决;
                                 approve-once 注"无台账",grant-run 注"台账命中",双编码)
     tierMigrationHtml(ev)       档位迁移(from_tier → tier,--perm-* 色板徽标)
     escalationTableHtml(events) 事件表(时间序;空态 emptyBlock)
     grantsTableHtml(grants)     Grant 台账表
     escalationPanelHtml(panel)  面板整体(汇总行 + 事件表 + 台账段)
     summaryLine(panel)          标题行合计摘要(折叠态也可见)
   mountEscalationsPanel 是唯一碰 DOM 的部分:<details> 展开懒加载(load 由
   workbench 注入,返回 escalations JSON);重试经 workbench 事件委托
   (data-action="ep-retry")转发。 */

import { absTs, emptyBlock, esc, shortId } from "../util.js";
import { TIER_PERM } from "./inbox.js";

/* ── 决定徽标(颜色 + 文字双编码;颜色走 data-decision → app.css token)── */

const DECISIONS = new Set(["approve-once", "approve-run", "grant-run", "deny"]);
/* approve-once 不登记台账(docs/ESCALATION.md §4:下次同调用重新过人眼);
   grant-run = 台账命中放行(无 pre,无本次人审) */
const DECISION_NOTE = { "approve-once": "无台账", "grant-run": "台账命中" };

export function decisionBadgeHtml(ev) {
  if (ev?.kind === "pre" && !ev?.paired) {
    return `<span class="ep-badge" data-decision="pending">待裁决</span>`;
  }
  const raw = String(ev?.decision ?? "");
  const decision = DECISIONS.has(raw) ? raw : "unknown";
  const label = ev?.kind === "denied" ? "denied" : raw || "—";
  const note = DECISION_NOTE[decision];
  return (
    `<span class="ep-badge" data-decision="${esc(decision)}">${esc(label)}</span>` +
    (note ? `<span class="ep-badge-note">${esc(note)}</span>` : "")
  );
}

/* ── 档位迁移(inbox.js TIER_PERM 先例:档名直渲,颜色走 --perm-* token)── */

const tierBadge = (tier) => {
  const t = String(tier ?? "");
  if (!Object.hasOwn(TIER_PERM, t)) return `<span class="ep-tier-none">—</span>`;
  return (
    `<span class="perm-badge" data-perm="${TIER_PERM[t]}" title="tier: ${esc(t)}">` +
    `<span class="perm-dot" aria-hidden="true"></span>${esc(t)}</span>`
  );
};

export function tierMigrationHtml(ev) {
  const target = tierBadge(ev?.tier);
  if (ev?.from_tier == null) return target; // 发起帧档未知(在途/旧档):只显目标档
  return `${tierBadge(ev.from_tier)}<span class="ep-tier-arrow" aria-hidden="true">→</span>${target}`;
}

/* ── 事件表(时间序;配对行 = pre 并入 post,params 摘要可折叠)───────── */

function eventRowHtml(ev) {
  const asked = ev?.asked_ts != null ? ` title="请求于 ${esc(absTs(ev.asked_ts))}"` : "";
  const params = ev?.params
    ? `<details class="json-fold ep-params"><summary>参数</summary>` +
      `<pre class="msg-pre">${esc(ev.params)}</pre></details>`
    : "—";
  return (
    `<tr data-kind="${esc(ev?.kind ?? "")}" data-frame-id="${esc(ev?.frame_id ?? "")}">` +
    `<td class="mono"${asked}>${esc(absTs(ev?.ts))}</td>` +
    `<td>${esc(ev?.skill ?? "—")}</td>` +
    `<td class="ep-migration">${tierMigrationHtml(ev)}</td>` +
    `<td>${decisionBadgeHtml(ev)}</td>` +
    `<td class="mono">${esc(ev?.decided_by ?? "—")}</td>` +
    `<td class="mono">${esc(ev?.scope ?? "—")}</td>` +
    `<td>${params}</td>` +
    `</tr>`
  );
}

export function escalationTableHtml(events) {
  const rows = Array.isArray(events) ? events : [];
  if (!rows.length) {
    return emptyBlock("无升权事件", "本 run 未触发升权确认(低档 → 高档调用)", "inbox");
  }
  const head =
    `<tr>` +
    `<th scope="col">时间</th><th scope="col">技能</th><th scope="col">档位迁移</th>` +
    `<th scope="col">决定</th><th scope="col">批准人</th><th scope="col">scope</th>` +
    `<th scope="col">参数</th>` +
    `</tr>`;
  return (
    `<table class="us-table ep-table">` +
    `<thead>${head}</thead>` +
    `<tbody>${rows.map(eventRowHtml).join("")}</tbody>` +
    `</table>`
  );
}

/* ── Grant 台账(checkpoint run.grants;approve-once 不登记,说明随段头)── */

export function grantsTableHtml(grants) {
  const rows = (Array.isArray(grants) ? grants : []).map(
    (g) =>
      `<tr>` +
      `<td>${esc(g?.skill ?? "—")}</td>` +
      `<td>${tierBadge(g?.tier)}</td>` +
      `<td class="mono">${esc(g?.scope ?? "—")}</td>` +
      `<td class="mono">${esc(g?.decided_by ?? "—")}</td>` +
      `<td class="mono">${esc(absTs(g?.decided_at))}</td>` +
      `<td class="mono" title="${esc(g?.question_id ?? "")}">${esc(g?.question_id ?? "—")}</td>` +
      `<td class="mono" title="${esc(g?.frame_id ?? "")}">f-${esc(shortId(g?.frame_id))}</td>` +
      `</tr>`,
  );
  const head =
    `<tr>` +
    `<th scope="col">技能/工具</th><th scope="col">档</th><th scope="col">scope</th>` +
    `<th scope="col">批准人</th><th scope="col">批准时间</th><th scope="col">question_id</th>` +
    `<th scope="col">发起帧</th>` +
    `</tr>`;
  return (
    `<table class="us-table ep-table ep-grants-table">` +
    `<thead>${head}</thead>` +
    `<tbody>${rows.join("")}</tbody>` +
    `</table>`
  );
}

/* ── 面板整体:汇总行 + 事件表 + 台账段 ──────────────────────────── */

export function summaryLine(panel) {
  const s = panel?.summary ?? {};
  const n = (k) => Number(s[k]) || 0;
  return `${n("total")} 次裁决 · 批准 ${n("approved")} · 拒绝 ${n("denied")} · Grant 命中 ${n("grant_run")}`;
}

export function escalationPanelHtml(panel) {
  const s = panel?.summary ?? {};
  const stat = (label, value, key) =>
    `<span class="ep-stat" data-k="${key}"><b class="mono">${Number(value) || 0}</b>${esc(label)}</span>`;
  const stats =
    `<div class="ep-stats" role="note">` +
    stat("次裁决", s.total, "total") +
    stat("批准", s.approved, "approved") +
    stat("拒绝", s.denied, "denied") +
    stat("Grant 命中", s.grant_run, "grant_run") +
    `</div>`;
  const grants = Array.isArray(panel?.grants) ? panel.grants : [];
  const grantsSec =
    `<div class="ep-grants">` +
    `<div class="ep-grants-head">Grant 台账</div>` +
    `<div class="ep-grants-note">approve-once 不登记台账(下次同调用重新过人眼);` +
    `grant-run 命中无 pre 信号(Grant 直接放行);工具确认(tool-confirm)的 approve-run 同台账登记。</div>` +
    (grants.length
      ? grantsTableHtml(grants)
      : `<div class="ep-grants-empty">本 run 无 run 档 Grant</div>`) +
    `</div>`;
  return stats + escalationTableHtml(panel?.events) + grantsSec;
}

/* ── DOM 挂载(唯一碰 DOM 的部分)──────────────────────────────
   mountEscalationsPanel(details, { load }) → { reload, destroy }。
   <details> 首次展开时懒加载;重试经 workbench 事件委托转发。 */
export function mountEscalationsPanel(details, { load } = {}) {
  const state = { status: "idle", panel: null, destroyed: false };
  details.innerHTML =
    `<summary>升权审计 <span class="ep-summary-totals"></span></summary>` +
    `<div class="wb-usage-body ep-body"></div>`;
  const body = details.querySelector(".ep-body");
  const summaryTotals = details.querySelector(".ep-summary-totals");

  const skeleton =
    `<div class="skeleton-stack skeleton-pad">` +
    `<span class="skeleton skeleton-line w-70"></span>` +
    `<span class="skeleton skeleton-line w-40"></span>` +
    `</div>`;

  function render() {
    if (state.destroyed || !body) return;
    if (state.status === "loading" || state.status === "idle") {
      body.innerHTML = skeleton;
      return;
    }
    if (state.status === "error") {
      body.innerHTML =
        `<div class="panel-error">` +
        `<span class="error-msg">加载升权审计失败</span>` +
        `<button class="btn" data-action="ep-retry">重试</button>` +
        `</div>`;
      return;
    }
    body.innerHTML = escalationPanelHtml(state.panel);
    if (summaryTotals) summaryTotals.textContent = `· ${summaryLine(state.panel)}`;
  }

  async function loadNow() {
    if (state.destroyed || state.status === "loading") return;
    state.status = "loading";
    render();
    try {
      state.panel = await load?.();
      state.status = "ready";
    } catch {
      state.status = "error";
    }
    render();
  }

  const onToggle = () => {
    if (details.open && state.status === "idle") loadNow(); // 展开懒加载(默认收起)
  };
  details.addEventListener("toggle", onToggle);
  render();

  return {
    reload() {
      if (state.status !== "loading") loadNow();
    },
    destroy() {
      state.destroyed = true;
      details.removeEventListener("toggle", onToggle);
    },
  };
}

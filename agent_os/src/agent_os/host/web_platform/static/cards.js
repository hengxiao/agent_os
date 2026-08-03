/* 产物卡渲染(docs/WEB-PLATFORM.md §4;六卡型,纯函数不碰 DOM)。
   cardHtml(card) 按 type 分发;actions 渲染为按钮(data-card-act 携带
   action id 与 payload,事件层统一经 POST /platform/api/cards/action 转发)。
   组件零分支:颜色全部走契约 token(perm 色板/lab 状态色),文案走 copy。 */

import { copy } from "/static/js/themes.js";
import { esc } from "/static/js/util.js";

const _act = (a) =>
  `<button class="btn" data-card-act="${esc(a.id)}" data-payload='${esc(JSON.stringify(a.payload ?? {}))}'>` +
  `${esc(a.label)}</button>`;

const _tier = (tier) =>
  tier
    ? `<span class="perm-badge" data-perm="${esc({ none: "READ", reversible: "WRITE", irreversible: "EXEC" }[tier] ?? "READ")}">` +
      `<span class="perm-dot" aria-hidden="true"></span>${esc(tier)}</span>`
    : "";

/* plan 卡:分解表(复用/新建)+ 批准动作 */
function planCard(card) {
  const d = card.data ?? {};
  const reuse = (d.reuse ?? [])
    .map((r) => `<li><span class="mono">${esc(r.name)}</span> <span class="pf-dim">${esc(r.reason ?? "")}</span></li>`)
    .join("");
  const create = (d.create ?? [])
    .map((c) => `<li><span class="mono">${esc(c.name)}</span> <span class="pf-dim">${esc(c.reason ?? "")}</span></li>`)
    .join("");
  return (
    `<div class="pf-sec">${esc(copy("platform.reuse"))}(${reuse ? "" : "0"})<ul>${reuse}</ul></div>` +
    `<div class="pf-sec">${esc(copy("platform.create"))}<ul>${create}</ul></div>`
  );
}

/* skill_pack 卡:成员列表 + tier 徽标 */
function skillPackCard(card) {
  const d = card.data ?? {};
  return (
    `<div class="pf-sec"><span class="mono">${esc(d.name ?? "")}</span> ${_tier(d.tier)}</div>` +
    `<div class="pf-sec">${esc(copy("platform.members"))}: ` +
    (d.members ?? []).map((m) => `<span class="chip mono">${esc(m)}</span>`).join(" ") +
    `</div>`
  );
}

/* gate_report 卡:五关色点 + findings 可展开 + 去修复 */
function gateReportCard(card) {
  const d = card.data ?? {};
  const gates = d.gates ?? {};
  const rows = Object.entries(gates)
    .map(([gid, gate]) => {
      const status = gate.status ?? "skip";
      const findings = (gate.findings ?? [])
        .map((f) => `<li data-level="${esc(f.level)}">${esc(f.message ?? "")}</li>`)
        .join("");
      return (
        `<div class="pf-gate" data-status="${esc(status)}">` +
        `<span class="pf-gate-dot" aria-hidden="true"></span>` +
        `<span class="mono">${esc(gid)}</span>` +
        (findings ? `<details><summary>${(gate.findings ?? []).length} 项</summary><ul>${findings}</ul></details>` : "") +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="pf-sec"><span class="mono">${esc(d.draft ?? "")}</span> ` +
    `<span class="lab-pkg-status" data-status="${esc(d.status ?? "")}">${esc(d.status ?? "")}</span></div>` +
    rows +
    `<div class="pf-sec"><a class="btn" href="/#/lab/${encodeURIComponent(d.draft ?? "")}">${esc(copy("platform.fix"))}</a></div>`
  );
}

/* diff 卡:字段新旧两列 + prompt 红绿行(Flow C 同构呈现) */
function diffCard(card) {
  const d = card.data ?? {};
  const members = (d.diff?.members ?? [])
    .map((m) => {
      const fields = (m.fields ?? [])
        .map(
          (f) =>
            `<div class="pf-twocol" data-kind="${esc(f.kind)}">` +
            `<div>${esc(JSON.stringify(f.old) ?? "—")}</div><div>${esc(JSON.stringify(f.new) ?? "—")}</div></div>`
        )
        .join("");
      const lines = (m.prompt_diff ?? [])
        .filter((l) => l.kind !== "same")
        .map(
          (l) =>
            `<div class="pf-dline" data-kind="${esc(l.kind)}">${l.kind === "add" ? "+" : "-"} ${esc(l.text)}</div>`
        )
        .join("");
      return (
        `<div class="pf-dmember"><span class="mono">${esc(m.member)}</span>` +
        `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
        fields + lines + `</div>`
      );
    })
    .join("");
  return members || `<div class="pf-dim">${esc(copy("platform.no.changes"))}</div>`;
}

/* publish 卡:成员 action 三态 + warnings 勾选门 */
function publishCard(card) {
  const d = card.data ?? {};
  const rows = (d.members ?? [])
    .map(
      (m) =>
        `<div class="pf-prow" data-action="${esc(m.action)}">` +
        `<span class="mono">${esc(m.name)}</span>` +
        `<span class="pf-dim mono">${esc(m.action)}</span>` +
        `<span class="mono">${esc(m.from_version ?? "—")} → ${esc(m.to_version ?? "")}</span></div>`
    )
    .join("");
  return (
    `<div class="pf-sec"><span class="mono">${esc(d.root ?? "")}</span> ` +
    `<span class="pf-dim mono">${esc(String(d.plan_id ?? "").slice(0, 16))}</span></div>` +
    rows +
    `<label class="pf-ack"><input type="checkbox" data-ack>` +
    `<span>${esc(copy("platform.warnings.ack"))}</span></label>`
  );
}

/* table 卡:通用键值表 */
function tableCard(card) {
  const d = card.data ?? {};
  const head = (d.columns ?? []).map((c) => `<th>${esc(c)}</th>`).join("");
  const rows = (d.rows ?? [])
    .map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`)
    .join("");
  return (
    (d.title ? `<div class="pf-sec"><b>${esc(d.title)}</b></div>` : "") +
    `<table class="pf-table"><tr>${head}</tr>${rows}</table>`
  );
}

const RENDERERS = {
  plan: planCard,
  skill_pack: skillPackCard,
  gate_report: gateReportCard,
  diff: diffCard,
  publish: publishCard,
  table: tableCard,
};

/* 卡渲染入口:type 分发 + actions 按钮区(未知卡型降级为 JSON 预览,不炸) */
export function cardHtml(card) {
  const type = card?.type ?? "";
  const render = RENDERERS[type];
  const body = render ? render(card) : `<pre class="mono">${esc(JSON.stringify(card?.data ?? {}, null, 2))}</pre>`;
  const actions = (card?.actions ?? []).map(_act).join("");
  return (
    `<div class="pf-card" data-card="${esc(type)}">` +
    `<div class="pf-card-tag mono">${esc(type)}</div>` +
    body +
    (actions ? `<div class="pf-card-actions">${actions}</div>` : "") +
    `</div>`
  );
}

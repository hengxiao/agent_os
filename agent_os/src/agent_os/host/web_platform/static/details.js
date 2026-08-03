/* 详情视图(docs/WEB-PLATFORM.md §10;左栏 tab 改造):四类详情 tab 的渲染纯函数。

   - gate:完整五关 + findings 全展开(条款号并列,不折叠——详情 tab 就是给全貌的);
   - pack:成员树(closure 数据)/tier/状态;生产成员链接旧 UI Skills 页;
   - plan:成员 action 三态全表 + package_hash + gate_report_id 链接;
   - run:状态/结果 + 信号时间线(复用旧 web trace.js 的 deriveTraceView/renderTrace,
     不新写一套轨迹组件)。 */

import { copy } from "/static/js/themes.js";
import { esc } from "/static/js/util.js";
import { deriveTraceView, renderTrace } from "/static/js/components/trace.js";
import { diffCard } from "./cards.js";

/* diff 详情:字段两列 + prompt 红绿行(技术面全貌 = 原 diff 卡本体;
   接受/放弃动作留在对话流摘要卡上,详情只看) */
export function diffDetailHtml(data) {
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(data?.name ?? "")}</div>` +
    diffCard({ data }) +
    `</div>`
  );
}

/* escalation 详情(W2):调用参数 JSON + 请求的权限集 + reason_hint 原文
   (摘要层只给"需要你批准"一句,技术面全部在这里——审的就是要执行的) */
export function escDetailHtml(data) {
  const requested = data?.requested ?? {};
  const chips = [...(requested.tools ?? []), ...(requested.skills ?? [])]
    .map((r) => `<span class="chip mono">${esc(r)}</span>`)
    .join(" ");
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(data?.skill ?? "")}</div>` +
    (data?.reason_hint
      ? `<div class="pf-sec">${esc(copy("platform.esc.reason"))}: <span class="mono">${esc(data.reason_hint)}</span></div>`
      : "") +
    (chips
      ? `<div class="pf-sec">${esc(copy("platform.esc.requested"))}: ${chips}</div>`
      : "") +
    `<div class="pf-sec">${esc(copy("platform.esc.params"))}:</div>` +
    `<pre class="mono">${esc(JSON.stringify(data?.params ?? {}, null, 2))}</pre>` +
    `</div>`
  );
}

/* plan(分解)详情(N6,O6):分解结构全量(goal + reuse/create 的 reason/template)
   + 路由来源角标(route_meta;技术标注只活在详情层,不进摘要——禁忌词纪律) */
export function decomposeDetailHtml(data) {
  const meta = data?.route_meta;
  const badge = meta
    ? `<div class="pf-sec">${esc(copy("platform.route.label"))}: ` +
      `<span class="lab-pkg-status" data-status="${esc(meta.route === "llm" ? "pass" : "warn")}">` +
      `${esc(copy(meta.route === "llm" ? "platform.route.llm" : "platform.route.rule"))}</span>` +
      (meta.reason ? ` <span class="pf-dim mono">${esc(meta.reason)}</span>` : "") +
      `</div>`
    : "";
  const reuse = (data?.reuse ?? [])
    .map(
      (r) =>
        `<div class="pf-prow" data-action="unchanged"><span class="mono">${esc(r.name)}</span>` +
        `<span class="pf-dim">${esc(r.reason ?? "")}</span></div>`
    )
    .join("");
  const create = (data?.create ?? [])
    .map(
      (c) =>
        `<div class="pf-prow" data-action="create"><span class="mono">${esc(c.name)}</span>` +
        `<span class="pf-dim mono">${esc(c.template ?? "")}</span> ` +
        `<span class="pf-dim">${esc(c.reason ?? "")}</span></div>`
    )
    .join("");
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head">${esc(data?.goal ?? "")}</div>` +
    badge +
    `<div class="pf-sec">${esc(copy("platform.reuse"))}</div>` +
    (reuse || `<div class="pf-dim">—</div>`) +
    `<div class="pf-sec">${esc(copy("platform.create"))}</div>` +
    create +
    `</div>`
  );
}

/* gate 详情:完整报告(全部 findings 展开) */
export function gateDetailHtml(data) {
  const gates = data?.gates ?? {};
  const rows = Object.entries(gates)
    .map(([gid, gate]) => {
      const status = gate.status ?? "skip";
      const findings = (gate.findings ?? [])
        .map(
          (f) =>
            `<li data-level="${esc(f.level)}">` +
            `<span class="pf-clause mono">${esc(f.clause ?? "")}</span> ${esc(f.message ?? "")}</li>`
        )
        .join("");
      return (
        `<div class="pf-gate" data-status="${esc(status)}">` +
        `<span class="pf-gate-dot" aria-hidden="true"></span><span class="mono">${esc(gid)}</span>` +
        (findings ? `<ul class="pf-findings">${findings}</ul>` : `<span class="pf-dim">${esc(gate.note ?? "ok")}</span>`) +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(data?.draft ?? "")}</div>` +
    `<div class="pf-sec"><span class="lab-pkg-status" data-status="${esc(data?.status ?? "")}">${esc(data?.status ?? "")}</span></div>` +
    rows +
    `</div>`
  );
}

/* pack 详情:成员树(closure 的成员行:depth 缩进 + tier 徽标 + 状态;生产成员可点) */
export function packDetailHtml(closure) {
  const tierOf = (t) =>
    t
      ? `<span class="perm-badge" data-perm="${esc({ none: "READ", reversible: "WRITE", irreversible: "EXEC" }[t] ?? "READ")}">` +
        `<span class="perm-dot" aria-hidden="true"></span>${esc(t)}</span>`
      : "";
  const rows = (closure?.members ?? [])
    .map((m) => {
      const last = m.name.split(".").pop();
      const linked =
        m.status === "production" || m.status === "external"
          ? `<a class="pf-link mono" href="/#/skills/${encodeURIComponent(m.name)}">${esc(last)}</a>`
          : `<span class="mono">${esc(last)}</span>`;
      return (
        `<div class="pf-pkgrow" style="--ns-depth:${m.depth ?? 0}">` +
        `${tierOf(m.tier)}${linked}` +
        `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
        (m.ref_by ? `<span class="pf-dim mono">← ${esc(m.ref_by)}</span>` : "") +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(closure?.root ?? "")} ${tierOf(closure?.root_tier)}</div>` +
    rows +
    ((closure?.errors ?? []).map((e) => `<div class="pf-errline">⚠ ${esc(e.message ?? "")}</div>`).join("")) +
    `</div>`
  );
}

/* plan 详情:成员三态全表 + package_hash + gate_report_id */
export function planDetailHtml(data) {
  const rows = (data?.members ?? [])
    .map(
      (m) =>
        `<div class="pf-prow" data-action="${esc(m.action)}">` +
        `<span class="lab-gate-status" data-status="${esc(m.gate_status)}">${esc(copy(`lab.gate.${m.gate_status}`))}</span>` +
        `<span class="mono">${esc(m.name)}</span>` +
        `<span class="pf-dim mono">${esc(m.action)}</span>` +
        `<span class="mono">${esc(m.from_version ?? "—")} → ${esc(m.to_version ?? "")}</span>` +
        (m.gate_report_id ? `<span class="pf-dim mono">${esc(m.gate_report_id)}</span>` : "") +
        `</div>`
    )
    .join("");
  const blockers = (data?.blockers ?? [])
    .map((b) => `<div class="pf-errline">⚠ <b>${esc(b.kind)}</b> ${esc(b.member ?? "")} ${esc(b.message ?? "")}</div>`)
    .join("");
  const warnings = (data?.warnings ?? []).map((w) => `<div class="pf-warnline">⚠ ${esc(w)}</div>`).join("");
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(data?.root ?? "")} ` +
    `<span class="pf-dim mono">${esc(data?.package_hash ?? "")}</span></div>` +
    rows + blockers + warnings +
    `</div>`
  );
}

/* run 详情:状态/结果 + 信号时间线(deriveTraceView/renderTrace 复用) */
export function runDetailHtml({ detail, signals }) {
  const status = esc(detail?.status ?? "");
  const result =
    detail?.result !== null && detail?.result !== undefined
      ? `<pre class="mono">${esc(JSON.stringify(detail.result, null, 2))}</pre>`
      : "";
  const error = detail?.error ? `<div class="pf-errline">${esc(detail.error)}</div>` : "";
  const trace = renderTrace(deriveTraceView(Array.isArray(signals) ? signals : [], null, {}));
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(detail?.skill ?? "")} ` +
    `<span class="lab-pkg-status" data-status="${status}">${status}</span></div>` +
    error + result +
    `<div class="pf-sec">${esc(copy("platform.detail.trace"))}</div>` +
    (trace || `<div class="pf-dim">${esc(copy("platform.detail.no.trace"))}</div>`) +
    `</div>`
  );
}

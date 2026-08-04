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
      // M3:草稿成员给"编辑"链接(开 lab-draft tab;生产成员跳旧 UI Skills 页)
      const edit =
        m.status === "draft"
          ? ` <button class="pf-detail-link" data-detail-kind="draft" data-detail-ref="${esc(m.name)}"` +
            ` data-detail='{}'>${esc(copy("platform.draft.edit"))}</button>`
          : "";
      return (
        `<div class="pf-pkgrow" style="--ns-depth:${m.depth ?? 0}">` +
        `${tierOf(m.tier)}${linked}` +
        `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
        edit +
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

/* run 详情:状态/结果 + 信号时间线(deriveTraceView/renderTrace 复用)。
   M3:状态允许时给动作按钮(stop/resume/rerun 走 action 管道,data-tab-act)
   与"开调试"链接(failed/running → replay 调试会话,docs/APP-MODEL.md §8 闭环) */
export function runDetailHtml({ detail, signals, launchSchema = null }) {
  const status = esc(detail?.status ?? "");
  const runId = detail?.run_id ?? detail?.id ?? "";
  const result =
    detail?.result !== null && detail?.result !== undefined
      ? `<pre class="mono">${esc(JSON.stringify(detail.result, null, 2))}</pre>`
      : "";
  const error = detail?.error ? `<div class="pf-errline">${esc(detail.error)}</div>` : "";
  const trace = renderTrace(deriveTraceView(Array.isArray(signals) ? signals : [], null, {}));
  const actions =
    (detail?.status === "running"
      ? `<button class="btn" data-tab-act="run.stop">${esc(copy("platform.run.stop"))}</button>`
      : `<button class="btn" data-tab-act="run.resume">${esc(copy("platform.run.resume"))}</button>` +
        `<button class="btn" data-tab-act="run.rerun">${esc(copy("platform.run.rerun"))}</button>`) +
    (["failed", "running", "aborted"].includes(detail?.status) && runId
      ? `<button class="btn" data-debug-run="${esc(runId)}">${esc(copy("platform.run.debug"))}</button>`
      : "");
  // M4a 发起面归一:app 内"再跑一次";W3 起 inputs schema 已知时升级为
  // W-form 逐字段表单(textarea 降级进"高级:JSON"折叠,两通道同源)
  const launch = runId
    ? `<div class="pf-sec">${esc(copy("platform.run.launch"))}</div>` +
      (launchSchema
        ? `<div data-launch-form="1"></div>` +
          `<details class="wd-adv"><summary>${esc(copy("w.form.advanced"))}</summary>` +
          `<textarea class="input mono" data-launch-input rows="3" placeholder="${esc(copy("platform.run.launch.ph"))}"></textarea></details>`
        : `<textarea class="input mono" data-launch-input rows="3" placeholder="${esc(copy("platform.run.launch.ph"))}"></textarea>`) +
      `<div class="pf-card-actions"><button class="btn" data-tab-act="run.launch">${esc(copy("platform.run.launch"))}</button></div>`
    : "";
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(detail?.skill ?? "")} ` +
    `<span class="lab-pkg-status" data-status="${status}">${status}</span></div>` +
    error + result +
    (actions ? `<div class="pf-card-actions">${actions}</div>` : "") +
    launch +
    `<div class="pf-sec">${esc(copy("platform.detail.trace"))}</div>` +
    (trace || `<div class="pf-dim">${esc(copy("platform.detail.no.trace"))}</div>`) +
    `</div>`
  );
}

/* debug Tab Surface(M3,简化调试台):暂停点/帧栈/断点 + 放行/停止
   (动作走 action 管道;干预全家桶——改参/注入/单步——留旧 web 调试台,深链在) */
export function debugDetailHtml(data) {
  const state = esc(data?.state ?? "");
  const pause = data?.pause_point;
  const frames = (data?.frame_stack ?? [])
    .map(
      (f) =>
        `<div class="pf-prow" data-action="unchanged"><span class="mono">${esc(f.skill ?? f.frame_id ?? "")}</span>` +
        `<span class="pf-dim mono">${esc(f.frame_id ?? "")}</span></div>`
    )
    .join("");
  const bps = (data?.breakpoints ?? [])
    .map(
      (b) =>
        `<div class="pf-ln mono">${esc(b.kind ?? "")} ${esc(b.match ?? "")}` +
        (b.hits ? ` <span class="pf-dim">×${b.hits}</span>` : "") +
        `</div>`
    )
    .join("");
  const pausedLine = pause
    ? `<div class="pf-card-sub">${esc(copy("platform.debug.paused")).replace("{where}", esc(pause.signal ?? pause.frame_id ?? ""))}</div>`
    : "";
  const actions =
    state === "paused"
      ? `<button class="btn" data-tab-act="debug.continue">${esc(copy("platform.debug.continue"))}</button>` +
        `<button class="btn" data-tab-act="debug.stop">${esc(copy("platform.debug.stop"))}</button>`
      : "";
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(data?.session_id ?? "")} ` +
    `<span class="lab-pkg-status" data-status="${esc(state === "paused" ? "warn" : "pass")}">${state}</span></div>` +
    pausedLine +
    (actions ? `<div class="pf-card-actions">${actions}</div>` : "") +
    `<div class="pf-sec">${esc(copy("platform.debug.frames"))}</div>` +
    (frames || `<div class="pf-dim">—</div>`) +
    (bps ? `<div class="pf-sec">${esc(copy("platform.debug.bps"))}</div>` + bps : "") +
    `</div>`
  );
}

/* lab-draft Tab Surface(M3 最简路径,不改 lab.js):草稿摘要 + 检查/提交动作
   (action 管道)+ 深链旧 Lab 编辑器(精确编辑一律回专家模式,§8 降级面) */
export function draftTabHtml(data) {
  const name = data?.name ?? "";
  const manifest = data?.manifest ?? {};
  return (
    `<div class="pf-detail">` +
    `<div class="pf-detail-head mono">${esc(name)}</div>` +
    `<div class="pf-card-sub">${esc(manifest.description ?? "")}</div>` +
    `<div class="pf-card-actions">` +
    `<button class="btn" data-tab-act="draft.check">${esc(copy("platform.draft.check"))}</button>` +
    `<button class="btn" data-tab-act="draft.promote">${esc(copy("platform.draft.promote"))}</button>` +
    `<a class="btn" href="/#/lab/${encodeURIComponent(name)}">${esc(copy("platform.draft.open"))}</a>` +
    `</div>` +
    `<div class="pf-sec">prompt</div>` +
    `<pre class="mono">${esc(data?.prompt ?? "")}</pre>` +
    `</div>`
  );
}

/* Tab Surface 分发(docs/APP-MODEL.md §3/§8;M1 概念归位):
   详情渲染按 "app kind + surface" 寻址——详情 kind 即 tab surface 名。 */
const _TAB_SURFACES = {
  gate: (data) => gateDetailHtml(data),
  pack: (data) => packDetailHtml(data),
  plan: (data) => planDetailHtml(data),
  run: (data) => runDetailHtml(data),
  diff: (data) => diffDetailHtml(data),
  esc: (data) => escDetailHtml(data),
  decompose: (data) => decomposeDetailHtml(data),
  debug: (data) => debugDetailHtml(data),
  draft: (data) => draftTabHtml(data),
};

export function renderTabSurface(kind, data) {
  const render = _TAB_SURFACES[kind];
  // 无 manifest 的 kind 拒绝渲染但不炸(docs/APP-MODEL.md §9 回退面)
  return render ? render(data) : `<div class="pf-detail"><pre class="mono">${esc(JSON.stringify(data ?? {}, null, 2))}</pre></div>`;
}

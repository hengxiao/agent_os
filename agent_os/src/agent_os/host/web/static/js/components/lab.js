/* Skill Lab(docs/SKILL-DEV.md;L1):#/lab 页面——草稿编辑器(全字段)+ 推导档实时显示。

   L1 范围:左栏编辑器(§1.3 七组全字段)+ 顶部条(草稿列表/新建/删除/保存/
   置灰的检查·提交)+ 中右栏空态占位(Agent 助手 L4、测试面板 L3)。
   保存永不报错(§2.4:编辑器不打断,闸门守出口——检查/提交是 L2 的事);
   推导档随 permissions 实时刷新(?tools=&skills= 未保存白名单覆盖,不必先存)。

   纯函数(不碰 DOM,node 单测可载):
     isValidDraftName(name)       NAMING 规范 + 路径穿越面(同后端 DraftStore)
     jsonError(text)              JSON 合法性提示(null = 合法)
     draftToForm(draft)           后端草稿 → 表单状态(七组全字段映射)
     formToManifest(form)         表单状态 → manifest dict(保存载荷)
     tierBadgeHtml(tier)          推导档徽标(tier→perm 色板槽位,同升权卡片)
     tierTitle(detail)            悬停来源明细("tool system.file.delete: irreversible")
     editorHtml(view)             编辑器整树 HTML
     topbarHtml(view)             顶部条 HTML(草稿选择/新建/删除/保存/置灰按钮)
     statusLine(view)             状态栏文本(已保存 HH:MM · 草稿(未提交)) */

import { deleteJson, getJson, postJson, putJson } from "../api.js";
import { copy } from "../themes.js";
import { deriveTraceView, renderTrace } from "./trace.js";
import { emptyBlock, esc, toast } from "../util.js";
import { TIER_PERM } from "./inbox.js";
import { skeletonFromSchema } from "./launch-dialog.js";

/* ── 纯函数 ─────────────────────────────────────────────────── */

const DRAFT_NAME_RE = /^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$/;

/* docs/NAMING.md §2:≥2 段点分小写 snake_case(与后端 DraftStore.check_name 同规则) */
export const isValidDraftName = (name) => DRAFT_NAME_RE.test(String(name ?? ""));

/* JSON 文本域的实时合法性提示:null = 合法(空串按合法——保存时回落 {}) */
export function jsonError(text) {
  if (!String(text ?? "").trim()) return null;
  try {
    JSON.parse(text);
    return null;
  } catch (e) {
    return String(e?.message ?? e);
  }
}

/* 后端草稿(read 端点形态)→ 表单状态;inputs/outputs 以 JSON 文本持有(§1.3 契约组) */
export function draftToForm(draft) {
  const m = draft?.manifest && typeof draft.manifest === "object" ? draft.manifest : {};
  const perms = m.permissions ?? {};
  const model = m.model ?? {};
  const cp = m.context_policy ?? {};
  const lim = m.limits ?? {};
  const trust = m.trust ?? {};
  const logic = m.logic ?? {};
  return {
    name: String(draft?.name ?? m.name ?? ""),
    version: String(m.version ?? ""),
    kind: m.kind === "code" ? "code" : "prompt",
    description: String(m.description ?? ""),
    inputsText: JSON.stringify(m.inputs ?? {}, null, 2),
    outputsText: JSON.stringify(m.outputs ?? {}, null, 2),
    prompt: String(draft?.prompt ?? m.prompt ?? ""),
    handler: draft?.handler ?? "",
    entry: String(m.entry ?? ""),
    handlerPath: String(m.handler ?? ""),
    logicMode: String(logic.mode ?? ""),
    tools: [...(perms.tools ?? [])],
    skills: [...(perms.skills ?? [])],
    blackboard: [...(perms.blackboard ?? [])],
    modelPrefer: (model.prefer ?? []).join(", "),
    modelTemperature: model.temperature ?? "",
    cpMaxTokens: cp.max_tokens ?? "",
    cpCompress: cp.compress ?? "hierarchical",
    limMaxSteps: lim.max_steps ?? "",
    limTimeout: lim.timeout ?? "",
    limRetry: lim.retry ?? 0,
    limMaxToolCalls: lim.max_tool_calls ?? "",
    trustConfirm: trust.confirm ?? "",
    trustReversal: trust.reversal ?? "",
    trustBlastRadius: trust.blast_radius ?? "",
    inline: m.inline === true,
    verifier: String(m.verifier ?? ""),
  };
}

const _num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

/* 表单状态 → manifest dict(PUT 载荷的 manifest 字段)。
   空的可选块(permissions.blackboard/model/limits/trust/…)整体省略——
   与 skills.yaml 单条同形,不写空壳字段。调用方先经 jsonError 把关。 */
export function formToManifest(form) {
  const manifest = {
    name: form.name,
    version: form.version || "0.1.0",
    kind: form.kind,
    description: form.description,
    inputs: JSON.parse(form.inputsText || "{}"),
    outputs: JSON.parse(form.outputsText || "{}"),
    permissions: { tools: [...form.tools], skills: [...form.skills] },
  };
  if (form.blackboard.length) manifest.permissions.blackboard = [...form.blackboard];
  if (form.entry) manifest.entry = form.entry;
  if (form.handlerPath) manifest.handler = form.handlerPath;
  if (form.logicMode) manifest.logic = { mode: form.logicMode };
  const prefer = String(form.modelPrefer ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  if (prefer.length || _num(form.modelTemperature) !== null) {
    manifest.model = { prefer };
    if (_num(form.modelTemperature) !== null) manifest.model.temperature = _num(form.modelTemperature);
  }
  if (_num(form.cpMaxTokens) !== null || form.cpCompress !== "hierarchical") {
    manifest.context_policy = { compress: form.cpCompress };
    if (_num(form.cpMaxTokens) !== null) manifest.context_policy.max_tokens = _num(form.cpMaxTokens);
  }
  const limits = {};
  if (_num(form.limMaxSteps) !== null) limits.max_steps = _num(form.limMaxSteps);
  if (_num(form.limTimeout) !== null) limits.timeout = _num(form.limTimeout);
  if (_num(form.limRetry)) limits.retry = _num(form.limRetry);
  if (_num(form.limMaxToolCalls) !== null) limits.max_tool_calls = _num(form.limMaxToolCalls);
  if (Object.keys(limits).length) manifest.limits = limits;
  const trust = {};
  if (form.trustConfirm) trust.confirm = form.trustConfirm;
  if (form.trustReversal) trust.reversal = form.trustReversal;
  if (form.trustBlastRadius) trust.blast_radius = form.trustBlastRadius;
  if (Object.keys(trust).length) manifest.trust = trust;
  if (form.inline) manifest.inline = true;
  if (form.verifier) manifest.verifier = form.verifier;
  return manifest;
}

/* 推导档徽标:tier→perm 色板槽位(升权卡片同一梯度,docs/SKILL-DEV.md §2.1) */
export function tierBadgeHtml(tier) {
  const t = ["none", "reversible", "irreversible"].includes(tier) ? tier : "none";
  return (
    `<span class="perm-badge" data-perm="${TIER_PERM[t]}" data-lab-tier="${esc(t)}">` +
    `<span class="perm-dot" aria-hidden="true"></span>${esc(t)}</span>`
  );
}

/* 悬停来源明细(§2.4"每处都有出处"):top 来源逐行;无则空串 */
export function tierTitle(detail) {
  return (detail?.top ?? []).map((s) => `${s.kind} ${s.name}: ${s.tier}`).join("\n");
}

/* chips 编辑器(§1.3 权限组):已选 chips + 候选 select + 添加按钮 */
function _chipsHtml(field, values, candidates, withPerm) {
  const chips = values
    .map(
      (v) =>
        `<span class="chip mono lab-chip" data-chip="${esc(v)}">${esc(v)}` +
        `<button class="lab-chip-x" data-chip-remove="${esc(field)}" data-value="${esc(v)}"` +
        ` aria-label="移除 ${esc(v)}">✕</button></span>`
    )
    .join("");
  const options = (candidates ?? [])
    .filter((c) => !values.includes(c.name ?? c))
    .map((c) => {
      const name = c.name ?? c;
      const badge = withPerm && c.perm
        ? ` [${c.perm}]` // 候选带 perm 标注(文本通道,徽标在 chips 区渲染)
        : "";
      return `<option value="${esc(name)}">${esc(name)}${badge}</option>`;
    })
    .join("");
  return (
    `<div class="lab-chips" data-chips="${esc(field)}">${chips}` +
    `<select class="input lab-chip-add" data-chip-candidate="${esc(field)}">` +
    `<option value="">+</option>${options}</select></div>`
  );
}

const _field = (key, label, inner, hint = "") =>
  `<label class="lab-field"><span class="lab-label">${esc(label)}</span>${inner}` +
  (hint ? `<span class="lab-hint">${esc(hint)}</span>` : "") +
  `</label>`;

const _text = (field, value, mono = false) =>
  `<input class="input${mono ? " mono" : ""}" data-field="${esc(field)}" value="${esc(value)}">`;

const _area = (field, value, rows) =>
  `<textarea class="input mono" rows="${rows}" data-field="${esc(field)}" spellcheck="false">` +
  `${esc(value)}</textarea>`;

const _select = (field, value, options) =>
  `<select class="input" data-field="${esc(field)}">` +
  options
    .map((o) => `<option value="${esc(o)}"${o === value ? " selected" : ""}>${esc(o || "(未设置)")}</option>`)
    .join("") +
  `</select>`;

/* 编辑器整树 HTML(§1.3 七组全字段;view = {form, tier, tierDetail, toolsCatalog, skillsCatalog}) */
export function editorHtml(view) {
  const f = view.form;
  const groups = [];
  // 身份组
  groups.push(
    `<section class="lab-group" data-group="identity"><h3>${esc(copy("lab.group.identity"))}</h3>` +
    _field("name", "name", _text("name", f.name, true), isValidDraftName(f.name) ? "" : copy("lab.name.invalid")) +
    _field("version", "version", _text("version", f.version, true)) +
    _field("kind", "kind", _select("kind", f.kind, ["prompt", "code"])) +
    _field("description", "description", _area("description", f.description, 2)) +
    `</section>`
  );
  // 契约组(inputs/outputs:JSON 文本域 + 实时合法性提示)
  groups.push(
    `<section class="lab-group" data-group="contract"><h3>${esc(copy("lab.group.contract"))}</h3>` +
    _field("inputs", "inputs (JSON Schema)", _area("inputsText", f.inputsText, 6)) +
    `<span class="lab-hint" data-json-hint="inputsText"></span>` +
    _field("outputs", "outputs (JSON Schema)", _area("outputsText", f.outputsText, 6)) +
    `<span class="lab-hint" data-json-hint="outputsText"></span>` +
    `</section>`
  );
  // 指令组
  groups.push(
    `<section class="lab-group" data-group="instruction"><h3>${esc(copy("lab.group.instruction"))}</h3>` +
    _field("prompt", "prompt", _area("prompt", f.prompt, 10)) +
    _field("handlerPath", "handler (dotted path)", _text("handlerPath", f.handlerPath, true)) +
    _field("handler", "handler.py", _area("handler", f.handler, 6)) +
    _field("entry", "entry", _text("entry", f.entry, true)) +
    _field("logicMode", "logic.mode", _select("logicMode", f.logicMode, ["", "trusted", "sandbox"])) +
    `</section>`
  );
  // 权限组(白名单 chips;候选带 perm 标注)
  groups.push(
    `<section class="lab-group" data-group="permissions"><h3>${esc(copy("lab.group.permissions"))}</h3>` +
    _field("tools", "permissions.tools", _chipsHtml("tools", f.tools, view.toolsCatalog, true)) +
    _field("skills", "permissions.skills", _chipsHtml("skills", f.skills, view.skillsCatalog, false)) +
    _field("blackboard", "permissions.blackboard", _chipsHtml("blackboard", f.blackboard, [], false)) +
    `</section>`
  );
  // 策略组
  groups.push(
    `<section class="lab-group" data-group="policy"><h3>${esc(copy("lab.group.policy"))}</h3>` +
    _field("modelPrefer", "model.prefer", _text("modelPrefer", f.modelPrefer, true)) +
    _field("modelTemperature", "model.temperature", _text("modelTemperature", f.modelTemperature)) +
    _field("cpMaxTokens", "context_policy.max_tokens", _text("cpMaxTokens", f.cpMaxTokens)) +
    _field("cpCompress", "context_policy.compress",
      _select("cpCompress", f.cpCompress, ["hierarchical", "off", "truncate", "spill", "summarize"])) +
    _field("limMaxSteps", "limits.max_steps", _text("limMaxSteps", f.limMaxSteps)) +
    _field("limTimeout", "limits.timeout", _text("limTimeout", f.limTimeout)) +
    _field("limRetry", "limits.retry", _text("limRetry", f.limRetry)) +
    _field("limMaxToolCalls", "limits.max_tool_calls", _text("limMaxToolCalls", f.limMaxToolCalls)) +
    `</section>`
  );
  // 信任组(推导档只读展示,随 permissions 刷新)
  groups.push(
    `<section class="lab-group" data-group="trust"><h3>${esc(copy("lab.group.trust"))}</h3>` +
    `<div class="lab-tier-row" title="${esc(tierTitle(view.tierDetail))}">` +
    `<span class="lab-label">推导档</span><span data-lab-tier-badge>${tierBadgeHtml(view.tier)}</span></div>` +
    _field("trustConfirm", "trust.confirm", _select("trustConfirm", f.trustConfirm, ["", "always", "first"])) +
    _field("trustReversal", "trust.reversal", _text("trustReversal", f.trustReversal)) +
    _field("trustBlastRadius", "trust.blast_radius", _text("trustBlastRadius", f.trustBlastRadius)) +
    `</section>`
  );
  // 行为组(inline ≥L2 硬闸门预警,docs/ESCALATION.md §3.4)
  const blocked = view.tier !== "none";
  groups.push(
    `<section class="lab-group" data-group="behavior"><h3>${esc(copy("lab.group.behavior"))}</h3>` +
    `<label class="lab-field lab-inline-row">` +
    `<input type="checkbox" data-field="inline"${f.inline && !blocked ? " checked" : ""}${blocked ? " disabled" : ""}>` +
    `<span class="lab-label">inline</span>` +
    (blocked ? `<span class="lab-hint">${esc(copy("lab.inline.blocked"))}</span>` : "") +
    `</label>` +
    _field("verifier", "verifier", _text("verifier", f.verifier, true)) +
    `</section>`
  );
  return groups.join("");
}

/* 顶部条 HTML:草稿选择 / 新建(空 / 从生产复制)/ 删除(两击确认)/ tier 徽标 / 检查·提交 / 保存 */
export function topbarHtml(view) {
  const rows = view.drafts ?? [];
  const options = rows
    .map(
      (r) =>
        `<option value="${esc(r.name)}"${r.name === view.name ? " selected" : ""}>` +
        `${esc(r.name)}</option>`
    )
    .join("");
  const prodOptions = (view.skillsCatalog ?? [])
    .map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`)
    .join("");
  // 模板库(docs/SKILL-DEV.md §4 L5;key 与后端 DRAFT_TEMPLATES 一一对应)
  const tplOptions = ["prompt_query", "file_process", "danger_op"]
    .map((k) => `<option value="tpl:${k}">${esc(copy(`lab.tpl.${k}`))}</option>`)
    .join("");
  return (
    `<div class="lab-top">` +
    `<select class="input lab-select" data-lab="select">${options}</select>` +
    `<input class="input mono lab-new-name" data-lab="new-name" placeholder="domain.action"` +
    ` aria-label="${esc(copy("lab.new"))}">` +
    `<select class="input lab-new-from" data-lab="new-from">` +
    `<option value="">${esc(copy("lab.new.empty"))}</option>${tplOptions}${prodOptions}</select>` +
    `<button class="btn" data-lab="create">${esc(copy("lab.new"))}</button>` +
    `<button class="btn" data-lab="delete">${esc(copy("lab.delete"))}</button>` +
    `<span class="lab-top-tier" data-lab-tier-badge title="${esc(tierTitle(view.tierDetail))}">` +
    `${tierBadgeHtml(view.tier)}</span>` +
    `<span class="lab-top-actions">` +
    `<button class="btn" data-lab="check">${esc(copy("lab.check"))}</button>` +
    `<button class="btn" data-lab="promote"${promoteReady(view) ? "" : " disabled"}` +
    ` title="${esc(promoteDisabledReason(view))}">` +
    `${esc(copy("lab.promote"))}</button>` +
    `<button class="btn btn-primary" data-lab="save"${view.dirty ? ` data-dirty title="${esc(copy("lab.dirty"))}"` : ""}>` +
    `${esc(copy("lab.save"))}</button>` +
    `</span></div>`
  );
}

/* 报告过期判定(§2.3):保存时间晚于报告时间 → 旧报告作废(服务端另有哈希校验兜底) */
export function isReportStale(view) {
  return Boolean(
    view?.report && view.savedAt && view.savedAt > view.report.created_at * 1000
  );
}

/* plan 过期判定(docs/SKILL-PACKAGES-V2.md §6.3):保存晚于 plan 生成 → 旧 plan 作废
   (服务端另有 package_hash 校验兜底) */
export function isPlanStale(view) {
  return Boolean(view?.plan && view.savedAt && view.savedAt > view.plan.created_at * 1000);
}

/* 提交按钮点亮条件(§1.4/§6.3):plan 在、无 blockers、不过期、warnings 已确认;
   无 plan 时回落旧单稿报告路径 */
export function promoteReady(view) {
  if (view?.dirty) return false; // F6:有未保存改动时禁提交(防把旧版本发进生产)
  if (view?.plan) {
    if (isPlanStale(view)) return false;
    if ((view.plan.blockers ?? []).length) return false;
    if ((view.plan.warnings ?? []).length && !view.ackWarn) return false;
    return true;
  }
  if (!view?.report || isReportStale(view)) return false;
  if (view.report.status === "fail") return false;
  if (view.report.status === "warn" && !view.ackWarn) return false;
  return true;
}

/* 置灰理由(UX 评审 P0-3:disabled 必须说明解锁条件,hover tooltip 展示) */
export function promoteDisabledReason(view) {
  if (promoteReady(view)) return "";
  if (view?.dirty) return copy("lab.promote.disabled.dirty"); // 未保存优先报(F6)
  if (view?.plan) {
    if (isPlanStale(view)) return copy("lab.promote.disabled.stale");
    if ((view.plan.blockers ?? []).length) return copy("lab.plan.blockers");
    if ((view.plan.warnings ?? []).length && !view.ackWarn) return copy("lab.promote.disabled.ack");
  }
  if (!view?.report && !view?.plan) return copy("lab.promote.disabled.noreport");
  if (view?.report && isReportStale(view)) return copy("lab.promote.disabled.stale");
  if (view?.report?.status === "fail") return copy("lab.promote.disabled.fail");
  if (view?.report?.status === "warn" && !view.ackWarn) return copy("lab.promote.disabled.ack");
  return "";
}

/* 提交计划面板(docs/SKILL-PACKAGES-V2.md §6.3/§6.8;P2):成员表(action 三态 +
   from→to 版本 + gate_status 色点)+ blockers(带 fix 按钮)+ warnings(ack 勾选)。
   展示的成员集与将要写入的内容由 package_hash 绑定(审的就是要执行的)。 */
export function planPanelHtml(view) {
  const plan = view.plan;
  if (!plan) return "";
  const memberRows = (plan.members ?? [])
    .map((m) => {
      const version = m.action === "create"
        ? `<span class="mono">${esc(m.to_version ?? "")}</span>`
        : `<span class="mono">${esc(m.from_version ?? "—")} → ${esc(m.to_version ?? "")}</span>`;
      return (
        `<div class="lab-plan-row" data-action="${esc(m.action)}">` +
        `<span class="lab-gate-status" data-status="${esc(m.gate_status)}">${esc(copy(`lab.gate.${m.gate_status}`))}</span>` +
        `<span class="mono">${esc(m.name)}</span>` +
        `<span class="lab-plan-action mono">${esc(m.action)}</span>${version}` +
        `</div>`
      );
    })
    .join("");
  const blockers = (plan.blockers ?? [])
    .map(
      (b) =>
        `<div class="lab-plan-blocker">⚠ <b>${esc(b.kind)}</b> ${esc(b.member ?? "")} ${esc(b.message ?? "")}` +
        (b.fix?.name
          ? `<button class="lab-pkg-act" data-pkg-create="${esc(b.fix.name)}">${esc(copy("lab.pkg.create"))}</button>`
          : "") +
        `</div>`
    )
    .join("");
  const warnings = (plan.warnings ?? [])
    .map((w) => `<div class="lab-plan-warning">⚠ ${esc(w)}</div>`)
    .join("");
  const ackRow = (plan.warnings ?? []).length
    ? `<label class="lab-ack"><input type="checkbox" data-lab-ack="1"${view.ackWarn ? " checked" : ""}>` +
      `<span>${esc(copy("lab.gate.ack"))}</span></label>`
    : "";
  return (
    `<div class="lab-plan">` +
    `<div class="lab-plan-head"><span class="lab-gate-title">${esc(copy("lab.plan.title"))}</span>` +
    `<span class="mono lab-plan-hash">${esc(String(plan.package_hash ?? "").slice(0, 8))}</span></div>` +
    memberRows +
    (blockers
      ? `<div class="lab-plan-blockers"><div class="lab-gate-title">${esc(copy("lab.plan.blockers"))}</div>${blockers}</div>`
      : "") +
    warnings +
    ackRow +
    `</div>`
  );
}

/* 五关卡片(§1.4/§2.1):绿 pass / 黄 warn / 红 fail / 灰 skip;findings 可展开,
   每条带条款号(clause,链 docs/TIER-STANDARDS.md 等)。 */
export function gateCardsHtml(report) {
  if (!report) return "";
  const order = ["g1", "g2", "g3", "g4", "g5"];
  const cards = order
    .map((gid) => {
      const gate = report.gates?.[gid] ?? { status: "skip", findings: [] };
      const status = gate.status ?? "skip";
      const title = copy(`lab.gate.${gid}`);
      const findings = (gate.findings ?? [])
        .map(
          (f) =>
            `<li class="lab-finding" data-level="${esc(f.level)}">` +
            `<span class="lab-clause mono">${esc(f.clause ?? "")}</span> ${esc(f.message ?? "")}</li>`
        )
        .join("");
      const body = gate.note
        ? `<div class="lab-gate-note">${esc(gate.note)}</div>`
        : findings
          ? `<details class="lab-gate-details"><summary>${(gate.findings ?? []).length} 项</summary>` +
            `<ul class="lab-findings">${findings}</ul></details>`
          : "";
      return (
        `<div class="lab-gate-card" data-status="${esc(status)}">` +
        `<div class="lab-gate-head"><span class="lab-gate-title">${esc(title)}</span>` +
        `<span class="lab-gate-status" data-status="${esc(status)}">${esc(copy(`lab.gate.${status}`))}</span></div>` +
        body +
        `</div>`
      );
    })
    .join("");
  return `<div class="lab-gate">${cards}</div>`;
}

/* 状态栏文本(§2.1):已保存 HH:MM · 草稿(未提交)[· 距上次检查有改动 ⚠] */
export function statusLine(view) {
  const parts = [];
  if (view.savedAt) {
    const d = new Date(view.savedAt);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    parts.push(`${copy("lab.saved")} ${hh}:${mm}`);
  }
  parts.push(copy("lab.uncommitted"));
  if (isReportStale(view) || isPlanStale(view)) parts.push(copy("lab.status.stale"));
  return parts.join(" · ");
}

/* 测试面板(§2.1 右栏;L3):输入 JSON + 用例下拉 + ▶ 试跑 + 结果/outputs 校验 + trace 区 */
export function testPanelHtml(view) {
  const cases = view.draftTests ?? [];
  const caseOpts = [`<option value="">${esc(copy("lab.test.case"))}</option>`]
    .concat(
      cases.map(
        (c) =>
          `<option value="${esc(c)}"${c === view.testCase ? " selected" : ""}>${esc(c)}</option>`
      )
    )
    .join("");
  const running = view.testRun?.status === "running";
  return (
    `<div class="lab-test-panel">` +
    `<textarea class="input mono" rows="4" data-lab-input spellcheck="false"` +
    ` placeholder='{"city": "北京"}'>${esc(view.testInput ?? "")}</textarea>` +
    `<span class="lab-hint" data-json-hint="testInput"></span>` +
    `<div class="lab-test-bar">` +
    `<select class="input" data-lab-case>${caseOpts}</select>` +
    `<button class="btn btn-primary" data-lab="test-run"${running ? " disabled" : ""}>` +
    `${esc(copy("lab.test.run"))}</button>` +
    `</div>` +
    `<div class="lab-test-result" data-lab-result>${testResultHtml(view.testRun)}</div>` +
    `<div class="lab-test-trace" data-lab-trace>${view.traceHtml ?? ""}</div>` +
    `</div>`
  );
}

/* 试跑结果区:status / result 摘要 / outputs 校验(绿 ✓ / 红 ✗ + 失败原因) */
export function testResultHtml(run) {
  if (!run) return "";
  if (run.status === "running") {
    return `<div class="lab-hint">${esc(copy("lab.test.running"))}</div>`;
  }
  const check = run.check ?? {};
  const outputs =
    check.ok === null || check.ok === undefined
      ? ""
      : `<div class="lab-outputs" data-ok="${check.ok}">${esc(copy("lab.test.outputs"))}: ` +
        (check.ok ? "✓" : `✗ ${esc(check.error ?? "")}`) +
        `</div>`;
  return (
    `<div class="lab-test-summary" data-status="${esc(run.status ?? "")}">` +
    `<span class="mono">${esc(run.status ?? "")}</span>` +
    (run.error ? `<div class="lab-hint">${esc(run.error)}</div>` : "") +
    (run.result !== null && run.result !== undefined
      ? `<pre class="mono">${esc(JSON.stringify(run.result, null, 2))}</pre>`
      : "") +
    outputs +
    `</div>`
  );
}

/* Agent 助手 chat(§2.2;L4):消息气泡(用户/助手/系统提示)+ 大多行输入框 + 发送 */
export function chatHtml(view) {
  const msgs = (view.chat ?? [])
    .map(
      (m) =>
        `<div class="lab-msg" data-role="${esc(m.role)}">` +
        `<div class="lab-msg-body">${esc(m.text)}</div></div>`
    )
    .join("");
  const log =
    msgs ||
    `<div class="lab-hint">${esc(copy("lab.agent.empty"))}</div>`;
  const busy = view.chatBusy ? `<div class="lab-hint">${esc(copy("lab.chat.thinking"))}</div>` : "";
  return (
    `<div class="lab-chat">` +
    `<div class="lab-chat-log" data-lab-chat-log>${log}${busy}</div>` +
    `<textarea class="input" rows="4" data-lab-chat-input ` +
    `placeholder="${esc(copy("lab.chat.placeholder"))}">${esc(view.chatInput ?? "")}</textarea>` +
    `<button class="btn btn-primary" data-lab="chat-send"${view.chatBusy ? " disabled" : ""}>` +
    `${esc(copy("lab.chat.send"))}</button>` +
    `</div>`
  );
}

/* manifest 顶层字段 diff(agent 改稿高亮用,§2.2"agent 改了 permissions.tools:+fs.write"行) */
export function diffGroups(oldManifest, newManifest) {
  const keys = new Set([
    ...Object.keys(oldManifest ?? {}),
    ...Object.keys(newManifest ?? {}),
  ]);
  return [...keys]
    .filter((k) => k !== "name") // 目录名钉死,不算改动
    .filter(
      (k) =>
        JSON.stringify(oldManifest?.[k] ?? null) !== JSON.stringify(newManifest?.[k] ?? null)
    )
    .sort();
}

/* 包面板(docs/SKILL-PACKAGES.md §3.1;P1):标题行(包名 + 根推导档)+ 成员行
   (缩进 = 闭包深度;末段名 + tier 徽标 + 状态徽标;根高亮)。
   状态四态:draft=草稿(可点进编辑器)/ production=同空间生产 / external=外链(跳 Skills 页)/
   missing=悬空(行内"创建该草稿",断点 1.1-B 的前端面)。 */
export function packagePanelHtml(view) {
  const pkg = view.pkg;
  if (!view.form || !pkg) return "";
  const rows = (pkg.members ?? [])
    .map((m) => {
      const lastSeg = m.name.split(".").pop();
      const isRoot = m.name === pkg.root;
      const tier = m.tier ? tierBadgeHtml(m.tier) : `<span class="lab-pkg-tier-none">—</span>`;
      const status = `<span class="lab-pkg-status" data-status="${esc(m.status)}">` +
        `${esc(copy(`lab.pkg.${m.status}`))}</span>`;
      let action = "";
      if (m.status === "draft" && !isRoot) {
        action = `<button class="lab-pkg-act" data-pkg-select="${esc(m.name)}">${esc(lastSeg)}</button>`;
      } else if (m.status === "missing") {
        action =
          `<button class="lab-pkg-act" data-pkg-create="${esc(m.name)}">` +
          `${esc(copy("lab.pkg.create"))}</button>`;
      } else {
        action =
          `<a class="lab-pkg-link mono" href="#/skills/${encodeURIComponent(m.name)}">${esc(lastSeg)}</a>`;
      }
      const refBy = m.ref_by ? `<span class="lab-pkg-refby mono">← ${esc(m.ref_by)}</span>` : "";
      return (
        `<div class="lab-pkg-row" style="--ns-depth:${m.depth ?? 0}" data-root="${isRoot}">` +
        `${tier}${action}${status}${refBy}</div>`
      );
    })
    .join("");
  const errors = (pkg.errors ?? [])
    .map((e) => `<div class="lab-pkg-error">⚠ ${esc(e.message ?? "")}</div>`)
    .join("");
  return (
    `<div class="lab-pkg">` +
    `<div class="lab-pkg-head"><span class="lab-pkg-title">${esc(copy("lab.pkg.title"))}: ` +
    `${esc(pkg.root)}</span>${tierBadgeHtml(pkg.root_tier ?? "none")}</div>` +
    rows +
    errors +
    `</div>`
  );
}

/* ── 页面(DOM)─────────────────────────────────────────────── */

let lab = null; // 当前页面状态;null = 未打开

const isLabOpen = () => lab !== null;
export { isLabOpen };

/* 保存流程(fetch 层在 api.js;测试 stub 全局 fetch 即可驱动):
   JSON 不合法时拦在客户端并提示(不打扰创作流≠把坏 JSON 写进草稿)。 */
export async function saveCurrentDraft() {
  if (!lab?.form) return null;
  for (const field of ["inputsText", "outputsText"]) {
    const err = jsonError(lab.form[field]);
    if (err) {
      toast(`${copy("lab.json.invalid")}: ${err}`, "error");
      return null;
    }
  }
  const manifest = formToManifest(lab.form);
  const saved = await putJson(
    `/api/lab/drafts/${encodeURIComponent(lab.form.name)}`,
    { manifest, prompt: lab.form.prompt, handler: lab.form.handler || null }
  );
  lab.savedAt = Date.now();
  lab.dirty = false; // 保存即清脏(圆点熄灭,_renderTop 随下方调用重绘)
  toast(copy("lab.saved"), "success");
  _renderStatus(); // 保存晚于上次检查 → "距上次检查有改动 ⚠"(§2.3)
  _renderTop(); // 报告过期 → 提交按钮立即熄灭(服务端哈希校验兜底)
  return saved;
}

/* 检查(§1.4):跑闸门 → 五关卡片进右栏;warn 时需勾选"我已阅读警告"才亮提交 */
export async function runCheck() {
  if (!lab?.form) return null;
  // P2(docs/SKILL-PACKAGES-V2.md §6.3):检查 = 生成提交计划(成员闸门 + blockers +
  // package_hash 绑定);单稿是包大小为 1 的退化,同一条路径
  const plan = await postJson(
    `/api/lab/packages/${encodeURIComponent(lab.form.name)}/plan`
  );
  lab.plan = plan;
  lab.report = null;
  lab.ackWarn = false;
  lab.confirming = false;
  _renderGate();
  _renderTop();
  _renderStatus();
  return plan;
}

function _renderGate() {
  const host = lab.root?.querySelector(".lab-gate-host");
  if (!host) return;
  if (lab.plan) {
    host.innerHTML = planPanelHtml(lab);
    return;
  }
  if (!lab.report) {
    host.innerHTML = emptyBlock(copy("lab.test.empty"), "", "inbox");
    return;
  }
  const ackRow =
    lab.report.status === "warn"
      ? `<label class="lab-ack"><input type="checkbox" data-lab-ack="1"${lab.ackWarn ? " checked" : ""}>` +
        `<span>${esc(copy("lab.gate.ack"))}</span></label>`
      : "";
  host.innerHTML = gateCardsHtml(lab.report) + ackRow;
}

/* 右栏整体(§2.1;L3):测试面板 + 闸门报告区 */
function _renderTestPanel() {
  const host = lab.root?.querySelector(".lab-test");
  if (!host) return;
  host.innerHTML = testPanelHtml(lab) + `<div class="lab-gate-host"></div>`;
  _renderGate();
}

/* 试跑(§1.5/§2.3 流程 3;L3):POST test-run → 轮询 check → 结果 + trace 渲染。
   轮询而非 SSE 增量(实现注:面板是低频人工动作,SSE 增量渲染留 L5 打磨);
   trace 复用调试台 deriveTraceView/renderTrace,不新写一套。 */
export async function startTestRun() {
  if (!lab?.form) return null;
  const err = jsonError(lab.testInput);
  if (err) {
    toast(`${copy("lab.json.invalid")}: ${err}`, "error");
    return null;
  }
  const name = lab.form.name;
  lab.testRun = { status: "running" };
  lab.traceHtml = "";
  _renderTestPanel();
  const body = lab.testCase
    ? { case: lab.testCase }
    : { input: JSON.parse(lab.testInput || "{}") };
  const started = await postJson(
    `/api/lab/drafts/${encodeURIComponent(name)}/test-run`,
    body
  );
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 500));
    const check = await getJson(
      `/api/lab/drafts/${encodeURIComponent(name)}/runs/${started.run_id}/check`
    );
    if (check.status === "running") continue;
    const signals = await getJson(`/api/runs/${started.run_id}/signals`).catch(() => []);
    lab.traceHtml = renderTrace(deriveTraceView(Array.isArray(signals) ? signals : [], null, {}));
    lab.testRun = {
      runId: started.run_id,
      status: check.status,
      result: check.result,
      error: check.error,
      check: check.outputs_check,
    };
    _renderTestPanel();
    return lab.testRun;
  }
  lab.testRun = { runId: started.run_id, status: "timeout" };
  _renderTestPanel();
  return lab.testRun;
}

/* 中栏 chat(§2.2;L4):发消息 = assistant run;回复进记录区;改稿后编辑器刷新 + diff 行 */
function _renderChat() {
  const host = lab.root?.querySelector(".lab-agent");
  if (host) host.innerHTML = chatHtml(lab);
}

export async function sendChat(text = null) {
  if (!lab?.form || lab.chatBusy) return null;
  const request = String(text ?? lab.chatInput ?? "").trim();
  if (!request) return null;
  lab.chat.push({ role: "user", text: request });
  lab.chatInput = "";
  lab.chatBusy = true;
  _renderChat();
  try {
    const started = await postJson("/api/lab/assistant", {
      request,
      draft: lab.form.name,
    });
    const reply = await _pollAssistant(started.run_id);
    lab.chat.push({ role: "assistant", text: reply });
    await _refreshAfterAgent();
    return reply;
  } catch (e) {
    lab.chat.push({ role: "assistant", text: `${copy("lab.chat.failed")}: ${e.message ?? e}` });
    return null;
  } finally {
    lab.chatBusy = false;
    _renderChat();
  }
}

async function _pollAssistant(runId) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 500));
    const detail = await getJson(`/api/runs/${runId}`);
    if (detail.status === "done") {
      return detail.result?.reply ?? JSON.stringify(detail.result);
    }
    if (detail.status !== "running") {
      throw new Error(detail.error ?? detail.status);
    }
  }
  throw new Error("timeout");
}

/* 助手改稿后(§2.2):重读草稿,diff 顶层字段给一行高亮提示,编辑器以服务端为准刷新 */
async function _refreshAfterAgent() {
  const draft = await getJson(`/api/lab/drafts/${encodeURIComponent(lab.form.name)}`);
  let changed = [];
  try {
    changed = diffGroups(formToManifest(lab.form), draft.manifest ?? {});
  } catch {
    changed = []; // 编辑器里 JSON 暂时不合法时跳过 diff(闸门会管)
  }
  if (changed.length) {
    lab.chat.push({ role: "system", text: `${copy("lab.chat.updated")}${changed.join("、")}` });
  }
  lab.form = draftToForm(draft);
  lab.draftTests = Object.keys(draft.tests ?? {});
  // P1:助手可能改了白名单(引用面变了)→ 包闭包同步刷新
  lab.pkg = await getJson(`/api/lab/drafts/${encodeURIComponent(lab.form.name)}/closure`)
    .catch(() => lab.pkg);
  _renderEditor();
  _renderPkg();
  _renderTestPanel();
  await refreshTier();
}

/* 提交确认(§2.3 流程 5):内联确认行(version 可改,留空自动 bump)+ 确认/取消 */
function _promoteConfirmHtml() {
  return (
    `<div class="lab-promote-confirm">` +
    `<input class="input mono" data-lab="promote-version" placeholder="${esc(copy("lab.promote.version"))}">` +
    `<button class="btn btn-primary" data-lab="promote-confirm">${esc(copy("lab.promote.confirm"))}</button>` +
    `<button class="btn" data-lab="promote-cancel">${esc(copy("lab.cancel"))}</button>` +
    `</div>`
  );
}

async function _doPromote() {
  const versionEl = lab.root.querySelector('[data-lab="promote-version"]');
  return confirmPromote({ version: versionEl?.value?.trim() || null, warningsAck: lab.ackWarn });
}

/* promote 提交(§2.3 流程 5;导出供测试):成功消费报告并提示;被拒(409 等)上抛 */
export async function confirmPromote({ version = null, warningsAck = false } = {}) {
  if (!lab?.form || !lab?.report) return null;
  const result = await postJson(
    `/api/lab/drafts/${encodeURIComponent(lab.form.name)}/promote`,
    { report_id: lab.report.report_id, version, warnings_ack: warningsAck }
  );
  lab.report = null; // 已进生产:旧报告消费掉,下一次迭代重新检查
  lab.ackWarn = false;
  lab.confirming = false;
  lab.promoted = { name: result.name, version: result.version }; // F15:状态条给"去 Skills 查看"出路
  _renderGate();
  _renderTop();
  _renderStatus();
  toast(`${copy("lab.promote.done")}: ${result.name}@${result.version}`, "success");
  return result;
}

/* 包级提交(docs/SKILL-PACKAGES-V2.md §6.3;P2):按 plan_id 原子提交,
   成功复用 F15 promoted 出路(状态条"去 Skills 查看") */
async function _doPackagePromote() {
  const result = await postJson("/api/lab/packages/promote", {
    plan_id: lab.plan.plan_id,
    warnings_ack: lab.ackWarn,
  });
  lab.plan = null; // 已进生产:旧 plan 消费掉,下一次迭代重新生成
  lab.ackWarn = false;
  const members = result.members ?? [];
  lab.promoted = {
    name: result.root,
    version: members.length > 1 ? `${members.length} 成员` : (members[0]?.to_version ?? ""),
  };
  _renderGate();
  _renderTop();
  _renderStatus();
  toast(`${copy("lab.promote.done")}: ${result.root}`, "success");
  return result;
}

/* 推导档实时刷新:?tools=&skills= 用未保存白名单覆盖(§1.3;不必先保存) */
export async function refreshTier() {
  if (!lab?.form) return null;
  const q = new URLSearchParams({
    tools: lab.form.tools.join(","),
    skills: lab.form.skills.join(","),
  });
  const detail = await getJson(
    `/api/lab/drafts/${encodeURIComponent(lab.form.name)}/tier?${q}`
  );
  lab.tier = detail.tier ?? "none";
  lab.tierDetail = detail;
  for (const el of lab.root?.querySelectorAll("[data-lab-tier-badge]") ?? []) {
    el.innerHTML = tierBadgeHtml(lab.tier);
    el.title = tierTitle(detail);
  }
  // inline 硬闸门预警(≥L2 禁用,docs/ESCALATION.md §3.4)
  const inlineBox = lab.root?.querySelector('[data-field="inline"]');
  if (inlineBox) inlineBox.disabled = lab.tier !== "none";
  return detail;
}

function _renderStatus() {
  const el = lab?.root?.querySelector(".lab-status");
  if (!el) return;
  // F15:发布成功给出路(状态条带"去 Skills 查看"链接,不在最激动时刻戛然而止)
  if (lab.promoted) {
    const p = lab.promoted;
    el.innerHTML =
      `${esc(copy("lab.promote.done"))}: ${esc(p.name)}@${esc(p.version)} · ` +
      `<a href="#/skills/${encodeURIComponent(p.name)}">${esc(copy("lab.promote.goto"))}</a>`;
    return;
  }
  el.textContent = statusLine(lab);
}

async function _loadCatalogs() {
  const [tools, skills] = await Promise.all([
    getJson("/api/tools").catch(() => []),
    getJson("/api/skills").catch(() => []),
  ]);
  lab.toolsCatalog = (Array.isArray(tools) ? tools : []).map((t) => ({
    name: t.name,
    perm: t.permission ?? "",
  }));
  lab.skillsCatalog = (Array.isArray(skills) ? skills : []).map((s) => ({ name: s.name }));
}

async function _loadDrafts() {
  lab.drafts = await getJson("/api/lab/drafts").catch(() => []);
}

async function _selectDraft(name) {
  // F5:有未保存改动时切草稿前确认(与 beforeunload 同源的数据保护)
  if (
    lab?.dirty &&
    lab.form?.name !== name &&
    typeof globalThis.confirm === "function" &&
    !globalThis.confirm(copy("lab.dirty.leave"))
  ) {
    return;
  }
  const draft = await getJson(`/api/lab/drafts/${encodeURIComponent(name)}`);
  lab.form = draftToForm(draft);
  lab.parseError = draft.parse_error ?? null;
  lab.savedAt = null;
  lab.dirty = false; // 换草稿:脏标不跨草稿
  lab.report = null; // 换草稿:旧报告不属于新对象
  lab.plan = null; // 换草稿:旧提交计划不属于新对象(P2)
  lab.promoted = null; // 换草稿:发布成功横幅不跨草稿
  lab.ackWarn = false;
  lab.confirming = false;
  lab.draftTests = Object.keys(draft.tests ?? {}); // 用例下拉数据源(tests/*.json)
  lab.testCase = null;
  lab.testRun = null;
  lab.traceHtml = "";
  // P1 包闭包(docs/SKILL-PACKAGES.md §3.1):根不存在/解析失败 → 面板空态
  lab.pkg = await getJson(`/api/lab/drafts/${encodeURIComponent(name)}/closure`)
    .catch(() => null);
  // F11:试跑输入预填由当前草稿 inputs schema 生成(默认必成功的合法样例,
  // 不再用与 schema 无关的静态占位)
  lab.testInput = JSON.stringify(skeletonFromSchema(draft.manifest?.inputs ?? null), null, 2);
  _renderEditor();
  _renderPkg();
  _renderTestPanel();
  await refreshTier();
  _renderStatus();
}

/* 包面板渲染(docs/SKILL-PACKAGES.md §3.1;P1):编辑器上方的包视图 */
function _renderPkg() {
  const host = lab.root?.querySelector(".lab-pkg");
  if (host) host.innerHTML = packagePanelHtml(lab);
}

function _renderEditor() {
  const host = lab.root?.querySelector(".lab-editor");
  if (!host) return;
  if (!lab.form) {
    // 空状态 CTA 化(UX 评审 P1-6):纯文本引导改为可点动作——聚焦命名框开始新建
    host.innerHTML =
      emptyBlock(copy("lab.no.selection"), copy("lab.no.selection.hint"), "inbox") +
      `<button class="btn btn-primary lab-empty-cta" data-lab="focus-new">${esc(copy("lab.no.selection.cta"))}</button>`;
    return;
  }
  host.innerHTML = editorHtml(lab);
}

function _renderTop() {
  const host = lab.root?.querySelector(".lab-top-host");
  if (host) host.innerHTML = topbarHtml(lab) + (lab.confirming ? _promoteConfirmHtml() : "");
}

export async function createDraft() {
  // 先经 .lab-top-host 再取输入(真实 DOM 嵌套结构与测试区域提取都可达)
  const topHost = lab.root.querySelector(".lab-top-host");
  const nameEl = topHost?.querySelector(".lab-new-name") ?? lab.root.querySelector(".lab-new-name");
  const fromEl = topHost?.querySelector(".lab-new-from") ?? lab.root.querySelector(".lab-new-from");
  const name = nameEl?.value?.trim() ?? "";
  if (!isValidDraftName(name)) {
    // 空名/非法名:toast 是瞬态的,可能错过(WebBridge 排障报告缺陷 #1)——
    // 同时把输入框标红并聚焦,让反馈驻留到用户修正为止(aria-invalid 同步,UX 复测遗留①)
    toast(copy("lab.name.invalid"), "error");
    nameEl?.classList.add("is-invalid");
    nameEl?.setAttribute("aria-invalid", "true");
    nameEl?.focus();
    nameEl?.addEventListener("input", () => {
      nameEl.classList.remove("is-invalid");
      nameEl.removeAttribute("aria-invalid");
    }, { once: true });
    return;
  }
  const from = fromEl?.value ?? "";
  const body = from.startsWith("tpl:")
    ? { name, template: from.slice(4) } // 模板库(docs/SKILL-DEV.md §4 L5)
    : { name, from_skill: from || null };
  await postJson("/api/lab/drafts", body);
  await _loadDrafts();
  _renderTop();
  await _selectDraft(name);
}

async function _deleteDraft(btn) {
  // 两击确认(§1.5 删除是 L2 语义):第一次点击进入确认态,第二次执行
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1";
    btn.textContent = copy("lab.delete.confirm");
    return;
  }
  await deleteJson(`/api/lab/drafts/${encodeURIComponent(lab.form.name)}`);
  lab.form = null;
  await _loadDrafts();
  _renderTop();
  _renderEditor();
  if (lab.drafts.length) await _selectDraft(lab.drafts[0].name);
}

function _bindEvents() {
  lab.root.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-lab]");
    const action = btn?.dataset.lab;
    try {
      if (action === "create") return await createDraft();
      if (action === "focus-new") {
        // 空态 CTA:聚焦命名框(引导从空状态直达新建动作)
        const topHost = lab.root.querySelector(".lab-top-host");
        const nameEl = topHost?.querySelector(".lab-new-name") ?? lab.root.querySelector(".lab-new-name");
        nameEl?.focus();
        return;
      }
      if (action === "delete") return await _deleteDraft(btn);
      if (action === "save") return await saveCurrentDraft();
      if (action === "check") return await runCheck();
      if (action === "test-run") return await startTestRun();
      if (action === "chat-send") return await sendChat();
      if (action === "promote") {
        if (!promoteReady(lab)) return; // 未点亮不响应(与 disabled 双保险)
        if (lab.plan) return await _doPackagePromote(); // P2:plan 即确认面,直接提交
        lab.confirming = true;
        return _renderTop();
      }
      if (action === "promote-confirm") return await _doPromote();
      if (action === "promote-cancel") {
        lab.confirming = false;
        return _renderTop();
      }
    } catch (err) {
      toast(err.message ?? String(err), "error");
    }
    // 包面板(docs/SKILL-PACKAGES.md §3.1;P1):草稿节点进编辑器;悬空一键成稿
    const pkgSelect = e.target.closest?.("[data-pkg-select]");
    if (pkgSelect) return _selectDraft(pkgSelect.dataset.pkgSelect);
    const pkgCreate = e.target.closest?.("[data-pkg-create]");
    if (pkgCreate) {
      try {
        const name = pkgCreate.dataset.pkgCreate;
        await postJson("/api/lab/drafts", { name });
        await _loadDrafts();
        _renderTop();
        await _selectDraft(name);
      } catch (err) {
        toast(err.message ?? String(err), "error");
      }
    }
    const remove = e.target.closest("[data-chip-remove]");
    if (remove) {
      const field = remove.dataset.chipRemove;
      lab.form[field] = lab.form[field].filter((v) => v !== remove.dataset.value);
      _renderEditor();
      if (field === "tools" || field === "skills") await refreshTier();
    }
  });
  lab.root.addEventListener("change", async (e) => {
    if (e.target.closest("[data-lab-case]")) {
      lab.testCase = e.target.value || null; // 选了用例 → 随 case 跑(mock_script 可带)
      return;
    }
    if (e.target.closest("[data-lab-ack]")) {
      lab.ackWarn = Boolean(e.target.checked);
      return _renderTop(); // warn 勾选门:提交按钮随勾选亮灭(§1.4)
    }
    const sel = e.target.closest("[data-lab='select']");
    if (sel?.value) return _selectDraft(sel.value);
    const cand = e.target.closest("[data-chip-candidate]");
    if (cand?.value) {
      const field = cand.dataset.chipCandidate;
      lab.form[field] = [...lab.form[field], cand.value];
      _renderEditor();
      if (field === "tools" || field === "skills") await refreshTier();
    }
    const field = e.target.closest("[data-field]")?.dataset.field;
    if (field && lab.form) {
      const el = e.target;
      lab.form[field] = el.type === "checkbox" ? el.checked : el.value;
      if (field === "kind") _renderEditor(); // code/prompt 形态差异(handler 区)
    }
  });
  lab.root.addEventListener("input", (e) => {
    if (e.target.closest("[data-lab-chat-input]")) {
      lab.chatInput = e.target.value;
      return;
    }
    if (e.target.closest("[data-lab-input]")) {
      lab.testInput = e.target.value;
      const hint = lab.root.querySelector('[data-json-hint="testInput"]');
      if (hint) {
        const err = jsonError(e.target.value);
        hint.textContent = err ? `${copy("lab.json.invalid")}: ${err}` : "";
      }
      return;
    }
    const field = e.target.closest("[data-field]")?.dataset.field;
    if (!field || !lab.form) return;
    lab.form[field] = e.target.value;
    if (!lab.dirty) { // 脏状态强化(UX 评审 P1-7):保存按钮圆点指示,首次变脏才重绘
      lab.dirty = true;
      _renderTop();
    }
    if (field === "inputsText" || field === "outputsText") {
      const hint = lab.root.querySelector(`[data-json-hint="${field}"]`);
      if (hint) {
        const err = jsonError(e.target.value);
        hint.textContent = err ? `${copy("lab.json.invalid")}: ${err}` : "";
      }
    }
    if (field === "name") lab.form.name = e.target.value;
  });
}

export function openLab(main, name = null) {
  if (lab) {
    closeLab();
  }
  // F2(UX 流程评审):进场前清空主区——Workbench 等上一页的 DOM(如 Usage
  // 折叠栏)残留在 main 里会浮在 Lab 上方,closeWorkbench 只收状态不收 DOM
  main.innerHTML = "";
  // F5:未保存改动离开页面时的浏览器级拦截(beforeunload;closeLab 时摘除)
  _beforeUnload ??= (e) => {
    if (lab?.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  };
  globalThis.addEventListener?.("beforeunload", _beforeUnload);
  lab = {
    form: null,
    drafts: [],
    toolsCatalog: [],
    skillsCatalog: [],
    tier: "none",
    tierDetail: null,
    savedAt: null,
    dirty: false, // 有未保存修改(保存按钮圆点指示)
    parseError: null,
    report: null, // 最近一次闸门报告(§1.4;保存后过期)
    plan: null, // 最近一次提交计划(docs/SKILL-PACKAGES-V2.md §6.3;P2 提交流程主路径)
    promoted: null, // 最近一次发布结果(F15:状态条出路)
    ackWarn: false, // warn 报告的"我已阅读警告"勾选
    confirming: false, // promote 内联确认行开关
    draftTests: [], // 当前草稿的用例文件名列表(tests/*.json)
    testCase: null, // 试跑选中的用例(null = 用输入框 JSON)
    testInput: "",
    testRun: null, // {runId, status, result, error, check}
    traceHtml: "", // 试跑 trace(deriveTraceView/renderTrace 复用)
    chat: [], // 中栏消息记录 {role: user|assistant|system, text}
    chatInput: "",
    chatBusy: false,
    pkg: null, // 包闭包(docs/SKILL-PACKAGES.md §3.1;P1 包面板数据源)
    root: document.createElement("div"),
  };
  lab.root.className = "lab";
  lab.root.innerHTML =
    `<div class="lab-top-host"></div>` +
    `<div class="lab-cols">` +
    `<div class="lab-col lab-left">` +
    `<div class="lab-pkg"></div>` + // 包面板(编辑器上方,§3.1)
    `<div class="lab-editor"></div>` +
    `</div>` +
    `<div class="lab-col lab-agent"></div>` +
    `<div class="lab-col lab-test"></div>` +
    `</div>` +
    `<div class="lab-status"></div>`;
  main.appendChild(lab.root);
  _bindEvents();
  _renderChat();
  (async () => {
    await Promise.all([_loadDrafts(), _loadCatalogs()]);
    _renderTop();
    const target = name ?? lab.drafts[0]?.name ?? null;
    if (target) await _selectDraft(target);
    else {
      _renderEditor();
      _renderTestPanel();
    }
  })().catch((e) => toast(e.message ?? String(e), "error"));
  return { root: lab.root };
}

/* beforeunload 处理器引用(openLab 幂等:重复进场不重复挂) */
let _beforeUnload = null;

export function closeLab() {
  lab?.root?.remove();
  lab = null;
}

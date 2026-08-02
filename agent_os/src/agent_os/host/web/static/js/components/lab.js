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
import { emptyBlock, esc, toast } from "../util.js";
import { TIER_PERM } from "./inbox.js";

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
  return (
    `<div class="lab-top">` +
    `<select class="input lab-select" data-lab="select">${options}</select>` +
    `<input class="input mono lab-new-name" data-lab="new-name" placeholder="domain.action"` +
    ` aria-label="${esc(copy("lab.new"))}">` +
    `<select class="input lab-new-from" data-lab="new-from">` +
    `<option value="">${esc(copy("lab.new.empty"))}</option>${prodOptions}</select>` +
    `<button class="btn" data-lab="create">${esc(copy("lab.new"))}</button>` +
    `<button class="btn" data-lab="delete">${esc(copy("lab.delete"))}</button>` +
    `<span class="lab-top-tier" data-lab-tier-badge title="${esc(tierTitle(view.tierDetail))}">` +
    `${tierBadgeHtml(view.tier)}</span>` +
    `<span class="lab-top-actions">` +
    `<button class="btn" data-lab="check">${esc(copy("lab.check"))}</button>` +
    `<button class="btn" data-lab="promote"${promoteReady(view) ? "" : " disabled"}>` +
    `${esc(copy("lab.promote"))}</button>` +
    `<button class="btn btn-primary" data-lab="save">${esc(copy("lab.save"))}</button>` +
    `</span></div>`
  );
}

/* 报告过期判定(§2.3):保存时间晚于报告时间 → 旧报告作废(服务端另有哈希校验兜底) */
export function isReportStale(view) {
  return Boolean(
    view?.report && view.savedAt && view.savedAt > view.report.created_at * 1000
  );
}

/* 提交按钮点亮条件(§1.4):报告在、无 fail、不过期、warn 已确认 */
export function promoteReady(view) {
  if (!view?.report || isReportStale(view)) return false;
  if (view.report.status === "fail") return false;
  if (view.report.status === "warn" && !view.ackWarn) return false;
  return true;
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
  if (isReportStale(view)) parts.push(copy("lab.status.stale"));
  return parts.join(" · ");
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
  toast(copy("lab.saved"), "success");
  _renderStatus(); // 保存晚于上次检查 → "距上次检查有改动 ⚠"(§2.3)
  _renderTop(); // 报告过期 → 提交按钮立即熄灭(服务端哈希校验兜底)
  return saved;
}

/* 检查(§1.4):跑闸门 → 五关卡片进右栏;warn 时需勾选"我已阅读警告"才亮提交 */
export async function runCheck() {
  if (!lab?.form) return null;
  const report = await postJson(
    `/api/lab/drafts/${encodeURIComponent(lab.form.name)}/validate`
  );
  lab.report = report;
  lab.ackWarn = false;
  lab.confirming = false;
  _renderGate();
  _renderTop();
  _renderStatus();
  return report;
}

function _renderGate() {
  const host = lab.root?.querySelector(".lab-test");
  if (!host) return;
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
  _renderGate();
  _renderTop();
  toast(`${copy("lab.promote.done")}: ${result.name}@${result.version}`, "success");
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
  if (el) el.textContent = statusLine(lab);
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
  const draft = await getJson(`/api/lab/drafts/${encodeURIComponent(name)}`);
  lab.form = draftToForm(draft);
  lab.parseError = draft.parse_error ?? null;
  lab.savedAt = null;
  lab.report = null; // 换草稿:旧报告不属于新对象
  lab.ackWarn = false;
  lab.confirming = false;
  _renderEditor();
  _renderGate();
  await refreshTier();
  _renderStatus();
}

function _renderEditor() {
  const host = lab.root?.querySelector(".lab-editor");
  if (!host) return;
  if (!lab.form) {
    host.innerHTML = emptyBlock(copy("lab.no.selection"), copy("lab.no.selection.hint"), "inbox");
    return;
  }
  host.innerHTML = editorHtml(lab);
}

function _renderTop() {
  const host = lab.root?.querySelector(".lab-top-host");
  if (host) host.innerHTML = topbarHtml(lab) + (lab.confirming ? _promoteConfirmHtml() : "");
}

async function _createDraft() {
  const nameEl = lab.root.querySelector('[data-lab="new-name"]');
  const fromEl = lab.root.querySelector('[data-lab="new-from"]');
  const name = nameEl?.value?.trim() ?? "";
  if (!isValidDraftName(name)) {
    toast(copy("lab.name.invalid"), "error");
    return;
  }
  const from = fromEl?.value ?? "";
  await postJson("/api/lab/drafts", { name, from_skill: from || null });
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
      if (action === "create") return await _createDraft();
      if (action === "delete") return await _deleteDraft(btn);
      if (action === "save") return await saveCurrentDraft();
      if (action === "check") return await runCheck();
      if (action === "promote") {
        if (!promoteReady(lab)) return; // 未点亮不响应(与 disabled 双保险)
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
    const remove = e.target.closest("[data-chip-remove]");
    if (remove) {
      const field = remove.dataset.chipRemove;
      lab.form[field] = lab.form[field].filter((v) => v !== remove.dataset.value);
      _renderEditor();
      if (field === "tools" || field === "skills") await refreshTier();
    }
  });
  lab.root.addEventListener("change", async (e) => {
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
    const field = e.target.closest("[data-field]")?.dataset.field;
    if (!field || !lab.form) return;
    lab.form[field] = e.target.value;
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
  lab = {
    form: null,
    drafts: [],
    toolsCatalog: [],
    skillsCatalog: [],
    tier: "none",
    tierDetail: null,
    savedAt: null,
    parseError: null,
    report: null, // 最近一次闸门报告(§1.4;保存后过期)
    ackWarn: false, // warn 报告的"我已阅读警告"勾选
    confirming: false, // promote 内联确认行开关
    root: document.createElement("div"),
  };
  lab.root.className = "lab";
  lab.root.innerHTML =
    `<div class="lab-top-host"></div>` +
    `<div class="lab-cols">` +
    `<div class="lab-col lab-editor"></div>` +
    `<div class="lab-col lab-agent">${emptyBlock(copy("lab.agent.empty"), "", "inbox")}</div>` +
    `<div class="lab-col lab-test">${emptyBlock(copy("lab.test.empty"), "", "inbox")}</div>` +
    `</div>` +
    `<div class="lab-status"></div>`;
  main.appendChild(lab.root);
  _bindEvents();
  (async () => {
    await Promise.all([_loadDrafts(), _loadCatalogs()]);
    _renderTop();
    const target = name ?? lab.drafts[0]?.name ?? null;
    if (target) await _selectDraft(target);
    else _renderEditor();
  })().catch((e) => toast(e.message ?? String(e), "error"));
  return { root: lab.root };
}

export function closeLab() {
  lab?.root?.remove();
  lab = null;
}

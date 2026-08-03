/* 产物卡渲染(docs/WEB-PLATFORM.md §4/§10;六卡型,纯函数不碰 DOM)。
   双层:对话流走 summaryHtml(摘要层——人话一句结论,数据驱动,黑话不收);
   详情 tab 走 cardHtml/details.js(技术层——全量字段)。
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

/* 详情链接(小字链接,不抢主按钮;点击开详情 tab,§布局改造) */
function _detailLink(kind, ref, label, data = null) {
  return (
    `<button class="pf-detail-link" data-detail-kind="${esc(kind)}" data-detail-ref="${esc(ref)}"` +
    ` data-detail='${esc(JSON.stringify(data ?? {}))}'>${esc(label)}</button>`
  );
}

/* skill_pack 卡:成员列表 + tier 徽标 */
function skillPackCard(card) {
  const d = card.data ?? {};
  return (
    `<div class="pf-sec"><span class="mono">${esc(d.name ?? "")}</span> ${_tier(d.tier)}</div>` +
    `<div class="pf-sec">${esc(copy("platform.members"))}: ` +
    (d.members ?? []).map((m) => `<span class="chip mono">${esc(m)}</span>`).join(" ") +
    `</div>` +
    _detailLink("pack", d.name ?? "", copy("platform.detail.pack"), { name: d.name, tier: d.tier, members: d.members ?? [] })
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
    `<div class="pf-sec"><a class="btn" href="/#/lab/${encodeURIComponent(d.draft ?? "")}">${esc(copy("platform.fix"))}</a>` +
    _detailLink("gate", d.draft ?? "", copy("platform.detail.gate"), d) +
    `</div>`
  );
}

/* diff 卡:字段新旧两列 + prompt 红绿行(Flow C 同构呈现)。
   注:对话流里只显示人话摘要(summaryHtml),本函数同时充当 diff 详情层。 */
export function diffCard(card) {
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
    `<span>${esc(copy("platform.warnings.ack"))}</span></label>` +
    _detailLink("plan", d.plan_id ?? "", copy("platform.detail.plan"), d)
  );
}

/* table 卡:通用键值表 */
function tableCard(card) {
  const d = card.data ?? {};
  const head = (d.columns ?? []).map((c) => `<th>${esc(c)}</th>`).join("");
  const rows = (d.rows ?? [])
    .map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`)
    .join("");
  const link =
    d.ref?.kind === "run" && d.ref?.id
      ? _detailLink("run", d.ref.id, copy("platform.detail.run"), { id: d.ref.id })
      : "";
  return (
    (d.title ? `<div class="pf-sec"><b>${esc(d.title)}</b></div>` : "") +
    `<table class="pf-table"><tr>${head}</tr>${rows}</table>` +
    link
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

/* 卡渲染入口:type 分发 + actions 按钮区(未知卡型降级为 JSON 预览,不炸)。
   这是**技术全量**渲染——详情层用;对话流(摘要层)走下面的 summaryHtml。 */
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

/* ── 摘要层(人话)──────────────────────────────────────────────
   原则(docs/WEB-PLATFORM.md §10):对话流默认只显示通俗易懂的一句结论 +
   补充行,技术字段(tier/五关/条款号/hash/run id/英文错误类名)一律收进
   详情 tab。所有句子从 card.data **算**出来(数据驱动),不是静态文案;
   文案模板走 copy(六主题,{n}/{name} 占位),值在填入前逐个 esc。 */

/* 模板填充:copy 里的 {xxx} 占位;调用方负责把值先 esc(本文件内统一如此) */
const _t = (key, vars = {}) => copy(key).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));

/* 「名字」引用(技能名是标识符不是黑话,保留原名,加引号表引用) */
const _q = (name) => `「${esc(name ?? "")}」`;

/* 英文错误类名 → 人话(摘要层一句;原文留在详情层)。
   未识别的去掉 `XxxError:` 前缀留消息体——不编造原因。 */
const _ERROR_HUMAN = [
  [/auth|api.?key|unauthorized|401/i, "platform.err.auth"],
  [/timeout|timed out/i, "platform.err.timeout"],
  [/ProviderError|model.*(unavailable|error)/i, "platform.err.provider"],
];
function humanError(raw) {
  const s = String(raw ?? "");
  for (const [re, key] of _ERROR_HUMAN) if (re.test(s)) return copy(key);
  return s.replace(/^[A-Z][\w.]*Error:\s*/, "");
}

/* 字段路径 → 人话(description→说明 等;未映射的留原段——是字段名不是术语) */
function _fieldName(path) {
  const seg = String(path ?? "").split(".")[0];
  const c = copy(`platform.sum.field.${seg}`);
  return c.startsWith("platform.") ? esc(seg) : esc(c);
}

/* plan 摘要:做「goal」需要 n 个技能:复用…/新建…(template 等结构数据省略) */
function planSummary(d) {
  const reuse = d.reuse ?? [];
  const create = d.create ?? [];
  const parts = [];
  if (reuse.length) {
    parts.push(_t("platform.sum.plan.reuse", { names: reuse.map((r) => _q(r.name)).join("") }));
  }
  if (create.length) {
    parts.push(_t("platform.sum.plan.create", { names: create.map((c) => _q(c.name)).join("") }));
  }
  return (
    `<div class="pf-card-lead">${_t("platform.sum.plan.lead", { goal: esc(d.goal ?? ""), n: reuse.length + create.length })}</div>` +
    (parts.length ? `<div class="pf-card-sub">${parts.join(",")}</div>` : "")
  );
}

/* skill_pack 摘要:「name」做好了,共 n 个技能;审批面按 tier 说人话 */
function packSummary(d) {
  const tierKey = { none: "none", reversible: "reversible", irreversible: "irreversible" }[d.tier];
  return (
    `<div class="pf-card-lead">${_t("platform.sum.pack.lead", { name: esc(d.name ?? ""), n: (d.members ?? []).length })}</div>` +
    (tierKey ? `<div class="pf-card-sub">${esc(copy(`platform.sum.pack.tier.${tierKey}`))}</div>` : "")
  );
}

/* gate_report 摘要:几项通过 + 第一个建议/没通过的人话消息(关号/条款号进详情) */
function gateSummary(d) {
  const gates = Object.values(d.gates ?? {});
  const p = gates.filter((g) => (g.status ?? "skip") === "pass").length;
  const fails = gates.filter((g) => g.status === "fail");
  const warns = gates.filter((g) => g.status === "warn");
  const firstMsg = (list) =>
    esc(list[0]?.findings?.[0]?.message ?? list[0]?.note ?? "");
  let sub = "";
  if (fails.length) sub = _t("platform.sum.gate.fail", { f: fails.length, msg: firstMsg(fails) });
  else if (warns.length) sub = _t("platform.sum.gate.warn", { w: warns.length, msg: firstMsg(warns) });
  return (
    `<div class="pf-card-lead">${_t("platform.sum.gate.lead", { p })}</div>` +
    (sub ? `<div class="pf-card-sub">${sub}</div>` : "")
  );
}

/* diff 摘要:改了什么(字段人话 + 措辞 + 用例增减);红绿细节进详情 */
function diffSummary(d) {
  const diff = d.diff ?? {};
  if (!diff.has_changes) {
    return `<div class="pf-card-lead">${esc(copy("platform.no.changes"))}</div>`;
  }
  const parts = [];
  const seen = new Set();
  for (const m of diff.members ?? []) {
    for (const f of m.fields ?? []) {
      if (f.kind === "same") continue;
      const name = _fieldName(f.path);
      if (!seen.has(name)) {
        seen.add(name);
        parts.push(name);
      }
    }
    if ((m.prompt_diff ?? []).some((l) => l.kind !== "same") && !seen.has("prompt")) {
      seen.add("prompt");
      parts.push(esc(copy("platform.sum.field.prompt")));
    }
    const ta = (m.tests?.added ?? []).length;
    const tr = (m.tests?.removed ?? []).length;
    if (ta || tr) parts.push(_t("platform.sum.diff.tests", { a: ta, r: tr }));
  }
  const what = parts.length ? parts.join(",") : esc(copy("platform.sum.diff.other"));
  return `<div class="pf-card-lead">${_t("platform.sum.diff.lead", { name: esc(d.name ?? ""), what })}</div>`;
}

/* publish 摘要:将发布 n 个技能:x 新建/y 更新/z 不变 + 阻塞与警告数
   (hash/plan_id/三态表进详情;warnings 勾选门留在摘要——它是动作不是术语) */
function publishSummary(d) {
  const members = d.members ?? [];
  const count = (a) => members.filter((m) => m.action === a).length;
  const parts = [];
  if (count("create")) parts.push(_t("platform.sum.publish.create", { c: count("create") }));
  if (count("replace")) parts.push(_t("platform.sum.publish.replace", { r: count("replace") }));
  if (count("unchanged")) parts.push(_t("platform.sum.publish.unchanged", { u: count("unchanged") }));
  const subs = [];
  if ((d.blockers ?? []).length) subs.push(_t("platform.sum.publish.blocked", { b: d.blockers.length }));
  if ((d.warnings ?? []).length) subs.push(_t("platform.sum.publish.warn", { w: d.warnings.length }));
  return (
    `<div class="pf-card-lead">${_t("platform.sum.publish.lead", { n: members.length, parts: parts.join(",") })}</div>` +
    subs.map((s) => `<div class="pf-card-sub">${s}</div>`).join("") +
    `<label class="pf-ack"><input type="checkbox" data-ack>` +
    `<span>${esc(copy("platform.warnings.ack"))}</span></label>`
  );
}

/* table 摘要:仅 run 摘要卡(带 ref/row_refs)双层化——隐去 run id 列,一行一个
   「技能」:人话错误;通用表(help/空表)本身即摘要,保持原样。 */
function tableSummary(card) {
  const d = card.data ?? {};
  const isRunTable = d.ref?.kind === "run" || (d.row_refs ?? []).some((r) => r?.kind === "run");
  if (!isRunTable) return tableCard(card);
  const rows = (d.rows ?? [])
    .map((r, i) => {
      const rowRef = d.row_refs?.[i];
      const link =
        rowRef?.kind === "run" && rowRef?.id
          ? ` ` + _detailLink("run", rowRef.id, copy("platform.detail.run"), { id: rowRef.id })
          : "";
      return (
        `<div class="pf-card-sub">${_t("platform.sum.run.row", { skill: esc(r[1] ?? ""), msg: esc(humanError(r[2])) })}${link}</div>`
      );
    })
    .join("");
  const lead = d.title ? esc(d.title) : esc(copy("platform.sum.run.lead"));
  return `<div class="pf-card-lead">${lead}</div>` + rows;
}

/* 升权选项 → 人话按钮文案(选项 id 是协议串,永不上屏) */
const _OPTION_LABEL = {
  "approve-once": "platform.esc.approve.once",
  "approve-run": "platform.esc.approve.run",
  deny: "platform.esc.deny",
};

/* escalation 摘要(W2):「skill」想执行操作(tier 人话),需要你批准 +
   就地三按钮(approve-run 仅 L2 选项里有才出现——选项面是内核给的,卡不造)。
   已决(data.resolved)按钮置灰 + 状态字。 */
function escalationSummary(d) {
  const tierKey = { none: "none", reversible: "reversible", irreversible: "irreversible" }[d.tier];
  const resolved = d.resolved;
  const status = resolved
    ? `<div class="pf-card-sub">${esc(
        copy(resolved === "gone" ? "platform.esc.gone"
          : resolved === "deny" ? "platform.esc.resolved.deny" : "platform.esc.resolved.approve")
      )}</div>`
    : "";
  const buttons = (d.options ?? [])
    .map(
      (opt) =>
        `<button class="btn" data-decision="${esc(d.question_id ?? "")}" data-answer="${esc(opt)}"` +
        `${resolved ? " disabled" : ""}>${esc(copy(_OPTION_LABEL[opt] ?? "platform.esc.deny"))}</button>`
    )
    .join("");
  return (
    `<div class="pf-card-lead">${_t("platform.esc.lead", { skill: esc(d.skill ?? "") })}</div>` +
    `<div class="pf-card-sub">` +
    (tierKey ? `${esc(copy(`platform.sum.pack.tier.${tierKey}`))},` : "") +
    `${esc(copy("platform.esc.need"))}</div>` +
    status +
    `<div class="pf-card-actions">` +
    _detailLink("esc", d.question_id ?? "", copy("platform.detail.esc"), d) +
    buttons +
    `</div>`
  );
}

/* 摘要卡渲染入口:一句结论(加粗)+ 补充行 + 详情链接/动作区(右下) */
export function summaryHtml(card) {
  const type = card?.type ?? "";
  const d = card?.data ?? {};
  const render = { plan: planSummary, skill_pack: packSummary, gate_report: gateSummary,
    diff: diffSummary, publish: publishSummary, escalation: escalationSummary }[type];
  const body = render ? render(d) : type === "table" ? tableSummary(card)
    : `<pre class="mono">${esc(JSON.stringify(d, null, 2))}</pre>`;
  const link =
    type === "plan"
      ? _detailLink("decompose", d.create?.[0]?.name ?? "", copy("platform.detail.decompose"), d) // N6:结构 + 路由角标在详情层
      : type === "skill_pack"
      ? _detailLink("pack", d.name ?? "", copy("platform.detail.pack"), { name: d.name, tier: d.tier, members: d.members ?? [] })
      : type === "gate_report"
        ? `<a class="btn" href="/#/lab/${encodeURIComponent(d.draft ?? "")}">${esc(copy("platform.fix"))}</a>` +
          _detailLink("gate", d.draft ?? "", copy("platform.detail.gate"), d)
        : type === "diff"
          ? _detailLink("diff", d.name ?? "", copy("platform.detail.diff"), d)
          : type === "publish"
            ? _detailLink("plan", d.plan_id ?? "", copy("platform.detail.plan"), d)
            : type === "table" && d.ref?.kind === "run" && d.ref?.id
              ? _detailLink("run", d.ref.id, copy("platform.detail.run"), { id: d.ref.id })
              : "";
  const actions = (card?.actions ?? []).map(_act).join("");
  return (
    `<div class="pf-card" data-card="${esc(type)}">` +
    body +
    (link || actions ? `<div class="pf-card-actions">${link}${actions}</div>` : "") +
    `</div>`
  );
}

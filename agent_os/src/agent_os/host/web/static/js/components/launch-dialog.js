/* New Run Modal(WEB-UI.md §4.3 运行发起):TopBar `+ New Run` 打开,不打断当前页。
   Skill 下拉(GET /api/skills,hover 显示 description,选中后显示 description 与
   inputs 摘要)→ Input JSON 编辑器(mono,按该技能 inputs schema 生成示例骨架,
   实时校验:非法 JSON / schema 错误进编辑器下方错误条 + 红框,有错禁用 Run)→
   高级区(model/max_cost/max_steps/inline → POST body overrides;inline 是 §9 merge
   消融开关:继承配置/on/off)→ Run:POST /api/runs
   {skill, input, overrides, wait:false} → 关 Modal 跳 #/runs/<run_id> 进 live;
   失败(4xx/5xx 或 run 未开始的 200+failed)在 Modal 底部错误条显示。
   Esc 与遮罩点击关闭;打开聚焦第一个输入,关闭还原焦点。

   D6 一站多 skill set(≥2 sets 时):技能下拉逐 set 拉取并按 <optgroup> 分组,
   option value 为 "<set>/<name>"(state.byValue 反查),详情按 ?skill_set= 消歧,
   提交带 skill_set;openLaunchDialog 可传 presetSet 精确预填。单 set/无 sets 时
   行为与之前完全一致(value 即技能名,不带 skill_set)。

   纯函数(不碰 DOM,node 单测可载):
     validateAgainstSchema(value, schema)  手写小型 JSON Schema 校验器 → errors[]
     skeletonFromSchema(schema)            inputs schema → 示例骨架({"n": 1} 形式)
     summarizeInputs(schema)               inputs schema → 可读字段摘要行
     buildOverrides(fields)                高级区表单值 → overrides 对象(只含已填项) */

import { getJson, postJson } from "../api.js";
import { store } from "../store.js";
import { esc } from "../util.js";

/* ── 手写小型 JSON Schema 校验器 ──────────────────────────────
   覆盖常见子集:type(object/array/string/number/integer/boolean/null)、
   required、properties(递归)、items(递归)、minimum/maximum。
   不做:enum/const/pattern/format/oneOf 等(超出 Launch Modal 需要;
   后端 jsonschema 全量校验兜底,§6.1 加载期同一套 schema)。
   返回 [{ path, message }];type 不符即停(不再深入子约束)。 */

const typeOk = (value, type) => {
  switch (type) {
    case "object":
      return value !== null && typeof value === "object" && !Array.isArray(value);
    case "array":
      return Array.isArray(value);
    case "string":
      return typeof value === "string";
    case "number":
      return typeof value === "number" && Number.isFinite(value);
    case "integer":
      return Number.isInteger(value);
    case "boolean":
      return typeof value === "boolean";
    case "null":
      return value === null;
    default:
      return true; // 未知/未声明类型:不拦(子集哲学:宁缺毋滥)
  }
};

const joinPath = (base, key) => (base === "$" ? `$.${key}` : `${base}.${key}`);

export function validateAgainstSchema(value, schema) {
  const errors = [];
  const walk = (val, sch, path) => {
    if (!sch || typeof sch !== "object") return;
    if (sch.type && !typeOk(val, sch.type)) {
      errors.push({ path, message: `应为 ${sch.type}` });
      return;
    }
    if (sch.type === "object") {
      for (const key of sch.required ?? []) {
        if (!Object.hasOwn(val, key)) {
          errors.push({ path: joinPath(path, key), message: "缺少必填字段" });
        }
      }
      for (const [key, sub] of Object.entries(sch.properties ?? {})) {
        if (Object.hasOwn(val, key)) walk(val[key], sub, joinPath(path, key));
      }
    } else if (sch.type === "array" && sch.items) {
      val.forEach((item, i) => walk(item, sch.items, `${path}[${i}]`));
    }
    if (typeof val === "number") {
      if (sch.minimum != null && val < sch.minimum) {
        errors.push({ path, message: `不能小于 minimum ${sch.minimum}` });
      }
      if (sch.maximum != null && val > sch.maximum) {
        errors.push({ path, message: `不能大于 maximum ${sch.maximum}` });
      }
    }
  };
  walk(value, schema, "$");
  return errors;
}

/* ── inputs schema → 示例骨架(编辑器默认填,{"n": 1} 形式)── */
function skeletonValue(sch) {
  switch (sch?.type) {
    case "object": {
      const out = {};
      for (const [key, sub] of Object.entries(sch.properties ?? {})) {
        out[key] = skeletonValue(sub);
      }
      return out;
    }
    case "integer":
      return Number.isFinite(sch.minimum) ? Math.ceil(sch.minimum) : 1;
    case "number":
      return Number.isFinite(sch.minimum) ? sch.minimum : 1;
    case "string":
      return "";
    case "boolean":
      return false;
    case "array":
      return [];
    case "null":
      return null;
    default:
      return null; // 未声明类型:null 占位(合法 JSON,交给用户改)
  }
}

export function skeletonFromSchema(schema) {
  if (!schema || typeof schema !== "object") return {};
  // 技能 input 恒为 JSON 对象:顶层 schema 未声明类型时退化为 {} 占位
  return skeletonValue(schema) ?? {};
}

/* ── inputs schema → 可读摘要行("n: integer, minimum 1(必填)")── */
export function summarizeInputs(schema) {
  const props = schema?.properties ?? {};
  const required = new Set(schema?.required ?? []);
  return Object.entries(props).map(([name, sub]) => {
    const bits = [sub?.type ?? "any"];
    if (sub?.minimum != null) bits.push(`minimum ${sub.minimum}`);
    if (sub?.maximum != null) bits.push(`maximum ${sub.maximum}`);
    return `${name}: ${bits.join(", ")}${required.has(name) ? "(必填)" : ""}`;
  });
}

/* ── 高级区表单值 → overrides(只含已填项;全空返回 {})────── */
export function buildOverrides({ model = "", maxCost = "", maxSteps = "", inline = "" } = {}) {
  const overrides = {};
  const m = String(model).trim();
  if (m) overrides.model = m;
  const c = String(maxCost).trim();
  if (c !== "" && Number.isFinite(Number(c))) overrides.max_cost = Number(c);
  const s = String(maxSteps).trim();
  if (s !== "" && Number.isFinite(Number(s))) overrides.max_steps = Math.trunc(Number(s));
  // SKILL-INLINING.md §9 消融开关:仅 "on"/"off" 生效,""(继承配置)不带出
  const i = String(inline).trim();
  if (i === "on" || i === "off") overrides.inline = i;
  return overrides;
}

/* ── Modal(DOM;openLaunchDialog 以外不碰 document)─────────── */

export function openLaunchDialog({ presetSkill = null, presetSet = null } = {}) {
  const doc = document;
  const prevFocus = doc.activeElement;
  const $el = (tag, className, text) => {
    const el = doc.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = text;
    return el;
  };

  /* 骨架:overlay > dialog > head/body/foot;元素引用集中 els(免 querySelector) */
  const overlay = $el("div", "modal-overlay");
  const dialog = $el("div", "modal");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", "New Run");

  const head = $el("div", "modal-head");
  head.appendChild($el("span", "modal-title", "New Run"));
  const closeBtn = $el("button", "icon-btn", "✕");
  closeBtn.setAttribute("aria-label", "关闭");
  head.appendChild(closeBtn);

  const body = $el("div", "modal-body");
  const skillField = $el("label", "field");
  skillField.appendChild($el("span", "field-label", "Skill"));
  const skillSel = $el("select", "input mono ld-skill");
  skillSel.disabled = true;
  skillField.appendChild(skillSel);
  const skillInfo = $el("div", "ld-skill-info");

  const inputField = $el("label", "field");
  inputField.appendChild($el("span", "field-label", "Input(JSON,按 inputs schema 实时校验)"));
  const inputArea = $el("textarea", "input mono ld-input");
  inputArea.rows = 7;
  inputArea.spellcheck = false;
  inputField.appendChild(inputArea);
  const errorBar = $el("div", "ld-errors");
  errorBar.setAttribute("role", "alert");
  errorBar.hidden = true;

  const adv = $el("details", "ld-adv");
  adv.appendChild($el("summary", "", "高级(覆盖本次 run 配置)"));
  const advGrid = $el("div", "ld-adv-grid");
  const modelIn = $el("input", "input mono");
  modelIn.placeholder = "继承配置";
  modelIn.setAttribute("aria-label", "model 覆盖");
  const costIn = $el("input", "input mono");
  costIn.placeholder = "继承配置";
  costIn.setAttribute("inputmode", "decimal");
  costIn.setAttribute("aria-label", "max_cost 覆盖");
  const stepsIn = $el("input", "input mono");
  stepsIn.placeholder = "继承配置";
  stepsIn.setAttribute("inputmode", "numeric");
  stepsIn.setAttribute("aria-label", "max_steps 覆盖");
  // SKILL-INLINING.md §9 消融开关:继承配置(不带出)/ on / off → overrides.inline
  const inlineSel = $el("select", "input mono");
  inlineSel.setAttribute("aria-label", "inline 覆盖");
  inlineSel.title = "merge 消融开关(§9):off 时 inline 技能退化为压帧调用";
  for (const [value, text] of [["", "继承配置"], ["on", "on"], ["off", "off"]]) {
    const opt = $el("option", "", text);
    opt.value = value;
    inlineSel.appendChild(opt);
  }
  for (const [label, el] of [
    ["model", modelIn],
    ["max_cost", costIn],
    ["max_steps", stepsIn],
    ["inline", inlineSel],
  ]) {
    const f = $el("label", "field field-inline");
    f.appendChild($el("span", "field-label", label));
    f.appendChild(el);
    advGrid.appendChild(f);
  }
  const inlineHint = $el(
    "div",
    "ld-inline-hint",
    "merge 消融开关(§9):off 时 inline 技能退化为压帧调用",
  );
  advGrid.appendChild(inlineHint);
  adv.appendChild(advGrid);

  const modalError = $el("div", "ld-modal-error");
  modalError.setAttribute("role", "alert");
  modalError.hidden = true;

  body.appendChild(skillField);
  body.appendChild(skillInfo);
  body.appendChild(inputField);
  body.appendChild(errorBar);
  body.appendChild(adv);
  body.appendChild(modalError);

  const foot = $el("div", "modal-foot");
  const cancelBtn = $el("button", "btn", "Cancel");
  const runBtn = $el("button", "btn btn-primary", "Run ▶");
  runBtn.disabled = true;
  foot.appendChild(cancelBtn);
  foot.appendChild(runBtn);

  dialog.appendChild(head);
  dialog.appendChild(body);
  dialog.appendChild(foot);
  overlay.appendChild(dialog);

  const els = {
    skill: skillSel,
    info: skillInfo,
    input: inputArea,
    errors: errorBar,
    model: modelIn,
    maxCost: costIn,
    maxSteps: stepsIn,
    inline: inlineSel,
    modalError,
    run: runBtn,
  };
  // byValue(D6):option value → { name, set }(多 set 时 value = "<set>/<name>")
  const state = { details: new Map(), schema: null, busy: false, closed: false, byValue: new Map() };

  /* ── 校验:非法 JSON / schema 错误 → 错误条 + 红框 + 禁用 Run ── */
  function validate() {
    const text = els.input.value;
    let value = null;
    let errors = [];
    try {
      value = JSON.parse(text);
    } catch (e) {
      errors = [{ path: "$", message: `非法 JSON:${e.message}` }];
    }
    if (!errors.length && state.schema) errors = validateAgainstSchema(value, state.schema);
    els.errors.innerHTML = errors
      .map((er) => `<div class="ld-error"><span class="mono">${esc(er.path)}</span> ${esc(er.message)}</div>`)
      .join("");
    els.errors.hidden = errors.length === 0;
    els.input.classList.toggle("is-invalid", errors.length > 0);
    els.run.disabled = errors.length > 0 || !els.skill.value || state.busy;
    return { value, errors };
  }

  function showModalError(msg) {
    els.modalError.textContent = msg;
    els.modalError.hidden = !msg;
  }

  /* ── 技能选中:拉全量 manifest(inputs schema)→ 摘要 + 骨架 + 校验 ── */
  async function onSkillChange() {
    const value = els.skill.value;
    const entry = state.byValue.get(value) ?? { name: value, set: null }; // D6:value 反查 set
    const name = entry.name;
    state.schema = null;
    els.skill.title = "";
    if (!value) {
      els.info.innerHTML = "";
      validate();
      return;
    }
    els.info.innerHTML = `<div class="ld-skill-desc">加载技能详情…</div>`;
    try {
      if (!state.details.has(value)) {
        // D6:多 set 下详情按 ?skill_set= 消歧;缓存 key 用 value(含 set,同名不串)
        const q = entry.set ? `?skill_set=${encodeURIComponent(entry.set)}` : "";
        state.details.set(value, await getJson(`/api/skills/${encodeURIComponent(name)}${q}`));
      }
    } catch {
      state.details.set(value, null);
    }
    if (state.closed || els.skill.value !== value) return; // 加载期间已切换/关闭
    const d = state.details.get(value);
    if (!d) {
      els.info.innerHTML =
        `<div class="ld-skill-desc ld-warn">schema 加载失败,仅做 JSON 合法性校验</div>`;
      els.input.value = "{}";
      validate();
      return;
    }
    state.schema = d.inputs ?? null;
    els.skill.title = d.description ?? ""; // hover 悬浮提示(原生 title)
    const lines = summarizeInputs(state.schema);
    els.info.innerHTML =
      `<div class="ld-skill-desc">${esc(d.description || "(无描述)")}</div>` +
      (lines.length
        ? `<ul class="ld-skill-inputs">${lines.map((l) => `<li class="mono">${esc(l)}</li>`).join("")}</ul>`
        : "");
    els.input.value = JSON.stringify(skeletonFromSchema(state.schema), null, 2);
    validate();
  }

  /* ── 提交:POST /api/runs → 关 Modal → 跳 live;失败进底部错误条 ── */
  async function submit() {
    const { value, errors } = validate();
    if (errors.length || state.busy) return;
    state.busy = true;
    els.run.disabled = true;
    els.run.textContent = "启动中…";
    showModalError("");
    const overrides = buildOverrides({
      model: els.model.value,
      maxCost: els.maxCost.value,
      maxSteps: els.maxSteps.value,
      inline: els.inline.value,
    });
    const entry = state.byValue.get(els.skill.value) ?? { name: els.skill.value, set: null };
    const body = { skill: entry.name, input: value, wait: false };
    if (entry.set) body.skill_set = entry.set; // D6:多 set 提交带 skill_set
    if (Object.keys(overrides).length) body.overrides = overrides;
    try {
      const res = await postJson("/api/runs", body);
      if (res?.run_id) {
        close();
        location.hash = `#/runs/${encodeURIComponent(res.run_id)}`;
        return;
      }
      showModalError(res?.error ?? "run 未开始(未知错误)");
    } catch (e) {
      showModalError(e.message ?? "发起失败");
    } finally {
      state.busy = false;
      if (!state.closed) {
        els.run.textContent = "Run ▶";
        validate();
      }
    }
  }

  function close() {
    if (state.closed) return;
    state.closed = true;
    doc.removeEventListener("keydown", onKeydown);
    overlay.remove();
    prevFocus?.focus?.(); // 焦点管理:还原打开前焦点
  }

  function onKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault?.();
      close();
    }
  }

  closeBtn.addEventListener("click", close);
  cancelBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close(); // 遮罩点击关闭(dialog 内点击不透出)
  });
  doc.addEventListener("keydown", onKeydown);
  els.skill.addEventListener("change", onSkillChange);
  els.input.addEventListener("input", validate);
  els.run.addEventListener("click", submit);

  doc.body.appendChild(overlay);
  els.skill.focus(); // 焦点管理:打开聚焦第一个输入

  /* ── 技能下拉:GET /api/skills 填充(option title = description);
        D6:≥2 sets 时逐 set 拉取并按 <optgroup> 分组 ── */
  (async () => {
    try {
      const sets = store.get("skillsets") ?? [];
      const multi = sets.length >= 2;
      const groups = multi
        ? await Promise.all(
            sets.map(async (s) => ({
              set: s.name,
              // 坏 set 隔离:该组为空,不拖垮其它 set(与 /api/skillsets 同口径)
              skills: await getJson(`/api/skills?skill_set=${encodeURIComponent(s.name)}`).catch(
                () => [],
              ),
            })),
          )
        : [{ set: null, skills: await getJson("/api/skills") }];
      if (state.closed) return;
      const entries = []; // { value, name, set }(填充顺序 = 下拉顺序)
      for (const g of groups) {
        for (const s of Array.isArray(g.skills) ? g.skills : []) {
          entries.push({
            value: multi ? `${g.set}/${s.name}` : s.name,
            name: s.name,
            set: g.set,
          });
        }
      }
      if (!entries.length) {
        showModalError("无可用技能(在 agent-os.toml 配置 skills.path)");
        return;
      }
      state.byValue = new Map(entries.map((e) => [e.value, e]));
      for (const g of groups) {
        const list = (Array.isArray(g.skills) ? g.skills : []).filter((s) => s?.name);
        if (!list.length) continue;
        const parent = multi ? $el("optgroup") : skillSel;
        if (multi) parent.setAttribute("label", `${g.set} (${list.length})`);
        for (const s of list) {
          const opt = $el("option", "", s.name);
          opt.value = multi ? `${g.set}/${s.name}` : s.name;
          opt.title = s.description ?? ""; // hover 显示 description 悬浮提示
          parent.appendChild(opt);
        }
        if (multi) skillSel.appendChild(parent);
      }
      skillSel.disabled = false;
      const initial =
        (presetSkill &&
          entries.find((e) => e.name === presetSkill && (!presetSet || e.set === presetSet))) ||
        entries[0];
      skillSel.value = initial.value;
      await onSkillChange();
    } catch (e) {
      if (!state.closed) showModalError(`技能列表加载失败:${e.message}`);
    }
  })();

  return { close, els };
}

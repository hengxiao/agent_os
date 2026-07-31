/* 调试首页(Agent OS Debugger P4,#/debug;计划 §4):
   上卡 = 新调试会话表单(skill 下拉复用 /api/skills、input JSON 编辑器
   复用 launch-dialog 的 schema 校验/骨架,**启动前断点**可预填——
   POST /api/debug/sessions 的 breakpoints 数组在 run 启动前注册,调试启动即断,
   确定性语义见 tests/web/test_debug_api.py 头注);
   下卡 = 活跃会话列表(P3 无列表端点:本页用 localStorage 记下本浏览器开过的会话,
   逐条 GET 快照刷新状态;404 自动清理;「结束」= DELETE 会话 detach 放行)。

   DOM 模式照 launch-dialog.js:表单与列表行都是真实元素(免区域提取),
   页面骨架走 innerHTML;node 冒烟测试经 dom-stub 驱动。 */

import { getJson, postJson } from "../api.js";
import { copy } from "../themes.js";
import { emptyBlock, esc, shortId, shortSkill } from "../util.js";
import { statusPill } from "./status-pill.js";
import { BREAKPOINT_KINDS, matchEditable } from "./breakpoint-list.js";
import {
  skeletonFromSchema,
  summarizeInputs,
  validateAgainstSchema,
} from "./launch-dialog.js";

/* 已知会话 localStorage 键(条目:{session_id, run_id, skill, at};上限 20 条) */
const STORAGE_KEY = "agent-os.debug.sessions";

/* ── 页面私有状态 ─────────────────────────────────────────────── */

const dh = {
  main: null,
  closed: true,
  busy: false,
  schema: null, // 当前技能的 inputs schema(校验/骨架)
  details: new Map(), // 技能名 → 全量 manifest 缓存
  els: null, // { skill, info, input, errors, bpRows, submit, sessions, modalError }
};

/* ── 已知会话(localStorage;node 测试环境无 localStorage 时降级空)── */

function loadKnown() {
  try {
    if (typeof localStorage === "undefined") return [];
    const list = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((x) => x?.session_id) : [];
  } catch {
    return []; // 坏数据按空处理
  }
}

function saveKnown(list) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, 20)));
    }
  } catch {
    /* 存储不可用时静默(隐私模式等):列表仅本次会话可见 */
  }
}

function recordSession(entry) {
  saveKnown([entry, ...loadKnown().filter((x) => x.session_id !== entry.session_id)]);
}

function forgetSession(sessionId) {
  saveKnown(loadKnown().filter((x) => x.session_id !== sessionId));
}

/* ── 入口(app.js 路由分发;幂等)───────────────────────────────── */

export function openDebugHome(main) {
  dh.main = main;
  dh.closed = false;
  renderShell();
  loadSkills();
  refreshSessions();
}

export function closeDebugHome() {
  dh.main = null;
  dh.closed = true;
  dh.busy = false;
  dh.schema = null;
  dh.els = null;
}

/* ── 渲染:骨架 + 新会话表单(真实 DOM)──────────────────────────── */

const $el = (tag, className, text) => {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
};

function renderShell() {
  const main = dh.main;
  if (!main) return;
  main.innerHTML =
    `<div class="dh">` +
    `<div class="card dh-new" id="dhNew"></div>` +
    `<div class="card dh-sessions" id="dhSessions"></div>` +
    `</div>`;
  buildForm(main.querySelector("#dhNew"));
}

function buildForm(card) {
  if (!card) return;
  card.innerHTML = "";
  card.appendChild($el("div", "dh-title", "新调试会话"));

  // Skill 下拉(加载前禁用;option title = description,同 Launch Modal)
  const skillField = $el("label", "field");
  skillField.appendChild($el("span", "field-label", "Skill"));
  const skillSel = $el("select", "input mono dh-skill");
  skillSel.disabled = true;
  skillField.appendChild(skillSel);
  const info = $el("div", "ld-skill-info");

  // Input JSON(schema 骨架 + 实时校验,复用 launch-dialog 纯函数)
  const inputField = $el("label", "field");
  inputField.appendChild($el("span", "field-label", "Input(JSON,按 inputs schema 实时校验)"));
  const inputArea = $el("textarea", "input mono dh-input");
  inputArea.rows = 6;
  inputArea.spellcheck = false;
  inputField.appendChild(inputArea);
  const errors = $el("div", "ld-errors");
  errors.setAttribute("role", "alert");
  errors.hidden = true;

  // 启动前断点(可预填;POST body 的 breakpoints 数组,run 启动前注册)
  const bpHead = $el("div", "dh-bp-head");
  bpHead.appendChild($el("span", "field-label", "启动前断点(可选;run 启动前注册,启动即断)"));
  const bpAdd = $el("button", "btn btn-mini", "＋ 断点");
  bpAdd.type = "button";
  bpHead.appendChild(bpAdd);
  const bpRows = $el("div", "dh-bp-rows");

  const modalError = $el("div", "ld-modal-error");
  modalError.setAttribute("role", "alert");
  modalError.hidden = true;

  const foot = $el("div", "dh-foot");
  const submit = $el("button", "btn btn-primary", copy("home.submit"));
  submit.disabled = true;
  foot.appendChild(submit);

  card.appendChild(skillField);
  card.appendChild(info);
  card.appendChild(inputField);
  card.appendChild(errors);
  card.appendChild(bpHead);
  card.appendChild(bpRows);
  card.appendChild(modalError);
  card.appendChild(foot);

  dh.els = {
    ...(dh.els ?? {}),
    skill: skillSel,
    info,
    input: inputArea,
    errors,
    bpRows,
    submit,
    modalError,
  };

  bpAdd.addEventListener("click", () => addBpRow());
  skillSel.addEventListener("change", onSkillChange);
  inputArea.addEventListener("input", validate);
  submit.addEventListener("click", submitSession);
}

/* 启动前断点行:kind 下拉 + match glob 输入(step/error 忽略 match,禁用)+ 移除钮 */
function addBpRow(kind = "step", match = "") {
  const rows = dh.els?.bpRows;
  if (!rows) return;
  const row = $el("div", "dh-bp-row");
  const sel = $el("select", "input dh-bp-kind");
  sel.setAttribute("aria-label", "断点类型");
  for (const k of BREAKPOINT_KINDS) {
    const opt = $el("option", "", k);
    opt.value = k;
    sel.appendChild(opt);
  }
  sel.value = kind;
  const input = $el("input", "input mono dh-bp-match");
  input.placeholder = "glob,如 system.*";
  input.setAttribute("aria-label", "断点匹配 glob");
  input.value = match;
  input.disabled = !matchEditable(kind);
  sel.addEventListener("change", () => {
    input.disabled = !matchEditable(sel.value);
  });
  const del = $el("button", "icon-btn", "✕");
  del.type = "button";
  del.title = "移除该断点";
  del.addEventListener("click", () => row.remove());
  row.appendChild(sel);
  row.appendChild(input);
  row.appendChild(del);
  rows.appendChild(row);
}

/* 表单 → 启动前断点数组(match 缺省 "*";step/error 恒 "*") */
function collectBreakpoints() {
  // children 是 HTMLCollection,没有 .map——必须展开为数组(否则点击提交即抛
  // TypeError,且发生在 try 之前:POST 不发、按钮卡「启动中…」)
  return [...(dh.els?.bpRows?.children ?? [])].map((row) => {
    const kind = row.querySelector(".dh-bp-kind")?.value ?? "step";
    const raw = row.querySelector(".dh-bp-match")?.value ?? "";
    return { kind, match: matchEditable(kind) ? raw.trim() || "*" : "*" };
  });
}

/* ── 校验:非法 JSON / schema 错误 → 错误条 + 禁用提交(照 Launch Modal)── */

function validate() {
  const els = dh.els;
  if (!els) return { value: null, errors: [] };
  let value = null;
  let errors = [];
  try {
    value = JSON.parse(els.input.value);
  } catch (e) {
    errors = [{ path: "$", message: `非法 JSON:${e.message}` }];
  }
  if (!errors.length && dh.schema) errors = validateAgainstSchema(value, dh.schema);
  els.errors.innerHTML = errors
    .map((er) => `<div class="ld-error"><span class="mono">${esc(er.path)}</span> ${esc(er.message)}</div>`)
    .join("");
  els.errors.hidden = errors.length === 0;
  els.input.classList.toggle("is-invalid", errors.length > 0);
  els.submit.disabled = errors.length > 0 || !els.skill.value || dh.busy;
  return { value, errors };
}

function showModalError(msg) {
  const els = dh.els;
  if (!els) return;
  els.modalError.textContent = msg;
  els.modalError.hidden = !msg;
}

/* ── 技能下拉:/api/skills(POST /api/debug/sessions 不收 skill_set,
      调试会话始终走共享 registry 全局技能)────────────────────────── */

async function loadSkills() {
  const els = dh.els;
  if (!els) return;
  try {
    const skills = await getJson("/api/skills");
    if (dh.closed || dh.els !== els) return;
    const list = (Array.isArray(skills) ? skills : []).filter((s) => s?.name);
    if (!list.length) {
      showModalError("无可用技能(在 agent-os.toml 配置 skills.path)");
      return;
    }
    for (const s of list) {
      const opt = $el("option", "", s.name);
      opt.value = s.name;
      opt.title = s.description ?? "";
      els.skill.appendChild(opt);
    }
    els.skill.disabled = false;
    els.skill.value = list[0].name;
    await onSkillChange();
  } catch (e) {
    if (!dh.closed) showModalError(`技能列表加载失败:${e.message}`);
  }
}

async function onSkillChange() {
  const els = dh.els;
  const name = els?.skill.value;
  dh.schema = null;
  if (!name) {
    if (els) els.info.innerHTML = "";
    validate();
    return;
  }
  els.info.innerHTML = `<div class="ld-skill-desc">加载技能详情…</div>`;
  try {
    if (!dh.details.has(name)) {
      dh.details.set(name, await getJson(`/api/skills/${encodeURIComponent(name)}`));
    }
  } catch {
    dh.details.set(name, null);
  }
  if (dh.closed || dh.els !== els || els.skill.value !== name) return;
  const d = dh.details.get(name);
  if (!d) {
    els.info.innerHTML =
      `<div class="ld-skill-desc ld-warn">schema 加载失败,仅做 JSON 合法性校验</div>`;
    els.input.value = "{}";
    validate();
    return;
  }
  dh.schema = d.inputs ?? null;
  els.skill.title = d.description ?? "";
  const lines = summarizeInputs(dh.schema);
  els.info.innerHTML =
    `<div class="ld-skill-desc">${esc(d.description || "(无描述)")}</div>` +
    (lines.length
      ? `<ul class="ld-skill-inputs">${lines.map((l) => `<li class="mono">${esc(l)}</li>`).join("")}</ul>`
      : "");
  els.input.value = JSON.stringify(skeletonFromSchema(dh.schema), null, 2);
  validate();
}

/* ── 提交:POST /api/debug/sessions(带启动前断点)→ 跳调试台 ──────── */

async function submitSession() {
  const els = dh.els;
  if (!els || dh.busy) return;
  const { value, errors } = validate();
  if (errors.length) return;
  dh.busy = true;
  els.submit.disabled = true;
  els.submit.textContent = copy("home.submitting");
  showModalError("");
  try {
    const breakpoints = collectBreakpoints();
    const body = { skill: els.skill.value, input: value };
    if (breakpoints.length) body.breakpoints = breakpoints; // run 启动前注册(启动即断)
    const res = await postJson("/api/debug/sessions", body);
    if (res?.session_id) {
      recordSession({
        session_id: res.session_id,
        run_id: res.run_id ?? null,
        skill: els.skill.value,
        at: new Date().toISOString(),
      });
      location.hash = `#/debug/${encodeURIComponent(res.session_id)}`;
      return;
    }
    // run 未开始的校验错与 POST /api/runs 同归类:200 + {status:"failed"}
    showModalError(res?.error ?? "会话未开始(未知错误)");
  } catch (e) {
    showModalError(e.message ?? "发起调试会话失败"); // 409 已有活跃会话等
  } finally {
    dh.busy = false;
    // 无条件复位:成功路径已跳转(组件 detach),但 bfcache 回退时旧 DOM 可能重现,
    // 此时按钮不能停在"启动中…";对已 detach 的 els 复位无害。
    els.submit.disabled = false;
    els.submit.textContent = copy("home.submit");
    validate();
  }
}

/* ── 活跃会话列表(localStorage 已知会话 × GET 快照;404 清理)──────── */

function sessionRowPill(doc) {
  const st = doc?.state;
  if (st === "paused" || st === "running") return statusPill(st);
  return (
    `<span class="status-pill" data-status="unknown">` +
    `<span class="pill-dot" aria-hidden="true"></span>` +
    `<span class="pill-label">已结束</span></span>`
  );
}

async function endSession(sessionId) {
  try {
    const res = await fetch(`/api/debug/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    if (!res.ok && res.status !== 404) return; // 失败保留条目,下轮刷新再试
  } catch {
    return;
  }
  forgetSession(sessionId);
  refreshSessions();
}

async function refreshSessions() {
  const card = dh.main?.querySelector("#dhSessions");
  if (!card) return;
  if (dh.els) dh.els.sessions = card; // 原地赋值:不替换对象(loadSkills 持引用做切页守卫)
  card.innerHTML = "";
  card.appendChild($el("div", "dh-title", "活跃会话"));
  const known = loadKnown();
  if (!known.length) {
    const empty = $el("div", "dh-sess-empty");
    empty.innerHTML = emptyBlock(
      copy("home.sessions.empty"),
      copy("home.sessions.empty.hint"),
      "terminal");
    card.appendChild(empty);
    return;
  }
  const docs = await Promise.all(
    known.map(async (k) => {
      try {
        const doc = await getJson(`/api/debug/sessions/${encodeURIComponent(k.session_id)}`);
        return { k, doc };
      } catch (e) {
        if (e?.status === 404) forgetSession(k.session_id); // 已清理的会话自动摘除
        return { k, doc: null };
      }
    }));
  if (dh.closed) return;
  for (const { k, doc } of docs) {
    if (!doc) continue; // 404 已摘除,不再渲染
    const row = $el("div", "dh-sess");
    row.dataset.sessionId = k.session_id;
    const pill = $el("span", "dh-sess-pill");
    pill.innerHTML = sessionRowPill(doc);
    const label = $el("span", "dh-sess-label");
    label.textContent = `${shortSkill(k.skill)} · #${shortId(k.session_id)}`;
    label.title = `${k.skill ?? ""} · ${k.session_id}`;
    row.appendChild(pill);
    row.appendChild(label);
    if (doc.run_id) {
      const run = $el("a", "dh-sess-run mono", `run ${shortId(doc.run_id)}`);
      run.href = `#/runs/${encodeURIComponent(doc.run_id)}`;
      run.title = `run_id: ${doc.run_id}`;
      row.appendChild(run);
    }
    const open = $el("a", "btn btn-mini", "打开");
    open.href = `#/debug/${encodeURIComponent(k.session_id)}`;
    row.appendChild(open);
    if (doc.state !== "detached") {
      const end = $el("button", "btn btn-mini", "结束");
      end.title = "DELETE 会话:detach 放行,run 继续跑完";
      end.addEventListener("click", () => endSession(k.session_id));
      row.appendChild(end);
    }
    card.appendChild(row);
  }
}

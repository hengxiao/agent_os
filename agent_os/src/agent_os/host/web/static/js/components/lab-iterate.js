/* Lab 迭代模式(docs/LAB-ITERATION.md §4 + docs/LAB-ITERATION-FLOWS.md Flow C 样板):
   双栏 diff + 内嵌边注。左栏 = working 结构化只读渲染(成员分节,每单元 💬 锚点,
   点击弹边注小框;已存边注显示为内容旁的边注卡);右栏 = 空态 → ✨生成 →
   diff 视图(prompt 红绿增删、字段新旧两列、tests 新增绿色)+ 接受/放弃/再生成。
   顶栏 = 包名 + tier 徽标 + 版本下拉(rewind 两击确认)+ 回专家模式。

   与专家模式(lab.js)并存,不改其任何交互;路由 #/lab/<name>/iterate。

   纯函数(不碰 DOM,node 单测可载):
     noteCardHtml(note)             边注卡(锚点标签 + 文本)
     memberSectionsHtml(doc)        成员分节渲染(description/io/prompt 段/permissions/trust/tests)
     diffViewHtml(diff)             diff 视图(字段新旧两列 / 行级红绿 / tests 绿色)
     anchorOf(el)                   事件层从 DOM 元素取锚点对象 */

import { getJson, postJson } from "../api.js";
import { copy } from "../themes.js";
import { esc, toast } from "../util.js";
import { mountBubble } from "../widgets/index.js";
import { tierBadgeHtml } from "./lab.js";

/* ── 纯函数 ─────────────────────────────────────────────────── */

const _para = (text) =>
  String(text ?? "").split(/\n{2,}|\n(?=\S)/).map((s) => s.trim()).filter(Boolean);

/* 边注卡(锚点标签 + 文本;挂在内容旁,Flow C 的 margin note) */
export function noteCardHtml(note) {
  const a = note.anchor ?? {};
  const to = [a.member, a.path].filter(Boolean).join(" · ");
  return (
    `<div class="it-note"><span class="it-note-to mono">${esc(to)}</span>` +
    `<span class="it-note-tx">${esc(note.text ?? "")}</span></div>`
  );
}

/* 一个锚点单元:内容 + 💬 按钮 + 该单元已挂的边注 */
function _unit(kind, path, member, title, bodyHtml, notes, span = null) {
  const anchor = { member, kind, path, ...(span ? { span } : {}) };
  const key = JSON.stringify(anchor);
  const attached = notes
    .filter((n) => JSON.stringify(n.anchor ?? {}) === key)
    .map(noteCardHtml)
    .join("");
  return (
    `<div class="it-unit" data-anchor='${esc(key)}'>` +
    `<div class="it-sec">` +
    (title
      ? `<h4>${esc(title)}<button class="it-note-btn" data-it-note title="${esc(copy("it.note.ph"))}" aria-label="${esc(copy("it.note.ph"))}">💬</button></h4>`
      : "") +
    bodyHtml +
    `</div>` +
    attached +
    `</div>`
  );
}

/* 成员分节渲染(只读):description / inputs·outputs / prompt 段落(span 锚)/
   permissions / trust / tests(case 锚) */
export function memberSectionsHtml(doc, notes) {
  const m = doc.manifest ?? {};
  const parts = [];
  const member = doc.name;
  parts.push(
    _unit("field", "description", member, "manifest.description",
      `<div class="it-kv">${esc(m.description ?? "")}</div>`, notes)
  );
  parts.push(
    _unit("field", "inputs/outputs", member, "inputs / outputs",
      `<div class="it-kv mono">inputs: ${esc(JSON.stringify(m.inputs ?? {}))}\n` +
      `outputs: ${esc(JSON.stringify(m.outputs ?? {}))}</div>`, notes)
  );
  const paras = _para(doc.prompt);
  const paraHtml = paras
    .map((p, i) => {
      const span = { start: i, end: i };
      const anchorKey = JSON.stringify({ member, kind: "span", path: "prompt", span });
      const attached = notes
        .filter((n) => JSON.stringify(n.anchor ?? {}) === anchorKey)
        .map(noteCardHtml)
        .join("");
      return (
        `<div class="it-para" data-anchor='${esc(anchorKey)}'>${esc(p)}` +
        `<button class="it-note-btn" data-it-note aria-label="${esc(copy("it.note.ph"))}">💬</button>${attached}</div>`
      );
    })
    .join("");
  parts.push(
    _unit("field", "prompt", member, "prompt(段落可批)", paraHtml, notes)
  );
  const perms = m.permissions ?? {};
  parts.push(
    _unit("field", "permissions", member, "permissions",
      `<div class="it-kv mono">tools: ${esc(JSON.stringify(perms.tools ?? []))}\n` +
      `skills: ${esc(JSON.stringify(perms.skills ?? []))}</div>`, notes)
  );
  if (m.trust || m.inline !== undefined) {
    parts.push(
      _unit("field", "trust", member, "trust",
        `<div class="it-kv mono">${esc(JSON.stringify(m.trust ?? {}))}</div>`, notes)
    );
  }
  const tests = (doc.tests ?? [])
    .map((t) => {
      const anchorKey = JSON.stringify({ member, kind: "case", path: `tests/${t}` });
      const attached = notes
        .filter((n) => JSON.stringify(n.anchor ?? {}) === anchorKey)
        .map(noteCardHtml)
        .join("");
      return (
        `<div class="it-case" data-anchor='${esc(anchorKey)}'>` +
        `<span class="mono">${esc(t)}</span>` +
        `<button class="it-note-btn" data-it-note aria-label="${esc(copy("it.note.ph"))}">💬</button>${attached}</div>`
      );
    })
    .join("");
  parts.push(_unit("field", "tests", member, "tests", tests || `<span class="it-kv">(空)</span>`, notes));
  return parts.join("");
}

const _val = (v) => esc(JSON.stringify(v) ?? "—");

/* diff 视图(Flow C 右栏):字段新旧两列 / prompt 红绿行 / tests 新增绿色 */
export function diffViewHtml(diff) {
  if (!diff) return "";
  if (!diff.has_changes) {
    return `<div class="it-wait">${esc(copy("it.no.changes"))}</div>`;
  }
  return (diff.members ?? [])
    .map((m) => {
      const fields = (m.fields ?? [])
        .map(
          (f) =>
            `<div class="it-twocol" data-kind="${esc(f.kind)}">` +
            `<div>${_val(f.old)}</div><div>${_val(f.new)}</div></div>`
        )
        .join("");
      const lines = (m.prompt_diff ?? [])
        .filter((d) => d.kind !== "same")
        .map(
          (d) =>
            `<div class="it-dline" data-kind="${esc(d.kind)}">` +
            `${d.kind === "add" ? "+" : "-"} ${esc(d.text)}</div>`
        )
        .join("");
      const tests = (m.tests?.added ?? [])
        .map((t) => `<div class="it-dline" data-kind="add">+ ${esc(t)}</div>`)
        .join("");
      return (
        `<div class="it-diff-member" data-status="${esc(m.status)}">` +
        `<div class="it-diff-head mono">${esc(m.member)}` +
        `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span></div>` +
        (fields ? `<div class="it-diff-sec">fields</div>${fields}` : "") +
        (lines ? `<div class="it-diff-sec">prompt</div>${lines}` : "") +
        (tests ? `<div class="it-diff-sec">tests</div>${tests}` : "") +
        `</div>`
      );
    })
    .join("");
}

/* ── 页面(DOM)─────────────────────────────────────────────── */

let it = null;

const _notes = () => [...(it.savedComments ?? []), ...(it.notes ?? [])];

async function _loadAll() {
  const name = it.name;
  const [closure, comments, versions] = await Promise.all([
    getJson(`/api/lab/packages/${encodeURIComponent(name)}/closure`),
    getJson(`/api/lab/drafts/${encodeURIComponent(name)}/comments`).catch(() => ({ comments: [] })),
    getJson(`/api/lab/drafts/${encodeURIComponent(name)}/versions`).catch(() => []),
  ]);
  it.closure = closure;
  it.tier = closure.root_tier ?? "none";
  it.versions = Array.isArray(versions) ? versions : [];
  it.savedComments = comments.comments ?? [];
  it.docs = {};
  for (const m of closure.members ?? []) {
    if (m.status !== "draft") continue;
    it.docs[m.name] = await getJson(`/api/lab/drafts/${encodeURIComponent(m.name)}`);
  }
}

function _renderTop() {
  const vers = (it.versions ?? [])
    .map((v, i) => `<option value="${esc(v.version)}"${i === 0 ? " selected" : ""}>${esc(v.version)}</option>`)
    .join("");
  it.root.querySelector(".it-top").innerHTML =
    `<span class="it-pkg mono">${esc(it.name)}</span>` +
    tierBadgeHtml(it.tier ?? "none") +
    `<select class="input it-ver" data-it="version" aria-label="${esc(copy("it.versions"))}">${vers || `<option value="">(无版本)</option>`}</select>` +
    `<button class="btn" data-it="rewind"${it.rewindArmed ? ' data-armed="1"' : ""}>` +
    `${esc(copy(it.rewindArmed ? "it.rewind.confirm" : "it.rewind"))}</button>` +
    `<span style="flex:1"></span>` +
    `<a class="btn" href="#/lab/${encodeURIComponent(it.name)}">${esc(copy("it.expert"))}</a>`;
}

function _renderLeft() {
  const host = it.root.querySelector(".it-left");
  const notes = _notes();
  host.innerHTML = (it.closure?.members ?? [])
    .filter((m) => m.status === "draft")
    .map((m) => {
      const doc = it.docs[m.name];
      if (!doc) return "";
      return (
        `<div class="it-member"><div class="it-member-name mono">${esc(m.name)}</div>` +
        memberSectionsHtml({ ...doc, name: m.name }, notes) +
        `</div>`
      );
    })
    .join("");
}

function _renderRight() {
  const host = it.root.querySelector(".it-right");
  if (it.error) {
    host.innerHTML =
      `<div class="it-wait it-error">${esc(it.error)}</div>` +
      `<button class="btn btn-primary" data-it="generate">${esc(copy("it.generate"))}</button>`;
    return;
  }
  if (it.busy) {
    host.innerHTML = `<div class="it-wait">${esc(copy("it.busy"))}</div>`;
    return;
  }
  if (!it.diff) {
    host.innerHTML =
      `<div class="it-wait">${esc(copy("it.empty"))}</div>` +
      `<textarea class="input" rows="3" data-it="note" placeholder="${esc(copy("it.note.ph"))}">${esc(it.note ?? "")}</textarea>` +
      `<button class="btn btn-primary it-gen" data-it="generate">${esc(copy("it.generate"))}</button>`;
    return;
  }
  host.innerHTML =
    (it.reply ? `<div class="it-reply">${esc(it.reply)}</div>` : "") +
    diffViewHtml(it.diff) +
    `<div class="it-actions">` +
    `<button class="btn btn-primary" data-it="accept">${esc(copy("it.accept"))}</button>` +
    `<button class="btn" data-it="discard">${esc(copy("it.discard"))}</button>` +
    `<button class="btn" data-it="generate">${esc(copy("it.regen"))}</button>` +
    `</div>`;
}

function _renderAll() {
  _renderTop();
  _renderLeft();
  _renderRight();
}

async function _generate() {
  it.busy = true;
  it.error = null;
  _renderRight();
  try {
    const body = await postJson(`/api/lab/drafts/${encodeURIComponent(it.name)}/iterate`, {
      comments: it.notes,
      note: it.note ?? "",
    });
    it.diff = body.diff;
    it.reply = body.reply ?? "";
    it.notes = []; // 已随本轮落盘(服务端 comments/<round>.json)
    const comments = await getJson(`/api/lab/drafts/${encodeURIComponent(it.name)}/comments`)
      .catch(() => ({ comments: [] }));
    it.savedComments = comments.comments ?? [];
  } catch (e) {
    it.error = e.message ?? String(e);
  } finally {
    it.busy = false;
    _renderAll();
  }
}

async function _accept() {
  const result = await postJson(`/api/lab/drafts/${encodeURIComponent(it.name)}/candidate/accept`);
  it.diff = null;
  it.reply = null;
  toast(`${copy("it.accepted")} ${result.version}`, "success");
  await _loadAll();
  _renderAll();
}

async function _discard() {
  await postJson(`/api/lab/drafts/${encodeURIComponent(it.name)}/candidate/discard`);
  it.diff = null;
  it.reply = null;
  _renderRight();
}

/* W2 锚点路径(§14):/lab/iterate/member/<m>/<kind>/<path>[/span/<i>] */
function _anchorPath(anchor) {
  const base = `/lab/iterate/member/${anchor.member}/${anchor.kind}/${anchor.path}`;
  return anchor.span ? `${base}/span/${anchor.span.start}` : base;
}

/* W2 §16:cascade 各级 fragment 提供者(每级只出自己的;widget 级 =
   span/段落/全文,app 级 = 成员与草稿状态) */
function _iterateProviders(anchor) {
  return [
    {
      prefix: `/lab/iterate/member/${anchor.member}`,
      scope: "widget",
      fn: () => {
        const doc = it.docs?.[anchor.member] ?? {};
        const paras = _para(doc.prompt ?? "");
        return {
          span: anchor.span ?? null,
          paragraph: anchor.span ? (paras[anchor.span.start] ?? "") : "",
          full_text: anchor.kind === "span" || anchor.path === "prompt"
            ? (doc.prompt ?? "")
            : JSON.stringify((doc.manifest ?? {})[anchor.path] ?? ""),
        };
      },
    },
    {
      prefix: "/lab/iterate",
      scope: "app",
      fn: () => ({ draft: it.name, member: anchor.member, tier: it.tier, note: it.note }),
    },
  ];
}

function _bind() {
  it.root.addEventListener("click", async (e) => {
    const act = e.target.closest("[data-it]")?.dataset.it;
    try {
      if (act === "generate") return await _generate();
      if (act === "accept") return await _accept();
      if (act === "discard") return await _discard();
      if (act === "rewind") {
        const btn = e.target.closest("[data-it='rewind']");
        if (btn.dataset.armed !== "1") {
          // 两击确认(rewind 覆盖 working;历史不动)
          btn.dataset.armed = "1";
          btn.textContent = copy("it.rewind.confirm");
          return;
        }
        const version = it.root.querySelector("[data-it='version']")?.value;
        if (!version) return;
        await postJson(`/api/lab/drafts/${encodeURIComponent(it.name)}/rewind`, { version });
        toast(`${copy("it.rewind.done")} ${version}`, "success");
        await _loadAll();
        return _renderAll();
      }
    } catch (err) {
      toast(err.message ?? String(err), "error");
    }
    // 边注气泡(W2,docs/WIDGETS.md W-bubble;锚点单元的 💬):
    // 气泡 = 锚点引用行 + 消息流 + 输入框;submit 经 §16 cascade 组装信封,
    // 出海(POST comment)在本组件(父级),回复进气泡;apply → 边注(不越权)
    const noteBtn = e.target.closest("[data-it-note]");
    if (noteBtn) {
      const unit = noteBtn.closest("[data-anchor]");
      if (!unit || unit.querySelector(".w-bubble") || unit.querySelector(".it-note-form")) return;
      const anchor = JSON.parse(unit.dataset.anchor);
      // 既有边注数据兼容:该锚点已挂的 notes 作种子消息
      const key = unit.dataset.anchor;
      const seed = [...it.notes, ...it.savedComments]
        .filter((n) => JSON.stringify(n.anchor ?? {}) === key)
        .map((n) => ({ role: "user", text: n.text, ts: n.at ?? 0 }));
      const host = document.createElement("div");
      unit.appendChild(host);
      const bubble = mountBubble(host, {
        anchor, // 原样(与单元 data-anchor 同构,apply 落边注时锚键一致)
        triggerPath: _anchorPath(anchor), // cascade 的 §14 触发路径(独立字段,不污染锚)
        seedMessages: seed,
        cascadeProviders: _iterateProviders(anchor),
      });
      bubble.on("submit", async ({ anchor: a, text, cascade }) => {
        try {
          const body = await postJson(`/api/lab/drafts/${encodeURIComponent(it.name)}/comment`, {
            anchor: a, text, cascade: cascade.cascade,
          });
          bubble.receiveReply(body.reply ?? "");
        } catch (err) {
          bubble.receiveReply(`(助手暂不可用: ${err.message ?? err})`);
        }
      });
      bubble.on("apply", ({ anchor: a, text }) => {
        // apply_reply 只发事件:采纳为边注由父组件决定(气泡不越权)
        it.notes.push({ anchor: a, text, at: Date.now() / 1000 });
        _renderLeft();
      });
      bubble.focus();
    }
  });
  it.root.addEventListener("input", (e) => {
    if (e.target.closest("[data-it='note']")) it.note = e.target.value;
  });
}

export function openIterate(main, name) {
  closeIterate();
  it = {
    name,
    tier: "none",
    closure: null,
    docs: {},
    notes: [],
    savedComments: [],
    note: "",
    diff: null,
    reply: "",
    versions: [],
    busy: false,
    error: null,
    rewindArmed: false,
    root: document.createElement("div"),
  };
  it.root.className = "it";
  it.root.innerHTML =
    `<div class="it-top"></div>` +
    `<div class="it-cols"><div class="it-left"></div><div class="it-right"></div></div>`;
  main.appendChild(it.root);
  _bind();
  _renderRight();
  (async () => {
    await _loadAll();
    _renderAll();
  })().catch((e) => toast(e.message ?? String(e), "error"));
  return { root: it.root };
}

export function closeIterate() {
  it?.root?.remove();
  it = null;
}

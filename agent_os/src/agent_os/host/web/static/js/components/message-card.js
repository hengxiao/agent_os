/* 上下文检视器消息卡片(docs/WEB-UI.md §4.2 右栏 420px):按 role 渲染气泡、
   tool_call/tool_result 成对渲染(§4.2 规则 3)、reasoning 折叠、
   veto/纠偏 Banner 嵌入、逐条复制、大消息 head+tail 折叠。

   纯函数(不碰 DOM,node 单测可载):
     pairMessages(messages)                  §4.2 规则 3:messages → renderUnits
     parseToolResult(message)                tool 消息内容 → { ok, errorKind, pretty }
     focusMessageIndex(signal, step, msgs)   时间线选中 → 应滚动到的消息下标(联动映射)
   renderMessages / renderUnit 返回 HTML 字符串;DOM 接线在 workbench。 */

import { COPY_SVG, esc } from "../util.js";
import { banner } from "./banner.js";

/* 大消息阈值(§6.3:>20k 字符折叠 head+tail) */
const BIG_LEN = 20_000;
const BIG_HEAD = 8_000;
const BIG_TAIL = 4_000;
/* reasoning 折叠一行摘要长度(§4.2 规则 3:前 80 字符) */
const REASONING_SUMMARY = 80;

/* ── §4.2 规则 3:pairMessages ───────────────────────────────────
   assistant 的 tool_calls 与其 TOOL 消息合并为一张卡片:
     { kind: "pair", call, callIndex, results: [{ message, index }], index }
   其余消息(含配不到对的孤儿 tool result,容错)原样成单元:
     { kind: "message", message, index, orphan? }
   配对优先 tool_call_id 精确匹配;缺 id 时按"最近一个尚有同名未满足 call 的 pair"兜底。 */
export function pairMessages(messages) {
  const units = [];
  const byCallId = new Map(); // tool_call_id -> pair unit
  (Array.isArray(messages) ? messages : []).forEach((m, index) => {
    if (!m || typeof m !== "object") return;
    const role = m.role ?? "unknown";
    if (role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) {
      const unit = { kind: "pair", call: m, callIndex: index, results: [], index };
      unit._pending = new Map(); // name -> 未满足 call 数(id 缺失兜底用)
      for (const tc of m.tool_calls) {
        if (tc?.id) byCallId.set(tc.id, unit);
        const n = tc?.name ?? "";
        unit._pending.set(n, (unit._pending.get(n) ?? 0) + 1);
      }
      units.push(unit);
      return;
    }
    if (role === "tool") {
      let unit = m.tool_call_id ? byCallId.get(m.tool_call_id) : undefined;
      if (!unit) {
        // id 缺失/失配兜底:从后往前找同名 call 仍未满足的 pair
        for (let i = units.length - 1; i >= 0; i -= 1) {
          const u = units[i];
          if (u.kind !== "pair" || (u._pending.get(m.name ?? "") ?? 0) <= 0) continue;
          unit = u;
          break;
        }
      }
      if (unit) {
        unit._pending.set(m.name ?? "", (unit._pending.get(m.name ?? "") ?? 1) - 1);
        unit.results.push({ message: m, index });
      } else {
        units.push({ kind: "message", message: m, index, orphan: true }); // 孤儿容错
      }
      return;
    }
    units.push({ kind: "message", message: m, index });
  });
  for (const u of units) delete u._pending; // 输出保持纯数据(可 deepEqual)
  return units;
}

/* tool 消息内容解析:内核 tool_result 为 JSON 串 {"ok","value","error":{"kind","message"}}。
   非 JSON 内容按纯文本处理(ok=null)。 */
export function parseToolResult(message) {
  const raw = String(message?.content ?? "");
  try {
    const doc = JSON.parse(raw);
    if (doc && typeof doc === "object" && "ok" in doc) {
      return {
        ok: doc.ok === true,
        errorKind: doc.error?.kind ?? null,
        errorMessage: doc.error?.message ?? null,
        pretty: JSON.stringify(doc.ok ? (doc.value ?? null) : (doc.error ?? doc), null, 2),
      };
    }
  } catch {
    /* 纯文本结果 */
  }
  return { ok: null, errorKind: null, errorMessage: null, pretty: raw };
}

/* ── 时间线选中 → 消息下标(§4.2 规则 1 的检视器侧映射)────────────
   单帧 loop 每步恰好追加一条 assistant:step N → 第 N 条 assistant;
   tool.call 信号进一步定位到该 assistant 之后、下一条 assistant 之前的同名 tool 消息;
   无 step(帧边界组):frame.pop → 末条,其余 → 首条。 */
export function focusMessageIndex(signal, step, messages) {
  const msgs = Array.isArray(messages) ? messages : [];
  if (!msgs.length || !signal) return null;
  const s = typeof step === "number" && step >= 1 ? step : null;
  if (s == null) return String(signal.name ?? "").endsWith("frame.pop") ? msgs.length - 1 : 0;
  const assistants = msgs.flatMap((m, i) => (m?.role === "assistant" ? [i] : []));
  const ai = assistants[s - 1];
  if (ai == null) return null;
  if (String(signal.name ?? "").includes("tool.call")) {
    const tool = signal.payload?.tool;
    for (let i = ai + 1; i < msgs.length; i += 1) {
      const m = msgs[i];
      if (m?.role === "assistant") break;
      if (m?.role === "tool" && (!tool || m.name === tool)) return i;
    }
  }
  return ai;
}

/* ── 渲染 ─────────────────────────────────────────────────────── */

const ROLE_LABELS = { system: "system", user: "user", assistant: "assistant", tool: "tool" };

const copyBtn = (msgIndex, label) =>
  `<button class="copy-btn" data-action="wb-copy-msg" data-msg-index="${msgIndex}"` +
  ` data-tip="${esc(label)}" aria-label="${esc(label)}">${COPY_SVG}</button>`;

/* 内容体:JSON 可解析则 pretty;>20k 折叠 head+tail(§6.3);fold=true 时整体 <details> */
function contentHtml(raw, { fold = false, prettyJson = false } = {}) {
  let text = String(raw ?? "");
  if (prettyJson) {
    try {
      text = JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      /* 非 JSON,原样展示 */
    }
  }
  let body;
  if (text.length > BIG_LEN) {
    const omitted = text.length - BIG_HEAD - BIG_TAIL;
    body =
      `<pre class="msg-pre">${esc(text.slice(0, BIG_HEAD))}</pre>` +
      `<details class="json-fold msg-big"><summary>… 省略 ${omitted} 字符,点击展开全文 …</summary>` +
      `<pre class="msg-pre">${esc(text)}</pre></details>` +
      `<pre class="msg-pre">${esc(text.slice(text.length - BIG_TAIL))}</pre>`;
  } else {
    body = `<pre class="msg-pre">${esc(text)}</pre>`;
  }
  if (!fold) return body;
  return `<details class="json-fold"><summary>内容(${text.length} 字符)</summary>${body}</details>`;
}

/* reasoning:默认折叠一行摘要(前 80 字符),点击展开(§4.2 规则 3) */
function reasoningHtml(reasoning) {
  const r = String(reasoning ?? "").trim();
  if (!r) return "";
  const summary = r.length > REASONING_SUMMARY ? `${r.slice(0, REASONING_SUMMARY)}…` : r;
  return (
    `<details class="msg-reasoning">` +
    `<summary><span class="reasoning-label">reasoning</span> ${esc(summary)}</summary>` +
    `<pre class="msg-pre">${esc(r)}</pre>` +
    `</details>`
  );
}

function msgHead(role, source, msgIndex, extraClass = "") {
  return (
    `<div class="msg-head${extraClass}">` +
    `<span class="msg-role">${esc(ROLE_LABELS[role] ?? role)}</span>` +
    (source ? `<span class="msg-src">${esc(source)}</span>` : "") +
    copyBtn(msgIndex, "复制该消息原文") +
    `</div>`
  );
}

/* veto/纠偏检测:veto = tool 结果 error.kind === "vetoed"(红);
   纠偏 = source === "injected"(reviewer 打回 / sidecar 注入,黄)。 */
const isInjected = (m) => m?.source === "injected";

function pairHtml(unit) {
  const m = unit.call;
  const calls = (m.tool_calls ?? [])
    .map((tc, i) => {
      let args;
      try {
        args = JSON.stringify(tc?.args ?? {}, null, 2);
      } catch {
        args = String(tc?.args);
      }
      return (
        `<div class="tc-call">` +
        `<span class="tc-name">${esc(tc?.name ?? "?")}</span>` +
        `<span class="tc-seq">#${i + 1}</span>` +
        `<details class="json-fold tc-args"><summary>参数 JSON</summary>` +
        `<pre class="msg-pre">${esc(args)}</pre></details>` +
        `</div>`
      );
    })
    .join("");
  const results = unit.results
    .map(({ message: tm, index }) => {
      const r = parseToolResult(tm);
      const okBadge =
        r.ok === true
          ? `<span class="tc-ok" data-ok="true">✓ ok</span>`
          : r.ok === false
            ? `<span class="tc-ok" data-ok="false">✗ ${esc(r.errorKind ?? "error")}</span>`
            : `<span class="tc-ok" data-ok="unknown">结果</span>`;
      const vetoBanner =
        r.errorKind === "vetoed" ? banner("danger", "vetoed — 调用被 sidecar 否决", esc(r.errorMessage ?? "")) : "";
      return (
        `<div class="tc-result" data-ok="${r.ok === true ? "true" : r.ok === false ? "false" : "unknown"}"` +
        ` data-msg-index="${index}">` +
        `<div class="tc-result-head">${okBadge}` +
        `<span class="tc-tool-name">${esc(tm.name ?? "")}</span>` +
        copyBtn(index, "复制该结果原文") +
        `</div>` +
        vetoBanner +
        contentHtml(String(tm.content ?? ""), { fold: true, prettyJson: true }) +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="msg msg-pair" data-msg-index="${unit.callIndex}">` +
    msgHead("assistant", m.source, unit.callIndex) +
    reasoningHtml(m.reasoning) +
    (String(m.content ?? "").trim() ? contentHtml(m.content) : "") +
    `<div class="tc-calls">${calls}</div>` +
    (results ? `<div class="tc-results">${results}</div>` : "") +
    `</div>`
  );
}

function messageHtml(unit) {
  const m = unit.message;
  const role = m.role ?? "unknown";
  const injected = isInjected(m);
  const body =
    role === "tool"
      ? (() => {
          const r = parseToolResult(m);
          const vetoBanner =
            r.errorKind === "vetoed"
              ? banner("danger", "vetoed — 调用被 sidecar 否决", esc(r.errorMessage ?? ""))
              : "";
          return vetoBanner + contentHtml(String(m.content ?? ""), { prettyJson: true });
        })()
      : contentHtml(m.content);
  return (
    `<div class="msg msg-${esc(role)}" data-msg-index="${unit.index}"` +
    `${unit.orphan ? ` data-orphan="true" title="未配对的 tool result(容错显示)"` : ""}` +
    `${injected ? ` data-injected="true"` : ""}>` +
    (injected ? banner("warn", "纠偏观察(注入消息)", "") : "") +
    msgHead(role, m.source, unit.index) +
    reasoningHtml(m.reasoning) +
    body +
    `</div>`
  );
}

export function renderUnit(unit) {
  return unit.kind === "pair" ? pairHtml(unit) : messageHtml(unit);
}

/* 帧消息流整体 HTML。 */
export function renderMessages(messages) {
  const units = pairMessages(messages);
  if (!units.length) return "";
  return `<div class="msg-list">${units.map(renderUnit).join("")}</div>`;
}

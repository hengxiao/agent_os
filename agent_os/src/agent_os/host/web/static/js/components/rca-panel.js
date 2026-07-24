/* RCA 模式(WEB-UI.md §4.4):异常 run 的一键定位动线规划 + veto 归因卡片 + 异常 Banner。

   纯函数(不碰 DOM,node 单测可载):
     planRcaJump(rca, frames, signals)  定位动线规划 → { frameId, signalIndex, messageIndex, step, reason }
     rcaBannerHtml(status, error)       顶部 Banner(status + error 摘要 + 定位/Resume 按钮)
     vetoCardHtml(firstError)           veto 归因卡片(裁决来源 / 理由全文 / 被否决参数 JSON 折叠+复制)
   动线执行(取数 / selection / 滚动 / 脉冲)由 workbench 承担。

   定位策略(§4.4 "定位首个错误",与后端 rca.py 的检测顺序同源):
     vetoed     → 该帧第一个被否决的 pre:tool.call(veto 不发 post:tool.call,§5.2,
                  经 timeline.findVetoedCalls 推断,优先匹配 call.name);
     tool_error → 该帧第一个 ok:false 的 post:tool.call(优先匹配 call.name);
     aborted    → 该帧最后一个信号(断电/中止无显式错误信号);
     以上都无对应信号 → 回退该帧最后一个信号。
   messageIndex 恒为 null:消息下标依赖帧上下文(懒加载),由检视器在帧就绪后经
   focusMessageIndex + 出错卡片(data-ok="false")定位补算;此处只保留字段形状。 */

import { COPY_SVG, esc } from "../util.js";
import { findVetoedCalls, groupSignals } from "./timeline.js";

/* ── planRcaJump ──────────────────────────────────────────────
   rca     = GET /api/runs/{id}/rca → { status, first_error | null }
   frames  = detail.frames(帧摘要,校验 frame_id 存在性)
   signals = run 信号流(定位时间线信号)
   返回 { frameId, signalIndex, messageIndex, step, reason };
   不可定位时 frameId/signalIndex 为 null,reason ∈
   "no-error"(first_error 为空)| "frame-missing"(帧不在摘要中)。 */
export function planRcaJump(rca, frames, signals) {
  const fe = rca?.first_error ?? null;
  const none = (reason) => ({ frameId: null, signalIndex: null, messageIndex: null, step: null, reason });
  if (!fe) return none("no-error");
  const frameId = (Array.isArray(frames) ? frames : []).some((f) => f?.frame_id === fe.frame_id)
    ? fe.frame_id
    : null;
  if (!frameId) return none("frame-missing");

  const rows = Array.isArray(signals) ? signals : [];
  const inFrame = (s) => (s?.frame_id ?? null) === frameId;
  const toolName = fe.call?.name ?? null;
  let signalIndex = null;

  if (fe.kind === "vetoed") {
    const vetoed = findVetoedCalls(rows); // 被否决 pre:tool.call 下标集(§5.2 推断)
    const cands = [...vetoed]
      .filter((i) => inFrame(rows[i]))
      .sort((a, b) => a - b);
    const named = toolName ? cands.filter((i) => rows[i]?.payload?.tool === toolName) : [];
    signalIndex = named[0] ?? cands[0] ?? null; // 首个错误:取下标最小者
  } else if (fe.kind === "tool_error") {
    const failed = rows.flatMap((s, i) =>
      inFrame(s) && s?.name === "post:tool.call" && s?.payload?.ok === false ? [i] : []);
    const named = toolName ? failed.filter((i) => rows[i]?.payload?.tool === toolName) : [];
    signalIndex = named[0] ?? failed[0] ?? null;
  }

  if (signalIndex == null) {
    // aborted / 无对应信号回退:该帧最后一个信号(§4.4 动线第二步兜底)
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      if (inFrame(rows[i])) {
        signalIndex = i;
        break;
      }
    }
  }

  /* 信号所在组的 step(检视器 focusMessageIndex 映射用);组折叠态与本规划无关 */
  let step = null;
  if (signalIndex != null) {
    const g = groupSignals(rows).find((gr) => gr.items.some((it) => it.index === signalIndex));
    step = g?.step ?? null;
  }
  return { frameId, signalIndex, messageIndex: null, step, reason: null };
}

/* ── 异常 Banner(§4.4:status + error 摘要 + 定位首个错误 ⌘J + Resume)── */

const ERROR_SUMMARY = 160;

export function rcaBannerHtml(status, error) {
  const tone = status === "aborted" ? "aborted" : "danger";
  const icon = status === "aborted" ? "■" : "✖";
  const full = String(error ?? (status === "aborted" ? "已中止" : "未知错误"));
  const summary = full.length > ERROR_SUMMARY ? `${full.slice(0, ERROR_SUMMARY)}…` : full;
  return (
    `<div class="banner rca-banner" data-tone="${tone}" role="alert">` +
    `<span class="banner-icon" aria-hidden="true">${icon}</span>` +
    `<div class="banner-main">` +
    `<span class="banner-title">run ${esc(status)} — <span title="${esc(full)}">${esc(summary)}</span></span>` +
    `</div>` +
    `<div class="rca-actions">` +
    `<button class="copy-btn" data-action="copy" data-copy="${esc(full)}"` +
    ` data-copy-label="已复制错误全文" data-tip="复制错误全文" aria-label="复制错误全文">${COPY_SVG}</button>` +
    `<button class="btn" data-action="wb-rca-jump" data-tip="定位首个错误(⌘J)">定位首个错误 ⌘J</button>` +
    `<button class="btn" data-action="wb-resume" data-tip="从 checkpoint 恢复运行">Resume ▶</button>` +
    `</div></div>`
  );
}

/* ── veto 归因卡片(§4.4:裁决来源 kind / 理由全文 message /
      被否决的调用参数 JSON 折叠 + 复制)───────────────────────── */

export function vetoCardHtml(firstError) {
  const fe = firstError ?? {};
  let argsText = "null";
  try {
    argsText = JSON.stringify(fe.call?.args ?? null, null, 2);
  } catch {
    argsText = String(fe.call?.args);
  }
  return (
    `<div class="rca-veto" data-kind="${esc(fe.kind ?? "")}" role="note">` +
    `<div class="rca-veto-head">` +
    `<span class="rca-veto-badge">veto 归因</span>` +
    `<span class="rca-veto-src">裁决来源:<b class="mono">${esc(fe.kind ?? "—")}</b></span>` +
    (fe.skill ? `<span class="rca-veto-skill">${esc(fe.skill)}</span>` : "") +
    `</div>` +
    `<div class="rca-veto-msg">${esc(fe.message ?? "—")}</div>` +
    (fe.call
      ? `<div class="rca-veto-call">` +
        `<span class="rca-veto-call-label">被否决调用:</span>` +
        `<span class="tc-name mono">${esc(fe.call.name ?? "?")}</span>` +
        `<details class="json-fold rca-veto-args"><summary>参数 JSON</summary>` +
        `<pre class="msg-pre">${esc(argsText)}</pre></details>` +
        `<button class="copy-btn" data-action="copy" data-copy="${esc(argsText)}"` +
        ` data-copy-label="已复制被否决参数 JSON" data-tip="复制参数 JSON" aria-label="复制参数 JSON">${COPY_SVG}</button>` +
        `</div>`
      : "") +
    `</div>`
  );
}

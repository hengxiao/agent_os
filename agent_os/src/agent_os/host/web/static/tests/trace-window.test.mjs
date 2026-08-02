/* trace.js 窗口化纯逻辑单测(docs/WEB-UI.md §6.3:>500 渲染行窗口化渲染):
   windowRange(边界:空 / 顶部 / 底部 / 超滚 / 零视窗 / 自定义行高与 buffer)、
   flattenTraceRows(折叠隐藏行不出 / payload 展开加高)、findRowPosition(合并行
   区间定位与偏移)、renderTrace window 切片(只渲染子集 + 上下占位行"还有 N 行")。
   运行:node static/tests/trace-window.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  ROW_H,
  WINDOW_BUFFER,
  WINDOW_THRESHOLD,
  buildTraceRows,
  deriveTraceView,
  findRowPosition,
  flattenTraceRows,
  renderTrace,
  windowRange,
} from "../js/components/trace.js";
import { makeSignals } from "./fixtures.mjs";

/* ── windowRange:边界 ─────────────────────────────────────── */
{
  assert.deepEqual(windowRange(0, 0, 24, 600), { start: 0, end: 0 }, "空集");
  assert.deepEqual(windowRange(1000, 0, 24, 600, 50), { start: 0, end: 75 },
    "顶部:0 起,end = ceil(600/24)+50 = 25+50");
  assert.deepEqual(windowRange(1000, -5, 24, 600, 50), { start: 0, end: 75 }, "负滚动 clamp");
  const mid = windowRange(1000, 24 * 500, 24, 240, 50);
  assert.deepEqual(mid, { start: 450, end: 560 },
    "中部:floor(500)-50 起,ceil((12000+240)/24)=510+50 止");
  const bottom = windowRange(1000, 24 * 999, 24, 240, 50);
  assert.deepEqual(bottom, { start: 949, end: 1000 }, "底部:end clamp total");
  const beyond = windowRange(1000, 24 * 5000, 24, 240, 50);
  assert.deepEqual(beyond, { start: 949, end: 1000 }, "超滚 clamp 到末行附近");
  const zeroVh = windowRange(1000, 24 * 100, 24, 0, 50);
  assert.deepEqual(zeroVh, { start: 50, end: 151 }, "零视窗高:保底 1 行 + buffer");
  const small = windowRange(3, 0, 24, 600, 50);
  assert.deepEqual(small, { start: 0, end: 3 }, "total 小于窗口:全量");
  const custom = windowRange(100, 100, 10, 50, 5);
  assert.deepEqual(custom, { start: 5, end: 20 }, "自定义 rowH/buffer");
  const defBuf = windowRange(1000, 24 * 500, 24, 240);
  assert.deepEqual(defBuf, { start: 450, end: 560 }, "缺省 buffer = WINDOW_BUFFER");
  assert.equal(WINDOW_BUFFER, 50, "§6.3 buffer = 50");
  assert.equal(WINDOW_THRESHOLD, 500, "§6.3 阈值 = 500");
}

/* ── flattenTraceRows / findRowPosition ───────────────────── */
{
  const signals = makeSignals();
  const view = deriveTraceView(signals, null);
  const flat = flattenTraceRows(view.rows);
  assert.equal(flat.length, 14, "14 可见行(fixtures)");
  assert.equal(flat[0].h, ROW_H);

  /* 合并行区间命中:post 下标也定位到同一行 */
  const pos = findRowPosition(flat, 24); // vetoed pre:tool.call
  assert.ok(pos, "veto 行可定位");
  assert.equal(flat[pos.index].row.sigIndex, 24);
  assert.equal(pos.offset, 9 * ROW_H, "偏移 = 前行估算高之和");
  const postHit = findRowPosition(flat, 5); // post:llm.response → 合并行 @4
  assert.equal(flat[postHit.index].row.sigIndex, 4, "post 下标命中合并行区间");

  /* 折叠子树内信号:落最近可见前行(被折的 call 行)而非 null */
  const view2 = deriveTraceView(signals, null, { collapsed: new Set([19]) }); // 折 f2 call
  const flat2 = flattenTraceRows(view2.rows);
  assert.equal(flat2.length, 14 - 3, "f2 子树 3 行隐藏");
  const fb = findRowPosition(flat2, 24);
  assert.equal(flat2[fb.index].row.sigIndex, 19, "折叠子树内信号落被折 call 行");
  assert.ok(findRowPosition(flat2, 19), "call 行自身可定位");

  /* 非行信号(pre:skill.invoke @18):落最近前行(llm @16) */
  const pre = findRowPosition(flat, 18);
  assert.equal(flat[pre.index].row.sigIndex, 16, "invoke 不占行,落最近前行");

  /* payload 展开行加高 */
  const view3 = deriveTraceView(signals, null, { payloadOpen: new Set([4]) });
  const flat3 = flattenTraceRows(view3.rows);
  assert.equal(flat3[2].h > ROW_H, true, "payload 展开行加面板高");
  assert.equal(findRowPosition(flat3, 6).offset, 2 * ROW_H + flat3[2].h,
    "展开行之后的偏移随高度变化");
}

/* ── renderTrace window 切片:子集渲染 + 占位行 ─────────────── */
{
  /* 1400 信号(700 tool.call 对)→ 700 合并行 > 500 阈值 */
  const many = Array.from({ length: 1400 }, (_, i) => ({
    v: 1,
    type: "signal",
    name: i % 2 ? "post:tool.call" : "pre:tool.call",
    run_id: "r1",
    frame_id: "f1",
    ts: i,
    payload: { tool: "t", ok: true },
  }));
  const view = deriveTraceView(many, null);
  const flat = flattenTraceRows(view.rows);
  assert.equal(flat.length, 700, "1400 信号 → 700 合并行");
  assert.ok(flat.length > WINDOW_THRESHOLD);

  const { start, end } = windowRange(flat.length, 0, ROW_H, 600, WINDOW_BUFFER);
  const html = renderTrace(view, {
    window: { start, end, topPad: 0, bottomPad: (flat.length - end) * ROW_H,
      topCount: 0, bottomCount: flat.length - end },
  });
  const rendered = (html.match(/class="tl-row"/g) ?? []).length;
  assert.equal(rendered, end - start, "只渲染窗口切片(75 行)");
  assert.ok(rendered < flat.length, "渲染行数远小于全量");
  assert.match(html, /还有 \d+ 行/, "底部占位行显示剩余行数");
  assert.match(html, /data-side="bottom"/);
  assert.doesNotMatch(html, /data-side="top"/, "顶部无裁切时无占位");
  const padHeight = Number(html.match(/data-side="bottom" style="height:(\d+)px/)?.[1]);
  assert.equal(padHeight, (flat.length - end) * ROW_H, "占位高度 = 被裁行估算高之和");

  /* 中部窗口:上下占位同时出现;行号 gutter 随切片保持真实行号 */
  const mid = windowRange(flat.length, ROW_H * 300, ROW_H, 600, WINDOW_BUFFER);
  const html2 = renderTrace(view, {
    window: { start: mid.start, end: mid.end, topPad: mid.start * ROW_H, bottomPad: 0,
      topCount: mid.start, bottomCount: 0 },
  });
  assert.match(html2, /data-side="top"/, "顶部占位出现");
  assert.match(html2, /data-signal-index="[4-6]\d\d"/, "新窗口渲染目标区信号");
  const firstGutter = html2.match(/tr-gutter" aria-hidden="true">(\d{4})</)?.[1];
  assert.equal(Number(firstGutter), mid.start + 1, "行号 = 平铺下标 + 1(连续编号)");

  /* 当前位置 ▶:最后一行可见指令(底部窗口内唯一) */
  const bot = windowRange(flat.length, ROW_H * flat.length, ROW_H, 600, WINDOW_BUFFER);
  const html3 = renderTrace(view, {
    window: { start: bot.start, end: bot.end, topPad: bot.start * ROW_H, bottomPad: 0,
      topCount: bot.start, bottomCount: 0 },
  });
  assert.match(html3, /tr-mark" aria-hidden="true">▶</, "▶ 在末行");
  assert.equal((html3.match(/>▶</g) ?? []).length, 1, "▶ 全局唯一");
  assert.doesNotMatch(html, />▶</, "顶部窗口不含末行时无 ▶(随滚动落窗出现)");
}

/* ── renderTrace 关键结构:行号/彩虹轨/call-ret/选中/veto/过滤条 ── */
{
  const signals = makeSignals();
  const sel = { frameId: "f2", signalIndex: 24, source: "timeline" };
  const view = deriveTraceView(signals, sel);
  const html = renderTrace(view, { selection: sel });
  assert.match(html, /tr-gutter" aria-hidden="true">0001</, "行号 0001 起");
  assert.match(html, /class="tr-g" data-d="0"/, "彩虹轨按层着色");
  assert.match(html, /→<\/span><span class="tr-kw">call<\/span><span class="tr-name">skill\.fib/, "call 指令");
  assert.match(html, /←<\/span><span class="tr-kw">ret<\/span>/, "ret 指令");
  assert.match(html, /class="tl-row" data-signal-index="24"[^>]*aria-selected="true"/, "选中联动");
  assert.match(html, /data-vetoed="true"/, "vetoed 行标记");
  assert.match(html, /data-action="tr-toggle"/, "call 行折叠 chevron");
  assert.match(html, /data-action="tr-payload"/, "payload 展开钮");

  const fv = deriveTraceView(signals, { frameId: "f1", signalIndex: null, source: "tree" });
  const fhtml = renderTrace(fv, { selection: { frameId: "f1", signalIndex: null, source: "tree" }, frameSkill: "fib" });
  assert.match(fhtml, /已过滤 <b>fib · f-f1<\/b>/, "过滤提示条含帧标识");
  assert.match(fhtml, /data-action="tl-clear"/, "提示条可点击清除");
}

console.log("trace-window.test.mjs: all assertions passed");

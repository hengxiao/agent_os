/* timeline.js 窗口化纯逻辑单测(WEB-UI.md §6.3:>500 信号行窗口化渲染):
   windowRange(边界:空 / 顶部 / 底部 / 超滚 / 零视窗 / 自定义行高与 buffer)、
   flattenTimelineRows(组头 + 展开行,折叠组只出头)、findRowPosition(定位与偏移),
   renderTimeline window 切片(只渲染子集 + 上下占位行"还有 N 条")。
   运行:node static/tests/timeline-window.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  HEAD_H,
  ROW_H,
  WINDOW_BUFFER,
  WINDOW_THRESHOLD,
  deriveTimelineView,
  flattenTimelineRows,
  findRowPosition,
  renderTimeline,
  windowRange,
} from "../js/components/timeline.js";
import { makeSignals } from "./fixtures.mjs";

/* ── windowRange:边界 ─────────────────────────────────────── */
{
  assert.deepEqual(windowRange(0, 0, 28, 600), { start: 0, end: 0 }, "空集");
  assert.deepEqual(windowRange(1000, 0, 28, 600, 50), { start: 0, end: 72 },
    "顶部:0 起,end = ceil(600/28)+50 = 22+50");
  assert.deepEqual(windowRange(1000, -5, 28, 600, 50), { start: 0, end: 72 }, "负滚动 clamp");
  const mid = windowRange(1000, 28 * 500, 28, 280, 50);
  assert.deepEqual(mid, { start: 450, end: 560 },
    "中部:floor(500)-50 起,ceil((14000+280)/28)=510+50 止");
  const bottom = windowRange(1000, 28 * 999, 28, 280, 50);
  assert.deepEqual(bottom, { start: 949, end: 1000 }, "底部:end clamp total");
  const beyond = windowRange(1000, 28 * 5000, 28, 280, 50);
  assert.deepEqual(beyond, { start: 949, end: 1000 }, "超滚 clamp 到末行附近");
  const zeroVh = windowRange(1000, 28 * 100, 28, 0, 50);
  assert.deepEqual(zeroVh, { start: 50, end: 151 }, "零视窗高:保底 1 行 + buffer");
  const small = windowRange(3, 0, 28, 600, 50);
  assert.deepEqual(small, { start: 0, end: 3 }, "total 小于窗口:全量");
  const custom = windowRange(100, 100, 10, 50, 5);
  assert.deepEqual(custom, { start: 5, end: 20 }, "自定义 rowH/buffer");
  const defBuf = windowRange(1000, 28 * 500, 28, 280);
  assert.deepEqual(defBuf, { start: 450, end: 560 }, "缺省 buffer = WINDOW_BUFFER");
  assert.equal(WINDOW_BUFFER, 50, "§6.3 buffer = 50");
  assert.equal(WINDOW_THRESHOLD, 500, "§6.3 阈值 = 500");
}

/* ── flattenTimelineRows / findRowPosition ────────────────── */
{
  const signals = makeSignals();
  const view = deriveTimelineView(signals, null, { collapsed: new Set() });
  const rows = flattenTimelineRows(view.groups);
  assert.equal(rows.filter((r) => r.type === "head").length, 10, "10 组头(fixtures)");
  assert.equal(rows.filter((r) => r.type === "row").length, 31, "31 信号行(36 信号 - 5 个 pre:step 组头)");
  assert.equal(rows[0].h, HEAD_H);
  assert.equal(rows[1].h, ROW_H);

  const pos = findRowPosition(rows, 24); // veto 信号
  assert.ok(pos, "veto 信号可见可定位");
  assert.equal(rows[pos.index].item.index, 24);
  assert.equal(pos.offset > 0, true, "偏移为前缀高度和");

  /* 折叠组内信号不可定位 */
  const g24 = view.all.find((g) => g.items.some((it) => it.index === 24));
  assert.equal(g24.anomaly, true, "veto 组异常");
  const plain = view.all.find((g) => g.items.some((it) => it.index === 4));
  const view2 = deriveTimelineView(signals, null, { collapsed: new Set([plain.key]) });
  const rows2 = flattenTimelineRows(view2.groups);
  assert.equal(findRowPosition(rows2, 4), null, "折叠组内信号不可定位");
  assert.ok(findRowPosition(rows2, 24), "异常组无视折叠仍可见");
}

/* ── renderTimeline window 切片:子集渲染 + 占位行 ─────────── */
{
  /* 造 600+ 信号行:120 组 ×(1 pre:step + 5 行)= 120 头 + 480 行? 不够——
     直接 700 个 tool.call 对(无 pre:step,同帧)→ 1 组 700 行。 */
  const many = Array.from({ length: 700 }, (_, i) => ({
    v: 1,
    type: "signal",
    name: i % 2 ? "post:tool.call" : "pre:tool.call",
    run_id: "r1",
    frame_id: "f1",
    ts: i,
    payload: { tool: "t", ok: true },
  }));
  const view = deriveTimelineView(many, null, { collapsed: new Set() });
  const rows = flattenTimelineRows(view.groups);
  assert.equal(rows.length, 701, "700 信号行 + 1 组头");
  assert.ok(rows.length > WINDOW_THRESHOLD);

  const { start, end } = windowRange(rows.length, 0, ROW_H, 600, WINDOW_BUFFER);
  const html = renderTimeline(many, view, {
    window: {
      start,
      end,
      topPad: 0,
      bottomPad: (rows.length - end) * ROW_H,
      topCount: 0,
      bottomCount: rows.length - end,
    },
  });
  const rendered = (html.match(/class="tl-row"/g) ?? []).length;
  assert.equal(rendered, end - start - 1, "只渲染窗口切片(1 组头 + 71 信号行)");
  assert.ok(rendered < rows.length, "渲染行数远小于全量");
  assert.match(html, /还有 \d+ 条信号/, "底部占位行显示剩余条数");
  assert.match(html, /data-side="bottom"/);
  assert.doesNotMatch(html, /data-side="top"/, "顶部无裁切时无占位");
  const padHeight = Number(html.match(/data-side="bottom" style="height:(\d+)px/)?.[1]);
  assert.equal(padHeight, (rows.length - end) * ROW_H, "占位高度 = 被裁行估算高之和");

  /* 首片落在组中间:补该组头(语境保持) */
  const mid = windowRange(rows.length, ROW_H * 300, ROW_H, 600, WINDOW_BUFFER);
  const html2 = renderTimeline(many, view, {
    window: {
      start: mid.start,
      end: mid.end,
      topPad: mid.start * ROW_H,
      bottomPad: 0,
      topCount: mid.start,
      bottomCount: 0,
    },
  });
  assert.match(html2, /data-side="top"/, "顶部占位出现");
  assert.ok(html2.indexOf("tl-group-head") < html2.indexOf('class="tl-row"'),
    "切片落在组中时补组头再出行");
}

console.log("timeline-window.test.mjs: all assertions passed");

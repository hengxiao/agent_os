/* usage-panel.js 纯逻辑单测(WEB-UI.md §4.5):
   aggregateUsage(七字段合计 / 缺字段按 0 / run 权威值优先)、
   sortRows(数字列升降序 / 字符串列 / 稳定性 / 不改动入参)、
   costBarWidth(比例 / 上限 100 / 零与负值边界)、
   usageTableHtml(排序头 / 分色 / 内联条 / 合计行)、totalsLine。
   运行:node static/tests/usage-panel.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  aggregateUsage,
  costBarWidth,
  sortRows,
  totalsLine,
  usageTableHtml,
} from "../js/components/usage-panel.js";

const FRAMES = [
  { frame_id: "aaa111", skill: "local:fib@1.0.0", depth: 1, status: "done",
    steps: 3, prompt_tokens: 100, completion_tokens: 40, cache_read_tokens: 10,
    cache_write_tokens: 5, thinking_tokens: 7, cost: 0.2 },
  { frame_id: "bbb222", skill: "local:fib@1.0.0", depth: 2, status: "failed",
    steps: 2, prompt_tokens: 60, completion_tokens: 20, cache_read_tokens: 4,
    cache_write_tokens: 8, thinking_tokens: 3, cost: 0.5 },
  { frame_id: "ccc333", skill: "local:sum@1.0.0", depth: 2, status: "done",
    steps: 1, prompt_tokens: 40, completion_tokens: 10, cache_read_tokens: 0,
    cache_write_tokens: 0, thinking_tokens: 0, cost: 0.1 },
];

/* ── aggregateUsage:合计 + 缺字段容错 ─────────────────────── */
{
  const t = aggregateUsage(FRAMES);
  assert.equal(t.steps, 6);
  assert.equal(t.prompt_tokens, 200);
  assert.equal(t.completion_tokens, 70);
  assert.equal(t.cache_read_tokens, 14);
  assert.equal(t.cache_write_tokens, 13);
  assert.equal(t.thinking_tokens, 10);
  assert.ok(Math.abs(t.cost - 0.8) < 1e-9);

  const sparse = aggregateUsage([{ frame_id: "x" }, null, { steps: 2 }]);
  assert.equal(sparse.steps, 2, "缺字段/非对象按 0");
  assert.equal(sparse.cost, 0);
  assert.deepEqual(aggregateUsage(null), aggregateUsage([]), "null 输入安全");
}

/* ── sortRows:数字列 / 字符串列 / 稳定性 / 纯性 ───────────── */
{
  const desc = sortRows(FRAMES, "cost", "desc");
  assert.deepEqual(desc.map((r) => r.frame_id), ["bbb222", "aaa111", "ccc333"], "数字列降序");
  const asc = sortRows(FRAMES, "cost", "asc");
  assert.deepEqual(asc.map((r) => r.frame_id), ["ccc333", "aaa111", "bbb222"], "数字列升序");
  const bySkill = sortRows(FRAMES, "skill", "asc");
  assert.deepEqual(
    bySkill.map((r) => r.frame_id),
    ["aaa111", "bbb222", "ccc333"],
    "字符串列字典序(fib 两帧保持原相对序 = 稳定)");
  const byFrame = sortRows(FRAMES, "frame_id", "desc");
  assert.deepEqual(byFrame.map((r) => r.frame_id), ["ccc333", "bbb222", "aaa111"]);
  assert.deepEqual(FRAMES.map((r) => r.frame_id), ["aaa111", "bbb222", "ccc333"], "入参不被改动");
  assert.deepEqual(sortRows(FRAMES, "ghost", "asc").map((r) => r.frame_id),
    ["aaa111", "bbb222", "ccc333"], "未知列原序返回");
}

/* ── costBarWidth:比例 / 边界 ─────────────────────────────── */
{
  assert.equal(costBarWidth(0.5, 1), 50);
  assert.equal(costBarWidth(2, 1), 100, "超上限钳 100");
  assert.equal(costBarWidth(0, 1), 0, "零 cost 无条");
  assert.equal(costBarWidth(1, 0), 0, "max 为 0 无条");
  assert.equal(costBarWidth(-1, 2), 0, "负值无条");
  assert.equal(costBarWidth(0.333, 1), 33.3, "一位小数精度");
}

/* ── usageTableHtml:排序头 / 分色 / 内联条 / 合计行 ────────── */
{
  const usage = {
    run: { steps: 99, prompt_tokens: 1, completion_tokens: 1, cache_read_tokens: 1,
      cache_write_tokens: 1, thinking_tokens: 1, cost: 9.99 },
    frames: FRAMES,
  };
  const html = usageTableHtml(usage, { sortKey: "cost", sortDir: "desc" });
  assert.match(html, /<table class="us-table">/);
  assert.match(html, /data-action="us-sort" data-key="cost"/, "列头排序按钮");
  assert.match(html, /is-sorted/, "当前排序列高亮");
  assert.match(html, /cost ▼/, "降序箭头");
  assert.ok(
    html.indexOf('data-frame-id="bbb222"') < html.indexOf('data-frame-id="aaa111"'),
    "行按 cost 降序");
  assert.match(html, /us-cache-read/, "cache_read 分色类");
  assert.match(html, /us-cache-write/, "cache_write 分色类");
  assert.match(html, /<span style="width:100%">/, "最大 cost 内联条满宽");
  assert.match(html, /<span style="width:40%">/, "0.2/0.5 → 40% 内联条");
  assert.match(html, /f-bbb222/, "frame_id 短码");
  assert.match(html, /<tfoot>/, "合计行 tfoot 固定底部");
  assert.match(html, /合计\(3 帧\)/);
  assert.match(html, /\$9\.99/, "合计行 cost 用 run 级权威值");
  assert.match(html, />99</, "合计行 steps 用 run 级权威值");

  /* run 级缺字段回退帧聚合 */
  const fallback = usageTableHtml({ run: {}, frames: FRAMES });
  assert.match(fallback, /\$0\.80/, "run 缺失回退帧聚合 cost");
  assert.match(fallback, />6</, "回退帧聚合 steps");

  /* 空帧:empty 态 */
  assert.match(usageTableHtml({ run: {}, frames: [] }), /无 usage 数据/);

  /* totalsLine(标题行合计,折叠态可见) */
  assert.equal(totalsLine({ run: { steps: 12, cost: 0.31 }, frames: [] }), "12 steps · $0.31");
  assert.equal(totalsLine({ run: {}, frames: FRAMES }), "6 steps · $0.80");
}

console.log("usage-panel.test.mjs: all assertions passed");

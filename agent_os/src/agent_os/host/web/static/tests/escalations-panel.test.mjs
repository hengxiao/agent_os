/* escalations-panel.js 单测(docs/ESCALATION.md §5;WS2 升权审计面板):
   decisionBadgeHtml(四决定 + 待裁决 + approve-once 无台账/grant-run 台账命中标注)、
   tierMigrationHtml(from → to 双徽标 / from 未知只显目标档)、
   escalationTableHtml(行形状 / 参数折叠 / 空态)、
   grantsTableHtml + escalationPanelHtml(汇总行 / 台账段 / 空面板)、summaryLine、
   XSS 全转义;mount 懒加载(dom-stub)。
   运行:node static/tests/escalations-panel.test.mjs */

import assert from "node:assert/strict";
import {
  decisionBadgeHtml,
  escalationPanelHtml,
  escalationTableHtml,
  grantsTableHtml,
  mountEscalationsPanel,
  summaryLine,
  tierMigrationHtml,
} from "../js/components/escalations-panel.js";
import { StubEl } from "./dom-stub.mjs";

const PANEL = {
  summary: { total: 3, approved: 2, denied: 1, grant_run: 1 },
  events: [
    { ts: 1785000000, kind: "post", skill: "child_write", tier: "reversible",
      from_tier: "none", frame_id: "f-aaa111bbbb", decision: "approve-run",
      decided_by: "host:web-ui", scope: "run", question_id: "esc-q1",
      params: '{"cmd": "w"}', paired: true, asked_ts: 1784999999 },
    { ts: 1785000001, kind: "post", skill: "child_write", tier: "reversible",
      from_tier: "none", frame_id: "f-aaa111bbbb", decision: "grant-run",
      decided_by: "host:web-ui", scope: "run", question_id: "esc-q1",
      params: null, paired: false, asked_ts: null },
    { ts: 1785000002, kind: "denied", skill: "child_exec", tier: "irreversible",
      from_tier: "none", frame_id: "f-aaa111bbbb", decision: "deny",
      decided_by: "host:web-ui", scope: null, question_id: "esc-q2",
      params: '{"cmd": "rm"}', paired: false, asked_ts: null },
    { ts: 1785000003, kind: "pre", skill: "child_exec", tier: "irreversible",
      from_tier: null, frame_id: "f-ccc333", decision: null,
      decided_by: null, scope: null, question_id: null,
      params: '{"cmd": "ls"}', paired: false, asked_ts: null },
  ],
  grants: [
    { skill: "child_write", tier: "reversible", scope: "run",
      decided_by: "host:web-ui", decided_at: 1785000000, question_id: "esc-q1",
      frame_id: "f-aaa111bbbb" },
  ],
};

/* ── decisionBadgeHtml:四决定 + 待裁决 + 台账标注 ─────────────── */
{
  const approveOnce = decisionBadgeHtml({ kind: "post", decision: "approve-once" });
  assert.match(approveOnce, /data-decision="approve-once"/);
  assert.match(approveOnce, /无台账/, "approve-once 注无台账(不登记 Grant)");
  const approveRun = decisionBadgeHtml({ kind: "post", decision: "approve-run" });
  assert.match(approveRun, /data-decision="approve-run"/);
  assert.ok(!approveRun.includes("无台账"), "approve-run 有台账,不注");
  const grantRun = decisionBadgeHtml({ kind: "post", decision: "grant-run" });
  assert.match(grantRun, /data-decision="grant-run"/);
  assert.match(grantRun, /台账命中/, "grant-run 注台账命中");
  const deny = decisionBadgeHtml({ kind: "post", decision: "deny" });
  assert.match(deny, /data-decision="deny"/);
  const deniedRow = decisionBadgeHtml({ kind: "denied", decision: "deny" });
  assert.match(deniedRow, />denied</, "denied 溯源行徽标文案 denied");
  const pending = decisionBadgeHtml({ kind: "pre", decision: null, paired: false });
  assert.match(pending, /data-decision="pending"/);
  assert.match(pending, /待裁决/);
  const unknown = decisionBadgeHtml({ kind: "post", decision: "weird" });
  assert.match(unknown, /data-decision="unknown"/, "未知决定兜底(防御)");
}

/* ── tierMigrationHtml:双徽标 / 单徽标 / 未知档 ────────────────── */
{
  const html = tierMigrationHtml({ from_tier: "none", tier: "irreversible" });
  assert.equal((html.match(/perm-badge/g) || []).length, 2, "from → to 两枚徽标");
  assert.match(html, /data-perm="READ"/);
  assert.match(html, /data-perm="EXEC"/);
  assert.match(html, /→/, "迁移箭头");
  const noFrom = tierMigrationHtml({ from_tier: null, tier: "reversible" });
  assert.match(noFrom, /data-perm="WRITE"/);
  assert.ok(!noFrom.includes("→"), "from 未知只显目标档");
  const unknownTier = tierMigrationHtml({ from_tier: null, tier: "weird" });
  assert.match(unknownTier, /—/, "未知档占位不炸");
}

/* ── escalationTableHtml:行形状 / 参数折叠 / 空态 ───────────────── */
{
  const html = escalationTableHtml(PANEL.events);
  assert.equal((html.match(/<tr data-kind=/g) || []).length, 4, "四条事件行");
  assert.match(html, /<th scope="col">时间<\/th>/);
  assert.match(html, /<th scope="col">档位迁移<\/th>/);
  assert.match(html, /child_write/);
  assert.match(html, /host:web-ui/);
  assert.match(html, /title="请求于 /, "配对行 title 带请求时刻(asked_ts)");
  const folds = html.match(/<details class="json-fold ep-params">/g) || [];
  assert.equal(folds.length, 3, "有 params 的行给参数折叠;grant-run 无 params 给 —");
  const empty = escalationTableHtml([]);
  assert.match(empty, /无升权事件/, "空态");
  assert.ok(!empty.includes("<table"), "空态不出表格");
}

/* ── grantsTableHtml + 台账段 ─────────────────────────────────── */
{
  const html = grantsTableHtml(PANEL.grants);
  assert.match(html, /esc-q1/, "question_id 配对键入台账表");
  assert.match(html, /f-f-aaa1/, "发起帧短码");
  assert.match(html, /data-perm="WRITE"/, "台账档徽标");
}

/* ── escalationPanelHtml:汇总行 + 段齐备 + 空面板 ──────────────── */
{
  const html = escalationPanelHtml(PANEL);
  assert.match(html, /<span class="ep-stat" data-k="total"><b class="mono">3<\/b>次裁决/);
  assert.match(html, /data-k="grant_run"><b class="mono">1<\/b>Grant 命中/);
  assert.match(html, /Grant 台账/, "台账段头");
  assert.match(html, /approve-once 不登记台账/, "approve-once 说明随段");
  assert.ok(!html.includes("本 run 无 run 档 Grant"), "有台账不出空台账行");
  const empty = escalationPanelHtml({ summary: { total: 0, approved: 0, denied: 0, grant_run: 0 }, events: [], grants: [] });
  assert.match(empty, /无升权事件/);
  assert.match(empty, /本 run 无 run 档 Grant/, "空台账说明");
  assert.ok(!escalationPanelHtml(null).includes("undefined"), "null 面板不炸");
  assert.equal(summaryLine(PANEL), "3 次裁决 · 批准 2 · 拒绝 1 · Grant 命中 1");
}

/* ── XSS:技能名/批准人/参数/question_id 全转义 ─────────────────── */
{
  const evil = {
    summary: { total: 1, approved: 0, denied: 1, grant_run: 0 },
    events: [
      { ts: 1, kind: "post", skill: '<img src=x onerror="alert(1)">', tier: "reversible",
        from_tier: "none", frame_id: "f-1", decision: "deny",
        decided_by: "<script>alert(2)</script>", scope: "run",
        question_id: "esc-<b>", params: '"><svg onload=alert(3)>', paired: true, asked_ts: 0 },
    ],
    grants: [
      { skill: '"><script>alert(4)</script>', tier: "reversible", scope: "run",
        decided_by: "u", decided_at: 1, question_id: "esc-<i>", frame_id: "f-1" },
    ],
  };
  const html = escalationPanelHtml(evil);
  for (const raw of ["<img src=x", "<script>alert(2)", "<svg onload", "<b>", "<i>"]) {
    assert.ok(!html.includes(raw), `未转义: ${raw}`);
  }
  assert.ok(html.includes("&lt;img src=x"), "技能名转义");
  assert.ok(html.includes("&lt;script&gt;alert(2)"), "批准人转义");
  assert.ok(html.includes("&lt;svg onload=alert(3)&gt;"), "参数摘要转义");
  assert.ok(html.includes("esc-&lt;i&gt;"), "台账 question_id 转义");
}

/* ── mount:dom-stub 懒加载 + 重试 ─────────────────────────────── */
{
  const flush = () => new Promise((r) => setTimeout(r, 0));
  const details = new StubEl("details");
  let calls = 0;
  const handle = mountEscalationsPanel(details, {
    load: async () => {
      calls += 1;
      return PANEL;
    },
  });
  assert.match(details.innerHTML, /升权审计/, "折叠头文案");
  assert.equal(calls, 0, "收起不加载(懒加载)");
  details.open = true;
  details.trigger("toggle");
  await flush();
  assert.equal(calls, 1, "首次展开触发加载");
  const body = details.querySelector(".ep-body");
  assert.ok(body, "body 区域提取");
  assert.match(body.innerHTML, /ep-table/, "加载后渲事件表");
  const totals = details.querySelector(".ep-summary-totals");
  assert.match(totals.textContent, /3 次裁决/, "折叠头合计摘要(折叠态可见)");
  /* 失败 → 重试按钮(data-action 走 workbench 事件委托) */
  const failing = new StubEl("details");
  const h2 = mountEscalationsPanel(failing, {
    load: async () => {
      throw new Error("boom");
    },
  });
  failing.open = true;
  failing.trigger("toggle");
  await flush();
  assert.match(failing.querySelector(".ep-body").innerHTML, /data-action="ep-retry"/);
  h2.destroy();
  handle.destroy();
}

console.log("escalations-panel.test.mjs: all assertions passed");

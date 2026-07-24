/* timeline.js 纯逻辑单测(WEB-UI.md §4.2 规则 1/2):
   groupSignals(分组边界 / pre:step 占组头不占行 / 异常组标记:veto、ok:false、
   run.aborted、失败帧)、deriveTimelineView(过滤 / 聚焦 / 异常组自动展开)。
   运行:node static/tests/timeline.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import {
  deriveTimelineView,
  findVetoedCalls,
  groupSignals,
  isAnomalousSignal,
  renderTimeline,
  signalKind,
  summarizeSignal,
} from "../js/components/timeline.js";
import { makeSignals } from "./fixtures.mjs";

const signals = makeSignals();
const groups = groupSignals(signals);

/* ── signalKind:六类字母章映射(§3.1 信号语义色)────────────────── */
{
  assert.equal(signalKind("pre:llm.request"), "llm");
  assert.equal(signalKind("post:tool.call"), "tool");
  assert.equal(signalKind("pre:logic.exec"), "tool");
  assert.equal(signalKind("budget.exceeded"), "budget");
  assert.equal(signalKind("post:compress"), "compress");
  assert.equal(signalKind("sidecar.veto"), "sidecar");
  assert.equal(signalKind("pre:frame.push"), "frame");
  assert.equal(signalKind("run.started"), "frame");
  assert.equal(signalKind("post:step"), "frame");
}

/* ── groupSignals:分组边界(§4.2 规则 2)────────────────────────── */
{
  assert.equal(groups.length, 10, "应分成 10 组(见 fixtures 行内注释)");
  assert.deepEqual(
    groups.map((g) => [g.frameId, g.step]),
    [
      [null, null], ["f1", null], ["f1", 1], ["f1", 2], ["f1", 3],
      ["f2", null], ["f2", 1], ["f2", 2], ["f1", null], [null, null],
    ],
    "pre:step 开新组;帧边界(frame_id 变化,含回到父帧)强制收尾",
  );
  // pre:step 占组头不占行
  assert.deepEqual(groups[2].items.map((it) => it.index), [4, 5, 6, 7, 8]);
  assert.equal(groups[2].startIndex, 3, "组起始下标 = pre:step 的位置");
  assert.equal(groups[2].skill, "fib", "组头 skill 缩写");
  // 同帧的 frame.pop 并入当前组;回到父帧的信号是新的帧边界组
  assert.deepEqual(groups[7].items.map((it) => it.index), [27, 28, 29, 30]);
  assert.deepEqual(groups[8].items.map((it) => it.index), [31, 32, 33, 34]);
}

/* ── groupSignals:异常组标记(自动展开 + 红条的判定源)───────────── */
{
  const byIdx = (i) => groups.findIndex((g) => g.items.some((it) => it.index === i));
  assert.equal(groups[3].anomaly, true, "ok:false 信号所在组标异常");
  assert.equal(groups[6].anomaly, true, "veto 信号所在组标异常");
  for (const [i, g] of groups.entries()) {
    if (i !== 3 && i !== 6) assert.equal(g.anomaly, false, `组 g${i} 不应标异常`);
  }
  // veto 标记到具体信号行
  const vetoItem = groups[6].items.find((it) => it.index === 24);
  assert.equal(vetoItem.vetoed, true, "pre:tool.call 无 post 应标 vetoed");
  assert.equal(byIdx(24), 6);
  // 失败帧(来自 detail.frames)所在全部组标异常
  const g2 = groupSignals(signals, { failedFrameIds: ["f2"] });
  for (const g of g2.filter((gr) => gr.frameId === "f2")) {
    assert.equal(g.anomaly, true, `失败帧 f2 的组 ${g.key} 应标异常`);
  }
  // run.aborted 自身即异常
  const aborted = groupSignals([...signals, { ...signals[0], name: "run.aborted", ts: 99 }]);
  assert.equal(aborted.at(-1).anomaly, true, "run.aborted 组标异常");
}

/* ── findVetoedCalls / isAnomalousSignal 边界 ─────────────────── */
{
  assert.deepEqual([...findVetoedCalls(signals)], [24], "fixture 中仅 24 被否决");
  assert.equal(isAnomalousSignal(signals[13]), true); // ok:false
  assert.equal(isAnomalousSignal(signals[7]), false);
  assert.equal(isAnomalousSignal({ name: "budget.exceeded", payload: {} }), true);
  // 有 post 配对的 pre 不误判
  assert.equal(findVetoedCalls(signals).has(6), false);
}

/* ── deriveTimelineView:过滤语义(source="tree")────────────────── */
{
  const view = deriveTimelineView(signals, { frameId: "f1", signalIndex: null, source: "tree" });
  assert.equal(view.filtered, true);
  assert.equal(view.filterFrameId, "f1");
  assert.deepEqual(view.groups.map((g) => g.key), ["g1", "g2", "g3", "g4", "g8"]);
  assert.equal(view.hiddenCount, 5);
  assert.equal(view.focused, null, "树选帧不带信号聚焦");
  assert.equal(view.all.length, 10, "all 始终为全量组");
}

/* ── deriveTimelineView:聚焦语义(source="timeline")────────────── */
{
  const view = deriveTimelineView(signals, { frameId: "f2", signalIndex: 24, source: "timeline" });
  assert.equal(view.filtered, false, "时间线选中不过滤自身");
  assert.equal(view.groups.length, 10);
  assert.deepEqual(view.focused, { signalIndex: 24, groupKey: "g6", visible: true });
  // 过滤与聚焦错位时 focused.visible=false(防御:UI 不产生此类选择)
  const cross = deriveTimelineView(signals, { frameId: "f1", signalIndex: 24, source: "tree" });
  assert.equal(cross.focused.visible, false);
  // 空选择
  const none = deriveTimelineView(signals, null);
  assert.equal(none.filtered, false);
  assert.equal(none.focused, null);
}

/* ── deriveTimelineView:折叠与异常组自动展开 ──────────────────── */
{
  const view = deriveTimelineView(signals, null, { collapsed: new Set(["g2", "g6"]) });
  const byKey = Object.fromEntries(view.groups.map((g) => [g.key, g]));
  assert.equal(byKey.g2.expanded, false, "用户折叠生效");
  assert.equal(byKey.g6.expanded, true, "异常组无视用户折叠,自动展开");
  assert.equal(byKey.g1.expanded, true, "默认展开");
}

/* ── summarizeSignal / renderTimeline 关键渲染 ─────────────────── */
{
  assert.equal(summarizeSignal(signals[4]), "mock/fib");
  assert.match(summarizeSignal(signals[5]), /1\/1 tok/);
  assert.match(summarizeSignal(signals[6]), /python_exec\(/);
  assert.match(summarizeSignal(signals[7]), /✓/);
  assert.match(summarizeSignal(signals[13]), /✗/);

  const sel = { frameId: "f1", signalIndex: null, source: "tree" };
  const view = deriveTimelineView(signals, sel);
  const html = renderTimeline(signals, view, { selection: sel, frameSkill: "fib" });
  assert.match(html, /已过滤 <b>fib · f-f1<\/b>/, "过滤提示条含帧标识");
  assert.match(html, /data-action="tl-clear"/, "提示条可点击清除");
  assert.match(html, /icon-badge/, "信号行含 IconBadge");
  assert.match(html, /step 1/, "组头含 step 号");

  const full = renderTimeline(signals, deriveTimelineView(signals, null));
  assert.match(full, /data-anomaly="true"/, "异常组带红条标记");
  assert.match(full, /tl-veto/, "vetoed 行带标记");
}

console.log("timeline.test.mjs: all assertions passed");

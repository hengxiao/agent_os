/* store.js 纯逻辑单测(WEB-UI.md §6.1):set / subscribe / 退订。
   运行:node static/tests/store.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import { createStore, store } from "../js/store.js";

// createStore:初始值读取(按键与整体快照)
{
  const s = createStore({ a: 1 });
  assert.equal(s.get("a"), 1);
  assert.deepEqual(s.get(), { a: 1 });
  assert.equal(s.get("missing"), undefined);
}

// set:浅合并更新 + 订阅者收到 (state, patch)
{
  const s = createStore({ a: 1, b: 2 });
  const seen = [];
  const unsub = s.subscribe((state, patch) => seen.push([state, patch]));
  s.set({ a: 10 });
  assert.equal(s.get("a"), 10);
  assert.equal(s.get("b"), 2); // 未涉及的键不受影响
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0][1], { a: 10 });
  assert.equal(seen[0][0].a, 10); // 通知时已是新值

  // 退订后不再通知,但 set 仍生效
  unsub();
  s.set({ a: 99 });
  assert.equal(seen.length, 1);
  assert.equal(s.get("a"), 99);
}

// 多订阅者都收到通知;退订函数幂等
{
  const s = createStore({});
  let n1 = 0;
  let n2 = 0;
  s.subscribe(() => { n1 += 1; });
  const off2 = s.subscribe(() => { n2 += 1; });
  s.set({ x: 1 });
  off2();
  off2(); // 重复退订不抛错
  s.set({ x: 2 });
  assert.equal(n1, 2);
  assert.equal(n2, 1);
}

// 全局单例:带 §6.1 约定的四个键与初始加载态
{
  for (const k of ["route", "runs", "runsStatus", "selectedRunId"]) {
    assert.ok(k in store.get(), `缺少初始键 ${k}`);
  }
  assert.equal(store.get("runsStatus"), "loading");
  assert.deepEqual(store.get("runs"), []);
}

console.log("store.test.mjs: all assertions passed");

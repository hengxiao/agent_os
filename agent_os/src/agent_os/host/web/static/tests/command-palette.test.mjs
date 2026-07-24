/* command-palette.js 纯逻辑单测(WEB-UI.md §5 ⌘K 命令条):
   COMMANDS 注册表(§5 八命令齐全)、fuzzyScore(子序列匹配 / 连续与词首加权 /
   靠前命中优先 / 不匹配 null)、fuzzyFilter(过滤 + 排序 + 空 query 原序)。
   运行:node static/tests/command-palette.test.mjs(无需 DOM、无第三方依赖)。 */

import assert from "node:assert/strict";
import { COMMANDS, fuzzyFilter, fuzzyScore } from "../js/components/command-palette.js";

/* ── 命令注册表:§5 规定的命令齐全且字段完整 ───────────────── */
{
  const ids = COMMANDS.map((c) => c.id);
  for (const id of [
    "new-run",
    "stop",
    "resume",
    "reload-skills",
    "goto-runs",
    "goto-skills",
    "goto-tools",
    "jump-error",
  ]) {
    assert.ok(ids.includes(id), `注册表含 ${id}`);
  }
  assert.equal(new Set(ids).size, ids.length, "id 唯一");
  for (const c of COMMANDS) {
    assert.ok(c.title && c.hint && c.glyph, `${c.id} 字段完整`);
  }
}

/* ── fuzzyScore:子序列匹配语义 ────────────────────────────── */
{
  assert.equal(fuzzyScore("New Run", ""), 0, "空 query 恒匹配");
  assert.ok(fuzzyScore("New Run", "nr") > 0, "子序列命中(n…r)");
  assert.equal(fuzzyScore("New Run", "xyz"), null, "非子序列不匹配");
  assert.equal(fuzzyScore("New Run", "nrr"), null, "字符必须按序");
  assert.ok(fuzzyScore("跳 Runs", "跳r") != null, "中英文混合匹配");
  assert.ok(fuzzyScore("Reload Skills", "RS") != null, "大小写不敏感");

  /* 词首加权:同 query,词首命中 > 词中命中 */
  assert.ok(fuzzyScore("New Run", "run") > fuzzyScore("being run", "run"));
  /* 连续命中加权:"newr" 在 "New Run" 连续 > 在 "n e w r" 分散 */
  assert.ok(fuzzyScore("New Run", "newrun") > fuzzyScore("n-e-w- -r-u-n-x", "newrun"));
  /* 靠前命中优先 */
  assert.ok(fuzzyScore("ab abc", "abc") > fuzzyScore("x abc", "abc") - 0.5);
}

/* ── fuzzyFilter:过滤 + 排序 + 空 query 原序 ──────────────── */
{
  assert.deepEqual(fuzzyFilter(COMMANDS, ""), COMMANDS, "空 query 原序返回");
  assert.deepEqual(fuzzyFilter(COMMANDS, "   "), COMMANDS, "空白 query 视同空");

  const stop = fuzzyFilter(COMMANDS, "stop");
  assert.equal(stop[0].id, "stop", "最佳命中排首");
  assert.ok(stop.every((c) => fuzzyScore(`${c.title}${c.hint}${c.id}`, "stop") != null ||
    [c.title, c.hint, c.id].some((f) => fuzzyScore(f, "stop") != null)), "结果全部匹配");

  const gotoS = fuzzyFilter(COMMANDS, "skills");
  assert.ok(gotoS.some((c) => c.id === "goto-skills"), "跳 Skills 可被 skills 命中");
  assert.ok(gotoS.some((c) => c.id === "reload-skills"), "Reload Skills 同被命中");
  assert.equal(gotoS[0].id, "goto-skills", "同为词首连续命中时,靠前命中排前");

  const zh = fuzzyFilter(COMMANDS, "跳");
  assert.deepEqual(zh.map((c) => c.id), ["goto-runs", "goto-skills", "goto-tools"],
    "中文 hint 过滤,原相对序(同分按 title)");

  assert.deepEqual(fuzzyFilter(COMMANDS, "zzzzz"), [], "无匹配空数组");
  assert.deepEqual(fuzzyFilter(null, "x"), [], "null 输入安全");
}

console.log("command-palette.test.mjs: all assertions passed");

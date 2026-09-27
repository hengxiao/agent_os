/* GDB 命令方言解析器单测(docs/TUI-DEBUG.md §5/§6;逐语义移植
   host/tui/apps/debugger/commands.py,停止行/错误语式逐字对齐 theme.py dbg.*):
   1) spec 语法四形态(b fs_* 缺省 tool_call / b skill:fib / b step / b error / b *N);
   2) 唯一前缀缩写(fin = finish、i b = info breakpoints)与 alias 精确匹配;
   3) 歧义候选(报错列出候选,排序);
   4) 裸 Enter 重复白名单(c/s/n/finish/until/up/down;危险命令不重复);
   5) 停止行与错误语式逐字断言(§6 文案契约);
   6) help/apropos 从命令表生成(不硬编码)。
   运行:node static/tests/debug-console.test.mjs(纯函数,无 DOM)。 */

import assert from "node:assert/strict";
import {
  COMMAND_SPECS,
  MSG,
  REPEATABLE,
  aproposLines,
  btLines,
  fmt,
  frameRowLine,
  helpLines,
  infoBLines,
  parse,
  parseBpSpec,
  parseErrorLines,
  shortSkillName,
  stopLine,
} from "../js/components/debug-commands.js";

/* ══ 1. spec 语法四形态(§5.1 位置规范)══════════════════════════════ */
{
  assert.deepEqual(parseBpSpec("fs_*"), { kind: "tool_call", match: "fs_*", until: null },
    "b fs_*:缺省 tool_call,match = 名字 glob");
  assert.deepEqual(parseBpSpec("system.python.exec"),
    { kind: "tool_call", match: "system.python.exec", until: null }, "普通名字 = tool_call");
  assert.deepEqual(parseBpSpec("skill:fib"), { kind: "skill_invoke", match: "fib", until: null },
    "b skill:fib = skill_invoke");
  assert.deepEqual(parseBpSpec("skill:demo.*"), { kind: "skill_invoke", match: "demo.*", until: null },
    "skill: glob 透传");
  assert.deepEqual(parseBpSpec("step"), { kind: "step", match: "*", until: null }, "b step");
  assert.deepEqual(parseBpSpec("error"), { kind: "error", match: "*", until: null }, "b error");
  assert.deepEqual(parseBpSpec("*3"), { kind: "step", match: "*", until: 3 },
    "b *N = until 步数断点(仅 step)");
  assert.equal(parseBpSpec("*0").error.kind, "usage", "b *0 非法(N ≥ 1)");
  assert.equal(parseBpSpec("*x").error.kind, "usage", "b *x 非法");
  assert.equal(parseBpSpec("skill:").error.kind, "usage", "b skill: 空 glob 非法");
  assert.equal(parseBpSpec("").error.kind, "usage", "空 spec 非法");
  assert.equal(parseBpSpec("*0").error.usage, "b *N(N ≥ 1 的步数断点)", "usage 原文");
}

/* ══ 2. 命令词解析:alias 精确 > 全名精确 > 唯一前缀 ═══════════════════ */
{
  assert.deepEqual(parse("b fs_*"), { name: "break", args: ["fs_*"], raw: "b fs_*" }, "b = break");
  assert.deepEqual(parse("break skill:fib").name, "break");
  assert.deepEqual(parse("fin").name, "finish", "fin = finish(唯一前缀)");
  assert.deepEqual(parse("f").name, "frame", "f = frame(alias)");
  assert.deepEqual(parse("c").name, "continue", "c = continue(alias)");
  assert.deepEqual(parse("s").name, "step");
  assert.deepEqual(parse("n").name, "next");
  assert.deepEqual(parse("bt").name, "backtrace");
  assert.deepEqual(parse("p").name, "print");
  assert.deepEqual(parse("q").name, "quit");
  assert.deepEqual(parse("h").name, "help");
  assert.deepEqual(parse("cont").name, "continue", "cont 唯一前缀");
  assert.deepEqual(parse("fram").name, "frame");
  assert.deepEqual(parse("detach").name, "detach");
  assert.deepEqual(parse("").name, "", "空行 = 裸 Enter(白名单裁决在页面层)");
  assert.deepEqual(parse("   ").name, "", "全空白同裸 Enter");
  /* info 子命令:alias 精确 + 唯一前缀 */
  assert.deepEqual(parse("info b").args, ["breakpoints"], "i b 的 b = breakpoints(alias)");
  assert.deepEqual(parse("i b").name, "info", "i = info(alias)");
  assert.deepEqual(parse("info breakpoints").args, ["breakpoints"]);
  assert.deepEqual(parse("i fr").args, ["frame"], "info 子命令唯一前缀");
  assert.deepEqual(parse("info ar").args, ["args"]);
  assert.deepEqual(parse("info sess").args, ["sessions"]);
  assert.equal(parse("info").error.kind, "usage", "info 缺子命令 → usage");
  assert.equal(parse("info").error.usage, "info b|frame|args|sessions");
  /* set / x 的形参校验(解析层) */
  assert.deepEqual(parse("set args {\"a\":1}").name, "set");
  assert.equal(parse("set foo").error.kind, "usage", "set 非 args → usage");
  assert.deepEqual(parse("x messages").name, "x");
  assert.equal(parse("x foo").error.kind, "usage", "x 非 messages|working → usage");
}

/* ══ 3. 歧义候选 / 未知命令(§5:GDB 容错)══════════════════════════════ */
{
  const d = parse("d");
  assert.equal(d.error.kind, "ambiguous");
  assert.deepEqual(d.error.candidates, ["delete", "detach", "disable", "down"],
    "d 歧义:候选排序列出");
  assert.deepEqual(parse("de").error.candidates, ["delete", "detach"], "de 歧义两个候选");
  assert.equal(parse("dis").name, "disable", "dis 唯一前缀");
  assert.equal(parse("del").name, "delete", "del 唯一前缀");
  assert.equal(parse("foo").error.kind, "unknown", "未知命令");
  /* 错误语式逐字(§6,theme.py dbg.err.*) */
  assert.deepEqual(parseErrorLines(parse("d").error),
    ['Ambiguous command "d": delete, detach, disable, down.'], "歧义语式逐字");
  assert.deepEqual(parseErrorLines(parse("foo").error),
    ['Undefined command: "foo". Try "help".'], "未知语式逐字");
  assert.deepEqual(parseErrorLines(parse("info").error),
    ["Usage: info b|frame|args|sessions"], "usage 语式逐字");
}

/* ══ 4. 裸 Enter 重复白名单(§5.2;危险命令永不重复)═════════════════════ */
{
  for (const n of ["continue", "step", "next", "finish", "until", "up", "down"]) {
    assert.ok(REPEATABLE.has(n), `${n} 在白名单`);
  }
  for (const n of ["kill", "run", "detach", "set", "inject", "break", "delete",
    "print", "x", "frame", "backtrace", "info", "rerun", "session", "quit",
    "help", "apropos", "enable", "disable"]) {
    assert.ok(!REPEATABLE.has(n), `${n} 不重复(危险/只读命令)`);
  }
}

/* ══ 5. 停止行与错误语式逐字(§6 文案契约)══════════════════════════════ */
{
  /* Breakpoint 行:§5 头注例句逐字 */
  const snapBp = {
    state: "paused",
    pause_point: {
      signal: "pre:tool.call", frame_id: "f-abc", step: 2,
      tool: "system.python.exec", skill: "demo.fib",
      reason: "breakpoint", breakpoint_ids: ["bp1"],
    },
  };
  assert.equal(stopLine(snapBp, () => 1),
    "Breakpoint 1, pre:tool.call system.python.exec, frame f-abc (fib), step 2",
    "停止行(breakpoint)逐字");
  /* 查不到 bp 编号 → ?(TUI _bp_num 同款) */
  assert.equal(stopLine(snapBp, () => "?"),
    "Breakpoint 1, pre:tool.call system.python.exec, frame f-abc (fib), step 2".replace(" 1,", " ?,"),
    "未知断点号回 ?");
  /* pause 行(无 skill 段) */
  assert.equal(stopLine({ state: "paused", pause_point: {
    signal: "pre:step", frame_id: "f-abc", step: 3, reason: "pause" } }),
    "Run paused by user (pause), frame f-abc, step 3", "停止行(pause)逐字");
  /* step 行(步进结束) */
  assert.equal(stopLine({ state: "paused", pause_point: {
    signal: "pre:step", frame_id: "f-def", step: 1, skill: "local:fib@1.0.0", reason: "step:into" } }),
    "Step finished, pre:step, frame f-def (fib), step 1", "停止行(step)逐字");
  /* 无 step 字段不带后缀(live 内核 pre:tool.call 负载无 step 顶层字段,D2 注 8) */
  assert.equal(stopLine({ state: "paused", pause_point: {
    signal: "pre:tool.call", frame_id: "f1", tool: "system.python.exec",
    skill: null, reason: "breakpoint", breakpoint_ids: ["bp9"] } }, () => 9),
    "Breakpoint 9, pre:tool.call system.python.exec, frame f1 ()", "无 step 不带后缀");
  assert.equal(stopLine({ state: "running", pause_point: null }), null, "非暂停无停止行");
  /* 错误语式常量逐字 */
  assert.equal(MSG.notPaused, "The run is not paused.");
  assert.equal(MSG.noSession, "The run is not being debugged.");
  assert.equal(MSG.setArgsPos, "Cannot set args: not paused at pre:tool.call.");
  assert.equal(MSG.enableDisable, "Not supported: delete and re-add.");
  assert.equal(MSG.killConfirm, "Kill the run being debugged? (y or n)");
  assert.equal(fmt(MSG.endRun, { status: "done" }), "Run finished: done");
  assert.equal(fmt(MSG.endRun, { status: "aborted" }), "Run finished: aborted");
  /* 干预回声(§6 留痕) */
  assert.equal(fmt(MSG.patched, { patch: "{\"code\":\"result = 41\"}" }),
    'args patched: {"code":"result = 41"}(提交即放行)');
  assert.equal(fmt(MSG.injected, { frame: "f-abc", skill: "fib" }),
    "injected -> frame f-abc (fib)(注入即放行)");
  assert.equal(fmt(MSG.bpSet, { num: 2, kind: "tool_call", match: "fs_*" }),
    "Breakpoint 2 set: tool_call fs_*");
  assert.equal(fmt(MSG.bpDel, { num: 2 }), "Breakpoint 2 deleted.");
  assert.equal(fmt(MSG.resumed, { cmd: "step_into" }), "resumed (step_into)");
}

/* ══ 6. info b 表 / bt 行 / 选帧行(GDB 表格习惯)══════════════════════ */
{
  const bps = [
    { num: 1, kind: "tool_call", match: "fs_*", enabled: true, hits: 2 },
    { num: 2, kind: "step", match: "*", enabled: true, hits: 0 },
  ];
  const lines = infoBLines(bps);
  assert.equal(lines[0], "Num  Kind         Match            Enb  Hits", "info b 表头逐字");
  assert.equal(lines[1], "1   tool_call    fs_*             y    2", "行 1 列宽对译");
  assert.equal(lines[2], "2   step         *                y    0", "行 2");
  assert.deepEqual(infoBLines([]), ["(无断点)"], "空断点");

  const snap = {
    state: "paused",
    pause_point: { signal: "pre:tool.call", frame_id: "f1", step: 2 },
    frame_stack: [
      { frame_id: "f1", skill: "local:fib@1.0.0", depth: 1 },
      { frame_id: "f2", skill: "demo.fib", depth: 2 },
    ],
  };
  assert.deepEqual(btLines(snap), [
    "#0  fib (f2)",
    "#1  fib (f1) at pre:tool.call step 2",
  ], "bt 行:#0 = 栈顶,暂停帧带 at 后缀(dbg.bt.row)");
  assert.deepEqual(btLines({ frame_stack: [] }), ["(帧栈为空)"], "空栈");
  assert.equal(frameRowLine(1, { frame_id: "f1", skill: "local:fib@1.0.0" }),
    "#1  fib (f1)", "选帧回声行(dbg.frame.row)");
}

/* ══ 7. help/apropos 从命令表生成(§5.1;不硬编码)══════════════════════ */
{
  const all = helpLines([]);
  assert.equal(all[0], "命令(GDB 方言;唯一前缀可缩写;裸 Enter 重复步进类命令):", "help 表头");
  assert.equal(all.length, COMMAND_SPECS.length + 1, "每命令一行");
  assert.ok(all.some((l) => l.includes("break") && l.includes("<spec>")), "help 含 break 行");
  const one = helpLines(["break"]);
  assert.match(one[0], /^break <spec>  \(别名: b\)/, "help <cmd> 单行带别名");
  assert.equal(one.length, 2);
  assert.deepEqual(helpLines(["nope"]), ['Undefined command: "nope". Try "help".'],
    "help 未知命令");
  const hits = aproposLines(["断点"]);
  assert.ok(hits.some((l) => l.startsWith("break ")), "apropos 命中 break");
  assert.ok(hits.some((l) => l.startsWith("delete ")), "apropos 命中 delete");
  assert.deepEqual(aproposLines([]), ["Usage: apropos <词>"], "apropos 缺词 → usage");
  assert.deepEqual(aproposLines(["zzzz"]), ['Undefined command: "zzzz". Try "help".'],
    "apropos 无命中");
}

/* ══ 8. 技能名短形(停止行 (skill) 段;两种命名习惯 → §6 例的 (fib))═════ */
{
  assert.equal(shortSkillName("demo.fib"), "fib", "点号尾段(trace_rows short_skill)");
  assert.equal(shortSkillName("local:fib@1.0.0"), "fib", "命名空间+版本(util shortSkill)");
  assert.equal(shortSkillName("fib"), "fib");
  assert.equal(shortSkillName(null), "");
}

console.log("debug-console.test.mjs: all assertions passed");

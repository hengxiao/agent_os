/* GDB 命令方言(纯函数,node 可直测;docs/TUI-DEBUG.md §5/§6 的 web 镜像)。
   逐语义移植 host/tui/apps/debugger/commands.py(命令表/唯一前缀缩写/歧义候选/
   spec 语法/裸 Enter 白名单),停止行与错误语式逐字对齐 tui/theme.py dbg.* copy 表
   (§6 文案契约:占位符不可吞,技术原文豁免翻译)。

   与 TUI 的差异(web 形态裁决):
   - parse 不抛异常,返回 { error: { kind, cmd, candidates, usage } }(kind ∈
     unknown / ambiguous / usage);正常返回 { name, args, raw }(name = 全名,
     空串 = 裸 Enter,白名单裁决在页面层,同 commands.py parse 契约);
   - 断点界面编号 num 是客户端显示号(REST 只有 bp_id;TUI D2 注 5 同款裁决),
     编号分配在页面层(bpNums 映射),本模块只消费 numFor 回调;
   - run/enable/disable/until/b *N 的"诚实人话"文案在页面层(执行路由在
     debug-console.js,REST 出海),本模块只出解析与文案模板。 */

import { esc } from "../util.js";

/* ── 命令表(help/apropos 从本表生成,§5.1;alias 精确匹配,全名允许唯一前缀)── */

export const COMMAND_SPECS = [
  { name: "run", aliases: [], usage: "<skill> [--input <json>] [-b <spec>]…",
    summary: "起调试会话(控制台不做表单:去 #/debug 首页)" },
  { name: "break", aliases: ["b"], usage: "<spec>",
    summary: "加断点:fs_*(缺省 tool_call)/ skill:fib / step / error / *N(步数断点)" },
  { name: "info", aliases: ["i"], usage: "b|frame|args|sessions",
    summary: "info 族:断点表 / 帧摘要 / 调用参数 / 会话" },
  { name: "delete", aliases: [], usage: "<N>", summary: "删断点(N = info b 的 Num 列)" },
  { name: "enable", aliases: [], usage: "<N>", summary: "启用断点(后端缺口,诚实拒绝)" },
  { name: "disable", aliases: [], usage: "<N>", summary: "禁用断点(后端缺口,诚实拒绝)" },
  { name: "continue", aliases: ["c"], usage: "", summary: "继续到下一断点" },
  { name: "step", aliases: ["s"], usage: "", summary: "单步步入(step_into)" },
  { name: "next", aliases: ["n"], usage: "", summary: "单步步过(step_over)" },
  { name: "finish", aliases: ["fin"], usage: "", summary: "步出当前帧(step_out)" },
  { name: "until", aliases: [], usage: "<N>", summary: "直达第 N 条 pre:step(REST 无 until 字段,诚实拒绝)" },
  { name: "backtrace", aliases: ["bt"], usage: "", summary: "调用栈(GDB bt)" },
  { name: "frame", aliases: ["f"], usage: "<N>", summary: "选帧(#0 = 栈顶;选帧即改检视上下文)" },
  { name: "up", aliases: [], usage: "", summary: "向调用者一帧(GDB up)" },
  { name: "down", aliases: [], usage: "", summary: "向被调者一帧(GDB down)" },
  { name: "print", aliases: ["p"], usage: "[<key>]", summary: "暂停点 payload(全量或子键,客户端取值零裁决)" },
  { name: "x", aliases: [], usage: "messages|working", summary: "检视帧 messages / working 全文" },
  { name: "set", aliases: [], usage: "args <json>", summary: "改本次调用参数(仅停在 pre:tool.call;提交即放行)" },
  { name: "inject", aliases: [], usage: "<text>", summary: "注入一条 user 消息(注入即放行)" },
  { name: "kill", aliases: [], usage: "", summary: "中止 run(两段确认:Kill the run being debugged? (y or n))" },
  { name: "detach", aliases: [], usage: "", summary: "放行,run 继续跑完(会话摘下)" },
  { name: "rerun", aliases: [], usage: "", summary: "以同参数重开新会话" },
  { name: "session", aliases: [], usage: "[<SID>]", summary: "已知会话列表(localStorage 账本)/ session <SID> 切换" },
  { name: "quit", aliases: ["q"], usage: "", summary: "回三栏视图 #/debug/<sid>(会话保留,不 detach)" },
  { name: "help", aliases: ["h"], usage: "[<cmd>]", summary: "帮助(从命令表生成)" },
  { name: "apropos", aliases: [], usage: "<词>", summary: "按词搜命令" },
];

const SPEC_BY_NAME = new Map(COMMAND_SPECS.map((s) => [s.name, s]));
const ALIAS = new Map(COMMAND_SPECS.flatMap((s) => s.aliases.map((a) => [a, s.name])));

/* info 子命令(alias 精确 + 全名唯一前缀;commands.py _INFO_SUBS) */
const INFO_SUBS = { breakpoints: ["b"], frame: [], args: [], sessions: [] };

/* 裸 Enter 重复白名单(§5.2,GDB 同款:步进系可重复,危险命令不重复) */
export const REPEATABLE = new Set(["continue", "step", "next", "finish", "until", "up", "down"]);

/* ── 文案(§6 逐字契约,theme.py dbg.* 同值;技术原文豁免翻译)────────── */

export const MSG = {
  prompt: "(adb) ",
  stopBp: "Breakpoint {num}, {signal}, frame {frame} ({skill}){step_suffix}",
  stopStep: "Step finished, {signal}, frame {frame} ({skill}){step_suffix}",
  stopPause: "Run paused by user (pause), frame {frame}{step_suffix}",
  endRun: "Run finished: {status}",
  bpSet: "Breakpoint {num} set: {kind} {match}",
  bpDel: "Breakpoint {num} deleted.",
  resumed: "resumed ({cmd})",
  patched: "args patched: {patch}(提交即放行)",
  injected: "injected -> frame {frame} ({skill})(注入即放行)",
  rerun: "rerun -> 新会话 {sid} · run {run_id}",
  detached: "detached(放行,run 继续跑完;会话已摘下)",
  notPaused: "The run is not paused.",
  noSession: "The run is not being debugged.",
  noStack: "No stack.",
  noBp: "No breakpoint number {num}.",
  noFrame: "No frame number {num}.",
  ambiguous: "Ambiguous command \"{cmd}\": {candidates}.",
  unknown: "Undefined command: \"{cmd}\". Try \"help\".",
  usage: "Usage: {usage}",
  enableDisable: "Not supported: delete and re-add.",
  setArgsPos: "Cannot set args: not paused at pre:tool.call.",
  killConfirm: "Kill the run being debugged? (y or n)",
  quitHint: "(退出不 detach;paused 会话保留给下次连接)",
  emptyBps: "(无断点)",
  emptyStack: "(帧栈为空)",
  emptySessions: "(无已知会话——本机账本为空;从调试首页开过会话才会记)",
  helpHeader: "命令(GDB 方言;唯一前缀可缩写;裸 Enter 重复步进类命令):",
  infoBHeader: "Num  Kind         Match            Enb  Hits",
  /* web 形态新增的诚实人话(console 无开会话表单;REST 断点端点只有 kind/match) */
  runToHome: "run 在控制台不可用:开会话请走调试首页表单 #/debug(启动前断点也在那里预填)。",
  noUntil: "until 步数断点在 web 控制台不可用(REST 断点端点只有 kind/match;TUI --replay --until N 可用)。",
};

export const fmt = (tpl, vars = {}) =>
  tpl.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));

/* 解析错误 → 命令窗行(TUI app.py _parse_error_lines 对译) */
export function parseErrorLines(err) {
  if (err.kind === "ambiguous") {
    return [fmt(MSG.ambiguous, { cmd: err.cmd, candidates: err.candidates.join(", ") })];
  }
  if (err.kind === "usage") return [fmt(MSG.usage, { usage: err.usage })];
  return [fmt(MSG.unknown, { cmd: err.cmd })];
}

/* ── 词解析:alias 精确 > 全名精确 > 全名唯一前缀;歧义/未知回 error ────── */

function resolveWord(word, names, aliases) {
  if (aliases.has(word)) return aliases.get(word);
  if (names.includes(word)) return word;
  const cands = names.filter((n) => n.startsWith(word));
  if (cands.length === 1) return cands[0];
  if (cands.length) {
    return { error: { kind: "ambiguous", cmd: word, candidates: [...cands].sort() } };
  }
  return { error: { kind: "unknown", cmd: word } };
}

/* ── b <spec> spec 语法(§5.1,学 GDB 位置规范;commands.py parse_bp_spec 对译)──
   b step / b error:kind 直给(match 忽略);b *N:until 步数断点(仅 step);
   b skill:<glob>:skill_invoke;其余(b fs_* 等):缺省 tool_call,match = 名字 glob。 */
export function parseBpSpec(spec) {
  const text = String(spec ?? "").trim();
  if (!text) return { error: { kind: "usage", cmd: "break", usage: "b <spec>" } };
  if (text === "step") return { kind: "step", match: "*", until: null };
  if (text === "error") return { kind: "error", match: "*", until: null };
  if (text.startsWith("*")) {
    const num = text.slice(1);
    if (!/^\d+$/.test(num) || Number(num) < 1) {
      return { error: { kind: "usage", cmd: "break", usage: "b *N(N ≥ 1 的步数断点)" } };
    }
    return { kind: "step", match: "*", until: Number(num) };
  }
  if (text.startsWith("skill:")) {
    const match = text.slice("skill:".length);
    if (!match) return { error: { kind: "usage", cmd: "break", usage: "b skill:<glob>" } };
    return { kind: "skill_invoke", match, until: null };
  }
  return { kind: "tool_call", match: text, until: null };
}

/* ── parse:命令行 → { name, args, raw } | { error }(纯函数)─────────────
   空行 → { name: "" },裸 Enter 白名单裁决在页面层(commands.py parse 同契约)。 */
export function parse(line) {
  const raw = String(line ?? "").trim();
  if (!raw) return { name: "", args: [], raw: "" };
  const words = raw.split(/\s+/);
  const name = resolveWord(words[0], [...SPEC_BY_NAME.keys()], ALIAS);
  if (typeof name !== "string") return name;
  const args = words.slice(1);
  if (name === "info") {
    if (!args.length) {
      return { error: { kind: "usage", cmd: "info", usage: "info b|frame|args|sessions" } };
    }
    const subAlias = new Map(Object.entries(INFO_SUBS).flatMap(([n, al]) => al.map((a) => [a, n])));
    const sub = resolveWord(args[0], Object.keys(INFO_SUBS), subAlias);
    if (typeof sub !== "string") return sub;
    return { name: "info", args: [sub, ...args.slice(1)], raw };
  }
  if (name === "set" && (!args.length || args[0] !== "args")) {
    return { error: { kind: "usage", cmd: "set", usage: "set args <json>" } };
  }
  if (name === "x" && (!args.length || !["messages", "working"].includes(args[0]))) {
    return { error: { kind: "usage", cmd: "x", usage: "x messages|working" } };
  }
  return { name, args, raw };
}

/* ── 技能名短形(停止行/bt 行用):先剥命名空间与版本(web util.js shortSkill
      语义,"local:fib@1.0.0" → "fib"),再剥点号尾段(trace_rows short_skill
      语义,"demo.fib" → "fib")——两步叠加,两种命名习惯都得到 §6 例的 (fib)。 */
export function shortSkillName(skill) {
  let s = String(skill ?? "");
  if (s.includes(":")) s = s.slice(s.lastIndexOf(":") + 1);
  s = s.split("@")[0];
  if (s.includes(".")) s = s.slice(s.lastIndexOf(".") + 1);
  return s;
}

/* JSON 摘要(无空格紧凑形,超长截断;trace_rows short_json 对译) */
export function shortJson(value, max = 64) {
  let s;
  try {
    s = JSON.stringify(value);
  } catch {
    s = String(value);
  }
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/* ── 停止行(§6;每次暂停自动打印,学 `Breakpoint 1, main () at main.c:4`)──
   numFor(bpId) → 界面编号(客户端显示号,页面层 bpNums 映射;查不到回 "?")。 */
export function stopLine(snap, numFor = () => "?") {
  const pp = snap?.pause_point ?? null;
  if (!pp) return null;
  const reason = String(pp.reason ?? "");
  const frame = String(pp.frame_id ?? "-");
  const skill = shortSkillName(pp.skill);
  const stepSuffix = pp.step != null ? `, step ${pp.step}` : "";
  let signal = String(pp.signal ?? "");
  if (pp.tool) signal = `${signal} ${pp.tool}`;
  if (reason === "breakpoint") {
    const num = numFor((pp.breakpoint_ids ?? [])[0]);
    return fmt(MSG.stopBp, { num, signal, frame, skill, step_suffix: stepSuffix });
  }
  if (reason === "pause") return fmt(MSG.stopPause, { frame, step_suffix: stepSuffix });
  return fmt(MSG.stopStep, { signal, frame, skill, step_suffix: stepSuffix });
}

/* ── info b 表(dbg.info_b.header + 行;列宽对译 commands.py _cmd_info_b)── */
export function infoBLines(bps) {
  const list = Array.isArray(bps) ? bps : [];
  if (!list.length) return [MSG.emptyBps];
  return [
    MSG.infoBHeader,
    ...list.map((bp) =>
      `${String(bp.num ?? "?").padEnd(4)}${String(bp.kind ?? "").padEnd(13)}` +
      `${String(bp.match ?? "").padEnd(17)}${(bp.enabled === false ? "n" : "y").padEnd(5)}` +
      `${bp.hits ?? 0}`),
  ];
}

/* ── bt 行(dbg.bt.row:`#N  skill (fid) at …`;#0 = 栈顶,GDB 编号习惯)──── */
export function btLines(snap) {
  const frames = [...(snap?.frame_stack ?? [])].reverse();
  if (!frames.length) return [MSG.emptyStack];
  const pp = snap?.state === "paused" ? snap?.pause_point ?? {} : {};
  const pausedFid = pp.frame_id ?? null;
  return frames.map((f, n) => {
    const fid = String(f?.frame_id ?? "");
    let atSuffix = "";
    if (fid && fid === pausedFid) {
      let at = String(pp.signal ?? "");
      if (pp.step != null) at += ` step ${pp.step}`;
      atSuffix = ` at ${at}`;
    }
    return `#${n}  ${shortSkillName(f?.skill)} (${fid})${atSuffix}`;
  });
}

/* 选帧回声行(dbg.frame.row:`#N  skill (fid)`) */
export const frameRowLine = (n, f) =>
  `#${n}  ${shortSkillName(f?.skill)} (${String(f?.frame_id ?? "")})`;

/* ── help / apropos(从命令表生成,不硬编码;commands.py _help/_apropos 对译)── */

export function helpLines(args = []) {
  if (args.length) {
    const name = resolveWord(args[0], [...SPEC_BY_NAME.keys()], ALIAS);
    if (typeof name !== "string") return [fmt(MSG.unknown, { cmd: args[0] })];
    const spec = SPEC_BY_NAME.get(name);
    const aliases = spec.aliases.length ? `(别名: ${spec.aliases.join(", ")})` : "";
    return [
      `${`${spec.name} ${spec.usage}`.trimEnd()}  ${aliases}`.trimEnd(),
      `  ${spec.summary}`,
    ];
  }
  const width = Math.max(...COMMAND_SPECS.map((s) => s.name.length));
  return [
    MSG.helpHeader,
    ...COMMAND_SPECS.map((s) =>
      `  ${s.name.padEnd(width)}  ${s.usage.padEnd(28)} ${s.summary}`.trimEnd()),
  ];
}

export function aproposLines(args = []) {
  if (!args.length) return [fmt(MSG.usage, { usage: "apropos <词>" })];
  const word = args[0].toLowerCase();
  const hits = COMMAND_SPECS.filter((s) =>
    s.name.toLowerCase().includes(word) ||
    s.summary.toLowerCase().includes(word) ||
    s.usage.toLowerCase().includes(word));
  if (!hits.length) return [fmt(MSG.unknown, { cmd: args[0] })];
  return hits.map((s) => `${s.name} ${s.usage}  — ${s.summary}`.trimEnd());
}

/* 命令窗输出行的 HTML(页面层 appendLines 用;kind = cmd|out|err|stop) */
export const outLineHtml = (text, kind = "out") =>
  `<div class="dbc-line" data-kind="${esc(kind)}">${esc(text)}</div>`;

/* 执行轨迹(WEB-UI.md §4.2 中栏,debug trace 视图):把信号流重排成一段带行号的
   "程序执行轨迹"——缩进跟随帧栈深,call/ret 是头等指令,最新指令行带调试器黄箭头 ▶。
   参考 VS / VS Code 调用堆栈与调试控制台;"step N" 分组与"帧边界"组概念已退役。

   纯函数(不碰 DOM,node 单测可载):
     mergePairs(signals)                pre/post 配对(llm/tool/exec)→ [{ kind, pre, post }]
     buildTraceRows(signals, frames)    信号流 → 轨迹行[{ line, depth, kind, label, detail,
                                        durMs, frameId, status, sigIndex, sigEnd, step,
                                        kids, retLine, payload, ts, names }]
     collapseTrace(rows, callLine)      折叠某个 call 到其配对 ret 的整段子树(纯)
     deriveTraceView(signals, sel, opts) 过滤(帧树选帧)/ 折叠(默认深度 + 用户态)/ 聚焦
     flattenTraceRows(rows)             视图 → 窗口化平铺行(带估算高)
     findRowPosition(rows, signalIndex) 信号下标 → 平铺行位置(滚动定位)
     windowRange(total, scrollTop, ...) §6.3 窗口化可见行区间
     stepOfSignal(signals, index)       信号 → 所在帧当前 step 号(rca-panel 用)
     findVetoedCalls(signals)           veto 推断(pre 后同帧有后续信号但无 post)
   renderTrace 返回 HTML 字符串(opts.window 传窗口时只渲染切片 + 上下占位行);
   滚动/事件由 workbench 承担。

   行模型规则:
     call   pre:frame.push(+post)     → "→ call skill__fib({"n": 4})";无配对 ret 标 …(未返回)
     ret    pre:frame.pop(+post)      → "← ret {"seq":[0,1,1]}";失败帧红色;孤儿 ret 按当前 depth
     llm    pre:llm.request + 紧随 post:llm.response 合并 → "llm mock/fib → 1/1 tok · 0.4s"
     tool   pre/post:tool.call 合并   → "tool get_ticket({...}) ✓ 0.01s";ok:false / vetoed 红色
     exec   pre/post:logic.exec 合并  → "exec python_exec · trusted ✓";被 tool.call 全包时
                                        折进 tool 行(trust 标注带上),不重复出行
     obs    budget.* / compress / sidecar / 未知信号一行(黄)
     inline post:context.inline       → "⇥ inline date_style@1.0.0(+1) · 42 chars"
                                        (merge 内联能力快照,一次性;弱化色,不占语义色)
     run    run.started/finished/aborted(粗体行)
     depth  信号时刻的帧栈深(call 行 = 父 depth,子行 depth+1,ret 行回到父 depth)
     step   pre:step 只更新帧内当前 step(供检视器消息定位),自身不占行;
     pre/post:skill.invoke 不占行(pre 用于给紧随的 frame.push 起 skill__ 调用名;
     post ok:false 且无配对 push 时补一行失败调用,防静默丢错) */

import { absTs, esc, fmtCost, shortSkill } from "../util.js";

/* ── 窗口化常量(§6.3;行高与 app.css .tl-row 行高一致)────────────── */

export const ROW_H = 24; // 轨迹行高(px)
export const PAYLOAD_H = 208; // 展开的 payload 面板估算高(固定高 200 + 间距)
export const WINDOW_THRESHOLD = 500; // 渲染行数超过此值才启用窗口化
export const WINDOW_BUFFER = 50; // 视窗上下各多渲染的行数

/* call 默认折叠的深度阈:row.depth > 4(≈ 帧 payload depth ≥ 6)的 call 收起 */
export const DEFAULT_COLLAPSE_DEPTH = 4;

/* ── 小工具 ─────────────────────────────────────────────────── */

const shortJson = (v, max = 64) => {
  let s;
  try {
    s = JSON.stringify(v);
  } catch {
    s = String(v);
  }
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};

/* payload 摘要时剥掉到处都有的公共字段 */
const COMMON_KEYS = new Set(["depth", "frame_id", "skill"]);
const stripCommon = (p) =>
  Object.fromEntries(Object.entries(p ?? {}).filter(([k]) => !COMMON_KEYS.has(k)));

const durOf = (preTs, postTs) => {
  const a = Number(preTs);
  const b = Number(postTs);
  return Number.isFinite(a) && Number.isFinite(b) && b >= a ? (b - a) * 1000 : null;
};

/* 时长显示:<5ms 视为 mock 噪音不显示;<1s 两位小数去尾零;≥1s 一位小数 */
export const fmtDur = (ms) => {
  if (!Number.isFinite(ms) || ms < 5) return "";
  const s = ms / 1000;
  return s < 1 ? `${s.toFixed(2).replace(/0$/, "")}s` : `${s.toFixed(1)}s`;
};

/* ── veto 推断(rca-panel 复用)───────────────────────────────────
   帧内 loop 串行分发:pre:tool.call 之后循环又往下走了(post:step / 下一步 /
   另一个 tool.call / frame.pop…),而 post 始终未发 → 被否决/中断(§5.2:不发
   post:tool.call)。同帧 logic.exec 是这次调用的内部执行,不算"走过";
   流尾未配对不算 veto(live 中 post 未到在途;aborted 时在途被掐断),
   均由调用方标 "open"。返回被否决信号下标集合。 */
export function findVetoedCalls(signals) {
  const vetoed = new Set();
  (signals ?? []).forEach((sig, i) => {
    if (!sig || sig.name !== "pre:tool.call") return;
    for (let j = i + 1; j < signals.length; j += 1) {
      const nxt = signals[j];
      if (!nxt || (nxt.frame_id ?? null) !== (sig.frame_id ?? null)) continue;
      if (nxt.name === "post:tool.call") break; // 正常配对
      if (nxt.name === "pre:logic.exec" || nxt.name === "post:logic.exec") continue; // 调用内部
      vetoed.add(i);
      break;
    }
  });
  return vetoed;
}

/* 信号 → 所在帧当前 step(自 index 向前找同帧最近 pre:step;rca-panel 定位用) */
export function stepOfSignal(signals, index) {
  const rows = Array.isArray(signals) ? signals : [];
  const fid = rows[index]?.frame_id ?? null;
  for (let i = Math.min(index, rows.length - 1); i >= 0; i -= 1) {
    const s = rows[i];
    if (s?.name === "pre:step" && (s.frame_id ?? null) === fid) return s.payload?.step ?? null;
  }
  return null;
}

/* ── mergePairs:llm / tool / exec 的 pre+post 配对 ─────────────────
   返回 [{ kind: "llm"|"tool"|"exec", pre, post|null }](按 pre 升序)。
   配对按帧隔离(帧内串行分发);同帧前一个 pre 未遇 post 就迎来下一个 pre
   → 前一个记未配对(post=null;vetoed 与 open/在途由 buildTraceRows 区分)。 */
const PAIR_SPECS = [
  ["pre:llm.request", "post:llm.response", "llm"],
  ["pre:tool.call", "post:tool.call", "tool"],
  ["pre:logic.exec", "post:logic.exec", "exec"],
];

export function mergePairs(signals) {
  const rows = Array.isArray(signals) ? signals : [];
  const pairs = [];
  for (const [preName, postName, kind] of PAIR_SPECS) {
    const openByFrame = new Map(); // frameId -> pre 下标
    rows.forEach((sig, i) => {
      if (!sig || typeof sig !== "object") return;
      const fid = sig.frame_id ?? null;
      if (sig.name === preName) {
        if (openByFrame.has(fid)) pairs.push({ kind, pre: openByFrame.get(fid), post: null });
        openByFrame.set(fid, i);
      } else if (sig.name === postName && openByFrame.has(fid)) {
        pairs.push({ kind, pre: openByFrame.get(fid), post: i });
        openByFrame.delete(fid);
      }
    });
    for (const pre of openByFrame.values()) pairs.push({ kind, pre, post: null });
  }
  return pairs.sort((a, b) => a.pre - b.pre);
}

/* ── buildTraceRows:信号流 → 轨迹行 ──────────────────────────────
   frames = detail.frames 帧摘要(可带 input;缺省/缺字段均降级):
   帧 status 供 call/ret 着色,input 供 call 行参数显示。 */
export function buildTraceRows(signals, frames = []) {
  const sigs = Array.isArray(signals) ? signals : [];
  const frameStatus = new Map();
  const frameInput = new Map();
  for (const f of Array.isArray(frames) ? frames : []) {
    if (!f?.frame_id) continue;
    if (f.status != null) frameStatus.set(f.frame_id, f.status);
    if (f.input !== undefined) frameInput.set(f.frame_id, f.input);
  }

  const pairs = mergePairs(sigs);
  const pairByPre = new Map(pairs.map((p) => [p.pre, p]));
  const postCovered = new Set(pairs.filter((p) => p.post != null).map((p) => p.post));
  const vetoedIdx = findVetoedCalls(sigs); // veto 推断(与 rca-panel 同源)
  // 被 tool.call 全包的 logic.exec 折进 tool 行(builtin 工具的实现细节,不重复出行)
  for (const ep of pairs) {
    if (ep.kind !== "exec" || ep.post == null) continue;
    const tp = pairs.find(
      (t) => t.kind === "tool" && t.pre < ep.pre && t.post != null && ep.post < t.post);
    if (tp) ep.foldedInto = tp.pre;
  }

  const rows = [];
  const stack = []; // [{ frameId, call }] 当前打开的帧(call 行引用,ret 时配对)
  const stepByFrame = new Map(); // frameId -> 当前 step(pre:step 更新)
  let pendingInvoke = null; // { skill, sigIndex, consumed } pre:skill.invoke 待配对 push

  const mkRow = (row) => {
    rows.push(row);
    return row;
  };

  sigs.forEach((sig, i) => {
    if (!sig || typeof sig !== "object") return;
    const name = String(sig.name ?? "");
    const p = sig.payload ?? {};
    const fid = sig.frame_id ?? null;
    const depth = stack.length;
    const step = stepByFrame.get(fid) ?? null;

    /* run 边界:粗体行 */
    if (name === "run.started" || name === "run.finished" || name === "run.aborted") {
      const status = name === "run.started" ? "running" : name === "run.finished" ? "done" : "aborted";
      mkRow({
        kind: "run", depth: 0, label: name.replace(".", " "), status,
        detail: name === "run.started" ? shortSkill(p.skill) : "",
        durMs: null, frameId: null, sigIndex: i, sigEnd: i, step: null,
        payload: stripCommon(p), ts: sig.ts, names: name,
      });
      return;
    }

    /* call:pre:frame.push(+紧随 post)合并 */
    if (name === "pre:frame.push") {
      let end = i;
      if (sigs[i + 1]?.name === "post:frame.push" && (sigs[i + 1]?.frame_id ?? null) === fid) end = i + 1;
      const invoked = pendingInvoke && !pendingInvoke.consumed ? pendingInvoke : null;
      if (invoked) invoked.consumed = true;
      const args = frameInput.get(fid);
      const row = mkRow({
        kind: "call", depth, status: frameStatus.get(fid) ?? "running",
        label: invoked ? `skill__${shortSkill(invoked.skill)}` : shortSkill(p.skill),
        detail: args != null ? shortJson(args) : "",
        durMs: null, frameId: fid, parentFrameId: stack.at(-1)?.frameId ?? null,
        sigIndex: i, sigEnd: end,
        step: stepByFrame.get(stack.at(-1)?.frameId ?? null) ?? null, // 调用点所在 step(父帧)
        payload: { skill: p.skill ?? null, input: args ?? null },
        ts: sig.ts, names: end > i ? "pre:frame.push → post:frame.push" : name,
      });
      stack.push({ frameId: fid, call: row });
      return;
    }
    if (name === "post:frame.push") return; // 已并入 call

    /* step:只更新帧内当前 step,不占行(§4.2:"step N" 组概念退役) */
    if (name === "pre:step") {
      stepByFrame.set(fid, p.step ?? null);
      return;
    }
    if (name === "post:step") return;

    /* skill.invoke:不占行;pre 给紧随的 push 起调用名,post ok:false 且无 push 补错行 */
    if (name === "pre:skill.invoke") {
      pendingInvoke = { skill: p.skill, sigIndex: i, consumed: false };
      return;
    }
    if (name === "post:skill.invoke") {
      if (p.ok === false && pendingInvoke && !pendingInvoke.consumed) {
        mkRow({
          kind: "tool", depth, status: "failed",
          label: `skill__${shortSkill(p.skill)}`, detail: "调用失败",
          durMs: null, frameId: fid, sigIndex: pendingInvoke.sigIndex, sigEnd: i, step,
          payload: stripCommon(p), ts: sig.ts, names: "pre:skill.invoke → post:skill.invoke",
        });
      }
      pendingInvoke = null;
      return;
    }

    /* post:context.inline(SKILL-INLINING.md §7):帧首次 build 的一次性内联能力行。
       payload {frame_id, skills:[{name, version, chars}]};弱化样式(不占 call/ret
       语义色),payload 面板照常可展开。 */
    if (name === "post:context.inline") {
      const caps = Array.isArray(p.skills) ? p.skills : [];
      const total = caps.reduce((acc, c) => acc + (Number(c?.chars) || 0), 0);
      const firstCap = caps[0];
      const extra = caps.length > 1 ? `(+${caps.length - 1})` : "";
      mkRow({
        kind: "inline", depth, status: "obs",
        label: firstCap ? `${shortSkill(firstCap.name)}@${firstCap.version ?? "—"}${extra}` : "—",
        detail: caps.length ? `${total} chars` : "",
        durMs: null, frameId: fid, sigIndex: i, sigEnd: i, step,
        payload: stripCommon(p), ts: sig.ts, names: name,
      });
      return;
    }

    /* ret:pre:frame.pop(+紧随 post)合并;pre 携带 result */
    if (name === "pre:frame.pop") {
      let end = i;
      if (sigs[i + 1]?.name === "post:frame.pop" && (sigs[i + 1]?.frame_id ?? null) === fid) end = i + 1;
      let call = null;
      for (let k = stack.length - 1; k >= 0; k -= 1) {
        if (stack[k].frameId === fid) {
          for (let m = stack.length - 1; m > k; m -= 1) {
            const e = stack.pop(); // 被跨越的内层帧:嵌套中断,标未返回
            if (e.call.status === "running") e.call.status = "open";
          }
          call = stack.pop().call;
          break;
        }
      }
      const st = frameStatus.get(fid) ?? null;
      const row = mkRow({
        kind: "ret", depth: stack.length, label: "ret",
        status: st ?? (call ? "done" : "orphan"),
        detail: p.result !== undefined ? shortJson(p.result) : "",
        durMs: call ? durOf(call.ts, sig.ts) : null,
        frameId: fid, parentFrameId: stack.at(-1)?.frameId ?? null,
        sigIndex: i, sigEnd: end, step,
        payload: { result: p.result ?? null },
        ts: sig.ts, names: end > i ? "pre:frame.pop → post:frame.pop" : name,
      });
      if (call) {
        call.durMs = row.durMs;
        call.status = st ?? "done";
        call.retLine = null; // 行号未分配,收尾统一回填
        call._ret = row;
        row._call = call;
      }
      return;
    }
    if (name === "post:frame.pop") return; // 已并入 ret

    /* llm / tool / exec 合并行 */
    const pair = pairByPre.get(i);
    if (pair) {
      if (pair.kind === "exec" && pair.foldedInto != null) return; // 折进 tool 行
      const post = pair.post != null ? sigs[pair.post] : null;
      const pp = post?.payload ?? {};
      const durMs = post ? durOf(sig.ts, post.ts) : null;
      const names = post ? `${name} → ${post.name}` : name;
      if (pair.kind === "llm") {
        const u = pp.usage ?? {};
        const cost = Number(u.cost) > 0 ? ` · ${fmtCost(u.cost)}` : "";
        mkRow({
          kind: "llm", depth, status: post ? "done" : "open",
          label: String(p.model ?? pp.model ?? "—"),
          detail: `${u.prompt ?? 0}/${u.completion ?? 0} tok${cost}`,
          durMs, frameId: fid, sigIndex: i, sigEnd: pair.post ?? i, step,
          payload: { pre: stripCommon(p), post: post ? stripCommon(pp) : null },
          ts: sig.ts, names,
        });
      } else if (pair.kind === "tool") {
        /* 未配对区分:vetoed(后续同帧 tool 信号非 post,§5.2 推断)与
           open(流尾在途,live 中 post 未到;aborted 时在途被掐断) */
        const status =
          pair.post == null ? (vetoedIdx.has(i) ? "vetoed" : "open") : pp.ok === false ? "failed" : "done";
        const folded = pairs.find((ep) => ep.kind === "exec" && ep.foldedInto === i);
        const trust = sigs[folded?.pre]?.payload?.trust ?? p.trust ?? null;
        mkRow({
          kind: "tool", depth, status, trust,
          label: String(p.tool ?? pp.tool ?? "—"),
          detail: shortJson(p.args ?? {}),
          durMs, frameId: fid, sigIndex: i, sigEnd: pair.post ?? i, step,
          payload: { pre: stripCommon(p), post: post ? stripCommon(pp) : null },
          ts: sig.ts, names,
        });
      } else {
        mkRow({
          kind: "exec", depth,
          status: pp.ok === false ? "failed" : post ? "done" : "open",
          label: String(p.tool ?? shortSkill(p.skill) ?? p.language ?? "logic"),
          detail: [p.language, p.trust ?? pp.trust].filter(Boolean).join(" · "),
          durMs, frameId: fid, sigIndex: i, sigEnd: pair.post ?? i, step,
          payload: { pre: stripCommon(p), post: post ? stripCommon(pp) : null },
          ts: sig.ts, names,
        });
      }
      return;
    }
    if (postCovered.has(i)) return; // 已并入配对行

    /* 其余:obs 行(budget.* / compress / sidecar / veto / 未识别信号,黄色) */
    const anomalous = name === "budget.exceeded" || /veto/i.test(name) || p.ok === false;
    mkRow({
      kind: "obs", depth, status: anomalous ? "failed" : "obs",
      label: name, detail: shortJson(stripCommon(p)),
      durMs: null, frameId: fid, sigIndex: i, sigEnd: i, step,
      payload: stripCommon(p), ts: sig.ts, names: name,
    });
  });

  /* 收尾:行号 / 未返回 call / call↔ret 配对回填(kids、retLine)。
     未配对 call 的子树到末尾为止,但尾部 run 行(finished/aborted)不属于任何帧,
     折叠与子指令计数都停在首个后续 run 行前。 */
  rows.forEach((r, idx) => {
    r.line = idx + 1;
  });
  for (const e of stack) if (e.call.status === "running") e.call.status = "open";
  const firstRunLineAfter = (line) =>
    rows.find((r) => r.kind === "run" && r.line > line)?.line ?? rows.length + 1;
  for (const r of rows) {
    if (r.kind !== "call") continue;
    const ret = r._ret ?? null;
    r.retLine = ret ? ret.line : null;
    r.kids = (ret ? ret.line : firstRunLineAfter(r.line)) - r.line - 1;
    delete r._ret;
  }
  for (const r of rows) delete r._call;
  return rows;
}

/* ── collapseTrace:折叠 call 到配对 ret 的整段子树(纯)──────────────
   rows 中 callLine 对应 call 行:返回新数组,call 行标 collapsed,
   (call, ret) 区间内的行标 hidden(已 hidden 的保持);未配对 ret 折到
   子树末(不含尾部 run 行,end 由 kids 推出,与 buildTraceRows 同语义)。
   非 call 行 / 已折叠 → 原样返回。 */
export function collapseTrace(rows, callLine) {
  const list = Array.isArray(rows) ? rows : [];
  const call = list.find((r) => r?.line === callLine && r.kind === "call");
  if (!call || call.collapsed) return list;
  const end = call.retLine ?? call.line + (call.kids ?? 0) + 1;
  return list.map((r) => {
    if (r.line === call.line) return { ...r, collapsed: true };
    if (!r.hidden && r.line > call.line && r.line < end) return { ...r, hidden: true };
    return r;
  });
}

/* call 行有效折叠态:用户显式展开 > 用户显式折叠 > 深度默认折叠 */
export const isCallCollapsed = (row, collapsed, expanded) =>
  row?.kind === "call" &&
  !expanded?.has(row.sigIndex) &&
  (collapsed?.has(row.sigIndex) === true || row.depth > DEFAULT_COLLAPSE_DEPTH);

/* 信号下标 → 轨迹行:优先精确覆盖([sigIndex, sigEnd] 区间命中的合并行);
   未命中(不占行的 pre:step / skill.invoke 等)→ 最近的可见前行(sigIndex ≤ 目标)。
   RCA 回退定位/深链接指向非行信号时,仍能落到最近指令行。 */
export function rowForSignal(rows, signalIndex) {
  const list = Array.isArray(rows) ? rows : [];
  let best = null;
  for (const r of list) {
    if (!r) continue;
    if (r.sigIndex <= signalIndex && signalIndex <= (r.sigEnd ?? r.sigIndex)) return r;
    if (r.sigIndex <= signalIndex && (!best || r.sigIndex > best.sigIndex)) best = r;
  }
  return best;
}

/* ── deriveTraceView:过滤 / 折叠 / 聚焦的派生视图 ──────────────────
   selection = { frameId, signalIndex, source } | null:
     source "tree" → 只保留该帧的行 + 直接子帧的 call/ret 边界行
     (parentFrameId 命中;调试器 step-over 视角,边界行在视图内重算 kids);
     其余来源 → 不过滤,focused 指向所在行(高亮 + scrollIntoView)。
   opts: frames(帧摘要+input)/ collapsed / expanded / payloadOpen(payload 展开集)。
   返回 { rows(视图行,含 hidden/collapsed 标记), all(全量行), focused,
   filtered, filterFrameId, hiddenCount, payloadOpen }。 */
export function deriveTraceView(signals, selection, opts = {}) {
  const all = buildTraceRows(signals, opts.frames);
  const collapsed = opts.collapsed ?? new Set();
  const expanded = opts.expanded ?? new Set();
  const payloadOpen = opts.payloadOpen ?? new Set();
  const filtering = Boolean(selection?.frameId) && selection?.source === "tree";
  let rows = filtering
    ? all.filter(
        (r) => r.frameId === selection.frameId || r.parentFrameId === selection.frameId)
    : all.slice();

  /* 过滤视图:直接子帧的 call/ret 保留为边界行(调试器 step-over 视角),
     但其内部行不在视图 → 视图内重算有效 kids(边界 call 无 chevron) */
  if (filtering) {
    const posByLine = new Map(rows.map((r, i) => [r.line, i]));
    rows = rows.map((r, i) => {
      if (r.kind !== "call") return r;
      const retPos = r.retLine != null ? posByLine.get(r.retLine) : undefined;
      const kids = (retPos ?? rows.length) - i - 1;
      return kids === r.kids ? r : { ...r, kids };
    });
  }

  /* 折叠:按行序应用有效折叠的 call;已隐藏子树内的 call 跳过(外层已折) */
  const calls = rows.filter(
    (r) => r.kind === "call" && (r.kids ?? 0) > 0 && isCallCollapsed(r, collapsed, expanded));
  for (const c of calls) {
    if (rows.find((r) => r.line === c.line)?.hidden) continue;
    rows = collapseTrace(rows, c.line);
  }

  /* payload 展开态标到视图行(hidden 行不展开) */
  if (payloadOpen.size) {
    rows = rows.map((r) =>
      !r.hidden && payloadOpen.has(r.sigIndex) ? { ...r, payloadOpen: true } : r);
  }

  let focused = null;
  if (selection?.signalIndex != null) {
    const inAll = rowForSignal(all, selection.signalIndex);
    const inView = rowForSignal(rows.filter((r) => !r.hidden), selection.signalIndex);
    if (inAll) {
      focused = {
        signalIndex: selection.signalIndex,
        line: inAll.line,
        visible: Boolean(inView), // 精确行被折叠隐藏时,inView 落到外层可见行(仍可滚动)
      };
    }
  }
  return {
    rows,
    all,
    focused,
    filtered: filtering,
    filterFrameId: filtering ? selection.frameId : null,
    hiddenCount: filtering ? all.length - rows.length : 0,
    payloadOpen,
  };
}

/* ── 窗口化(§6.3:>500 渲染行只渲染视窗 ±buffer,上下占位行)─────── */

/* 可见窗口纯函数:总行数 + 滚动位置 → [start, end) 行区间(边界 clamp)。 */
export function windowRange(total, scrollTop, rowH, viewportH, buffer = WINDOW_BUFFER) {
  const t = Math.max(0, Math.floor(Number(total) || 0));
  if (!t) return { start: 0, end: 0 };
  const rh = Number(rowH) > 0 ? Number(rowH) : ROW_H;
  const st = Math.max(0, Number(scrollTop) || 0);
  const vh = Math.max(0, Number(viewportH) || 0);
  const buf = Math.max(0, Math.floor(Number(buffer) || 0));
  const start = Math.max(0, Math.min(t - 1, Math.floor(st / rh)) - buf);
  const end = Math.min(t, Math.max(Math.ceil((st + vh) / rh), Math.floor(st / rh) + 1) + buf);
  return { start, end: Math.max(start, end) };
}

/* 视图行 → 平铺渲染行(只含可见行;每行带估算高 h,payload 展开加面板高) */
export function flattenTraceRows(rows) {
  const out = [];
  for (const r of rows ?? []) {
    if (r.hidden) continue;
    out.push({ type: "row", row: r, h: ROW_H + (r.payloadOpen ? PAYLOAD_H : 0) });
  }
  return out;
}

/* 信号全局下标 → 平铺行位置 { index(行下标), offset(距顶估算 px) };
   合并行匹配区间 [sigIndex, sigEnd];无覆盖(非行信号 / 折叠子树内)落最近前行。 */
export function findRowPosition(rows, signalIndex) {
  let offset = 0;
  let best = null;
  for (let i = 0; i < (rows ?? []).length; i += 1) {
    const r = rows[i];
    const row = r?.row;
    if (row) {
      if (row.sigIndex <= signalIndex && signalIndex <= (row.sigEnd ?? row.sigIndex)) {
        return { index: i, offset };
      }
      if (row.sigIndex <= signalIndex) best = { index: i, offset };
    }
    offset += r?.h ?? ROW_H;
  }
  return best;
}

/* ── 渲染(HTML 字符串;DOM 接线在 workbench)───────────────────── */

const CHEV_SVG =
  `<svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor"` +
  ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
  `<path d="M6 3 L11 8 L6 13"/></svg>`;

/* 深度彩虹轨:depth 条 2px 竖条,颜色按层取 6 色循环(token --trace-d0..d5) */
const indentHtml = (depth) => {
  const d = Math.max(0, Math.floor(Number(depth) || 0));
  if (!d) return `<span class="tr-indent" aria-hidden="true"></span>`;
  let s = `<span class="tr-indent" aria-hidden="true">`;
  for (let i = 0; i < d; i += 1) s += `<i class="tr-g" data-d="${i % 6}"></i>`;
  return `${s}</span>`;
};

const OK_MARK = { done: `<span class="tr-ok">✓</span>`, failed: `<span class="tr-bad">✗</span>` };

/* 行主体:kind 分派(debugger 控制台风:箭头/关键字着色 + 名 600 + 参数弱色) */
const paramsHtml = (detail) =>
  detail ? `<span class="tr-params">(${esc(detail)})</span>` : "";

function bodyHtml(r) {
  const detail = r.detail ? `<span class="tr-args">${esc(r.detail)}</span>` : "";
  switch (r.kind) {
    case "run":
      return (
        `<span class="tr-dot" data-status="${esc(r.status)}" aria-hidden="true"></span>` +
        `<span class="tr-run">${esc(r.label)}</span>${detail}`
      );
    case "call": {
      const open =
        r.status === "open" || r.status === "running"
          ? `<span class="tr-open">…(未返回)</span>`
          : "";
      const kids = r.collapsed ? `<span class="tr-kids">+${r.kids ?? 0}</span>` : "";
      return (
        `<span class="tr-arrow">→</span><span class="tr-kw">call</span>` +
        `<span class="tr-name">${esc(r.label)}</span>${paramsHtml(r.detail)}${open}${kids}`
      );
    }
    case "ret":
      return (
        `<span class="tr-arrow">←</span><span class="tr-kw">ret</span>` +
        (r.status === "failed" ? `<span class="tr-bad">✗</span>` : "") +
        (detail || `<span class="tr-args">—</span>`)
      );
    case "llm":
      return (
        `<span class="tr-kw" data-k="llm">llm</span><span class="tr-name">${esc(r.label)}</span>` +
        `<span class="tr-args">→ ${esc(r.detail)}</span>`
      );
    case "tool": {
      const mark = r.status === "vetoed"
        ? `<span class="tl-veto">vetoed</span>`
        : OK_MARK[r.status] ?? "";
      const trust = r.trust ? `<span class="tr-trust">${esc(r.trust)}</span>` : "";
      return (
        `<span class="tr-kw" data-k="tool">tool</span><span class="tr-name">${esc(r.label)}</span>` +
        `${paramsHtml(r.detail)}${trust}${mark}`
      );
    }
    case "exec":
      return (
        `<span class="tr-kw" data-k="exec">exec</span><span class="tr-name">${esc(r.label)}</span>` +
        `${detail}${OK_MARK[r.status] ?? ""}`
      );
    case "inline": // ⇥ inline:merge 内联能力快照(弱化色,不占 call/ret 语义色)
      return (
        `<span class="tr-kw" data-k="inline">⇥ inline</span>` +
        `<span class="tr-args">${esc(r.label)}${r.detail ? ` · ${esc(r.detail)}` : ""}</span>`
      );
    default: // obs
      return `<span class="tr-kw" data-k="obs">obs</span><span class="tr-args">${esc(r.label)}${r.detail ? ` ${esc(r.detail)}` : ""}</span>`;
  }
}

function rowHtml(r, selection, currentLine) {
  const sel =
    selection?.signalIndex != null &&
    r.sigIndex <= selection.signalIndex &&
    selection.signalIndex <= (r.sigEnd ?? r.sigIndex);
  const cur = r.line === currentLine;
  const chevron =
    r.kind === "call" && (r.kids ?? 0) > 0
      ? `<button class="tr-chev" data-action="tr-toggle" data-sig="${r.sigIndex}"` +
        ` aria-expanded="${!r.collapsed}" title="折叠/展开该调用的子树" aria-label="折叠/展开调用子树">${CHEV_SVG}</button>`
      : `<span class="tr-chev-sp" aria-hidden="true"></span>`;
  const payloadBtn =
    `<button class="tr-json" data-action="tr-payload" data-sig="${r.sigIndex}"` +
    ` title="展开/收起完整 payload" aria-label="展开完整 payload" aria-expanded="${Boolean(r.payloadOpen)}">{}</button>`;
  const dur = fmtDur(r.durMs);
  const statusAttr = ["failed", "vetoed", "aborted", "open", "running"].includes(r.status)
    ? ` data-status="${esc(r.status)}"`
    : "";
  const row =
    `<div class="tl-row" data-signal-index="${r.sigIndex}" data-sig-end="${r.sigEnd ?? r.sigIndex}"` +
    ` data-frame-id="${esc(r.frameId ?? "")}" data-kind="${esc(r.kind)}"` +
    ` data-line="${r.line}" role="option" tabindex="0" aria-selected="${sel}"` +
    (r.status === "vetoed" ? ` data-vetoed="true"` : "") +
    statusAttr +
    ` title="${esc(r.names ?? "")} · ${esc(absTs(r.ts))}">` +
    `<span class="tr-gutter" aria-hidden="true">${String(r.line).padStart(4, "0")}</span>` +
    `<span class="tr-mark" aria-hidden="true">${cur ? "▶" : ""}</span>` +
    indentHtml(r.depth) +
    chevron +
    `<span class="tr-body">${bodyHtml(r)}</span>` +
    payloadBtn +
    `<span class="tr-dur">${esc(dur)}</span>` +
    `</div>`;
  if (!r.payloadOpen) return row;
  let pretty;
  try {
    pretty = JSON.stringify(r.payload ?? null, null, 2);
  } catch {
    pretty = String(r.payload);
  }
  return (
    row +
    `<div class="tr-payload" data-line="${r.line}" style="margin-left:calc(5ch + var(--s2) + var(--s4) + var(--s4) + var(--s1) + ${Math.max(0, r.depth) * 12}px)">` +
    `<div class="tr-payload-head">payload · ${esc(r.names ?? "")}</div>` +
    `<pre class="tr-payload-pre">${esc(pretty)}</pre>` +
    `</div>`
  );
}

/* 占位行(§6.3:"还有 N 行"):高度 = 被裁行估算高之和,维持滚动位置 */
function gapHtml(count, height, side) {
  if (height <= 0) return "";
  return (
    `<div class="tl-gap" data-side="${side}" style="height:${Math.max(0, Math.round(height))}px"` +
    ` aria-hidden="true">` +
    (count > 0 ? `<span class="tl-gap-text">还有 ${count} 行</span>` : "") +
    `</div>`
  );
}

/* 轨迹整体 HTML:过滤提示条(帧树选帧,"已过滤 f-x,点击清除")+ 行列表。
   当前位置 ▶ = 最后一行可见指令(完成 run 即末行;live 跟随最新信号)。
   opts.window(窗口化,§6.3)= { start, end, topPad, bottomPad, topCount, bottomCount }:
   只渲染 [start,end) 平铺行,上下以占位行补齐高度;缺省渲染全部。 */
export function renderTrace(view, { selection = null, frameSkill = null, window: win = null } = {}) {
  const visible = (view?.rows ?? []).filter((r) => !r.hidden);
  if (!visible.length) return "";
  const currentLine = visible[visible.length - 1]?.line ?? null;
  const filterBar = view.filtered
    ? `<div class="tl-filter" data-action="tl-clear" role="button" tabindex="0"` +
      ` title="点击清除过滤,显示全部指令">` +
      `已过滤 <b>${esc(frameSkill ?? "—")} · f-${esc(String(view.filterFrameId).slice(0, 6))}</b>` +
      `<span class="tl-filter-hint">点击清除 ✕</span>` +
      `</div>`
    : "";
  const flat = flattenTraceRows(view.rows);
  const slice = win ? flat.slice(win.start, win.end) : flat;
  const body =
    (win ? gapHtml(win.topCount, win.topPad, "top") : "") +
    slice.map((f) => rowHtml(f.row, selection, currentLine)).join("") +
    (win ? gapHtml(win.bottomCount, win.bottomPad, "bottom") : "");
  return (
    filterBar +
    `<div class="tl-list tr-list" role="listbox" aria-label="执行轨迹">` +
    body +
    `</div>`
  );
}

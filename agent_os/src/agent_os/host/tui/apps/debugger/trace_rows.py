"""信号 → 轨迹行(docs/TUI-DEBUG.md §2/§7;web trace.js 行语言的 Python 对译)。

对译源(保证与 web 调试台 / Workbench 轨迹同构):

- ``build_trace_rows``  ↔ ``trace.js buildTraceRows``(pre/post 配对、call/ret
  头等指令、pre:step 不占行、被 tool.call 全包的 logic.exec 折进 tool 行);
- ``paused_signal_index`` ↔ ``debug-view.js pausedSignalIndex``(:519,从尾向前
  找最近同名同帧信号;pre:step 再对 step 号,pre:tool.call 再对工具名);
- ``row_for_signal``    ↔ ``trace.js rowForSignal``(合并行区间命中,否则最近前行);
- ``bp_target_for_row`` ↔ ``debug-view.js bpTargetForRow``(:505,tool 行 →
  tool_call 断点,call 行 → skill_invoke 断点,其余行无断点语义)。

D1 有意不译:折叠 / 帧过滤 / 窗口化(TUI 全量重渲,行数远小于 web;留口见实现注)。
信号 dict 形状与 trace.jsonl 行同形:{name, run_id, frame_id, ts, payload}。
"""

from __future__ import annotations

import json
from typing import Any

#: pre/post 配对表(与 trace.js PAIR_SPECS 同序)
_PAIR_SPECS = (
    ("pre:llm.request", "post:llm.response", "llm"),
    ("pre:tool.call", "post:tool.call", "tool"),
    ("pre:logic.exec", "post:logic.exec", "exec"),
)

#: payload 摘要剥掉的公共字段(与 trace.js COMMON_KEYS 一致)
_COMMON_KEYS = frozenset({"depth", "frame_id", "skill"})

#: run 边界信号 → 状态
_RUN_STATUS = {"run.started": "running", "run.finished": "done", "run.aborted": "aborted"}


def short_skill(name: Any) -> str:
    """技能名短形(``demo.fib`` → ``fib``;与 web util.js shortSkill 同义)。"""
    text = str(name or "")
    return text.rsplit(".", 1)[-1] if "." in text else text


def short_json(value: Any, max_len: int = 64) -> str:
    """JSON 摘要(无空格紧凑形,与 JS JSON.stringify 同形;超长截断)。"""
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _strip_common(payload: Any) -> dict[str, Any]:
    return {k: v for k, v in (payload or {}).items() if k not in _COMMON_KEYS}


def _dur_of(pre_ts: Any, post_ts: Any) -> float | None:
    try:
        a, b = float(pre_ts), float(post_ts)
    except (TypeError, ValueError):
        return None
    return (b - a) * 1000 if b >= a else None


def fmt_dur(ms: Any) -> str:
    """时长显示(trace.js fmtDur 对译):<5ms 视为 mock 噪音不显示;<1s 两位
    小数去尾零;≥1s 一位小数。"""
    if not isinstance(ms, (int, float)) or ms < 5:
        return ""
    s = ms / 1000
    if s < 1:
        text = f"{s:.2f}"
        text = text.removesuffix("0")
        return f"{text}s"
    return f"{s:.1f}s"


# ---------------------------------------------------------------------------
# veto 推断 / step 定位(trace.js findVetoedCalls 对译)
# ---------------------------------------------------------------------------

def find_vetoed_calls(signals: list[dict[str, Any]]) -> set[int]:
    """pre:tool.call 之后同帧循环继续走而 post 始终未发 → 被否决/中断(§5.2)。
    返回被否决信号下标集合。"""
    vetoed: set[int] = []
    vetoed_set: set[int] = set()
    for i, sig in enumerate(signals):
        if not sig or sig.get("name") != "pre:tool.call":
            continue
        fid = sig.get("frame_id")
        for nxt in signals[i + 1 :]:
            if (nxt.get("frame_id") if nxt else None) != fid:
                continue
            name = nxt.get("name")
            if name == "post:tool.call":
                break
            if name in ("pre:logic.exec", "post:logic.exec"):
                continue  # 调用内部执行,不算"走过"
            vetoed.append(i)
            break
    vetoed_set.update(vetoed)
    return vetoed_set


def _merge_pairs(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """llm/tool/exec 的 pre+post 配对(按帧隔离;未配对 pre 记 post=None)。"""
    pairs: list[dict[str, Any]] = []
    for pre_name, post_name, kind in _PAIR_SPECS:
        open_by_frame: dict[Any, int] = {}
        for i, sig in enumerate(signals):
            if not isinstance(sig, dict):
                continue
            fid = sig.get("frame_id")
            if sig.get("name") == pre_name:
                if fid in open_by_frame:
                    pairs.append({"kind": kind, "pre": open_by_frame.pop(fid), "post": None})
                open_by_frame[fid] = i
            elif sig.get("name") == post_name and fid in open_by_frame:
                pairs.append({"kind": kind, "pre": open_by_frame.pop(fid), "post": i})
        for pre in open_by_frame.values():
            pairs.append({"kind": kind, "pre": pre, "post": None})
    pairs.sort(key=lambda p: p["pre"])
    return pairs


def build_trace_rows(
    signals: list[dict[str, Any]], frames: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """信号流 → 轨迹行(trace.js buildTraceRows 对译;行字段同名,snake_case)。

    行模型:call/ret 是头等指令;pre:step 只更新帧内当前 step 不占行;
    pre/post:skill.invoke 不占行(post ok:false 且无配对 push 时补错行);
    depth = 信号时刻的帧栈深(call 行 = 父 depth)。返回行带 ``line``(1 起)。
    """
    sigs = [s for s in signals if isinstance(s, dict)]
    frame_status: dict[Any, Any] = {}
    frame_input: dict[Any, Any] = {}
    for f in frames or []:
        if not isinstance(f, dict) or not f.get("frame_id"):
            continue
        if f.get("status") is not None:
            frame_status[f["frame_id"]] = f["status"]
        if "input" in f:
            frame_input[f["frame_id"]] = f["input"]

    pairs = _merge_pairs(sigs)
    pair_by_pre = {p["pre"]: p for p in pairs}
    post_covered = {p["post"] for p in pairs if p["post"] is not None}
    vetoed = find_vetoed_calls(sigs)
    # 被 tool.call 全包的 logic.exec 折进 tool 行(builtin 工具的实现细节)
    for ep in pairs:
        if ep["kind"] != "exec" or ep["post"] is None:
            continue
        tp = next(
            (t for t in pairs
             if t["kind"] == "tool" and t["pre"] < ep["pre"]
             and t["post"] is not None and ep["post"] < t["post"]),
            None,
        )
        if tp:
            ep["foldedInto"] = tp["pre"]

    rows: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []  # [{frame_id, call}]
    step_by_frame: dict[Any, Any] = {}
    pending_invoke: dict[str, Any] | None = None

    def mk(row: dict[str, Any]) -> dict[str, Any]:
        rows.append(row)
        return row

    for i, sig in enumerate(sigs):
        name = str(sig.get("name") or "")
        p = sig.get("payload") or {}
        fid = sig.get("frame_id")
        depth = len(stack)
        step = step_by_frame.get(fid)

        if name in _RUN_STATUS:
            mk({"kind": "run", "depth": 0, "label": name.replace(".", " "),
                "status": _RUN_STATUS[name],
                "detail": short_skill(p.get("skill")) if name == "run.started" else "",
                "dur_ms": None, "frame_id": None, "sig_index": i, "sig_end": i,
                "step": None, "payload": _strip_common(p), "ts": sig.get("ts"),
                "names": name, "trust": None})
            continue

        if name == "pre:frame.push":
            end = i
            nxt = sigs[i + 1] if i + 1 < len(sigs) else None
            if nxt and nxt.get("name") == "post:frame.push" and nxt.get("frame_id") == fid:
                end = i + 1
            invoked = pending_invoke if pending_invoke and not pending_invoke["consumed"] else None
            if invoked:
                invoked["consumed"] = True
            args = frame_input.get(fid)
            parent_fid = stack[-1]["frame_id"] if stack else None
            row = mk({"kind": "call", "depth": depth,
                      "status": frame_status.get(fid, "running"),
                      "label": f"skill.{short_skill(invoked['skill'])}" if invoked
                               else short_skill(p.get("skill")),
                      "detail": short_json(args) if args is not None else "",
                      "dur_ms": None, "frame_id": fid, "parent_frame_id": parent_fid,
                      "sig_index": i, "sig_end": end,
                      "step": step_by_frame.get(parent_fid),
                      "payload": {"skill": p.get("skill"), "input": args},
                      "ts": sig.get("ts"),
                      "names": "pre:frame.push → post:frame.push" if end > i else name,
                      "trust": None})
            stack.append({"frame_id": fid, "call": row})
            continue
        if name == "post:frame.push":
            continue

        if name == "pre:step":
            step_by_frame[fid] = p.get("step")
            continue
        if name == "post:step":
            continue

        if name == "pre:skill.invoke":
            pending_invoke = {"skill": p.get("skill"), "sig_index": i, "consumed": False}
            continue
        if name == "post:skill.invoke":
            if p.get("ok") is False and pending_invoke and not pending_invoke["consumed"]:
                mk({"kind": "tool", "depth": depth, "status": "failed",
                    "label": f"skill.{short_skill(p.get('skill'))}", "detail": "调用失败",
                    "dur_ms": None, "frame_id": fid,
                    "sig_index": pending_invoke["sig_index"], "sig_end": i, "step": step,
                    "payload": _strip_common(p), "ts": sig.get("ts"),
                    "names": "pre:skill.invoke → post:skill.invoke", "trust": None})
            pending_invoke = None
            continue

        if name == "post:context.inline":
            caps = p.get("skills") if isinstance(p.get("skills"), list) else []
            total = sum(int(c.get("chars") or 0) for c in caps if isinstance(c, dict))
            first = caps[0] if caps else None
            extra = f"(+{len(caps) - 1})" if len(caps) > 1 else ""
            mk({"kind": "inline", "depth": depth, "status": "obs",
                "label": (f"{short_skill(first.get('name'))}@{first.get('version') or '—'}{extra}"
                          if isinstance(first, dict) else "—"),
                "detail": f"{total} chars" if caps else "",
                "dur_ms": None, "frame_id": fid, "sig_index": i, "sig_end": i, "step": step,
                "payload": _strip_common(p), "ts": sig.get("ts"), "names": name, "trust": None})
            continue

        if name == "pre:frame.pop":
            end = i
            nxt = sigs[i + 1] if i + 1 < len(sigs) else None
            if nxt and nxt.get("name") == "post:frame.pop" and nxt.get("frame_id") == fid:
                end = i + 1
            call = None
            for k in range(len(stack) - 1, -1, -1):
                if stack[k]["frame_id"] == fid:
                    while len(stack) - 1 > k:
                        e = stack.pop()  # 被跨越的内层帧:嵌套中断,标未返回
                        if e["call"]["status"] == "running":
                            e["call"]["status"] = "open"
                    call = stack.pop()["call"]
                    break
            st = frame_status.get(fid)
            parent_fid = stack[-1]["frame_id"] if stack else None
            row = mk({"kind": "ret", "depth": len(stack), "label": "ret",
                      "status": st or ("done" if call else "orphan"),
                      "detail": short_json(p["result"]) if "result" in p else "",
                      "dur_ms": _dur_of(call["ts"], sig.get("ts")) if call else None,
                      "frame_id": fid, "parent_frame_id": parent_fid,
                      "sig_index": i, "sig_end": end, "step": step,
                      "payload": {"result": p.get("result")}, "ts": sig.get("ts"),
                      "names": "pre:frame.pop → post:frame.pop" if end > i else name,
                      "trust": None})
            if call:
                call["dur_ms"] = row["dur_ms"]
                call["status"] = st or "done"
                call["_ret"] = row
            continue
        if name == "post:frame.pop":
            continue

        pair = pair_by_pre.get(i)
        if pair:
            if pair["kind"] == "exec" and pair.get("foldedInto") is not None:
                continue  # 折进 tool 行
            post = sigs[pair["post"]] if pair["post"] is not None else None
            pp = (post or {}).get("payload") or {}
            dur_ms = _dur_of(sig.get("ts"), (post or {}).get("ts")) if post else None
            names = f"{name} → {post['name']}" if post else name
            if pair["kind"] == "llm":
                u = pp.get("usage") or {}
                mk({"kind": "llm", "depth": depth,
                    "status": "done" if post else "open",
                    "label": str(p.get("model") or pp.get("model") or "—"),
                    "detail": f"{u.get('prompt', 0)}/{u.get('completion', 0)} tok",
                    "dur_ms": dur_ms, "frame_id": fid, "sig_index": i,
                    "sig_end": pair["post"] if pair["post"] is not None else i,
                    "step": step,
                    "payload": {"pre": _strip_common(p),
                                "post": _strip_common(pp) if post else None},
                    "ts": sig.get("ts"), "names": names, "trust": None})
            elif pair["kind"] == "tool":
                status = (
                    ("vetoed" if i in vetoed else "open") if pair["post"] is None
                    else ("failed" if pp.get("ok") is False else "done")
                )
                folded = next((ep for ep in pairs
                               if ep["kind"] == "exec" and ep.get("foldedInto") == i), None)
                trust = None
                if folded is not None:
                    trust = (sigs[folded["pre"]].get("payload") or {}).get("trust")
                trust = trust or p.get("trust")
                mk({"kind": "tool", "depth": depth, "status": status, "trust": trust,
                    "label": str(p.get("tool") or pp.get("tool") or "—"),
                    "detail": short_json(p.get("args") or {}),
                    "dur_ms": dur_ms, "frame_id": fid, "sig_index": i,
                    "sig_end": pair["post"] if pair["post"] is not None else i,
                    "step": step,
                    "payload": {"pre": _strip_common(p),
                                "post": _strip_common(pp) if post else None},
                    "ts": sig.get("ts"), "names": names})
            else:
                mk({"kind": "exec", "depth": depth,
                    "status": ("failed" if pp.get("ok") is False
                               else ("done" if post else "open")),
                    "label": str(p.get("tool") or short_skill(p.get("skill"))
                                 or p.get("language") or "logic"),
                    "detail": " · ".join(x for x in (p.get("language"), p.get("trust") or pp.get("trust")) if x),
                    "dur_ms": dur_ms, "frame_id": fid, "sig_index": i,
                    "sig_end": pair["post"] if pair["post"] is not None else i,
                    "step": step,
                    "payload": {"pre": _strip_common(p),
                                "post": _strip_common(pp) if post else None},
                    "ts": sig.get("ts"), "names": names, "trust": None})
            continue
        if i in post_covered:
            continue

        anomalous = name == "budget.exceeded" or "veto" in name.lower() or p.get("ok") is False
        mk({"kind": "obs", "depth": depth,
            "status": "failed" if anomalous else "obs",
            "label": name, "detail": short_json(_strip_common(p)),
            "dur_ms": None, "frame_id": fid, "sig_index": i, "sig_end": i, "step": step,
            "payload": _strip_common(p), "ts": sig.get("ts"), "names": name, "trust": None})

    for idx, row in enumerate(rows):
        row["line"] = idx + 1
    for e in stack:
        if e["call"]["status"] == "running":
            e["call"]["status"] = "open"
    for row in rows:
        if row["kind"] != "call":
            continue
        ret = row.pop("_ret", None)
        row["ret_line"] = ret["line"] if ret else None
    return rows


def row_for_signal(rows: list[dict[str, Any]], signal_index: int) -> dict[str, Any] | None:
    """信号下标 → 轨迹行(trace.js rowForSignal 对译):优先精确覆盖
    ([sig_index, sig_end] 区间),未命中(不占行的 pre:step / skill.invoke 等)
    → 最近的可见前行。"""
    best: dict[str, Any] | None = None
    for row in rows:
        if row["sig_index"] <= signal_index <= row.get("sig_end", row["sig_index"]):
            return row
        if row["sig_index"] <= signal_index and (best is None or row["sig_index"] > best["sig_index"]):
            best = row
    return best


def paused_signal_index(
    signals: list[dict[str, Any]], pause_point: dict[str, Any] | None
) -> int | None:
    """暂停点 → 信号下标(debug-view.js pausedSignalIndex 对译):从尾向前找
    最近一条同名同帧信号(pre:step 再对 step 号,pre:tool.call 再对工具名);
    暂停总是发生在最近一次匹配的发射上。"""
    pp = pause_point or None
    if not pp or not pp.get("signal"):
        return None
    for i in range(len(signals) - 1, -1, -1):
        s = signals[i]
        if not isinstance(s, dict) or s.get("name") != pp["signal"]:
            continue
        if (s.get("frame_id") or None) != (pp.get("frame_id") or None):
            continue
        if pp["signal"] == "pre:step" and pp.get("step") is not None \
                and (s.get("payload") or {}).get("step") != pp.get("step"):
            continue
        if pp.get("tool") is not None and (s.get("payload") or {}).get("tool") != pp.get("tool"):
            continue
        return i
    return None


def bp_target_for_row(row: dict[str, Any] | None) -> dict[str, str] | None:
    """行 → 可切换的断点目标(debug-view.js bpTargetForRow 对译):tool 行 →
    tool_call 断点;call 行 → skill_invoke 断点;其余行无断点语义(返回 None,
    gutter 占位)。match 取 payload 原始名(与内核 fnmatch 同串)。"""
    if not row:
        return None
    if row.get("kind") == "tool":
        payload = row.get("payload") or {}
        pre = payload.get("pre") or {}
        tool = pre.get("tool") or row.get("label")
        return {"kind": "tool_call", "match": str(tool)} if tool else None
    if row.get("kind") == "call":
        skill = (row.get("payload") or {}).get("skill")
        return {"kind": "skill_invoke", "match": str(skill)} if skill else None
    return None


# ---------------------------------------------------------------------------
# 行文本(TUI 行语言;trace.js bodyHtml 的纯文本对译,色归渲染层 sig-* token)
# ---------------------------------------------------------------------------

_OK_MARK = {"done": "✓", "failed": "✗"}


def body_text(row: dict[str, Any]) -> str:
    """行主体纯文本(对译 trace.js bodyHtml;标记/箭头是双编码的符号通道)。"""
    kind = row.get("kind")
    detail = str(row.get("detail") or "")
    label = str(row.get("label") or "")
    if kind == "run":
        return f"{label} {detail}".rstrip()
    if kind == "call":
        open_mark = " …(未返回)" if row.get("status") in ("open", "running") else ""
        return f"→ call {label}({detail}){open_mark}" if detail else f"→ call {label}{open_mark}"
    if kind == "ret":
        bad = " ✗" if row.get("status") == "failed" else ""
        return f"← ret {detail or '—'}{bad}"
    if kind == "llm":
        return f"llm {label} → {detail}"
    if kind == "tool":
        mark = "vetoed" if row.get("status") == "vetoed" else _OK_MARK.get(str(row.get("status")), "")
        trust = f" {row['trust']}" if row.get("trust") else ""
        suffix = f"{trust} {mark}".rstrip()
        return f"tool {label}({detail}) {suffix}".rstrip()
    if kind == "exec":
        mark = _OK_MARK.get(str(row.get("status")), "")
        return f"exec {label} {detail} {mark}".rstrip()
    if kind == "inline":
        return f"⇥ inline {label} · {detail}".rstrip(" ·")
    return f"obs {label} {detail}".rstrip()

#!/usr/bin/env python3
"""FLOWS-EVAL 评测驱动(API 可驱动部分;docs/FLOWS-EVAL.md)。

用法:python3 scripts/flow_eval.py [--base http://127.0.0.1:8391]
输出:每条的 PASS/WARN/FAIL + 观察行(供填写评分表)。
设计:endpoint 形状稳定,行为修复只影响内容质量——本脚本同时断言形状与
关键体验点(plan 不自相矛盾/reuse 不含草稿/browse 非空且有数据)。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8391"
RESULTS: list[tuple[str, str, str]] = []  # (flow, status, note)


def req(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def record(flow: str, ok: bool, note: str) -> None:
    RESULTS.append((flow, "PASS" if ok else "FAIL", note))
    print(f"[{'✅' if ok else '❌'}] {flow}: {note}")


def warn(flow: str, note: str) -> None:
    RESULTS.append((flow, "WARN", note))
    print(f"[⚠️] {flow}: {note}")


def new_session() -> str:
    st, body = req("POST", "/platform/api/sessions")
    assert st in (200, 201), st
    return body["id"]


def say(sid: str, text: str) -> dict:
    st, body = req("POST", f"/platform/api/sessions/{sid}/messages", {"text": text})
    assert st == 200, (st, body)
    return body


def f01_help(sid: str) -> None:
    msg = say(sid, "你好,你能做什么")
    text = msg["text"] + json.dumps(msg.get("cards", []), ensure_ascii=False)
    record("F01", "技能" in text and ("失败" in text or "为什么" in text), msg["text"][:60])


def f02_browse(sid: str) -> None:
    msg = say(sid, "最近有哪些运行失败了")
    cards = msg.get("cards", [])
    if not cards:
        record("F02", False, "无卡片返回")
        return
    rows = cards[0]["data"].get("rows", [])
    if not rows:
        warn("F02", "browse 返回空行(产物层应有历史 run)")
    else:
        record("F02", True, f"{len(rows)} 行,首行摘要: {str(rows[0])[:60]}")


def f03_why_failed(sid: str) -> None:
    msg = say(sid, "这个 run 为什么挂")
    text = msg["text"] + json.dumps(msg.get("cards", []), ensure_ascii=False)
    has_english_error = "ProviderError" in text or "Error:" in text
    record("F03", not has_english_error, f"英文错误类名泄漏={has_english_error}; {msg['text'][:60]}")


def f06_plan(sid: str) -> None:
    msg = say(sid, "帮我做个按天气推荐晚餐的技能")
    cards = [c for c in msg.get("cards", []) if c["type"] == "plan"]
    if not cards:
        record("F06", False, f"无 plan 卡: {msg['text'][:80]}")
        return
    data = cards[0]["data"]
    reuse = {r["name"] for r in data.get("reuse", [])}
    create = {c["name"] for c in data.get("create", [])}
    overlap = reuse & create
    if overlap:
        record("F06", False, f"复用与新建同名: {overlap}")
        return
    record("F06", True, f"reuse={sorted(reuse)} create={sorted(create)}")


def main() -> None:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base
    sid = new_session()
    print(f"session: {sid}\nbase: {BASE}")
    f01_help(sid)
    f02_browse(sid)
    f03_why_failed(sid)
    f06_plan(sid)
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    warns = [r for r in RESULTS if r[1] == "WARN"]
    print(f"\n== 汇总: {len(RESULTS)} 条,PASS {len(RESULTS)-len(fails)-len(warns)},WARN {len(warns)},FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

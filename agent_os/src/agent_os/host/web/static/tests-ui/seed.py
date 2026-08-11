#!/usr/bin/env python3
"""重置数据后的最小 fixture 种子(让系统不至于「裸奔」,UI 测试有据可查)。

用法:python3 seed.py [base_url]   (缺省 http://127.0.0.1:8391)

种两件:
  1. 一条成功 run:POST /api/runs {demo.fib, n:3, wait}(走真 LLM,需服务 token 有效);
  2. 一条失败 run 记录:直接写 .agent-os/runs/<id>/ 四件套——
     「启动了但跑挂」的 run 无法经 API 造(schema 不合的在记录前就被拒,
     overrides.model 不生效),只能落盘;这是 fixture,不是造假数据。
重置(清 sessions/drafts/runs/traces)后跑一次本脚本,tests-ui 全量即绿。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8391"
RUNS_DIR = Path(__file__).resolve().parents[6] / ".agent-os" / "runs"  # repo 根/.agent-os/runs


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main() -> None:
    ok = _post("/api/runs", {"skill": "demo.fib", "input": {"n": 3}, "wait": True})
    src_id = ok.get("run_id")
    assert ok.get("status") == "done" and src_id, f"成功 run 种子失败:{ok}"
    print(f"成功 run: {src_id[:12]} done")

    src = RUNS_DIR / src_id
    rid = f"fa17ed{int(time.time())}"[:32].ljust(32, "0")
    dst = RUNS_DIR / rid
    shutil.copytree(src, dst)
    meta = json.loads((dst / "meta.json").read_text())
    meta["run_id"] = rid
    meta["input"] = {"n": 90}
    result = json.loads((dst / "result.json").read_text())
    result.update(status="failed", result=None, error="ProviderError: LLM 路由超时(seed fixture)")
    ckpt = json.loads((dst / "checkpoint.json").read_text())
    ckpt["run"].update(run_id=rid, status="failed", result=None, error=result["error"])
    (dst / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    (dst / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (dst / "checkpoint.json").write_text(json.dumps(ckpt, ensure_ascii=False, indent=2))
    (dst / "trace.jsonl").write_text((dst / "trace.jsonl").read_text().replace(src_id, rid))
    print(f"失败 run 记录: {rid[:12]} failed(落盘 fixture)")


if __name__ == "__main__":
    main()

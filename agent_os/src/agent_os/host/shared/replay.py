"""复现层(docs/RUNNERS.md §3.4;replay 脚本重建与 run 结构化 diff,两个 runner 共用)。

replay 脚本重建(``build_mock_script``/``replace_providers``)已下沉
telemetry/replay.py(2026-09-30:register() 验证门的录制重放证据同样要消费,
skills/ 层不反向依赖 host/,telemetry/ 是双方都能到达的最低点);此处 re-export
保持既有消费面(cli/main.py、cli/debug.py、web/run_manager.py、web/app.py)零改动。
对齐规则(含压缩排干 ``source == "compress"`` 再排放必须过滤的修正)见
telemetry/replay.py 模块 docstring。

diff:两次 run 的信号序列按 ``(name, payload.skill, payload.tool, payload.ok,
payload.depth)`` 逐位比较(缺失为 None,忽略 frame_id/ts 等动态值),外加
result 与 usage 汇总——回归判断(§3.4 用途 A)与复现排查的消费面。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_os.host.shared.artifacts import read_result, read_trace

# 下沉后的唯一实现(re-export;勿在此再放一份拷贝)
from agent_os.telemetry.replay import build_mock_script, replace_providers

__all__ = ["build_mock_script", "diff_runs", "replace_providers"]


#: diff 逐信号比较的载荷字段(§3.2;缺失为 None,frame_id/ts 等动态值忽略)
_DIFF_PAYLOAD_KEYS = ("skill", "tool", "ok", "depth")


def _signal_key(row: dict[str, Any]) -> tuple[Any, ...]:
    payload = row.get("payload") or {}
    return (row.get("name"), *(payload.get(k) for k in _DIFF_PAYLOAD_KEYS))


def diff_runs(run_dir_a: str | Path, run_dir_b: str | Path) -> dict[str, Any]:
    """两次 run 的结构化 diff(§3.2;查询而非判决,报告不含退出语义)。

    ``first_divergence``:首个信号 key 不一致的下标;一方是另一方前缀时为
    较短者的长度(即首个"一边有信号、另一边没有"的位置);完全一致为 None。
    """
    result_a = read_result(run_dir_a)
    result_b = read_result(run_dir_b)
    keys_a = [_signal_key(r) for r in read_trace(run_dir_a) if r.get("type") == "signal"]
    keys_b = [_signal_key(r) for r in read_trace(run_dir_b) if r.get("type") == "signal"]
    first = next((i for i, (ka, kb) in enumerate(zip(keys_a, keys_b)) if ka != kb), None)
    if first is None and len(keys_a) != len(keys_b):
        first = min(len(keys_a), len(keys_b))
    return {
        "v": 1,
        "result_equal": result_a.get("result") == result_b.get("result"),
        "signals_equal": keys_a == keys_b,
        "signal_counts": {"a": len(keys_a), "b": len(keys_b)},
        "usage": {"a": result_a.get("usage") or {}, "b": result_b.get("usage") or {}},
        "first_divergence": first,
    }

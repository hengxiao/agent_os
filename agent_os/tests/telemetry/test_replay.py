"""telemetry/replay.py 锚点测试(docs/RUNNERS.md §3.4;2026-09-30 自 host/shared/replay.py 下沉)。

固定约定:

- ``build_mock_script``:trace 的 post:llm.response 顺序 ↔ checkpoint 各帧 assistant
  消息逐位对齐,重建 MockProvider 脚本;
- **压缩排干信号不计入对齐**(payload.source == "compress";runner._drain_compress_usage
  为压缩链 LLM 调用补发的成本信号,无对应 assistant 消息)——计入则下标错位,
  压缩 run 的 CLI replay 随之修复(有意的行为修正);
- ``host/shared/replay.py`` re-export 同一实现(CLI/Web 消费面零改动)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_run_dir(
    run_dir: Path,
    *,
    compress_first: bool = False,
    compress_only: bool = False,
) -> None:
    """手写最小 run 产物目录(tests/cli/test_replay.py:64-108 同款四件套)。"""
    run_dir.mkdir(parents=True)
    header = json.dumps({"v": 1, "type": "header", "schema": "agent_os.trace/1"})
    compress_row = json.dumps({
        "v": 1, "type": "signal", "name": "post:llm.response", "run_id": "r1",
        "frame_id": "f1", "ts": 0.05,
        "payload": {"model": "m", "usage": {"prompt": 2, "completion": 1}, "source": "compress"},
    })
    main_row = json.dumps({
        "v": 1, "type": "signal", "name": "post:llm.response", "run_id": "r1",
        "frame_id": "f1", "ts": 0.1,
        "payload": {"model": "m", "usage": {"prompt": 5, "completion": 3}},
    })
    rows = [header]
    if compress_first or compress_only:
        rows.append(compress_row)
    if not compress_only:
        rows.append(main_row)
    (run_dir / "trace.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (run_dir / "checkpoint.json").write_text(
        json.dumps({
            "v": 1,
            "frames": [{
                "frame_id": "f1",
                "context": {"messages": [{"role": "assistant", "content": '{"seq": [0]}'}]},
            }],
        }),
        encoding="utf-8",
    )


def test_build_mock_script_from_telemetry_home(tmp_path):
    """下沉后的新落点可独立消费(不经 host/):1 响应信号 ↔ 1 assistant 消息对齐。"""
    from agent_os.telemetry.replay import build_mock_script

    run_dir = tmp_path / "r1"
    _write_run_dir(run_dir)

    (resp,) = build_mock_script(run_dir)
    assert resp.message.content == '{"seq": [0]}'
    assert (resp.usage.prompt, resp.usage.completion) == (5, 3)
    assert resp.finish_reason == "stop"


def test_compress_reemission_not_counted_in_alignment(tmp_path):
    """压缩排干信号(source == "compress")被过滤:在前不抢占下标,重建脚本恰 1 条。

    不过滤时本用例必炸 ValueError(压缩信号消耗 assistants[0],主循环信号成为
    "帧 f1 的第 2 个响应信号无对应 assistant 消息")——这是压缩 run CLI replay
    修复的锚点。
    """
    from agent_os.telemetry.replay import build_mock_script

    run_dir = tmp_path / "r1"
    _write_run_dir(run_dir, compress_first=True)

    (resp,) = build_mock_script(run_dir)
    assert resp.message.content == '{"seq": [0]}'
    assert (resp.usage.prompt, resp.usage.completion) == (5, 3), "取的是主循环信号的 usage"


def test_compress_only_trace_yields_empty_script(tmp_path):
    """trace 全是压缩排干信号 → 脚本为空(压缩不产生主循环响应),不报错。"""
    from agent_os.telemetry.replay import build_mock_script

    run_dir = tmp_path / "r1"
    _write_run_dir(run_dir, compress_only=True)
    assert build_mock_script(run_dir) == []


def test_host_shared_replay_reexports_same_implementation():
    """host/shared/replay.py 是 re-export 而非拷贝:CLI/Web 消费面与下沉实现同一对象。"""
    import agent_os.host.shared.replay as host_replay
    import agent_os.telemetry.replay as telemetry_replay
    from agent_os.telemetry import build_mock_script, replace_providers

    assert host_replay.build_mock_script is telemetry_replay.build_mock_script
    assert host_replay.replace_providers is telemetry_replay.replace_providers
    assert build_mock_script is telemetry_replay.build_mock_script
    assert replace_providers is telemetry_replay.replace_providers
    assert callable(host_replay.diff_runs), "diff_runs 留在 host 层"


def test_malformed_trace_still_value_error(tmp_path):
    """畸形产物归 ValueError(JSON 解析失败)——与下沉前语义一致(宿主归退出码 2)。"""
    from agent_os.telemetry.replay import build_mock_script

    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "trace.jsonl").write_text("not-json\n", encoding="utf-8")
    (run_dir / "checkpoint.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        build_mock_script(run_dir)

"""R2 锚点测试:CLI 复现(RUNNERS.md §3.2/§3.4;replay/diff/skills validate)。

固定约定:

- ``agent-os replay <run_id>``:从原 run 的 trace(post:llm.response 顺序)+ checkpoint
  (各帧 assistant 消息)重建 MockProvider 脚本,确定性重放整棵帧树(不碰真实 API);
  产出与原 run 相同的结果与信号序列(frame_id/ts 除外);
- ``agent-os diff <a> <b>``:结构化对比两次运行——result、usage、信号序列
  (比较 name + 关键载荷字段:skill/tool/ok/depth,忽略 frame_id/ts/payload 动态值);
- ``agent-os skills validate <path.yaml>``:manifest 校验 + 依赖图检查,
  输出 ``{"ok", "skills": [...], "errors": [...]}``,失败退出码 2。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_cli_r1 import SKILLS_YAML, _run_cli, _write_config


def _completed_run(tmp_path: Path, capsys, n: int) -> str:
    cfg = _write_config(tmp_path)
    rc, out = _run_cli(
        capsys, "run", "fib", "--input", json.dumps({"n": n}),
        "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json",
    )
    assert rc == 0
    return out["run_id"]


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def test_replay_reproduces_same_result_and_signals(tmp_path, capsys):
    """录制 fib(4) → replay → 结果一致;与原始 run diff 为空(§3.4 用途 A)。"""
    run_id = _completed_run(tmp_path, capsys, 4)
    cfg = _write_config(tmp_path)

    rc, out = _run_cli(
        capsys, "replay", run_id,
        "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json",
    )
    assert rc == 0
    assert out["result"] == {"seq": [0, 1, 1, 2]}
    assert out["run_id"] != run_id, "replay 应是新 run"

    rc, d = _run_cli(capsys, "diff", run_id, out["run_id"],
                     "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 0
    assert d["result_equal"] is True
    assert d["signals_equal"] is True


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def test_diff_detects_difference(tmp_path, capsys):
    a = _completed_run(tmp_path, capsys, 3)
    b = _completed_run(tmp_path, capsys, 4)
    rc, d = _run_cli(capsys, "diff", a, b, "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 0
    assert d["signals_equal"] is False
    assert d["signal_counts"]["b"] > d["signal_counts"]["a"]


def test_diff_identical_runs_empty(tmp_path, capsys):
    a = _completed_run(tmp_path, capsys, 3)
    b = _completed_run(tmp_path, capsys, 3)
    rc, d = _run_cli(capsys, "diff", a, b, "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 0
    assert d["signals_equal"] is True
    assert d["result_equal"] is True


# ---------------------------------------------------------------------------
# skills validate
# ---------------------------------------------------------------------------


def test_skills_validate_ok(tmp_path, capsys):
    rc, out = _run_cli(capsys, "skills", "validate", str(SKILLS_YAML), "--json")
    assert rc == 0
    assert out["ok"] is True
    names = [s["name"] for s in out["skills"]]
    assert "fib" in names


def test_skills_validate_cycle_fails(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """skills:
  - name: a
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    permissions: { tools: [], skills: [b] }
    prompt: a
  - name: b
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    permissions: { tools: [], skills: [a] }
    prompt: b
""",
        encoding="utf-8",
    )
    rc, out = _run_cli(capsys, "skills", "validate", str(bad), "--json")
    assert rc == 2
    assert out["ok"] is False
    assert out["errors"], "应报告循环依赖错误"

"""K1 CLI run 覆盖选项锚点测试(docs/RUNNERS.md §2.5/§3.2)。

固定约定:

- ``agent-os run`` 的覆盖 flag 全部由注册表生成(P2);未注册字段退出码 2(P3);
- ``--workdir`` 非法路径 → 退出码 2 + stderr 错误消息;合法时 fs 工具落在该目录(§W0-1);
- 覆盖字段 provenance 写 meta.json ``overrides`` 段(P4):flag/env/toml 各至少一例,
  无覆盖且 toml 不含注册字段时 meta.json 无 ``overrides`` 键。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_os.host.cli.main import _parser, main
from agent_os.runtime.overrides import collect_cli_overrides
from tests.helpers.config import run_cli as _run_cli
from tests.helpers.config import write_config as _write_config

WRITER_SKILLS_YAML = """
skills:
  - name: test.file_writer
    version: 1.0.0
    kind: prompt
    description: 写一个文件。Use when 需要落盘产出。
    inputs:
      type: object
      properties: { name: { type: string } }
      required: [name]
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [system.file.write], skills: [] }
    model: { prefer: ["mock/fib"] }
    prompt: |
      把输入 name 作为文件名写入内容 "x"。
"""


def _read_meta(record: dict) -> dict:
    return json.loads((Path(record["artifacts"]["dir"]) / "meta.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# flag 收集(P5 命名/类型约定)
# ---------------------------------------------------------------------------


def test_read_paths_repeatable_flag_collects_list(tmp_path):
    """列表型约定(P5):``--read-paths`` 可重复,收集为 list(顺序保持)。"""
    p1, p2 = tmp_path / "a", tmp_path / "b"
    p1.mkdir()
    p2.mkdir()
    args = _parser().parse_args(
        ["run", "demo.fib", "--input", "{}",
         "--read-paths", str(p1), "--read-paths", str(p2)]
    )
    assert collect_cli_overrides(args)["read_paths"] == [str(p1), str(p2)]


def test_cli_types_coerced_by_argparse():
    """标量型约定(P5):--max-cost → float,--max-steps/--checkpoint-interval → int。"""
    args = _parser().parse_args(
        ["run", "demo.fib", "--input", "{}",
         "--max-cost", "0.5", "--max-steps", "7", "--checkpoint-interval", "3"]
    )
    assert collect_cli_overrides(args) == {"max_cost": 0.5, "max_steps": 7, "checkpoint_interval": 3}


def test_inline_choices_rejected_by_argparse():
    """枚举型约定(P5):--inline 只收 on|off,其余 argparse 退出码 2。"""
    with pytest.raises(SystemExit) as e:
        _parser().parse_args(["run", "demo.fib", "--input", "{}", "--inline", "maybe"])
    assert e.value.code == 2


# ---------------------------------------------------------------------------
# --workdir(P6:仅 CLI 开放)
# ---------------------------------------------------------------------------


def test_workdir_invalid_path_exit_2(tmp_path, capsys):
    """fail-closed(P3):--workdir 指向不存在路径 → 退出码 2 + stderr 错误消息。"""
    cfg = _write_config(tmp_path)
    rc = main(
        ["run", "demo.fib", "--input", '{"n": 3}',
         "--config", str(cfg), "--artifacts", str(tmp_path / "runs"),
         "--workdir", str(tmp_path / "nope"), "--json"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "路径不存在或不是目录" in err


def test_workdir_flag_lands_file_tools(tmp_path, capsys):
    """--workdir 生效:run 里 system.file.write 的相对路径落在该目录(§W0-1)。"""
    skills = tmp_path / "skills.yaml"
    skills.write_text(WRITER_SKILLS_YAML, encoding="utf-8")
    cfg = _write_config(
        tmp_path, builtins=True, skills=skills,
        brain="tests.helpers.brains:file_writer_brain",
    )
    workdir = tmp_path / "wd"
    workdir.mkdir()
    rc, out = _run_cli(
        capsys, "run", "test.file_writer", "--input", '{"name": "out.txt"}',
        "--config", str(cfg), "--artifacts", str(tmp_path / "runs"),
        "--workdir", str(workdir), "--json",
    )
    assert rc == 0, out
    assert (workdir / "out.txt").is_file(), "fs 工具产出应落在 --workdir 内"
    meta = _read_meta(out)
    assert meta["overrides"]["workdir"] == "flag"


# ---------------------------------------------------------------------------
# provenance 落盘 meta.json(P4)
# ---------------------------------------------------------------------------


def test_provenance_flag_source(tmp_path, capsys, monkeypatch):
    """flag 覆盖 → meta.json overrides 段该字段记 "flag"。"""
    monkeypatch.delenv("AGENT_OS_MODEL", raising=False)
    cfg = _write_config(tmp_path)
    rc, out = _run_cli(
        capsys, "run", "demo.fib", "--input", '{"n": 3}',
        "--config", str(cfg), "--artifacts", str(tmp_path / "runs"),
        "--max-steps", "50", "--json",
    )
    assert rc == 0
    assert _read_meta(out)["overrides"]["max_steps"] == "flag"


def test_provenance_env_source(tmp_path, capsys, monkeypatch):
    """env 别名生效(flag 未给)→ 记 "env"(P4:flag > env > toml)。"""
    monkeypatch.setenv("AGENT_OS_MODEL", "mock/fib")
    cfg = _write_config(tmp_path)
    rc, out = _run_cli(
        capsys, "run", "demo.fib", "--input", '{"n": 3}',
        "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json",
    )
    assert rc == 0
    assert _read_meta(out)["overrides"]["model"] == "env"


def test_provenance_toml_source(tmp_path, capsys, monkeypatch):
    """无 flag 无 env,toml [run] 含注册字段 → 记 "toml"。"""
    monkeypatch.delenv("AGENT_OS_MODEL", raising=False)
    cfg = _write_config(tmp_path)
    rc, out = _run_cli(
        capsys, "run", "demo.fib", "--input", '{"n": 3}',
        "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json",
    )
    assert rc == 0
    assert _read_meta(out)["overrides"]["model"] == "toml"


def test_no_overrides_no_meta_key(tmp_path, capsys, monkeypatch):
    """无覆盖(toml 不含注册字段)→ meta.json 无 overrides 键(空段不写键)。"""
    monkeypatch.delenv("AGENT_OS_MODEL", raising=False)
    from tests.helpers.kernels import FIB_SKILLS_YAML

    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        f"""
[run]
max_depth = 8
compression = "off"

[providers.mock]
brain = "tests.helpers.brains:fib_brain"

[tools]
builtins = false
python_exec = "subprocess"

[skills]
path = "{FIB_SKILLS_YAML}"

[telemetry]
dir = "{tmp_path / 'traces'}"
""",
        encoding="utf-8",
    )
    rc, out = _run_cli(
        capsys, "run", "demo.fib", "--input", '{"n": 3}',
        "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json",
    )
    assert rc == 0
    assert "overrides" not in _read_meta(out)

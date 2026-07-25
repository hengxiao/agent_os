"""CLI/Web 共用:agent-os.toml 写入与 CLI 进程内调用。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.kernels import FIB_SKILLS_YAML

CONFIG_TEMPLATE = """
[run]
model = "mock/fib"
max_depth = 8
max_steps = 200
max_cost = {max_cost}
compression = "off"

[providers.mock]
brain = "{brain}"

[tools]
builtins = {builtins}
python_exec = "subprocess"

[skills]
path = "{skills}"

[telemetry]
dir = "{telemetry}"
{extra}
"""


def write_config(
    tmp_path: Path,
    *,
    brain: str = "tests.helpers.brains:fib_brain",
    skills: str | Path | None = None,
    builtins: bool = False,
    max_cost: float = 2.0,
    extra: str = "",
) -> Path:
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TEMPLATE.format(
            brain=brain,
            skills=skills or FIB_SKILLS_YAML,
            builtins=str(builtins).lower(),
            max_cost=max_cost,
            telemetry=tmp_path / "traces",
            extra=extra,
        ),
        encoding="utf-8",
    )
    return cfg


def run_cli(capsys, *argv: str) -> tuple[int, dict]:
    """进程内跑 CLI 入口,返回 (退出码, stdout 里的首个 JSON 对象)。"""
    from agent_os.host.cli.main import main

    rc = main(list(argv))
    out = capsys.readouterr().out
    return rc, json.loads(out[out.index("{"):]) if "{" in out else {}

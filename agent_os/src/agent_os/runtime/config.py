"""配置文件加载(DESIGN.md §14.2;M0 配置)。

TOML 配置文件可等价完成 KernelBuilder 组装,供非代码宿主使用;
``seed`` / ``temperature`` 复现性钉死(§2.4,评测用)。
"""

from __future__ import annotations

from pathlib import Path

from agent_os.api.v1 import RunConfig


def load_config(path: str | Path) -> RunConfig:
    """TOML → RunConfig(字段逐字对齐 §2.4)。"""
    raise NotImplementedError("M0")

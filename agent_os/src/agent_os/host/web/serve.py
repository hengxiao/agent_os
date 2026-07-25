"""``agent-os-web`` 入口(RUNNERS.md §4.2):uvicorn 起 Web UI Runner。

单用户 localhost dev 工具:``--host`` 缺省 127.0.0.1,无认证(§4.5)。
一站多 skill set(D6):``--skillsets <root>`` 或配置 ``[skillsets] dir``
(CLI 优先;相对路径相对配置文件目录解析)。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _skillsets_dir(args: argparse.Namespace) -> Path | None:
    """生效 skillsets 根目录(D6):CLI ``--skillsets`` 优先,缺省读配置 ``[skillsets].dir``。"""
    if args.skillsets:
        return Path(args.skillsets)
    from agent_os.runtime.config import ConfigError, load_config

    try:
        cfg = load_config(args.config)
    except ConfigError:
        return None  # 配置文件缺失/畸形:create_app 会按原样报错,这里只负责探测
    raw = (cfg.get("skillsets") or {}).get("dir")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else Path(args.config).resolve().parent / path


def main() -> None:
    """CLI 入口(``agent-os-web = agent_os.host.web.serve:main``)。"""
    parser = argparse.ArgumentParser(
        prog="agent-os-web",
        description="Agent OS Web UI runner(RUNNERS.md §4):跑技能、实时观察、RCA",
    )
    parser.add_argument("--config", default="agent-os.toml", help="agent-os.toml 路径")
    parser.add_argument("--artifacts", default=".agent-os", help="产物根目录(与 CLI 互通)")
    parser.add_argument(
        "--skillsets",
        default=None,
        help="一站多 skill set 根目录(<root>/<set>/skills.yaml);缺省读配置 [skillsets].dir",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn  # optional extra `agent-os[web]`,延迟 import 保持主依赖干净

    from agent_os.host.web.app import create_app

    uvicorn.run(
        create_app(args.config, Path(args.artifacts), skillsets_dir=_skillsets_dir(args)),
        host=args.host,
        port=args.port,
    )

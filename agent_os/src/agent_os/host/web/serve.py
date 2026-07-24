"""``agent-os-web`` 入口(RUNNERS.md §4.2):uvicorn 起 Web UI Runner。

单用户 localhost dev 工具:``--host`` 缺省 127.0.0.1,无认证(§4.5)。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    """CLI 入口(``agent-os-web = agent_os.host.web.serve:main``)。"""
    parser = argparse.ArgumentParser(
        prog="agent-os-web",
        description="Agent OS Web UI runner(RUNNERS.md §4):跑技能、实时观察、RCA",
    )
    parser.add_argument("--config", default="agent-os.toml", help="agent-os.toml 路径")
    parser.add_argument("--artifacts", default=".agent-os", help="产物根目录(与 CLI 互通)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn  # optional extra `agent-os[web]`,延迟 import 保持主依赖干净

    from agent_os.host.web.app import create_app

    uvicorn.run(
        create_app(args.config, Path(args.artifacts)),
        host=args.host,
        port=args.port,
    )

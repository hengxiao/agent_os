"""``agent-os-chat`` 进程入口(P2-M2):交互式 coding 宿主(docs/RUNNERS.md §2 宿主契约)。

会话模型:一个交互会话 = 长存 run + checkpoint resume(WS2)。首轮任务经位置参数
``skill`` + ``--input`` 传入(REPL 服务「观察 + 应答 + 插话」);``--resume <session_id>``
从会话最后一个 paused turn 续跑(挂起期间未决问题以新 question_id 重问,UI 重答即可)。

退出码(沿用批 CLI 语义,docs/RUNNERS.md §3.3):

| code | 含义 |
|---|---|
| 0 | 正常退出(/quit、Ctrl-C/EOF 暂停落幕;run done/paused) |
| 2 | 输入/校验错误(--input JSON 解析错、会话不存在/无 paused turn、覆盖选项非法) |
| 3 | run 失败或中止(终态 failed/aborted) |
| 4 | 宿主/基础设施错误(配置缺失/畸形) |

run 覆盖选项(--model/--workdir 等)由 K1 注册表生成(docs/RUNNERS.md §2.5);
--config/--artifacts/--session-id/--resume/--input 是 host 私有选项,不进注册表(P1)。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from agent_os.host.coding_cli.repl import Repl
from agent_os.host.coding_cli.session import SessionRunner
from agent_os.host.coding_cli.session_store import SessionStore
from agent_os.runtime.config import ConfigError, load_config
from agent_os.runtime.overrides import (
    OverrideError,
    add_override_arguments,
    apply_overrides,
    collect_cli_overrides,
)


def _parse_input(raw: str) -> Any:
    """``--input '<json>'|@file`` → dict;JSON 解析错/文件缺失由调用方归退出码 2(批 CLI 先例)。"""
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    return json.loads(raw)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-os-chat",
        description="Agent OS 交互宿主(P2):观察 run、应答提问、中途插话、暂停/恢复会话",
    )
    parser.add_argument("skill", nargs="?", help="首轮任务的技能名(--resume 时从会话文档取,不给)")
    parser.add_argument("--input", help="首轮任务输入:'<json>' 或 @file(缺省 {})")
    parser.add_argument("--config", default="agent-os.toml")
    parser.add_argument("--artifacts", default=".agent-os")
    parser.add_argument("--session-id", default=None, help="会话 id(缺省生成短 id)")
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        default=None,
        help="恢复指定会话(从最后一个 paused turn 的 checkpoint 续跑)",
    )
    # K1(docs/RUNNERS.md §2.5):run 覆盖选项由注册表生成(与 cli/main.py 同一挂载点)
    add_override_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口(``agent-os-chat = agent_os.host.coding_cli.__main__:main``)。"""
    args = _parser().parse_args(argv)

    if args.input is not None:
        try:
            opening = _parse_input(args.input)
        except (OSError, json.JSONDecodeError) as e:
            print(f"输入错误: {e}", file=sys.stderr)
            return 2
    else:
        opening = {}
    if args.input is not None and not args.skill:
        print("输入错误: --input 须配合位置参数 <skill>(首轮任务的技能名)", file=sys.stderr)
        return 2

    try:
        cfg = load_config(args.config)  # 预检(配置缺失/畸形 fail fast,不进 REPL)
    except ConfigError as e:
        print(f"基础设施错误: {e}", file=sys.stderr)
        return 4
    overrides = collect_cli_overrides(args)
    if overrides:
        try:
            # 装配前预检覆盖值(非法值 fail fast;worker 装配时 apply_overrides 再校验一次)
            apply_overrides(cfg, overrides)
        except OverrideError as e:
            print(f"覆盖选项错误: {e}", file=sys.stderr)
            return 2

    store = SessionStore(Path(args.artifacts))
    if args.resume is not None:
        session_id = args.resume
        try:
            stored = store.load(session_id)
        except (FileNotFoundError, ValueError) as e:
            print(f"会话错误: {e}", file=sys.stderr)
            return 2
        # P2-M3:会话级覆盖随文档存档,resume 继承;显式 flag 优先于存档(K1 P4 精神)
        overrides = {**(stored.get("overrides") or {}), **overrides}
    else:
        session_id = args.session_id or uuid.uuid4().hex[:8]

    runner = SessionRunner(
        args.config,
        artifacts_root=Path(args.artifacts),
        session_store=store,
        session_id=session_id,
        overrides=overrides or None,
    )
    repl = Repl(runner)
    print(f"[会话] {session_id}(resume 提示: agent-os-chat --resume {session_id})")
    if args.resume is not None:
        try:
            runner.resume()
        except (RuntimeError, OSError, ValueError) as e:
            print(f"恢复失败: {e}", file=sys.stderr)
            return 2
    elif args.skill:
        runner.start(args.skill, opening)
    else:
        print("[引导] 未给首轮任务;REPL 进入观察模式(应答/插话/斜杠命令,/help 查看)")
    return repl.run()


if __name__ == "__main__":
    raise SystemExit(main())

"""agent-os CLI(RUNNERS.md §3;R1 核心:run/trace/inspect/resume;R2 复现:replay/diff/skills)。

面向 coding agent 的薄宿主:stdout = RunRecord JSON(``--json`` 时唯一输出;
缺省人读摘要 + 末行 JSON,§3.3),stderr 人类可读日志。退出码(§3.3):

| code | 含义 |
|---|---|
| 0 | run 成功(status=done) |
| 2 | 输入/配置/技能校验错误(SkillLoadError、输入不合 schema、输入 JSON 解析错) |
| 3 | run 失败或中止(技能返回错误、OutputValidationError、RunAborted) |
| 4 | 宿主/基础设施错误(配置缺失、provider 装配失败、docker 不可用) |

S2 增量(SUPERVISOR.md v2 §2.3):CLI 宿主通道——run/resume 装配时注入
``_cli_supervisor`` handler,run 挂起时把 question JSON 写 stderr(coding agent
可解析),从 stdin 读一行作答,单命令进程内闭环;跨进程 pending/answer 子命令
在单进程 CLI 下无收件箱可查,异步收件箱形态由 Web 宿主承载。replay 不注入
(回放按 trace 记录值走,不问第二次,§4)。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from agent_os.api.v1 import Question
from agent_os.host.shared.artifacts import (
    execute_resume,
    execute_run,
    frame_tree,
    read_checkpoint,
    read_trace,
)
from agent_os.host.shared.replay import build_mock_script, diff_runs, replace_providers
from agent_os.host.shared.runrecord import STATUS_DONE, dumps
from agent_os.kernel.errors import SkillLoadError
from agent_os.runtime.config import build_kernel, load_config
from agent_os.skills.local_file import LocalFileSkillRegistry


class _InfraError(RuntimeError):
    """宿主/基础设施错误标记(配置缺失、provider 装配失败 → 退出码 4)。"""


def _parse_input(raw: str) -> Any:
    """``--input '<json>'|@file`` → dict;JSON 解析错/文件缺失由调用方归退出码 2。"""
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    return json.loads(raw)


async def _cli_supervisor(question: Question) -> dict[str, Any]:
    """CLI 宿主通道(SUPERVISOR.md §2.3;S2 简化形态):stderr 打印 + stdin 作答。

    run 挂起时把 question 以单行 JSON 写 stderr(coding agent 可解析的协议行),
    随后从 stdin 读一行作为回答,run 进程内闭环;``previous_error`` 透传
    (options 不合时 SupervisorManager 的重问,§3)。stdin EOF 读得空串,
    通常不合 options,重问耗尽后按不合法答案闭环(帧可降级)。
    """
    row: dict[str, Any] = {
        "type": "supervisor.ask",
        "question_id": question.question_id,
        "run_id": question.run_id,
        "frame_id": question.frame_id,
        "question": question.question,
        "context": question.context,
        "options": question.options,
        "urgency": question.urgency,
    }
    if question.previous_error:
        row["previous_error"] = question.previous_error
    print(json.dumps(row, ensure_ascii=False, default=repr), file=sys.stderr, flush=True)
    line = await asyncio.to_thread(sys.stdin.readline)
    return {"answer": line.strip(), "decided_by": "host:cli"}


#: §5/S3 ``supervisor.ask`` 信号通道标签(SupervisorManager 读取)
_cli_supervisor.supervisor_channel = "cli"


def _build_kernel(
    config: str,
    inline: str | None = None,
    supervisor: bool = True,
    checkpoint_interval: int | None = None,
) -> Any:
    """build_kernel 的退出码归类包装:SkillLoadError → 2,其余装配失败 → 4。

    ``inline``(``--inline on|off``,SKILL-INLINING.md §9 消融开关):覆盖本次 run 的
    ``[run].inline``;缺省用配置文件值。改动只落在本次装配私有的 dict 副本上。

    ``checkpoint_interval``(``--checkpoint-interval``,Debugger P5):覆盖本次 run 的
    ``[run].checkpoint_interval``(每 N 步周期 checkpoint,0=关);与 inline 同路径。

    ``supervisor``(S2,§2.3):注入 CLI 宿主通道(``_cli_supervisor``);replay
    传 False——回放按 trace 记录值走,不应阻塞等 stdin(§4)。
    """
    handler = _cli_supervisor if supervisor else None
    try:
        if inline is None and checkpoint_interval is None:
            return build_kernel(config, supervisor_handler=handler)
        cfg = load_config(config)
        run_section = dict(cfg.get("run") or {})
        if inline is not None:
            run_section["inline"] = inline
        if checkpoint_interval is not None:
            run_section["checkpoint_interval"] = checkpoint_interval
        cfg["run"] = run_section
        return build_kernel(cfg, supervisor_handler=handler)
    except SkillLoadError:
        raise
    except Exception as e:  # 装配失败统一归基础设施错(§3.3 退出码 4)
        raise _InfraError(f"{type(e).__name__}: {e}") from e


def _emit_record(record: dict[str, Any], as_json: bool) -> None:
    """输出契约(§3.3):``--json`` → stdout 仅 RunRecord JSON;否则人读摘要 + 末行 JSON。"""
    line = dumps(record)
    if as_json:
        print(line)
        return
    usage = record.get("usage") or {}
    print(f"run_id: {record['run_id']}")
    print(f"status: {record['status']}")
    if record.get("error"):
        print(f"error: {record['error']}")
    print(f"usage: steps={usage.get('steps', 0)} cost={usage.get('cost', 0.0)}")
    print(f"artifacts: {record['artifacts']['dir']}")
    print(line)


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        run_input = _parse_input(args.input)
    except (OSError, json.JSONDecodeError) as e:
        print(f"输入错误: {e}", file=sys.stderr)
        return 2
    try:
        kernel = _build_kernel(
            args.config,
            inline=getattr(args, "inline", None),
            checkpoint_interval=args.checkpoint_interval,
        )
    except SkillLoadError as e:
        print(f"技能校验错误: {e}", file=sys.stderr)
        return 2
    except _InfraError as e:
        print(f"基础设施错误: {e}", file=sys.stderr)
        return 4
    try:
        record = execute_run(
            kernel, args.skill, run_input, artifacts_root=Path(args.artifacts), host="cli"
        )
    except SkillLoadError as e:
        # run 未开始的根帧输入校验/技能寻址错(execute_run 上抛)→ 2(§3.3)
        print(f"校验错误: {e}", file=sys.stderr)
        return 2
    _emit_record(record, args.json)
    return 0 if record["status"] == STATUS_DONE else 3


def _run_dir(args: argparse.Namespace) -> Path:
    return Path(args.artifacts) / "runs" / args.run_id


def _cmd_trace(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args)
    if not (run_dir / "trace.jsonl").is_file():
        print(f"找不到 run 产物目录: {run_dir}", file=sys.stderr)
        return 2
    signals = [row for row in read_trace(run_dir) if row.get("type") == "signal"]
    if args.format == "json":
        for row in signals:
            print(json.dumps(row, ensure_ascii=False, default=repr))
    else:
        for row in signals:
            print(
                f"{row.get('ts', '')!s:<24} {row.get('name', '')!s:<24}"
                f" frame={row.get('frame_id') or '-'}"
            )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args)
    if not (run_dir / "checkpoint.json").is_file():
        print(f"找不到 run 产物目录: {run_dir}", file=sys.stderr)
        return 2
    checkpoint = read_checkpoint(run_dir)
    if args.frame:
        frame = next(
            (f for f in checkpoint.get("frames", []) if f.get("frame_id") == args.frame),
            None,
        )
        if frame is None:
            print(f"找不到帧: {args.frame}", file=sys.stderr)
            return 2
        out: dict[str, Any] = {
            "frame_id": frame["frame_id"],
            "skill": frame.get("skill"),
            "depth": frame.get("depth"),
            "status": frame.get("status"),
            "input": frame.get("input"),
            "result": frame.get("result"),
            "error": frame.get("error"),
            "usage": frame.get("usage"),
        }
        if args.messages:
            # 帧上下文逐条:"模型那一步看到了什么"(§2.3 RCA 核心)
            out["messages"] = (frame.get("context") or {}).get("messages", [])
    else:
        out = {"run_id": args.run_id, "frames": frame_tree(checkpoint)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, default=repr))
    elif args.frame:
        for key, value in out.items():
            if key == "messages":
                for msg in value:
                    print(f"[{msg.get('role')}] {msg.get('content')}")
            else:
                print(f"{key}: {value}")
    else:
        for f in out["frames"]:
            print(
                f"depth={f['depth']} status={f['status']} steps={f['usage']['steps']}"
                f" skill={f['skill']} frame={f['frame_id']}"
            )
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    try:
        kernel = _build_kernel(args.config)
    except SkillLoadError as e:
        print(f"技能校验错误: {e}", file=sys.stderr)
        return 2
    except _InfraError as e:
        print(f"基础设施错误: {e}", file=sys.stderr)
        return 4
    try:
        record = execute_resume(
            kernel, args.checkpoint, artifacts_root=Path(args.artifacts), host="cli"
        )
    except (OSError, ValueError) as e:
        print(f"checkpoint 无效: {e}", file=sys.stderr)
        return 2
    _emit_record(record, args.json)
    return 0 if record["status"] == STATUS_DONE else 3


def _cmd_replay(args: argparse.Namespace) -> int:
    """replay(§3.4):trace + checkpoint 重建 MockProvider 脚本,确定性重放为新 run。"""
    run_dir = _run_dir(args)
    for name in ("meta.json", "trace.jsonl", "checkpoint.json"):
        if not (run_dir / name).is_file():
            print(f"找不到 run 产物目录: {run_dir}", file=sys.stderr)
            return 2
    try:
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        script = build_mock_script(run_dir)
    except (OSError, ValueError) as e:  # 产物畸形 / 信号与 checkpoint 对不齐
        print(f"replay 产物无效: {e}", file=sys.stderr)
        return 2
    try:
        kernel = _build_kernel(args.config, supervisor=False)  # replay 不问第二次(§4)
    except SkillLoadError as e:
        print(f"技能校验错误: {e}", file=sys.stderr)
        return 2
    except _InfraError as e:
        print(f"基础设施错误: {e}", file=sys.stderr)
        return 4
    replace_providers(kernel, script)
    try:
        record = execute_run(
            kernel,
            meta.get("skill"),
            meta.get("input") or {},
            artifacts_root=Path(args.artifacts),
            host="cli",
        )
    except SkillLoadError as e:
        print(f"校验错误: {e}", file=sys.stderr)
        return 2
    _emit_record(record, args.json)
    return 0 if record["status"] == STATUS_DONE else 3


def _emit_report(report: dict[str, Any], as_json: bool, summary: list[str]) -> None:
    """diff/skills 报告的输出契约:``--json`` → 仅 JSON;否则人读摘要 + 末行 JSON。"""
    line = json.dumps(report, ensure_ascii=False, default=repr)
    if as_json:
        print(line)
        return
    for row in summary:
        print(row)
    print(line)


def _cmd_diff(args: argparse.Namespace) -> int:
    """diff(§3.2):两次 run 的结构化对比;查询而非判决,算出报告即退出码 0。"""
    dir_a = Path(args.artifacts) / "runs" / args.run_id_a
    dir_b = Path(args.artifacts) / "runs" / args.run_id_b
    for run_dir in (dir_a, dir_b):
        if not (run_dir / "result.json").is_file() or not (run_dir / "trace.jsonl").is_file():
            print(f"找不到 run 产物目录: {run_dir}", file=sys.stderr)
            return 2
    report = diff_runs(dir_a, dir_b)
    counts = report["signal_counts"]
    _emit_report(
        report,
        args.json,
        [
            f"result_equal: {report['result_equal']}",
            f"signals_equal: {report['signals_equal']}",
            f"signal_counts: a={counts['a']} b={counts['b']}",
            f"first_divergence: {report['first_divergence']}",
        ],
    )
    return 0


def _cmd_skills(args: argparse.Namespace) -> int:
    """skills validate|list(§3.2):构造即加载走完整校验管线,失败退出码 2。"""
    try:
        registry = LocalFileSkillRegistry(args.path)
    except (SkillLoadError, OSError, yaml.YAMLError) as e:
        report: dict[str, Any] = {"ok": False, "skills": [], "errors": [str(e)]}
    else:
        report = {
            "ok": True,
            "skills": [
                {
                    "name": m.name,
                    "version": m.version,
                    "kind": m.kind.value,
                    "description": m.description,
                    "tools": list(m.permissions.tools),
                    "skills": list(m.permissions.skills),
                }
                for m in registry.manifests()
            ],
            "errors": [],
        }
    if report["ok"]:
        summary = [
            f"{s['name']} {s['version']} {s['kind']}: {s['description']}"
            for s in report["skills"]
        ]
    else:
        summary = [f"error: {e}" for e in report["errors"]]
    _emit_report(report, args.json, summary)
    return 0 if report["ok"] else 2


def _cmd_debug(args: argparse.Namespace) -> int:
    """debug(P2 调试前端):延迟导入——debug.py 复用本模块助手,顶层导入会成环。"""
    from agent_os.host.cli.debug import cmd_debug

    return cmd_debug(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-os",
        description="Agent OS CLI runner(RUNNERS.md §3):跑技能、拿结构化 debug 数据",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行技能,stdout 为 RunRecord JSON")
    p_run.add_argument("skill")
    p_run.add_argument("--input", required=True, help="'<json>' 或 @file")
    p_run.add_argument("--config", default="agent-os.toml")
    p_run.add_argument("--artifacts", default=".agent-os")
    p_run.add_argument(
        "--inline",
        choices=["on", "off"],
        default=None,
        help="merge 消融开关(SKILL-INLINING.md §9):off 时 inline 技能退化为压帧调用;缺省用配置值",
    )
    p_run.add_argument(
        "--checkpoint-interval",
        type=int,
        default=None,
        metavar="N",
        help="周期 checkpoint(Debugger P5):每 N 步覆盖写 checkpoint.json(最近现场);缺省用配置值,0=关",
    )
    p_run.add_argument("--json", action="store_true", help="stdout 仅 RunRecord JSON")
    p_run.set_defaults(func=_cmd_run)

    p_trace = sub.add_parser("trace", help="从 trace.jsonl 读信号时间线")
    p_trace.add_argument("run_id")
    p_trace.add_argument("--artifacts", default=".agent-os")
    p_trace.add_argument("--format", choices=["json", "table"], default="table")
    p_trace.set_defaults(func=_cmd_trace)

    p_inspect = sub.add_parser("inspect", help="从 checkpoint.json 读帧树/帧上下文")
    p_inspect.add_argument("run_id")
    p_inspect.add_argument("--frame", dest="frame", default=None, help="帧 id")
    p_inspect.add_argument("--messages", action="store_true", help="输出帧 context.messages")
    p_inspect.add_argument("--artifacts", default=".agent-os")
    p_inspect.add_argument("--json", action="store_true")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_resume = sub.add_parser("resume", help="从 checkpoint 恢复运行")
    p_resume.add_argument("checkpoint", help="checkpoint.json 路径")
    p_resume.add_argument("--config", default="agent-os.toml")
    p_resume.add_argument("--artifacts", default=".agent-os")
    p_resume.add_argument("--json", action="store_true")
    p_resume.set_defaults(func=_cmd_resume)

    p_replay = sub.add_parser("replay", help="重建 MockProvider 脚本,确定性重放该 run(§3.4)")
    p_replay.add_argument("run_id")
    p_replay.add_argument("--config", default="agent-os.toml")
    p_replay.add_argument("--artifacts", default=".agent-os")
    p_replay.add_argument("--json", action="store_true")
    p_replay.set_defaults(func=_cmd_replay)

    p_diff = sub.add_parser("diff", help="两次 run 的结构化 diff(result/usage/信号序列)")
    p_diff.add_argument("run_id_a")
    p_diff.add_argument("run_id_b")
    p_diff.add_argument("--artifacts", default=".agent-os")
    p_diff.add_argument("--json", action="store_true")
    p_diff.set_defaults(func=_cmd_diff)

    p_debug = sub.add_parser("debug", help="交互式调试技能 run(断点/单步/检视,Debugger P2)")
    p_debug.add_argument("skill", nargs="?", help="技能名(--replay 时从产物 meta 读取,不给)")
    p_debug.add_argument("--input", help="'<json>' 或 @file(live 调试必填;--replay 时从产物 meta 读取)")
    p_debug.add_argument(
        "--replay",
        dest="replay",
        default=None,
        metavar="RUN_ID",
        help="时间旅行(Debugger P5):回放该 run(trace+checkpoint 重建 Mock 脚本)并进调试会话",
    )
    p_debug.add_argument(
        "--until-step",
        dest="until_step",
        type=int,
        default=None,
        metavar="N",
        help="一次性步数断点:run 启动后不停,直到第 N 条 pre:step 才暂停",
    )
    p_debug.add_argument("--config", default="agent-os.toml")
    p_debug.add_argument("--artifacts", default=".agent-os")
    p_debug.set_defaults(func=_cmd_debug)

    p_skills = sub.add_parser("skills", help="skills.yaml lint(manifest 校验 + 依赖图检查)")
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)
    for action in ("validate", "list"):
        sp = skills_sub.add_parser(action, help="同一校验报告;失败退出码 2")
        sp.add_argument("path", help="skills.yaml 路径")
        sp.add_argument("--json", action="store_true")
        sp.set_defaults(func=_cmd_skills)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口(``agent-os = agent_os.host.cli.main:main``,§3.5)。"""
    args = _parser().parse_args(argv)
    return int(args.func(args))

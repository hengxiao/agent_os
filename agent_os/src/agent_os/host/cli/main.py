"""agent-os CLI(docs/RUNNERS.md §3;R1 核心:run/trace/inspect/resume;R2 复现:replay/diff/skills)。

面向 coding agent 的薄宿主:stdout = RunRecord JSON(``--json`` 时唯一输出;
缺省人读摘要 + 末行 JSON,§3.3),stderr 人类可读日志。退出码(§3.3):

| code | 含义 |
|---|---|
| 0 | run 成功(status=done) |
| 2 | 输入/配置/技能校验错误(SkillLoadError、输入不合 schema、输入 JSON 解析错、run 覆盖选项非法 §2.5) |
| 3 | run 失败或中止(技能返回错误、OutputValidationError、RunAborted) |
| 4 | 宿主/基础设施错误(配置缺失、provider 装配失败、docker 不可用) |

S2 增量(docs/SUPERVISOR.md v2 §2.3):CLI 宿主通道——run/resume 装配时注入
``_cli_supervisor`` handler,run 挂起时把 question JSON 写 stderr(coding agent
可解析),从 stdin 读一行作答,单命令进程内闭环;跨进程 pending/answer 子命令
在单进程 CLI 下无收件箱可查,异步收件箱形态由 Web 宿主承载。replay 不注入
(回放按 trace 记录值走,不问第二次,§4)。M1 增量(§8.3):system.user.ask/
notify 的宿主通道 ``_CliUserChannel``(与 ``_cli_supervisor`` 同构的
stdin/stderr 协议)随同一开关注入,replay 同样不注入。
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from agent_os.api.v1 import Question, cli_principal
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
from agent_os.runtime.overrides import (
    OverrideError,
    add_override_arguments,
    apply_overrides,
    collect_cli_overrides,
    resolve_effective,
    resolve_provenance,
)
from agent_os.skills.local_file import LocalFileSkillRegistry


class _InfraError(RuntimeError):
    """宿主/基础设施错误标记(配置缺失、provider 装配失败 → 退出码 4)。"""


def _parse_input(raw: str) -> Any:
    """``--input '<json>'|@file`` → dict;JSON 解析错/文件缺失由调用方归退出码 2。"""
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    return json.loads(raw)


async def _cli_supervisor(question: Question) -> dict[str, Any]:
    """CLI 宿主通道(docs/SUPERVISOR.md §2.3;S2 简化形态):stderr 打印 + stdin 作答。

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
    # 升权确认(docs/ESCALATION.md §3):kind 透传给 coding agent 区分渲染/作答;
    # 结构化载荷(skill/tier/params/requested)本就在 context 里直通
    if question.kind != "question":
        row["kind"] = question.kind
    if question.previous_error:
        row["previous_error"] = question.previous_error
    # 写**原始** stderr(sys.__stderr__):code 技能帧内经 contextlib.redirect_stderr
    # 捕获时 sys.stderr 已换成 StringIO(logic/inprocess.py),协议行会被吞进帧产物——
    # 用户看不到提问而 stdin 在等答;redirect_* 只换 sys.stderr/sys.stdout 绑定,
    # 不碰 dunder 原始流。机器消费者契约(单行 JSON 协议行)不变。
    print(json.dumps(row, ensure_ascii=False, default=repr), file=sys.__stderr__, flush=True)
    line = await asyncio.to_thread(sys.stdin.readline)
    return {"answer": line.strip(), "decided_by": "host:cli"}


#: §5/S3 ``supervisor.ask`` 信号通道标签(SupervisorManager 读取)
_cli_supervisor.supervisor_channel = "cli"


class _CliUserChannel:
    """M1(§8.3)CLI 宿主用户通道:与 ``_cli_supervisor`` 同构的 stdin/stderr 协议。

    ``ask``:stderr 打印 ``[user] {question}`` + stdin 读一行返回答(coding agent
    可解析的协议行,单命令进程内闭环);stdin EOF → EOFError(工具侧经 dispatch
    归一 INTERNAL,§8.1 分发边界)。``notify``:单向,只打印不读答。
    打印都写**原始** stderr(``sys.__stderr__``;理由见 ``_cli_supervisor`` 注释——
    code 技能帧内的 redirect_stderr 捕获会吞掉 sys.stderr 上的提示行)。
    """

    async def ask(self, question: str) -> str:
        print(f"[user] {question}", file=sys.__stderr__, flush=True)
        line = await asyncio.to_thread(sys.stdin.readline)
        if line == "":
            raise EOFError("stdin EOF:读不到用户回答")
        return line.strip()

    async def notify(self, message: str) -> None:
        print(f"[user] {message}", file=sys.__stderr__, flush=True)


# M1 接线(§8.3):system.user.ask/system.user.notify 的宿主通道已接到 CLI——
# _CliUserChannel(stdin/stderr 协议,与 _cli_supervisor 同构)在 _build_kernel
# 装配链上经 build_kernel(user_channel=...) 注入;replay 不注入(回放按 trace
# 记录值走,不问第二次,同 supervisor 通道的 §4 语义)。

def _build_kernel(
    config: str,
    overrides: dict[str, Any] | None = None,
    supervisor: bool = True,
) -> Any:
    """build_kernel 的退出码归类包装:OverrideError/SkillLoadError → 2,其余装配失败 → 4。

    ``overrides``(K1,docs/RUNNERS.md §2.5):run 覆盖选项集(``collect_cli_overrides``
    收集的显式 flag;``None`` = 本路径不做覆盖,resume/replay/lab 用)。非 None 时经
    注册表 ``resolve_effective`` 补 env 别名(P4:flag > env > toml)再
    ``apply_overrides`` 合并进本次装配私有的 config dict 副本——只影响本次 run,
    配置文件永不被运行时改写(D3 锚点语义)。

    ``supervisor``(S2,§2.3):注入 CLI 宿主通道(``_cli_supervisor``);replay
    传 False——回放按 trace 记录值走,不应阻塞等 stdin(§4)。M1(§8.3)同理:
    用户通道(``_CliUserChannel``,system.user.ask/notify 的宿主回调)随同一
    开关注入——回放不接线,两工具按"user 通道未装配"报 NOT_FOUND。
    """
    handler = _cli_supervisor if supervisor else None
    channel = _CliUserChannel() if supervisor else None
    try:
        effective = resolve_effective(overrides) if overrides is not None else {}
        if not effective:
            return build_kernel(config, supervisor_handler=handler, user_channel=channel)
        cfg = apply_overrides(load_config(config), effective)
        return build_kernel(cfg, supervisor_handler=handler, user_channel=channel)
    except (SkillLoadError, OverrideError):
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


def _close_telemetry(kernel: Any) -> None:
    """宿主退出点排干遥测 sink(§10.2:exporter 的正确性不依赖 run.finished 送达——
    ``close()`` 才是排干点;内核只按 run 调 ``close_run``,sink/exporter 的
    ``close()`` 归宿主)。

    duck-typed(同 runner._release_run 先例):telemetry 缺席/无 close 即 no-op;
    失败只警告——遥测是观察通道,收尾失败不该改变退出码(§5.3 精神)。
    execute_run/execute_resume 各跑各的 ``asyncio.run``,run loop 已销毁,
    close 在新 loop 上跑(exporter 的异步收尾不绑定旧 loop)。
    """
    close = getattr(getattr(kernel, "telemetry", None), "close", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            asyncio.run(result)
    except Exception as e:  # noqa: BLE001 — 遥测收尾失败仅告警(见 docstring)
        print(f"遥测收尾失败(忽略,不影响退出码): {type(e).__name__}: {e}", file=sys.stderr)


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        run_input = _parse_input(args.input)
    except (OSError, json.JSONDecodeError) as e:
        print(f"输入错误: {e}", file=sys.stderr)
        return 2
    overrides = collect_cli_overrides(args)  # K1(§2.5):显式 flag 集(env 补全在 _build_kernel)
    try:
        kernel = _build_kernel(args.config, overrides=overrides)
    except OverrideError as e:
        print(f"覆盖选项错误: {e}", file=sys.stderr)
        return 2
    except SkillLoadError as e:
        print(f"技能校验错误: {e}", file=sys.stderr)
        return 2
    except _InfraError as e:
        print(f"基础设施错误: {e}", file=sys.stderr)
        return 4
    try:
        try:
            # K1(§2.5 P4):覆盖字段 provenance 写 meta.json(空段不写键,artifacts.py)
            provenance = resolve_provenance(load_config(args.config).get("run") or {}, overrides)
        except Exception:  # noqa: BLE001 — provenance 是审计面,读取失败不阻断 run
            provenance = {}
        record = execute_run(
            kernel, args.skill, run_input, artifacts_root=Path(args.artifacts), host="cli",
            # 数据层身份(docs/DATA-AUTHZ.md §2.2):CLI 本机用户即身份
            principal=cli_principal(),
            overrides=provenance,
        )
    except SkillLoadError as e:
        # run 未开始的根帧输入校验/技能寻址错(execute_run 上抛)→ 2(§3.3)
        print(f"校验错误: {e}", file=sys.stderr)
        return 2
    finally:
        _close_telemetry(kernel)  # 排干 sink/exporter(§10.2;close 是排干点)
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
    finally:
        _close_telemetry(kernel)  # 同 run:resume 收尾也排干 sink/exporter(§10.2)
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
            # replay 同样是本机用户发起(§2.2);原 run 身份不进 replay(D3 派生链再议)
            principal=cli_principal(),
        )
    except SkillLoadError as e:
        print(f"校验错误: {e}", file=sys.stderr)
        return 2
    finally:
        _close_telemetry(kernel)  # replay 也真跑内核(§3.4),收尾同样排干
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


def _cmd_lab(args: argparse.Namespace) -> int:
    """lab validate(docs/SKILL-DEV.md §3;L5):草稿跑提交闸门(G1-G5),头less 输出。

    闸门与 Web 同一函数(skills/gate.py):G4 冒烟用本配置装配的内核真跑
    (overlay 草稿优先);报告落盘 ``drafts/<name>/gate/``(与 Web 共用
    drafts_root,promote 可直接消费)。退出码:pass/warn = 0,fail = 2(lint 先例)。
    """
    import asyncio as _asyncio

    import jsonschema as _jsonschema

    from agent_os.skills.draft_store import DraftStore, OverlaySkillRegistry
    from agent_os.skills.gate import validate_draft

    cfg = load_config(args.config)
    drafts_root = (cfg.get("lab") or {}).get("drafts_root") or str(
        Path(args.artifacts) / "drafts"
    )
    store = DraftStore(drafts_root)
    try:
        draft = store.read(args.name)
    except (FileNotFoundError, ValueError) as e:
        _emit_report({"ok": False, "error": str(e)}, args.json, [f"error: {e}"])
        return 2
    try:
        kernel = _build_kernel(args.config)
    except (SkillLoadError, _InfraError) as e:
        _emit_report({"ok": False, "error": str(e)}, args.json, [f"error: {e}"])
        return 4
    if kernel.skills is None:
        # 无 [skills] 配置也能验草稿:生产层给空 registry(推导档只算 tools)
        kernel.skills = _EmptyProduction()
    from agent_os.host.web.run_manager import (
        RunManager,  # 延迟导入(web 依赖不进 CLI 主路径)
    )

    RunManager.swap_skills_overlay(kernel, OverlaySkillRegistry(kernel.skills, store))
    outputs = (draft.get("manifest") or {}).get("outputs") or {}

    def smoke(case: dict[str, Any]) -> dict[str, Any]:
        try:
            result = _asyncio.run(kernel.run(args.name, case.get("input") or {}))
        except Exception as e:  # noqa: BLE001 — 冒烟失败归 G4 finding,不炸 validate
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if outputs:
            try:
                _jsonschema.validate(result, outputs)
            except _jsonschema.ValidationError as e:
                return {"ok": False, "error": f"outputs 校验失败: {e.message}"}
        return {"ok": True, "error": None}

    report = validate_draft(draft, production=kernel.skills, tools=kernel.tools,
                            smoke_runner=smoke, store=store)
    report = store.save_gate_report(args.name, report)
    ok = report["status"] != "fail"
    _emit_report(
        {"ok": ok, "status": report["status"], "report": report},
        args.json,
        [f"{args.name}: {report['status']}"],
    )
    return 0 if ok else 2


class _EmptyProduction:
    """空生产 registry(CLI 无 [skills] 配置时的推导档兜底)。"""

    def get(self, ref: Any) -> Any:
        raise SkillLoadError(f"未注册的技能: {ref}")

    def manifests(self) -> list[Any]:
        return []


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
        description="Agent OS CLI runner(docs/RUNNERS.md §3):跑技能、拿结构化 debug 数据",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行技能,stdout 为 RunRecord JSON")
    p_run.add_argument("skill")
    p_run.add_argument("--input", required=True, help="'<json>' 或 @file")
    p_run.add_argument("--config", default="agent-os.toml")
    p_run.add_argument("--artifacts", default=".agent-os")
    # K1(docs/RUNNERS.md §2.5 P2):run 覆盖选项全部由注册表生成(P1:--config/
    # --artifacts/--json 是 host 私有选项,不进注册表);挂载逻辑与 coding_cli
    # 入口共用 runtime/overrides.py 的 add_override_arguments
    add_override_arguments(p_run)
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

    # Skill Lab(docs/SKILL-DEV.md §3;L5):草稿闸门头less 入口(coding agent 可用)
    p_lab = sub.add_parser("lab", help="Skill Lab:草稿提交闸门(G1-G5)")
    lab_sub = p_lab.add_subparsers(dest="lab_command", required=True)
    sp_lab = lab_sub.add_parser("validate", help="跑提交闸门;pass/warn 退出码 0,fail 2")
    sp_lab.add_argument("name", help="草稿名(drafts_root 下的目录名)")
    sp_lab.add_argument("--config", default="agent-os.toml")
    sp_lab.add_argument("--artifacts", default=".agent-os")
    sp_lab.add_argument("--json", action="store_true")
    sp_lab.set_defaults(func=_cmd_lab)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口(``agent-os = agent_os.host.cli.main:main``,§3.5)。"""
    args = _parser().parse_args(argv)
    return int(args.func(args))

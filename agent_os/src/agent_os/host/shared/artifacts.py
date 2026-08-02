"""产物组织与读取层(docs/RUNNERS.md §2.2 产物布局;两个 runner 共用)。

每次运行落 ``<artifacts_root>/runs/<run_id>/``::

    meta.json        # {run_id, skill, input, host, started_at}
    trace.jsonl      # 从 telemetry 目录归档的 WAL(<run_id>.jsonl;缺则空文件)
    checkpoint.json  # run 结束/中止时经 kernel.checkpoint 快照(RCA 与 resume 数据源)
    result.json      # {status, result, error, usage 汇总}

错误归类锚点(§3.3):run 未开始即抛的校验类异常(SkillLoadError:根帧输入不合
schema、技能寻址失败)原样上抛,由宿主归退出码 2;run 开始后的异常一律捕获进
RunRecord(``failed``/``aborted`` → 退出码 3)。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_os.api.v1 import RUN_STARTED
from agent_os.host.shared.runrecord import (
    STATUS_ABORTED,
    STATUS_DONE,
    STATUS_FAILED,
    make_record,
)
from agent_os.kernel.checkpoint import CHECKPOINT_VERSION, PeriodicCheckpointer
from agent_os.kernel.errors import RunAborted


def _write_json(path: Path, doc: Any) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=repr), encoding="utf-8")


def _archive_trace(kernel: Any, run_id: str, run_dir: Path) -> None:
    """把 telemetry 目录下的 ``<run_id>.jsonl`` 归档进产物目录;找不到则写空文件。"""
    target = run_dir / "trace.jsonl"
    traces_dir = getattr(kernel.telemetry, "traces_dir", None)
    src = Path(traces_dir) / f"{run_id}.jsonl" if traces_dir is not None else None
    if src is not None and src.is_file():
        shutil.copyfile(src, target)
    elif not target.exists():
        target.write_text("", encoding="utf-8")


def _finalize_run(
    kernel: Any,
    run_id: str,
    artifacts_root: Path,
    *,
    meta: dict[str, Any],
    status: str,
    result: Any,
    error: str | None,
) -> dict[str, Any]:
    """写产物四件套并组 RunRecord(``run`` 与 ``resume`` 共用,§2.2)。

    meta.json 已存在(原 run 产物目录)时保留原文;checkpoint 在 run 失败但 run
    对象存在时也照常快照(帧上下文是 RCA 核心数据源,§2.3)。
    """
    run_dir = artifacts_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        _write_json(meta_path, meta)
    _archive_trace(kernel, run_id, run_dir)
    checkpoint_path = run_dir / "checkpoint.json"
    kernel.checkpoint(run_id, str(checkpoint_path))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    usage = checkpoint["run"]["usage"]
    _write_json(
        run_dir / "result.json",
        {"status": status, "result": result, "error": error, "usage": usage},
    )
    return make_record(
        run_id=run_id,
        status=status,
        result=result,
        error=error,
        usage=usage,
        frames=len(checkpoint["frames"]),
        run_dir=run_dir,
    )


def execute_run(
    kernel: Any,
    skill: str,
    input: dict[str, Any],
    *,
    artifacts_root: Path,
    host: str,
    principal: Any = None,
) -> dict[str, Any]:
    """跑一个 run 并落产物(§2.2),返回 RunRecord dict(§3.3)。

    订阅 ``run.started`` 捕获 run_id;status 判定:正常返回 → ``done``,
    RunAborted 及其子类 → ``aborted``,其余异常 → ``failed``
    (error = ``"Type: message"``)。run 未开始(无 run_id)的异常原样上抛。
    ``RunConfig.checkpoint_interval > 0`` 时挂载周期 checkpoint 订阅者
    (Debugger P5;覆盖写"最近现场",kernel/checkpoint.py)。
    ``principal``(docs/DATA-AUTHZ.md §2.2):宿主认证后的调用方身份,透传给
    ``Kernel.run``;缺省 None = v1 单用户语义。
    """
    started: list[str] = []

    async def _rec(sig: Any) -> None:
        started.append(sig.run_id)

    kernel.signals.subscribe(RUN_STARTED, _rec)
    interval = getattr(kernel.config, "checkpoint_interval", 0)
    if interval > 0:
        # Debugger P5 周期 checkpoint:每 N 步覆盖写"最近现场"(kernel/checkpoint.py)
        PeriodicCheckpointer(kernel, interval, artifacts_root).attach()
    started_at = datetime.now(UTC).isoformat()
    status, result, error = STATUS_DONE, None, None
    try:
        result = asyncio.run(kernel.run(skill, input, principal=principal))
    except RunAborted as e:
        status, error = STATUS_ABORTED, f"{type(e).__name__}: {e}"
    except Exception as e:  # 宿主边界故意兜底:run 失败归 RunRecord,不炸宿主(§3.3)
        if not started:
            raise  # run 未开始(校验/装配类):上抛由宿主归退出码 2/4
        status, error = STATUS_FAILED, f"{type(e).__name__}: {e}"
    run_id = started[0]
    return _finalize_run(
        kernel,
        run_id,
        Path(artifacts_root),
        meta={
            "run_id": run_id,
            "skill": skill,
            "input": input,
            "host": host,
            "started_at": started_at,
        },
        status=status,
        result=result,
        error=error,
    )


def execute_resume(
    kernel: Any,
    checkpoint_path: str | Path,
    *,
    artifacts_root: Path,
    host: str,
) -> dict[str, Any]:
    """从 checkpoint 恢复 run(§3.2 ``agent-os resume``),产物写回原 run 目录。

    产物目录按 checkpoint 里的 run_id 定位:已存在则更新 result/checkpoint
    (meta 保留),不存在则新起目录写全四件套。checkpoint 文件缺失/畸形抛
    ``OSError``/``ValueError``,由宿主归退出码 2。
    """
    path = Path(checkpoint_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("v") != CHECKPOINT_VERSION:
        raise ValueError(f"不支持的 checkpoint 版本: {doc.get('v')!r}")
    run_id = doc["run"]["run_id"]
    root = next((f for f in doc["frames"] if f.get("parent_id") is None), {})
    started_at = datetime.now(UTC).isoformat()
    status, result, error = STATUS_DONE, None, None
    try:
        result = asyncio.run(kernel.resume(str(path)))
    except RunAborted as e:
        status, error = STATUS_ABORTED, f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 — 与 execute_run 同旨:失败归 RunRecord(§3.3)
        status, error = STATUS_FAILED, f"{type(e).__name__}: {e}"
    return _finalize_run(
        kernel,
        run_id,
        Path(artifacts_root),
        meta={
            "run_id": run_id,
            "skill": root.get("skill", ""),
            "input": root.get("input", {}),
            "host": host,
            "started_at": started_at,
            "resumed_from": str(path),
        },
        status=status,
        result=result,
        error=error,
    )


def read_trace(run_dir: str | Path) -> list[dict[str, Any]]:
    """读产物目录的 trace.jsonl,逐行 JSON(版本头行原样保留,由调用方过滤)。"""
    rows: list[dict[str, Any]] = []
    for line in (Path(run_dir) / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_checkpoint(run_dir: str | Path) -> dict[str, Any]:
    """读产物目录的 checkpoint.json(§10.2 schema v1)。"""
    return json.loads((Path(run_dir) / "checkpoint.json").read_text(encoding="utf-8"))


def read_result(run_dir: str | Path) -> dict[str, Any]:
    """读产物目录的 result.json({status, result, error, usage 汇总},§2.2)。"""
    return json.loads((Path(run_dir) / "result.json").read_text(encoding="utf-8"))


def frame_tree(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    """checkpoint → 按 depth 排序的帧摘要列表(frame_id/skill/depth/status/usage.steps)。"""
    frames = sorted(checkpoint.get("frames", []), key=lambda f: f.get("depth", 0))
    return [
        {
            "frame_id": f["frame_id"],
            "skill": f.get("skill"),
            "depth": f.get("depth"),
            "status": f.get("status"),
            "usage": {"steps": (f.get("usage") or {}).get("steps", 0)},
        }
        for f in frames
    ]

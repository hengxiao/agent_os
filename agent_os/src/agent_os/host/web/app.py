"""FastAPI app(RUNNERS.md §4.3 API 契约;R3):Web UI Runner 的路由层。

``create_app(config_path, artifacts_root=...)`` 返回 app;路由层只调用
host/shared 的读取层与 :class:`RunManager`,不 import 内核私有实现(§4.5)。

校验错归类(与 CLI 退出码 2 同类,§3.3):技能不存在/输入不合 schema 等
"run 未开始"的失败统一返回 ``200 + {"status": "failed", "error": ...}``。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_os.host.shared.artifacts import (
    frame_tree,
    read_checkpoint,
    read_result,
    read_trace,
)
from agent_os.host.web.rca import locate_first_error, usage_panel
from agent_os.host.web.run_manager import (
    HUB_CLOSED,
    ResumeConflictError,
    RunManager,
    RunValidationError,
    _jsonable,
)
from agent_os.kernel.errors import SkillLoadError

_STATIC_DIR = Path(__file__).resolve().parent / "static"

#: SSE 空闲 keepalive 间隔(秒):防代理/浏览器断连,dev 工具取保守值
_SSE_KEEPALIVE = 15.0

#: kind 过滤(§4.3 ``kind=llm|tool|frame|sidecar|all``)→ 信号名子串集合
_KIND_HINTS = {
    "llm": ("llm",),
    "tool": ("tool",),
    "frame": ("frame",),
    "sidecar": ("budget", "veto", "sidecar"),
    "run": ("run.",),
}


class RunOverrides(BaseModel):
    """``POST /api/runs`` 的 ``overrides``(WEB-UI.md §4.3 高级区):合并进本次 run 的 RunConfig。"""

    model: str | None = None
    max_cost: float | None = None
    max_steps: int | None = None


class RunBody(BaseModel):
    """``POST /api/runs`` 请求体(§4.3 ``{skill, input, overrides?, wait?}``)。"""

    skill: str
    input: dict[str, Any]
    wait: bool = False
    overrides: RunOverrides | None = None


def _run_dir(artifacts_root: Path, run_id: str) -> Path:
    return artifacts_root / "runs" / run_id


def _detail(artifacts_root: Path, manager: RunManager, run_id: str) -> dict[str, Any] | None:
    """RunRecord 详情:终态从产物重建(result.json + checkpoint 帧树),在途取内存态。"""
    run_dir = _run_dir(artifacts_root, run_id)
    if (run_dir / "result.json").is_file():
        try:
            result_doc = read_result(run_dir)
            frames = frame_tree(read_checkpoint(run_dir))
        except (OSError, json.JSONDecodeError):
            pass  # 产物落盘中的半写窗口(write_text 非原子):按在途处理,调用方重试
        else:
            return {
                "run_id": run_id,
                "status": result_doc.get("status"),
                "result": result_doc.get("result"),
                "error": result_doc.get("error"),
                "usage": result_doc.get("usage") or {},
                "frames": frames,
            }
    state = manager.state_of(run_id)
    if state is None:
        return None
    kernel = state.get("kernel")
    cfg = getattr(kernel, "config", None)
    return {
        "run_id": run_id,
        "status": state["status"],
        "result": None,
        "error": state.get("error"),
        "usage": (state.get("record") or {}).get("usage") or {},
        "frames": [],
        # D3 live 视图数据源(§4.3):已用时长起点 + 本次 run 生效的 RunConfig
        # (steps/cost 双 ProgressBar 的分母;含 overrides 合并后的值)
        "started_at": state.get("started_at"),
        "config": (
            {"model": cfg.model, "max_steps": cfg.max_steps, "max_cost": cfg.max_cost}
            if cfg is not None
            else None
        ),
    }


def _list_runs(artifacts_root: Path, manager: RunManager) -> list[dict[str, Any]]:
    """run 列表(§4.3):内存 active 合并产物目录重建的历史,内存态优先。"""
    runs: dict[str, dict[str, Any]] = {}
    runs_dir = artifacts_root / "runs"
    if runs_dir.is_dir():
        for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            meta_path = run_dir / "meta.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            item: dict[str, Any] = {
                "run_id": meta.get("run_id") or run_dir.name,
                "skill": meta.get("skill"),
                "status": "running",  # meta 在、result 未落:视为在途(崩溃/断电遗留)
                "started_at": meta.get("started_at"),
                "cost": 0.0,
            }
            result_path = run_dir / "result.json"
            if result_path.is_file():
                try:
                    result_doc = json.loads(result_path.read_text(encoding="utf-8"))
                    item["status"] = result_doc.get("status")
                    item["cost"] = (result_doc.get("usage") or {}).get("cost", 0.0)
                except (OSError, json.JSONDecodeError):
                    pass
            runs[item["run_id"]] = item
    for run_id, state in manager.active_items():
        item = runs.get(run_id) or {
            "run_id": run_id,
            "skill": state.get("skill"),
            "started_at": state.get("started_at"),
            "cost": 0.0,
        }
        item["status"] = state["status"]
        item["cost"] = ((state.get("record") or {}).get("usage") or {}).get("cost", item["cost"])
        runs[run_id] = item
    return sorted(runs.values(), key=lambda r: r.get("started_at") or "", reverse=True)


def _filter_kind(rows: list[dict[str, Any]], kind: str | None) -> list[dict[str, Any]]:
    if not kind or kind == "all":
        return rows
    hints = _KIND_HINTS.get(kind, (kind,))
    return [r for r in rows if any(h in (r.get("name") or "") for h in hints)]


def _skill_summary(manifest: Any) -> dict[str, Any]:
    """manifest 摘要(WEB-UI.md §6.2):name/version/kind/description/permissions。"""
    perms = manifest.permissions
    return {
        "name": manifest.name,
        "version": manifest.version,
        "kind": getattr(manifest.kind, "value", manifest.kind),
        "description": manifest.description,
        "permissions": {
            "tools": list(perms.tools),
            "skills": list(perms.skills),
            "blackboard": list(perms.blackboard),
        },
    }


def _skill_doc(manifest: Any) -> dict[str, Any]:
    """全量 manifest 文档(``GET /api/skills/{name}``;D3 Launch Modal 取 inputs schema)。"""
    doc = _skill_summary(manifest)
    model = manifest.model
    limits = manifest.limits
    doc.update(
        {
            "inputs": manifest.inputs or {},
            "outputs": manifest.outputs or {},
            "model": (
                {"prefer": list(model.prefer), "temperature": model.temperature}
                if model
                else None
            ),
            "limits": (
                {
                    "max_steps": limits.max_steps,
                    "timeout": limits.timeout,
                    "retry": limits.retry,
                }
                if limits
                else None
            ),
            "prompt": manifest.prompt,
            "entry": manifest.entry,
            "handler": manifest.handler,
        }
    )
    return doc


def _sse_line(row: dict[str, Any]) -> str:
    return f"data: {json.dumps(row, ensure_ascii=False, default=repr)}\n\n"


def create_app(config_path: str | Path, artifacts_root: Path = Path(".agent-os")) -> FastAPI:
    """装配 Web UI Runner(§4.2):RunManager + REST + SSE + 静态 SPA。"""
    manager = RunManager(config_path, Path(artifacts_root))
    root = Path(artifacts_root)
    app = FastAPI(title="Agent OS Web UI")
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.post("/api/runs")
    async def post_run(body: RunBody) -> dict[str, Any]:
        overrides = body.overrides.model_dump(exclude_none=True) if body.overrides else None
        try:
            run_id = await manager.start_run(
                body.skill, body.input, wait=body.wait, overrides=overrides
            )
        except RunValidationError as e:
            return {"status": "failed", "error": str(e)}
        if body.wait:
            return _jsonable((manager.state_of(run_id) or {}).get("record") or {})
        return {"run_id": run_id}

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return _list_runs(root, manager)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        detail = _detail(root, manager, run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"找不到 run: {run_id}")
        return detail

    @app.get("/api/runs/{run_id}/signals")
    def get_signals(run_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        run_dir = _run_dir(root, run_id)
        rows: list[dict[str, Any]] | None = None
        if (run_dir / "trace.jsonl").is_file():
            try:
                rows = [r for r in read_trace(run_dir) if r.get("type") == "signal"]
            except (OSError, json.JSONDecodeError):
                rows = None  # trace 归档中的半写窗口:回退 hub 缓冲
        if rows is None:
            hub = manager.hub_of(run_id)
            if hub is None:
                raise HTTPException(status_code=404, detail=f"找不到 run: {run_id}")
            rows = hub.snapshot()
        return _filter_kind(rows, kind)

    @app.get("/api/runs/{run_id}/frames/{frame_id}")
    def get_frame(run_id: str, frame_id: str) -> dict[str, Any]:
        run_dir = _run_dir(root, run_id)
        if not (run_dir / "checkpoint.json").is_file():
            raise HTTPException(status_code=404, detail=f"找不到 run: {run_id}")
        try:
            checkpoint = read_checkpoint(run_dir)
        except (OSError, json.JSONDecodeError) as e:
            raise HTTPException(status_code=404, detail="run 产物落盘中,请重试") from e
        frame = next(
            (f for f in checkpoint.get("frames", []) if f.get("frame_id") == frame_id),
            None,
        )
        if frame is None:
            raise HTTPException(status_code=404, detail=f"找不到帧: {frame_id}")
        return {
            "frame_id": frame["frame_id"],
            "skill": frame.get("skill"),
            "status": frame.get("status"),
            "usage": frame.get("usage"),
            "input": frame.get("input"),
            "result": frame.get("result"),
            "error": frame.get("error"),
            # 帧上下文逐条:"模型那一步看到了什么"(§2.3 RCA 核心)
            "messages": (frame.get("context") or {}).get("messages", []),
        }

    @app.get("/api/runs/{run_id}/rca")
    def get_rca(run_id: str) -> dict[str, Any]:
        """失败定位(§4.4):首个错误的结构化位置(vetoed/tool_error/aborted)。"""
        run_dir = _run_dir(root, run_id)
        if not run_dir.is_dir() and manager.state_of(run_id) is None:
            raise HTTPException(status_code=404, detail=f"找不到 run: {run_id}")
        return locate_first_error(run_dir)

    @app.get("/api/runs/{run_id}/usage")
    def get_usage(run_id: str) -> dict[str, Any]:
        """usage 面板(§4.4):run 汇总(九字段)+ 按帧分列。"""
        run_dir = _run_dir(root, run_id)
        if not (run_dir / "checkpoint.json").is_file():
            raise HTTPException(status_code=404, detail=f"找不到 checkpoint: {run_id}")
        try:
            return usage_panel(run_dir)
        except (OSError, json.JSONDecodeError) as e:
            raise HTTPException(status_code=404, detail="run 产物落盘中,请重试") from e

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str) -> dict[str, Any]:
        """stop(§4.3):RunControl.stop,run 在下一个 safe point 中止。"""
        if manager.state_of(run_id) is None and not _run_dir(root, run_id).is_dir():
            raise HTTPException(status_code=404, detail=f"找不到 run: {run_id}")
        if not await manager.stop_run(run_id):
            raise HTTPException(status_code=409, detail=f"run {run_id} 已结束,无法 stop")
        return {"ok": True}

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict[str, Any]:
        """resume(§4.3):从该 run 的 checkpoint.json 恢复,返回 RunRecord JSON。"""
        try:
            record = await manager.resume_run(run_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ResumeConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"checkpoint 畸形: {e}") from e
        return _jsonable(record)

    @app.get("/api/skills")
    def list_skills() -> list[dict[str, Any]]:
        """技能清单(WEB-UI.md §6.2):共享 registry 的 manifest 摘要列表(Launch Modal 下拉)。"""
        try:
            manifests = manager.skills_manifests()
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return [_skill_summary(m) for m in manifests]

    @app.get("/api/skills/{name}")
    def get_skill(name: str) -> dict[str, Any]:
        """单个技能的全量 manifest(§6.2;Launch Modal 的 inputs schema 数据源)。"""
        try:
            manifest = manager.skill_manifest(name)
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"找不到技能: {name}")
        return _skill_doc(manifest)

    @app.post("/api/skills/reload")
    def reload_skills() -> dict[str, Any]:
        """热重载 skills 文件(§4.3;mtime 检查,只影响后续新建的 run,§6.1)。"""
        try:
            return {"reloaded": manager.reload_skills()}
        except (RunValidationError, SkillLoadError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/runs/{run_id}/stream")
    async def stream_run(run_id: str) -> StreamingResponse:
        """SSE(§4.3):先回放环形缓冲,run 未结束则持续推送,结束发 ``event: end`` 关闭。"""
        hub = manager.hub_of(run_id)
        run_dir = _run_dir(root, run_id)
        if hub is None and not (run_dir / "trace.jsonl").is_file():
            raise HTTPException(status_code=404, detail=f"找不到 run: {run_id}")

        async def events() -> AsyncIterator[str]:
            if hub is not None:
                queue, replay, closed = hub.subscribe()
                try:
                    for row in replay:
                        yield _sse_line(row)
                    while not closed:
                        try:
                            item = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE)
                        except TimeoutError:
                            yield ": keepalive\n\n"
                            continue
                        if item is HUB_CLOSED:
                            break
                        yield _sse_line(item)
                finally:
                    hub.unsubscribe(queue)
            else:
                # 历史 run(CLI 产物 / 进程重启):从 trace.jsonl 回放(§4.2 冷数据)
                try:
                    rows = [r for r in read_trace(run_dir) if r.get("type") == "signal"]
                except (OSError, json.JSONDecodeError):
                    rows = []  # trace 归档中的半写窗口:直接发终止事件,客户端重连
                for row in rows:
                    yield _sse_line(row)
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app

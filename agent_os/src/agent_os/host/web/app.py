"""FastAPI app(RUNNERS.md §4.3 API 契约;R3):Web UI Runner 的路由层。

``create_app(config_path, artifacts_root=..., skillsets_dir=...)`` 返回 app;路由层只调用
host/shared 的读取层与 :class:`RunManager`,不 import 内核私有实现(§4.5)。

校验错归类(与 CLI 退出码 2 同类,§3.3):技能不存在/输入不合 schema 等
"run 未开始"的失败统一返回 ``200 + {"status": "failed", "error": ...}``;
``skill_set`` 未知(D6)属请求本身非法,归 400。

S2 增量(SUPERVISOR.md v2 §2.3/§5):supervisor 收件箱两个端点——
``GET /api/supervisor/pending`` 列挂起中的裁决请求,
``POST /api/supervisor/{question_id}/answer`` 作答结算(对应 run 恢复);
Web 收件箱即默认宿主通道,装配即得。

P3 增量(Agent OS Debugger):``/api/debug/sessions`` 一族——调试会话
(开会话即起 run)、断点增删、恢复命令、modify/inject 干预、live 帧检视
与 SSE(state/bp_hit/paused/resumed/run_end);每 run 至多一个活跃会话。
P5 增量(时间旅行):开会话增 ``{replay_run_id, until_step?}`` replay 形态
(与 ``{skill, input}`` 互斥,响应带 ``mode: "replay"``)。
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
    DebugConflictError,
    ResumeConflictError,
    RunManager,
    RunValidationError,
    _jsonable,
)
from agent_os.kernel.errors import AgentOSError, SkillLoadError
from agent_os.skills.manifest import validate_manifest

_STATIC_DIR = Path(__file__).resolve().parent / "static"

#: SSE 空闲 keepalive 间隔(秒):防代理/浏览器断连,dev 工具取保守值
_SSE_KEEPALIVE = 15.0

#: 调试会话 SSE 的轮询间隔(秒):wait_paused 的 asyncio.Event 绑在 run worker
#: 循环上,REST/SSE 循环不能 await,轮询会话内存态是最简可靠方案(P3,简单优先)
_DEBUG_SSE_POLL = 0.1

#: kind 过滤(§4.3 ``kind=llm|tool|frame|sidecar|all``)→ 信号名子串集合
_KIND_HINTS = {
    "llm": ("llm",),
    "tool": ("tool",),
    "frame": ("frame",),
    "sidecar": ("budget", "veto", "sidecar"),
    "run": ("run.",),
}


class RunOverrides(BaseModel):
    """``POST /api/runs`` 的 ``overrides``(WEB-UI.md §4.3 高级区):合并进本次 run 的 RunConfig。

    ``inline``(SKILL-INLINING.md §9 消融开关):``"on" | "off"``,其余值 422。
    ``checkpoint_interval``(Debugger P5 周期 checkpoint):每 N 步覆盖写"最近现场",0=关。
    """

    model: str | None = None
    max_cost: float | None = None
    max_steps: int | None = None
    inline: Literal["on", "off"] | None = None
    checkpoint_interval: int | None = None


class RunBody(BaseModel):
    """``POST /api/runs`` 请求体(§4.3 ``{skill, input, overrides?, wait?}``;D6 增 ``skill_set``)。"""

    skill: str
    input: dict[str, Any]
    wait: bool = False
    overrides: RunOverrides | None = None
    skill_set: str | None = None


class ReloadBody(BaseModel):
    """``POST /api/skills/reload`` 请求体(D6):``skill_set`` 限定重载哪个 set,缺省全部。"""

    skill_set: str | None = None


class SupervisorAnswerBody(BaseModel):
    """``POST /api/supervisor/{question_id}/answer`` 请求体(SUPERVISOR.md §2.4;S2)。"""

    answer: str


class DebugBreakpointBody(BaseModel):
    """``POST /api/debug/sessions/{sid}/breakpoints`` 请求体:kind + 名字 glob。"""

    kind: str
    match: str = "*"


class DebugSessionBody(BaseModel):
    """``POST /api/debug/sessions`` 请求体(P3 live / P5 replay 两形态,互斥)。

    live 形态 ``{skill, input, breakpoints?}``;replay 形态(P5 时间旅行)
    ``{replay_run_id, until_step?, breakpoints?}``——回放该 run 并挂调试会话,
    ``until_step`` 注册一次性步数断点(直达第 N 条 ``pre:step`` 才暂停)。
    ``breakpoints`` 在 run 启动前注册(调试启动即断是确定性的:起 run 后再加
    断点会竞态错过早期信号)。两形态字段混给/都不给 → 400(路由层校验)。
    """

    skill: str | None = None
    input: dict[str, Any] | None = None
    breakpoints: list[DebugBreakpointBody] | None = None
    replay_run_id: str | None = None
    until_step: int | None = None


class DebugCommandBody(BaseModel):
    """``POST /api/debug/sessions/{sid}/command`` 请求体:恢复命令(仅 paused 可发)。"""

    cmd: Literal["continue", "step_into", "step_over", "step_out", "stop"]


class DebugModifyBody(BaseModel):
    """``POST /api/debug/sessions/{sid}/modify`` 请求体:合并进本次工具调用的 args。"""

    patch: dict[str, Any]


class DebugInjectBody(BaseModel):
    """``POST /api/debug/sessions/{sid}/inject`` 请求体:``frame_id`` 缺省=暂停帧。"""

    text: str
    frame_id: str | None = None


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
                # D6:历史产物无 skill_set 字段时归 "default"(全局 config 跑的 run)
                "skill_set": result_doc.get("skill_set") or "default",
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
        "skill_set": state.get("skill_set") or "default",
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
                # D6:历史产物无 skill_set 字段时归 "default"
                "skill_set": meta.get("skill_set") or "default",
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
        # D6:在途 run 从内存态取 tag;已有产物条目以产物为准(内存态兜底 "default")
        item["skill_set"] = state.get("skill_set") or item.get("skill_set") or "default"
        runs[run_id] = item
    return sorted(runs.values(), key=lambda r: r.get("started_at") or "", reverse=True)


def _filter_kind(rows: list[dict[str, Any]], kind: str | None) -> list[dict[str, Any]]:
    if not kind or kind == "all":
        return rows
    hints = _KIND_HINTS.get(kind, (kind,))
    return [r for r in rows if any(h in (r.get("name") or "") for h in hints)]


def _skill_summary(manifest: Any) -> dict[str, Any]:
    """manifest 摘要(WEB-UI.md §6.2):name/version/kind/description/permissions/inline。

    ``inline``(SKILL-INLINING.md §3.1):merge 技能标记,Skills 浏览器打标数据源。
    """
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
        "inline": bool(getattr(manifest, "inline", False)),
    }


def _skill_doc(manifest: Any) -> dict[str, Any]:
    """全量 manifest 文档(``GET /api/skills/{name}``;D3 Launch Modal 取 inputs schema)。

    D4 增补(WEB-UI.md §4.6):``lint`` = description 自洽性 lint 警告列表
    (与加载期同一套 :func:`validate_manifest`,警告不阻断;Skills 浏览器
    详情顶部横幅数据源)。
    """
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
            "lint": validate_manifest(manifest),
        }
    )
    return doc


def _tool_doc(spec: Any) -> dict[str, Any]:
    """ToolSpec 摘要(WEB-UI.md §6.2,``GET /api/tools``;Tools 浏览器数据源)。

    ``examples`` 为空则省略(§6.2 契约:缺失字段省略即可);布尔执行属性
    (idempotent/cacheable/concurrency_safe/untrusted_source)恒带。
    """
    doc: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "permission": getattr(spec.permission, "name", str(spec.permission)),
        "parameters": spec.parameters or {},
        "timeout": spec.timeout,
        "idempotent": bool(spec.idempotent),
        "cacheable": bool(spec.cacheable),
        "concurrency_safe": bool(spec.concurrency_safe),
        "untrusted_source": bool(spec.untrusted_source),
    }
    if getattr(spec, "examples", None):
        doc["examples"] = _jsonable(spec.examples)
    return doc


def _sse_line(row: dict[str, Any]) -> str:
    return f"data: {json.dumps(row, ensure_ascii=False, default=repr)}\n\n"


def _breakpoint_doc(bp: Any) -> dict[str, Any]:
    """Breakpoint → JSON(kernel/debug.py;含命中计数 hits)。"""
    return {
        "id": bp.id,
        "kind": bp.kind,
        "match": bp.match,
        "enabled": bp.enabled,
        "hits": bp.hits,
    }


def _debug_session_doc(session: Any) -> dict[str, Any]:
    """调试会话快照(``GET /api/debug/sessions/{sid}`` 与 SSE ``state`` 事件同形)。"""
    return {
        "session_id": session.id,
        "run_id": session.run_id,
        "state": session.state,
        "pause_point": _jsonable(session.pause_point),
        "breakpoints": [_breakpoint_doc(bp) for bp in session.breakpoints],
        "frame_stack": _jsonable(session.frame_stack),
    }


def create_app(
    config_path: str | Path,
    artifacts_root: Path = Path(".agent-os"),
    skillsets_dir: str | Path | None = None,
    token: str | None = None,
) -> FastAPI:
    """装配 Web UI Runner(§4.2):RunManager + REST + SSE + 静态 SPA。

    ``skillsets_dir``(D6):一站多 skill set 根目录(``<root>/<set>/skills.yaml``)。

    ``token``(RUNNERS.md §4.5):非 None 时全站要求
    ``Authorization: Bearer <token>``(或 ``?token=`` 供 EventSource 用——SSE
    的浏览器 API 不支持自定义头)。缺省 None = 无认证,**只可用于 loopback**;
    ``serve.py`` 在绑定非 loopback 且未给 token 时拒绝启动。
    """
    manager = RunManager(config_path, Path(artifacts_root), skillsets_dir=skillsets_dir)
    root = Path(artifacts_root)
    app = FastAPI(title="Agent OS Web UI")

    if token:
        @app.middleware("http")
        async def _require_token(request: Request, call_next):  # type: ignore[no-untyped-def]
            """Bearer 令牌门(§4.5)。常量时间比对,避免按字符早退泄漏前缀。"""
            supplied = ""
            header = request.headers.get("authorization", "")
            if header.startswith("Bearer "):
                supplied = header[len("Bearer "):]
            elif "token" in request.query_params:
                # EventSource 不能带自定义头,SSE 只能走 query 串
                supplied = request.query_params["token"]
            if not secrets.compare_digest(supplied, token):
                return JSONResponse(
                    {"detail": "需要 Authorization: Bearer <token>(RUNNERS.md §4.5)"},
                    status_code=401,
                )
            return await call_next(request)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.post("/api/runs")
    async def post_run(body: RunBody) -> dict[str, Any]:
        # D6:skill_set 未知属请求非法(400),与"run 未开始"的 200+failed 归类不同
        if body.skill_set is not None and body.skill_set not in manager.skillsets():
            raise HTTPException(status_code=400, detail=f"未知 skill set: {body.skill_set!r}")
        overrides = body.overrides.model_dump(exclude_none=True) if body.overrides else None
        try:
            run_id = await manager.start_run(
                body.skill,
                body.input,
                wait=body.wait,
                overrides=overrides,
                skill_set=body.skill_set,
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
            # 帧工作内存(checkpoint 已带,read 层透传):检视器内联能力小节
            # 取 working._inline_caps(SKILL-INLINING.md §4.2 帧内冻结快照)
            "working": (frame.get("context") or {}).get("working", {}),
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

    @app.get("/api/supervisor/pending")
    def list_supervisor_pending() -> list[dict[str, Any]]:
        """supervisor 收件箱(SUPERVISOR.md §5;S2):挂起中的裁决请求列表。

        Web 收件箱即默认宿主通道(§2.3):run 无需注入 handler,装配即得;
        每行含 question_id/run_id/frame_id/question/context/options/urgency/asked_at。
        """
        return manager.supervisor_pending()

    @app.post("/api/supervisor/{question_id}/answer")
    def answer_supervisor(question_id: str, body: SupervisorAnswerBody) -> dict[str, Any]:
        """作答(S2):结算挂起问题,提问帧以答案为 tool result 恢复(§2.4)。

        答案不匹配 options → 400 且问题保持挂起(§3:格式错误返回调用方重答,
        不重问子帧);找不到 question_id → 404。
        """
        try:
            manager.supervisor_answer(question_id, body.answer)
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"找不到 supervisor 问题: {question_id}"
            ) from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return {"ok": True, "question_id": question_id}

    @app.get("/api/skillsets")
    def list_skillsets() -> list[dict[str, Any]]:
        """一站多 skill set(D6):``[{name, skills(manifest 数), path}]``。

        装配失败的 set(坏 skills.yaml/装配错)不拖垮列表:``skills`` 归 0 并带
        ``error`` 字段,前端下拉仍可用其余 set。
        """
        out: list[dict[str, Any]] = []
        for name, path in manager.skillsets().items():
            item: dict[str, Any] = {"name": name, "path": str(path)}
            try:
                item["skills"] = len(manager.skills_manifests(name))
            except Exception as e:  # noqa: BLE001 — 坏 set 隔离:错误透传给 dev 用户
                item["skills"] = 0
                item["error"] = f"{type(e).__name__}: {e}"
            out.append(item)
        return out

    @app.get("/api/skills")
    def list_skills(skill_set: str | None = None) -> list[dict[str, Any]]:
        """技能清单(WEB-UI.md §6.2):共享 registry 的 manifest 摘要列表(Launch Modal 下拉)。

        D6:``?skill_set=<name>`` 按 set 过滤(未知 set 归 400);不带参数维持全局行为。
        """
        try:
            manifests = manager.skills_manifests(skill_set)
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return [_skill_summary(m) for m in manifests]

    @app.get("/api/skills/{name}")
    def get_skill(name: str, skill_set: str | None = None) -> dict[str, Any]:
        """单个技能的全量 manifest(§6.2;Launch Modal 的 inputs schema 数据源)。

        D6:``?skill_set=<name>`` 在多 set 下消歧(同名技能按 set 取装配)。
        """
        try:
            manifest = manager.skill_manifest(name, skill_set)
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"找不到技能: {name}")
        return _skill_doc(manifest)

    @app.post("/api/skills/reload")
    def reload_skills(body: ReloadBody | None = None) -> dict[str, Any]:
        """热重载 skills 文件(§4.3;mtime 检查,只影响后续新建的 run,§6.1)。

        D6:body 可带 ``skill_set`` 限定重载哪个 set;缺省全部。
        """
        try:
            return {"reloaded": manager.reload_skills(body.skill_set if body else None)}
        except (RunValidationError, SkillLoadError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/tools")
    def list_tools() -> list[dict[str, Any]]:
        """工具清单(WEB-UI.md §6.2):共享 tools registry 的全量 ToolSpec 摘要(Tools 浏览器)。"""
        return [_tool_doc(s) for s in manager.tools_specs()]

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

    # ------------------------------------------------------------------
    # Agent OS Debugger(P3):调试会话 API(错误语义跟随既有端点:
    # 找不到 404 / 请求非法 400 / 状态冲突 409 / run 未开始 200+failed)
    # ------------------------------------------------------------------

    @app.post("/api/debug/sessions")
    async def post_debug_session(body: DebugSessionBody) -> dict[str, Any]:
        """开调试会话:注入 DebugController 装配内核起 run,返回 ``{session_id, run_id}``。

        P5 replay 形态(``{replay_run_id, until_step?}``,与 ``{skill, input}`` 互斥,
        混给/都不给 400):回放指定 run 并挂调试会话,响应带 ``mode: "replay"``
        (回放边界:LLM Mock 回放,工具真实重跑)。
        """
        if body.replay_run_id is not None:
            if body.skill is not None or body.input is not None:
                raise HTTPException(
                    status_code=400,
                    detail="replay_run_id 与 skill/input 互斥(replay 从产物 meta 读取)",
                )
            try:
                session_id, run_id = await manager.start_debug_replay_session(
                    body.replay_run_id,
                    until_step=body.until_step,
                    breakpoints=[(bp.kind, bp.match) for bp in body.breakpoints or ()],
                )
            except DebugConflictError as e:
                raise HTTPException(status_code=409, detail=str(e)) from e
            except FileNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            except RunValidationError as e:
                return {"status": "failed", "error": str(e)}
            return {"session_id": session_id, "run_id": run_id, "mode": "replay"}
        if body.skill is None or body.input is None:
            raise HTTPException(
                status_code=400,
                detail="需要 {skill, input}(live)或 {replay_run_id}(replay)之一",
            )
        if body.until_step is not None:
            raise HTTPException(status_code=400, detail="until_step 仅 replay 形态有效")
        try:
            session_id, run_id = await manager.start_debug_session(
                body.skill,
                body.input,
                breakpoints=[(bp.kind, bp.match) for bp in body.breakpoints or ()],
            )
        except DebugConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e  # 未知断点 kind
        except RunValidationError as e:
            # 与 POST /api/runs 同归类(§3.3):技能不存在/输入不合 schema 等
            return {"status": "failed", "error": str(e)}
        return {"session_id": session_id, "run_id": run_id}

    @app.get("/api/debug/sessions/{sid}")
    def get_debug_session(sid: str) -> dict[str, Any]:
        """会话快照:state/pause_point/breakpoints(含 hits)/frame_stack/run_id。"""
        try:
            session = manager.debug_session(sid)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        return _debug_session_doc(session)

    @app.delete("/api/debug/sessions/{sid}")
    async def delete_debug_session(sid: str) -> dict[str, Any]:
        """显式结束会话:detach 放行(run 继续跑完),清理注册表。"""
        try:
            await manager.debug_close(sid)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        return {"ok": True, "session_id": sid}

    @app.post("/api/debug/sessions/{sid}/breakpoints")
    def post_debug_breakpoint(sid: str, body: DebugBreakpointBody) -> dict[str, Any]:
        try:
            bp = manager.debug_add_breakpoint(sid, body.kind, body.match)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except AgentOSError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return _breakpoint_doc(bp)

    @app.delete("/api/debug/sessions/{sid}/breakpoints/{bp_id}")
    def delete_debug_breakpoint(sid: str, bp_id: str) -> dict[str, Any]:
        try:
            manager.debug_remove_breakpoint(sid, bp_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        return {"ok": True}

    @app.post("/api/debug/sessions/{sid}/command")
    async def post_debug_command(sid: str, body: DebugCommandBody) -> dict[str, Any]:
        """恢复命令(仅 paused):continue / step_into / step_over / step_out / stop。"""
        try:
            await manager.debug_command(sid, body.cmd)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except AgentOSError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"ok": True, "cmd": body.cmd}

    @app.post("/api/debug/sessions/{sid}/modify")
    async def post_debug_modify(sid: str, body: DebugModifyBody) -> dict[str, Any]:
        """干预:改本次工具调用参数(仅暂停在 pre:tool.call;改完即放行)。"""
        try:
            await manager.debug_modify(sid, body.patch)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        except AgentOSError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"ok": True}

    @app.post("/api/debug/sessions/{sid}/inject")
    async def post_debug_inject(sid: str, body: DebugInjectBody) -> dict[str, Any]:
        """干预:向指定帧注入 USER/INJECTED 消息(``frame_id`` 缺省=暂停帧;注入后放行)。"""
        try:
            frame_id = await manager.debug_inject(sid, body.frame_id, body.text)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        except AgentOSError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"ok": True, "frame_id": frame_id}

    @app.get("/api/debug/sessions/{sid}/frames/{fid}")
    def get_debug_frame(sid: str, fid: str) -> dict[str, Any]:
        """帧检视(live 内存态;调试暂停时 checkpoint 尚未落盘):messages/working/usage。"""
        try:
            doc = manager.debug_frame(sid, fid)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        if doc is None:
            raise HTTPException(status_code=404, detail=f"找不到帧: {fid}")
        return doc

    @app.get("/api/debug/sessions/{sid}/stream")
    async def stream_debug(sid: str) -> StreamingResponse:
        """调试会话 SSE:``state``(连接快照)→ ``bp_hit``/``paused``/``resumed`` → ``run_end``。

        轮询会话内存态(_DEBUG_SSE_POLL):wait_paused 的 asyncio.Event 绑在
        run worker 循环上,SSE 循环不能 await,轮询是最简可靠方案(P3,简单优先)。
        """
        try:
            session = manager.debug_session(sid)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None

        def _event(name: str, data: dict[str, Any]) -> str:
            return f"event: {name}\ndata: {json.dumps(_jsonable(data), ensure_ascii=False)}\n\n"

        async def events() -> AsyncIterator[str]:
            yield _event("state", _debug_session_doc(session))
            # prev_state 不取当前态:已暂停的会话在连接后立即补发 paused(迟到客户端)
            prev_state = ""
            hits = {bp.id: bp.hits for bp in session.breakpoints}
            idle = 0.0
            while True:
                emitted = False
                for bp in session.breakpoints:
                    if bp.hits > hits.get(bp.id, 0):
                        hits[bp.id] = bp.hits
                        yield _event(
                            "bp_hit",
                            {
                                "session_id": sid,
                                "breakpoint_id": bp.id,
                                "kind": bp.kind,
                                "match": bp.match,
                                "hits": bp.hits,
                            },
                        )
                        emitted = True
                state = session.state
                if state == "paused" and prev_state != "paused":
                    yield _event(
                        "paused",
                        {"session_id": sid, "pause_point": _jsonable(session.pause_point)},
                    )
                    emitted = True
                elif state != "paused" and prev_state == "paused":
                    yield _event("resumed", {"session_id": sid})
                    emitted = True
                prev_state = state
                if state == "detached":
                    # run 收尾(run.finished emit 里 detach 早于 result 落盘):短暂等终态
                    status = None
                    if session.run_id is not None:
                        for _ in range(50):
                            status = (manager.state_of(session.run_id) or {}).get("status")
                            if status in ("done", "failed", "aborted"):
                                break
                            await asyncio.sleep(_DEBUG_SSE_POLL)
                    yield _event(
                        "run_end",
                        {"session_id": sid, "run_id": session.run_id, "status": status},
                    )
                    return
                if emitted:
                    idle = 0.0
                    continue
                await asyncio.sleep(_DEBUG_SSE_POLL)
                idle += _DEBUG_SSE_POLL
                if idle >= _SSE_KEEPALIVE:
                    idle = 0.0
                    yield ": keepalive\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app

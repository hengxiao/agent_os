"""FastAPI app(docs/RUNNERS.md §4.3 API 契约;R3):Web UI Runner 的路由层。

``create_app(config_path, artifacts_root=..., skillsets_dir=...)`` 返回 app;路由层只调用
host/shared 的读取层与 :class:`RunManager`,不 import 内核私有实现(§4.5)。

校验错归类(与 CLI 退出码 2 同类,§3.3):技能不存在/输入不合 schema 等
"run 未开始"的失败统一返回 ``200 + {"status": "failed", "error": ...}``;
``skill_set`` 未知(D6)属请求本身非法,归 400。

S2 增量(docs/SUPERVISOR.md v2 §2.3/§5):supervisor 收件箱两个端点——
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
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import jsonschema
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_os.api.v1 import (
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    SkillRef,
    ToolCall,
    explain_skill_tier,
)
from agent_os.host.shared.artifacts import (
    frame_tree,
    read_checkpoint,
    read_result,
    read_trace,
)
from agent_os.host.shared.replay import replace_providers
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
from agent_os.runtime.config import load_config
from agent_os.skills.closure import compute_closure
from agent_os.skills.draft_store import (
    DraftStore,
    OverlaySkillRegistry,
    package_template_members,
)
from agent_os.skills.gate import GateError, promote_draft
from agent_os.skills.gate import validate_draft as validate_gate_draft
from agent_os.skills.iterate import collect_package_docs, package_diff
from agent_os.skills.lab_assistant import (
    ASSISTANT_NAME,
    ITERATOR_NAME,
    assistant_skill,
    iterator_skill,
)
from agent_os.skills.manifest import validate_manifest
from agent_os.skills.package import build_plan, promote_package
from agent_os.tools.lab_tools import register_iterate_tools, register_lab_tools

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


def _lab_store(config_path: str | Path, artifacts_root: Path) -> DraftStore:
    """装配 DraftStore(docs/SKILL-DEV.md §1.2;L1)。

    drafts_root 取宿主配置 ``[lab].drafts_root``(照 ``[web].user`` 先例);
    缺省 ``<artifacts_root>/drafts``;配置读取失败退化为缺省,不拖垮装配。
    """
    drafts_root = None
    try:
        drafts_root = (load_config(config_path).get("lab") or {}).get("drafts_root")
    except Exception:  # noqa: BLE001 — Lab 存储配置失败不阻断 Web 启动
        drafts_root = None
    return DraftStore(drafts_root or (Path(artifacts_root) / "drafts"))


def _lab_replace_providers(kernel: Any, script: list[dict[str, Any]]) -> None:
    """用例的 ``mock_script``(dict 形态)→ MockProvider 回放(docs/RUNNERS.md §3.4 同机制;

    docs/SKILL-DEV.md §1.5:确定性重放优先,无 mock 才用装配的真实 provider)。
    """
    responses = []
    for item in script:
        msg = item.get("message") or {}
        usage = item.get("usage") or {}
        responses.append(
            ChatResponse(
                message=Message(
                    role=Role(msg.get("role", "assistant")),
                    content=msg.get("content", ""),
                    tool_calls=[
                        ToolCall(
                            id=str(tc.get("id", "")),
                            name=str(tc.get("name", "")),
                            args=dict(tc.get("args") or {}),
                        )
                        for tc in msg.get("tool_calls") or []
                    ],
                ),
                finish_reason=item.get("finish_reason", "stop"),
                usage=ChatUsage(
                    prompt=usage.get("prompt", 0),
                    completion=usage.get("completion", 0),
                    cost=usage.get("cost", 0.0),
                ),
            )
        )
    replace_providers(kernel, responses)


class RunOverrides(BaseModel):
    """``POST /api/runs`` 的 ``overrides``(docs/WEB-UI.md §4.3 高级区):合并进本次 run 的 RunConfig。

    ``inline``(docs/SKILL-INLINING.md §9 消融开关):``"on" | "off"``,其余值 422。
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
    """``POST /api/supervisor/{question_id}/answer`` 请求体(docs/SUPERVISOR.md §2.4;S2)。"""

    answer: str


class LabCreateBody(BaseModel):
    """``POST /api/lab/drafts``(docs/SKILL-DEV.md §1.5):空模板/模板库/从生产 skill 复制。"""

    name: str
    from_skill: str | None = None  # 生产 skill 名(前端把 `from` 关键字映射为本字段)
    template: str | None = None  # 模板库 key(§4 L5:prompt_query|file_process|danger_op)


class LabSaveBody(BaseModel):
    """``PUT /api/lab/drafts/{name}``(§1.5):整体替换;tests=None 不动 tests 目录。"""

    manifest: dict[str, Any]
    prompt: str = ""
    handler: str | None = None
    tests: dict[str, Any] | None = None


class LabPromoteBody(BaseModel):
    """``POST /api/lab/drafts/{name}/promote``(docs/SKILL-DEV.md §1.5;L2)。

    ``report_id`` 必填(闸门报告);``version`` 缺省自动(bump patch / 0.1.0);
    报告或复跑有 warn 时必须 ``warnings_ack``(§1.4:黄关强制人工确认)。
    """

    report_id: str
    version: str | None = None
    warnings_ack: bool = False


class LabTestRunBody(BaseModel):
    """``POST /api/lab/drafts/{name}/test-run``(docs/SKILL-DEV.md §1.5;L3)。

    ``input`` 直给,或 ``case`` 指名 tests/ 下的用例文件({input, expect?, mock_script?});
    二者都给时 case 优先。
    """

    input: dict[str, Any] | None = None
    case: str | None = None


class LabAssistantBody(BaseModel):
    """``POST /api/lab/assistant``(docs/SKILL-DEV.md §2.2;L4):中栏 chat 发消息。"""

    request: str
    draft: str


class LabIterateBody(BaseModel):
    """``POST /api/lab/drafts/{name}/iterate``(docs/LAB-ITERATION.md §4;Flow C 样板)。

    ``comments``:本轮边注 [{id?, anchor:{member,kind,path,span?}, text, at?}];
    ``note``:补充说明(可选,随边注一起进生成上下文)。
    """

    comments: list[dict[str, Any]] = []
    note: str = ""


class LabRewindBody(BaseModel):
    """``POST /api/lab/drafts/{name}/rewind``:回到指定版本(working 恢复,历史不动)。"""

    version: str


class LabPackagePromoteBody(BaseModel):
    """``POST /api/lab/packages/promote``(docs/SKILL-PACKAGES-V2.md §6.3;P2)。"""

    plan_id: str
    warnings_ack: bool = False


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
    """``POST /api/debug/sessions/{sid}/command`` 请求体。

    恢复命令(仅 paused 可发):continue/step_into/step_over/step_out/stop;
    ``pause``(仅 running 可发):随时暂停,run 在下一个可仲裁信号挂起
    (GDB SIGINT 语义,§5.2)。
    """

    cmd: Literal["continue", "step_into", "step_over", "step_out", "stop", "pause"]


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
    """manifest 摘要(docs/WEB-UI.md §6.2):name/version/kind/description/permissions/inline。

    ``inline``(docs/SKILL-INLINING.md §3.1):merge 技能标记,Skills 浏览器打标数据源。
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

    D4 增补(docs/WEB-UI.md §4.6):``lint`` = description 自洽性 lint 警告列表
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
    """ToolSpec 摘要(docs/WEB-UI.md §6.2,``GET /api/tools``;Tools 浏览器数据源)。

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
        # rerun 可用性(live 会话有创建参数 origin;replay/CLI 为 None)
        "rerunnable": session.origin is not None,
    }


def create_app(
    config_path: str | Path,
    artifacts_root: Path = Path(".agent-os"),
    skillsets_dir: str | Path | None = None,
    token: str | None = None,
) -> FastAPI:
    """装配 Web UI Runner(§4.2):RunManager + REST + SSE + 静态 SPA。

    ``skillsets_dir``(D6):一站多 skill set 根目录(``<root>/<set>/skills.yaml``)。

    ``token``(docs/RUNNERS.md §4.5):非 None 时全站要求
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
                    {"detail": "需要 Authorization: Bearer <token>(docs/RUNNERS.md §4.5)"},
                    status_code=401,
                )
            return await call_next(request)

    # html=True:目录索引(static/proto/ 等原型页直接以目录路径访问,仅静态语义)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

    @app.middleware("http")
    async def _static_no_cache(request: Request, call_next):  # type: ignore[no-untyped-def]
        """静态资源禁启发式缓存:无版本号的 js/css 在代码更新后必须立即生效,
        否则浏览器拿旧 JS 配新后端(调试台「启动中…」卡死即此因)。ETag 仍保
        304 协商缓存,不增加重复传输。"""
        resp = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path == "/":
            resp.headers["Cache-Control"] = "no-cache"
        return resp

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
            # 取 working._inline_caps(docs/SKILL-INLINING.md §4.2 帧内冻结快照)
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
        """supervisor 收件箱(docs/SUPERVISOR.md §5;S2):挂起中的裁决请求列表。

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
        """技能清单(docs/WEB-UI.md §6.2):共享 registry 的 manifest 摘要列表(Launch Modal 下拉)。

        D6:``?skill_set=<name>`` 按 set 过滤(未知 set 归 400);不带参数维持全局行为。
        """
        try:
            manifests = manager.skills_manifests(skill_set)
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return [_skill_summary(m) for m in manifests]

    @app.get("/api/skills/packages")
    def list_packages() -> list[dict[str, Any]]:
        """包识别(docs/SKILL-PACKAGES.md §3.6;P4):闭包完整的命名空间簇清单。

        判据:簇内 ≥2 成员,且存在根使运行闭包(runtime 模式)全部可解析
        (无 missing;簇内成员全覆盖)。Skills 页 ns-tree 的"包"徽标数据源。
        """
        manifests = manager.skills_manifests()
        by_ns: dict[str, list[str]] = {}
        for m in manifests:
            by_ns.setdefault(m.name.split(".")[0], []).append(m.name)

        class _NoDrafts:
            def load_skill(self, name: str) -> Any:
                raise FileNotFoundError(name)

        packages = []
        registry = manager.shared_skills_registry()
        for ns, names in sorted(by_ns.items()):
            if len(names) < 2:
                continue
            # 根候选 = 不被簇内其他成员引用的成员(包入口);逐个试,取第一个全解析的
            referenced = {d for m in manifests if m.name in names for d in m.permissions.skills}
            candidates = [n for n in names if n not in referenced] or names
            for root in candidates:
                closure = compute_closure(root, _NoDrafts(), registry, manager.shared_tools_registry(), mode="runtime")
                members = closure["members"]
                if any(m["status"] == "missing" for m in members) or closure["errors"]:
                    continue
                if {m["name"] for m in members} >= set(names):
                    packages.append(
                        {
                            "ns": ns,
                            "root": root,
                            "tier": closure["root_tier"],
                            "members": [m["name"] for m in members],
                        }
                    )
                    break
        return packages

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
        """工具清单(docs/WEB-UI.md §6.2):共享 tools registry 的全量 ToolSpec 摘要(Tools 浏览器)。"""
        return [_tool_doc(s) for s in manager.tools_specs()]

    # ------------------------------------------------------------------
    # Skill Lab(docs/SKILL-DEV.md §1.5;L1):drafts CRUD + 实时推导档
    # ------------------------------------------------------------------

    lab_store = _lab_store(config_path, root)

    def _lab_overlay() -> OverlaySkillRegistry:
        """生产 registry + 草稿层(草稿优先;L1 仅服务推导档,test-run 装配属 L3)。"""
        return OverlaySkillRegistry(manager.shared_skills_registry(), lab_store)

    def _lab_tier_of(manifest: Any) -> str:
        return explain_skill_tier(manifest, manager.shared_tools_registry(), _lab_overlay())["tier"]

    @app.get("/api/lab/drafts")
    def lab_list_drafts() -> list[dict[str, Any]]:
        """草稿列表(§1.5):name/推导档/最近修改时间;暂不合规的草稿 tier 置 None。"""
        rows = lab_store.list()
        for row in rows:
            try:
                row["tier"] = _lab_tier_of(lab_store.load_skill(row["name"]).manifest)
            except (SkillLoadError, RunValidationError):
                row["tier"] = None  # 半成品也要能进列表(§1.2 允许临时不合规)
        return rows

    @app.post("/api/lab/drafts", status_code=201)
    def lab_create_draft(body: LabCreateBody) -> dict[str, Any]:
        """新建草稿(§1.5):空模板 / 单技能模板 / 功能包模板(``pkg.*``,§3.2 整套生成)/
        ``from_skill`` 从生产 skill 复制。"""
        try:
            if body.template and body.template.startswith("pkg."):
                # 功能包模板(docs/SKILL-PACKAGES.md §3.2;P4):根 + 成员一次生成,
                # 白名单已对齐;任一成员冲突 → 已建的清掉,保持原子观感
                members = package_template_members(body.template, body.name)
                created: list[str] = []
                try:
                    for member_name, (manifest, prompt) in members.items():
                        lab_store.create(member_name)
                        lab_store.save(member_name, manifest=manifest, prompt=prompt)
                        created.append(member_name)
                except Exception:
                    for member_name in created:
                        lab_store.delete(member_name)
                    raise
                return {"name": body.name, "package": True, "members": created}
            source = None
            if body.from_skill:
                try:
                    source = manager.shared_skills_registry().get(SkillRef(name=body.from_skill))
                except (SkillLoadError, RunValidationError) as e:
                    raise HTTPException(status_code=404, detail=f"找不到生产技能: {body.from_skill}") from e
            return lab_store.create(body.name, source=source, template=body.template)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileExistsError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/api/lab/drafts/{name}")
    def lab_read_draft(name: str) -> dict[str, Any]:
        """读草稿(§1.5):manifest + prompt + handler + tests;解析失败带 parse_error 不 500。"""
        try:
            return lab_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.put("/api/lab/drafts/{name}")
    def lab_save_draft(name: str, body: LabSaveBody) -> dict[str, Any]:
        """保存草稿(§1.5):整体替换,上一版自动 .bak;不校验内容(闸门守在出口)。"""
        try:
            return lab_store.save(
                name,
                manifest=body.manifest,
                prompt=body.prompt,
                handler=body.handler,
                tests=body.tests,
            )
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.delete("/api/lab/drafts/{name}")
    def lab_delete_draft(name: str) -> dict[str, Any]:
        """删草稿(§1.5;L2 语义,UI 已确认)。"""
        try:
            lab_store.delete(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"ok": True, "name": name}

    @app.get("/api/lab/drafts/{name}/tier")
    def lab_draft_tier(name: str, tools: str | None = None, skills: str | None = None) -> dict[str, Any]:
        """实时推导档(§1.3/§2.4):tier + 每来源明细(top = 贡献最高档的工具/技能)。

        ``?tools=a,b&skills=c,d`` 用编辑器**未保存**的白名单覆盖计算——推导档随
        表单实时刷新,不必先保存(保存永不打断创作流,§2.4)。
        """
        try:
            draft = lab_store.read(name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if draft["parse_error"] is not None or draft["manifest"] is None:
            # 半成品给不出档:明示原因而不是 500(与 read 同一容错语义)
            return {"tier": None, "sources": [], "top": [], "parse_error": draft["parse_error"]}
        try:
            manifest = lab_store.load_skill(name).manifest
        except SkillLoadError as e:
            return {"tier": None, "sources": [], "top": [], "parse_error": str(e)}
        if tools is not None:
            manifest.permissions.tools = [t for t in tools.split(",") if t]
        if skills is not None:
            manifest.permissions.skills = [s for s in skills.split(",") if s]
        try:
            detail = explain_skill_tier(manifest, manager.shared_tools_registry(), _lab_overlay())
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        detail["parse_error"] = None
        return detail

    @app.post("/api/lab/drafts/{name}/validate")
    def lab_validate_draft(name: str) -> dict[str, Any]:
        """跑提交闸门(docs/SKILL-DEV.md §1.4;L2/L3):五关报告,落盘 ``gate/<ts>.json``。

        G4 冒烟执行器(L3):与 test-run 同逻辑的真 run(overlay 装配,同步跑,
        RunConfig 即预算封顶)——outputs 必须过草稿 outputs schema。
        """
        try:
            draft = lab_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        try:
            report = validate_gate_draft(
                draft,
                production=manager.shared_skills_registry(),
                tools=manager.shared_tools_registry(),
                smoke_runner=_lab_smoke_runner(name, draft),
                store=lab_store,  # P1:G2 引用完整性(草稿 ∪ 生产全量)
            )
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return lab_store.save_gate_report(name, report)

    def _lab_smoke_runner(name: str, draft: dict[str, Any]) -> Any:
        """构造 G4 冒烟执行器:overlay 装配(草稿优先)→ 真 run → outputs 校验。

        结果形态 ``{"ok": bool, "error": str}``(gate.py 消费);mock_script 用例
        走 replay MockProvider(确定性重放,§1.5;无 mock 用装配的真实 provider)。
        """
        outputs = (draft.get("manifest") or {}).get("outputs") or {}

        def _run(case: dict[str, Any]) -> dict[str, Any]:
            try:
                kernel = manager.assemble_lab_kernel(_lab_overlay())
                script = case.get("mock_script")
                if script:
                    _lab_replace_providers(kernel, script)
                result = asyncio.run(kernel.run(name, case.get("input") or {}))
            except Exception as e:  # noqa: BLE001 — 冒烟失败归 G4 finding,不炸 validate
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return _lab_outputs_check(outputs, result)

        return _run

    def _lab_outputs_check(outputs: dict[str, Any], result: Any) -> dict[str, Any]:
        """outputs schema 校验(§1.4 G4/test-run 结果区共用);空 schema 恒过。"""
        if outputs:
            try:
                jsonschema.validate(result, outputs)
            except jsonschema.ValidationError as e:
                return {"ok": False, "error": f"outputs 校验失败: {e.message}"}
        return {"ok": True, "error": None}

    @app.post("/api/lab/drafts/{name}/test-run")
    async def lab_test_run(name: str, body: LabTestRunBody) -> dict[str, Any]:
        """试跑(§1.5;L3):input 直给或 tests/case 文件;返回 run_id(SSE/记录复用现状)。

        装配走 kernel_patcher:生产内核 + overlay(草稿优先)——跑的就是生产形态的
        run(§2.4 所见即所得),生产 run 不受影响(overlay 仅本请求作用域)。
        """
        try:
            draft = lab_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        mock_script = None
        if body.case is not None:
            raw = draft["tests"].get(body.case)
            if raw is None:
                raise HTTPException(status_code=404, detail=f"草稿无用例: {body.case}")
            try:
                case = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError as e:
                raise HTTPException(status_code=400, detail=f"用例 {body.case} 不是合法 JSON: {e}") from e
            run_input = case.get("input") or {}
            mock_script = case.get("mock_script")
        else:
            run_input = body.input or {}

        def _patch(kernel: Any) -> None:
            manager.swap_skills_overlay(kernel, _lab_overlay())
            if mock_script:
                _lab_replace_providers(kernel, mock_script)

        try:
            run_id = await manager.start_run(name, run_input, kernel_patcher=_patch)
        except (RunValidationError, SkillLoadError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"run_id": run_id}

    @app.get("/api/lab/drafts/{name}/runs/{run_id}/check")
    def lab_test_run_check(name: str, run_id: str) -> dict[str, Any]:
        """试跑结果 + outputs 校验(§1.5;L3):状态/result/校验结论,前端轮询用。"""
        try:
            draft = lab_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        state = manager.state_of(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"找不到 run: {run_id}")
        record = state.get("record") or {}
        outputs = (draft.get("manifest") or {}).get("outputs") or {}
        check = {"ok": None, "error": None}
        if state.get("status") == "done":
            check = _lab_outputs_check(outputs, record.get("result"))
        return {
            "run_id": run_id,
            "status": state.get("status"),
            "result": record.get("result"),
            "error": record.get("error"),
            "outputs_check": check,
        }

    @app.get("/api/lab/packages/{root}/closure")
    def lab_package_closure(root: str, mode: str = "edit") -> dict[str, Any]:
        """包闭包(docs/SKILL-PACKAGES.md §3.1/§4.2;P1):成员表 + 状态四态 + 环 errors。

        ``?mode=runtime``(P4):运行闭包(穿过生产节点,Skills 页只读包视图用)。
        """
        if mode not in ("edit", "runtime"):
            raise HTTPException(status_code=400, detail=f"mode 应为 edit|runtime,得到: {mode!r}")
        return _lab_closure(root, mode=mode)

    @app.get("/api/lab/drafts/{name}/closure")
    def lab_draft_closure(name: str) -> dict[str, Any]:
        """= /api/lab/packages/{name}/closure 的别名(§4.2,平滑过渡)。"""
        return _lab_closure(name)

    def _lab_closure(root: str, *, mode: str = "edit") -> dict[str, Any]:
        try:
            return compute_closure(
                root,
                lab_store,
                manager.shared_skills_registry(),
                manager.shared_tools_registry(),
                mode=mode,
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/lab/packages/{root}/plan")
    def lab_package_plan(root: str) -> dict[str, Any]:
        """提交计划(docs/SKILL-PACKAGES-V2.md §6.3;P2):成员三态 + blockers + warnings。

        每个 draft 成员过完整闸门(提交期严格档);plan 落盘 ``_plans/``,
        package_hash 绑定"审的就是要执行的"。
        """
        try:
            return build_plan(
                root,
                store=lab_store,
                production=manager.shared_skills_registry(),
                tools=manager.shared_tools_registry(),
                smoke_runner=lambda name, draft: _lab_smoke_runner(name, draft),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except RunValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/lab/packages/promote")
    def lab_package_promote(body: LabPackagePromoteBody) -> dict[str, Any]:
        """按 plan 原子提交(docs/SKILL-PACKAGES-V2.md §6.3/§6.4;P2)。

        409:package_hash 不一致(成员在计划后改动)/ blockers 未清 / warnings 未 ack。
        P4:待写成员 ≥2 且配置了 skillsets 根目录 → 落目录形态 set(§4.4)。
        """
        try:
            result = promote_package(
                store=lab_store,
                plan_id=body.plan_id,
                warnings_ack=body.warnings_ack,
                production=manager.shared_skills_registry(),
                tools=manager.shared_tools_registry(),
                principal=manager.principal().subject,
                skillsets_root=manager.skillsets_root(),
            )
            if result.get("set"):
                manager.refresh_skillsets()  # 新 set 落盘:/api/skillsets 与下拉立即可见
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except GateError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except SkillLoadError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/lab/assistant")
    async def lab_assistant(body: LabAssistantBody) -> dict[str, Any]:
        """Agent 助手(docs/SKILL-DEV.md §2.2;L4):以 skill.dev.assistant 为根技能起 run。

        kernel_patcher 做两件事:overlay 注入 assistant meta-skill(草稿 → assistant →
        生产的解析序)+ 注册 ``lab.draft.*`` 五工具。**工具面没有 promote/delete**
        (§1.1:能改不能发);run 管理/SSE 复用现状,前端轮询 /api/runs/{id} 拿回复。
        """
        try:
            lab_store.read(body.draft)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        def _patch(kernel: Any) -> None:
            overlay = OverlaySkillRegistry(
                manager.shared_skills_registry(),
                lab_store,
                extra={ASSISTANT_NAME: assistant_skill()},
            )
            manager.swap_skills_overlay(kernel, overlay)
            register_lab_tools(
                kernel.tools,
                store=lab_store,
                production=manager.shared_skills_registry(),
                tools_registry=manager.shared_tools_registry(),
                kernel_factory=lambda: manager.assemble_lab_kernel(_lab_overlay()),
                # P3 信任边界(docs/SKILL-PACKAGES-V2.md §6.7):围栏以当前包根为界——
                # create 只能建在同名空间内,write 只能写包编辑闭包内成员
                package_root=body.draft,
            )

        try:
            run_id = await manager.start_run(
                ASSISTANT_NAME,
                {"request": body.request, "draft": body.draft},
                kernel_patcher=_patch,
            )
        except (RunValidationError, SkillLoadError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"run_id": run_id}

    @app.post("/api/lab/drafts/{name}/iterate")
    def lab_iterate(name: str, body: LabIterateBody) -> dict[str, Any]:
        """边注驱动迭代(docs/LAB-ITERATION.md §4;Flow C 样板)。

        存本轮边注 → 生成技能(skill.dev.iterator,工具面 = 读 working + 写候选,
        写不到 working)产候选 → 返回 working vs candidate 的 diff。
        助手不可用(provider 故障/凭证)→ 503 明确错误(前端显示"助手暂不可用")。
        """
        try:
            lab_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        lab_store.save_comments(name, str(int(time.time() * 1000)), body.comments)
        kernel = manager.assemble_lab_kernel(
            OverlaySkillRegistry(
                manager.shared_skills_registry(),
                lab_store,
                extra={ITERATOR_NAME: iterator_skill()},
            )
        )
        register_iterate_tools(
            kernel.tools,
            store=lab_store,
            production=manager.shared_skills_registry(),
            tools_registry=manager.shared_tools_registry(),
            pkg=name,
        )
        request_text = json.dumps(
            {"note": body.note, "comments": body.comments}, ensure_ascii=False
        )
        try:
            result = asyncio.run(
                kernel.run(ITERATOR_NAME, {"request": request_text, "draft": name})
            )
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"助手暂不可用: {type(e).__name__}: {e}"
            ) from e
        return {
            "candidate": True,
            "reply": (result or {}).get("reply", ""),
            "diff": _lab_candidate_diff(name),
        }

    def _lab_edit_members(name: str) -> list[str]:
        """编辑闭包的 draft 成员(working 集;快照/diff 共用)。"""
        closure = compute_closure(
            name, lab_store, manager.shared_skills_registry(), manager.shared_tools_registry(),
            mode="edit",
        )
        return [m["name"] for m in closure["members"] if m["status"] == "draft"]

    def _lab_candidate_diff(name: str) -> dict[str, Any]:
        """working vs candidate 的结构化 diff(skills/iterate.py 纯函数)。"""
        working = collect_package_docs(lab_store, name, _lab_edit_members(name))
        candidate: dict[str, Any] = {}
        for member in lab_store.candidate_members(name):
            data = lab_store.read_candidate_member(name, member)
            candidate[member] = {
                "manifest": data["manifest"] or {},
                "prompt": data["prompt"],
                "tests": sorted((data["tests"] or {}).keys()),
            }
        return package_diff(working, candidate)

    @app.get("/api/lab/drafts/{name}/candidate/diff")
    def lab_candidate_diff_get(name: str) -> dict[str, Any]:
        """候选 diff(§4;Flow C 右栏数据源;无候选 → 404)。"""
        try:
            if not lab_store.candidate_members(name):
                raise FileNotFoundError(f"无候选: {name}")
            return _lab_candidate_diff(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/api/lab/drafts/{name}/candidate/accept")
    def lab_candidate_accept(name: str) -> dict[str, Any]:
        """接受候选(§1.2:接受是人的动作):快照新版本 → 候选覆盖 working → 清候选。"""
        try:
            members = _lab_edit_members(name)
            cand = lab_store.candidate_members(name)
            if not cand:
                raise FileNotFoundError(f"无候选: {name}")
            latest = lab_store.list_versions(name)
            _, comments = lab_store.latest_comments(name)
            vid = lab_store.snapshot(
                name,
                members,
                source="iterate",
                parent=latest[0]["version"] if latest else None,
                comments_digest=f"{len(comments)} 条边注",
            )
            for member in cand:
                data = lab_store.read_candidate_member(name, member)
                current = lab_store.read(member)
                lab_store.save(
                    member,
                    manifest=data["manifest"] or {},
                    prompt=data["prompt"],
                    handler=current["handler"],
                    tests=data["tests"] or None,
                )
            lab_store.clear_candidate(name)
            return {"version": vid, "versions": lab_store.list_versions(name)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/api/lab/drafts/{name}/candidate/discard")
    def lab_candidate_discard(name: str) -> dict[str, Any]:
        """放弃候选(清候选区,working 不动)。"""
        try:
            lab_store.clear_candidate(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"ok": True}

    @app.get("/api/lab/drafts/{name}/versions")
    def lab_list_versions(name: str) -> list[dict[str, Any]]:
        """版本列表(新→旧;版本下拉数据源)。"""
        try:
            return lab_store.list_versions(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/api/lab/drafts/{name}/rewind")
    def lab_rewind(name: str, body: LabRewindBody) -> dict[str, Any]:
        """rewind(docs/LAB-ITERATION.md §1.2):恢复快照到 working,历史不动。"""
        try:
            lab_store.restore_version(name, body.version)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"ok": True, "version": body.version}

    @app.get("/api/lab/drafts/{name}/comments")
    def lab_latest_comments(name: str) -> dict[str, Any]:
        """最近一轮边注(左栏边注卡数据源)。"""
        try:
            round_id, comments = lab_store.latest_comments(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"round": round_id, "comments": comments}

    @app.post("/api/lab/drafts/{name}/promote")
    def lab_promote_draft(name: str, body: LabPromoteBody) -> dict[str, Any]:
        """promote(docs/SKILL-DEV.md §2.3 流程 5):闸门通过 → 写生产 → reload → 记录。

        错误归类:草稿/报告不存在 404;报告过期/含 fail/warn 未确认 409(GateError);
        生产面不支持(非单文件 skills.yaml)400。
        """
        try:
            return promote_draft(
                store=lab_store,
                name=name,
                report_id=body.report_id,
                version=body.version,
                warnings_ack=body.warnings_ack,
                production=manager.shared_skills_registry(),
                tools=manager.shared_tools_registry(),
                principal=manager.principal().subject,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except GateError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except SkillLoadError as e:
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
        """恢复命令(仅 paused):continue / 三种单步 / stop;``pause``(仅 running):
        随时暂停,run 在下一个可仲裁信号挂起(GDB SIGINT 语义)。"""
        try:
            if body.cmd == "pause":
                await manager.debug_pause(sid)
            else:
                await manager.debug_command(sid, body.cmd)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except AgentOSError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"ok": True, "cmd": body.cmd}

    @app.post("/api/debug/sessions/{sid}/rerun")
    async def post_debug_rerun(sid: str) -> dict[str, Any]:
        """重新运行:停掉并结束当前会话,以同一 skill/input/启动断点重开新会话。"""
        try:
            session_id, run_id = await manager.rerun_debug_session(sid)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=e.args[0]) from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except DebugConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except RunValidationError as e:
            # 与 POST /api/debug/sessions 同归类:技能已被卸载等
            return {"status": "failed", "error": str(e)}
        return {"session_id": session_id, "run_id": run_id}

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

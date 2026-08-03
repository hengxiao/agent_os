"""Agent OS Web Platform(方案 A:对话中枢)——FastAPI 应用(docs/WEB-PLATFORM.md §6)。

独立 app,可被主 app 挂载(``/platform``)或独立起服。本期端点:

- ``GET  /api/sessions`` / ``POST /api/sessions`` / ``GET /api/sessions/{id}``
- ``POST /api/sessions/{id}/messages``:user 发意图 → orchestrator 产 agent 消息(+卡);
- ``POST /api/cards/action``:卡片按钮统一入口——**白名单裁决**(artifacts.ACTION_WHITELIST)
  → 转发既有能力(draft create / iterate / packages promote / candidate / rewind),
  不引入新的 promote 路径(§7 红线)。

静态目录 ``static/`` 预留给下一步前端(本期不挂载空目录,前端落地时再挂)。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_os.api.v1 import derive_skill_tier
from agent_os.host.web_platform.artifacts import (
    ACTION_WHITELIST,
    build_diff_card,
    build_escalation_card,
    build_gate_report_card,
    build_publish_card,
    build_skill_pack_card,
    validate_card,
)
from agent_os.host.web_platform.orchestrator import Orchestrator
from agent_os.host.web_platform.sessions import SessionStore, new_message
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.draft_store import OverlaySkillRegistry
from agent_os.skills.gate import GateError, validate_draft
from agent_os.skills.iterate import edit_members, run_iterate
from agent_os.skills.lab_assistant import ITERATOR_NAME, iterator_skill
from agent_os.skills.package import promote_package

_log = logging.getLogger("agent_os.platform")


class MessageBody(BaseModel):
    text: str


class CardActionBody(BaseModel):
    """卡片按钮动作(统一入口;action_id 必须在白名单,endpoint 不采客户端声明)。

    ``session_id``(可选):给了就把动作结果作为 agent 消息追加进该会话
    (卡片动作的对话持久化——刷新后结果仍在,§3)。
    """

    action_id: str
    payload: dict[str, Any] = {}
    session_id: str | None = None


class DecisionBody(BaseModel):
    """升权作答(W2):answer 必须是该问题 options 之一(裁决在 run_manager)。"""

    answer: str


#: 升权档 → 人话(W2 decisions 聚合字段;摘要层禁 tier 术语,前端按 tier 自取 copy,
#: 本字段是给非前端消费方/调试面的固定中文)
_TIER_HUMAN = {"none": "只读", "reversible": "可改能撤销", "irreversible": "不可逆需审批"}


def create_platform_app(*, manager: Any, lab_store: Any, artifacts_root: Path) -> FastAPI:
    """装配平台 app:会话存储 + 编排器 + 卡片动作转发面 + 升权决策转发面。"""
    app = FastAPI(title="Agent OS Platform(对话中枢)")
    sessions = SessionStore(Path(artifacts_root) / "platform_sessions")
    providers, route_model = _llm_route_backend(manager, lab_store)
    orch = Orchestrator(
        skills=manager.shared_skills_registry(),
        runs_provider=lambda: _recent_runs(manager),
        provider=providers,
        model=route_model,
    )

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        """会话摘要列表(左栏任务/产物索引的数据源)。"""
        return sessions.list()

    @app.post("/api/sessions", status_code=201)
    def create_session() -> dict[str, Any]:
        return sessions.create()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            return sessions.get(session_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/api/sessions/{session_id}/messages")
    def post_message(session_id: str, body: MessageBody) -> dict[str, Any]:
        """发意图:user 消息落盘 → orchestrator 产 agent 消息(文本 + 卡)→ 落盘返回。"""
        try:
            sessions.append(session_id, new_message("user", text=body.text))
            session = sessions.get(session_id)
            agent_msg = orch.handle(session, body.text)
            for card in agent_msg["cards"]:  # 出服务端前过协议闸(防编排侧造出坏卡)
                validate_card(card)
            sessions.append(session_id, agent_msg)
            return agent_msg
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=500, detail=f"编排产物不合卡协议: {e}") from e

    # ------------------------------------------------------------------
    # 升权决策(W2,docs/ESCALATION.md §3):supervisor pending 的**纯转发**——
    # 聚合/作答都走 run_manager 的既有收件箱面,零新权限通道
    # ------------------------------------------------------------------

    def _decision_rows() -> list[dict[str, Any]]:
        """聚合 supervisor pending 里的升权请求(普通 question 不进对话中枢)。

        收件箱面异常时降级为空列表——决策轮询永远不能打断对话(fail-safe)。
        """
        try:
            pending = manager.supervisor_pending()
        except Exception:  # noqa: BLE001
            return []
        rows = []
        for r in pending:
            if r.get("kind") != "escalation":
                continue
            ctx = r.get("context") or {}
            tier = str(ctx.get("tier") or "none")
            rows.append(
                {
                    "question_id": r.get("question_id", ""),
                    "skill": ctx.get("skill", ""),
                    "tier": tier,
                    "tier_human": _TIER_HUMAN.get(tier, tier),
                    "reason_hint": ctx.get("reason_hint", ""),
                    "params": ctx.get("params") or {},
                    "requested": ctx.get("requested") or {},
                    "options": list(r.get("options") or ["approve-once", "deny"]),
                    "asked_at": r.get("asked_at", 0),
                }
            )
        return rows

    @app.get("/api/decisions")
    def list_decisions() -> list[dict[str, Any]]:
        """待决升权请求列表(人话字段 tier_human 随行)。"""
        return _decision_rows()

    @app.post("/api/decisions/{question_id}")
    def answer_decision(question_id: str, body: DecisionBody) -> dict[str, Any]:
        """作答转发:找不到 → 404;answer 不在 options → 400(与旧 web 收件箱同归类)。"""
        try:
            manager.supervisor_answer(question_id, body.answer)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/decisions/present")
    def present_decisions(session_id: str) -> dict[str, Any]:
        """轮询汇聚(W2 系统主动开口):新出现的 pending 以 agent 消息 + escalation
        卡落进会话并持久化;已呈现判定 = 扫会话消息里的 escalation 卡 question_id
        (无状态,进程重启/多标签页都安全)。"""
        try:
            session = sessions.get(session_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        seen = {
            card.get("data", {}).get("question_id")
            for m in session.get("messages", [])
            for card in m.get("cards", [])
            if card.get("type") == "escalation"
        }
        presented = []
        for d in _decision_rows():
            if d["question_id"] in seen:
                continue
            card = build_escalation_card(
                question_id=d["question_id"],
                skill=d["skill"],
                tier=d["tier"],
                reason_hint=d["reason_hint"],
                params=d["params"],
                requested=d["requested"],
                options=d["options"],
                asked_at=d["asked_at"],
            )
            msg = new_message(
                "agent",
                text=f"「{d['skill']}」请求批准({d['tier_human']}),细节见下卡。",
                cards=[card],
            )
            sessions.append(session_id, msg)
            presented.append(msg)
        return {"presented": presented}

    # ------------------------------------------------------------------
    # 卡片动作(统一入口:白名单裁决 → 转发既有能力,§7 不开新通道)
    # ------------------------------------------------------------------

    @app.post("/api/cards/action")
    def card_action(body: CardActionBody) -> dict[str, Any]:
        if body.action_id not in ACTION_WHITELIST:
            raise HTTPException(
                status_code=400,
                detail=f"action {body.action_id!r} 不在白名单(裁决面 ACTION_WHITELIST)",
            )
        handler = _ACTION_HANDLERS.get(body.action_id)
        if handler is None:
            raise HTTPException(status_code=400, detail=f"action {body.action_id!r} 本期未接线")
        try:
            result = handler(body.payload)
            # 对话持久化(§3):带 session_id 时,动作结果以 agent 消息落进会话
            if body.session_id and result.get("text"):
                sessions.append(
                    body.session_id,
                    new_message("agent", text=result["text"], cards=result.get("cards") or []),
                )
            return result
        except FileExistsError as e:
            # 草稿重名(重复批准/历史残留):友好 409,不 500(读屏层是人话,详情给原文)
            raise HTTPException(status_code=409, detail=f"同名草稿已存在:{e}") from e
        except GateError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except SkillLoadError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ------------------------------------------------------------------
    # action 处理器(每件都薄:调既有 store/gate/package 能力)
    # ------------------------------------------------------------------

    def _act_scaffold_approve(payload: dict[str, Any]) -> dict[str, Any]:
        """scaffold.approve(意图①批准):创建首稿 → 五关 → skill_pack + gate_report 卡。"""
        name = str(payload.get("name") or "")
        template = payload.get("template") or None
        lab_store.create(name, template=template)
        draft = lab_store.read(name)
        report = validate_draft(
            draft,
            production=manager.shared_skills_registry(),
            tools=manager.shared_tools_registry(),
            store=lab_store,
        )
        lab_store.save_gate_report(name, report)
        tier = derive_skill_tier(
            lab_store.load_skill(name).manifest, manager.shared_tools_registry(),
            OverlaySkillRegistry(manager.shared_skills_registry(), lab_store),
        )
        members = edit_members(lab_store, manager.shared_skills_registry(), manager.shared_tools_registry(), name)
        return {
            "ok": True,
            "text": f"首稿完成: {name}(推导档 {tier})。五关 {report['status']};"
            f"{'可以继续迭代,或生成提交计划。' if report['status'] != 'fail' else '有红关,先修再提交。'}",
            "cards": [
                build_skill_pack_card(name=name, tier=tier, members=members),
                build_gate_report_card(
                    draft=name, status=report["status"], gates=report["gates"],
                    plan_payload={"root": name},
                ),
            ],
        }

    def _act_plan_confirm(payload: dict[str, Any]) -> dict[str, Any]:
        """plan.confirm:走既有 packages/promote(P2 原子提交;不开新通道)。"""
        plan_id = str(payload.get("plan_id") or "")
        warnings_ack = bool(payload.get("warnings_ack"))
        result = promote_package(
            store=lab_store,
            plan_id=plan_id,
            warnings_ack=warnings_ack,
            production=manager.shared_skills_registry(),
            tools=manager.shared_tools_registry(),
            principal=manager.principal().subject,
            skillsets_root=manager.skillsets_root(),
        )
        if result.get("set"):
            manager.refresh_skillsets()
        return {
            "ok": True,
            "text": f"已发布 {result['root']}({result['form']},hash {result['package_hash'][:8]})。",
            "cards": [
                build_skill_pack_card(
                    name=result["root"],
                    tier="",
                    members=[m["name"] for m in result["members"]],
                )
            ],
        }

    def _act_candidate_accept(payload: dict[str, Any]) -> dict[str, Any]:
        """candidate.accept(与 Lab 端点同语义):快照 → 覆盖 working → 清候选。"""
        name = str(payload.get("name") or "")
        members = edit_members(
            lab_store, manager.shared_skills_registry(), manager.shared_tools_registry(), name
        )
        cand = lab_store.candidate_members(name)
        if not cand:
            raise FileNotFoundError(f"无候选: {name}")
        latest = lab_store.list_versions(name)
        _, comments = lab_store.latest_comments(name)
        vid = lab_store.snapshot(
            name, members, source="iterate",
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
        return {"ok": True, "text": f"已接受为 {vid}(候选已覆盖 working)。"}

    def _act_candidate_discard(payload: dict[str, Any]) -> dict[str, Any]:
        lab_store.clear_candidate(str(payload.get("name") or ""))
        return {"ok": True, "text": "已放弃该候选(working 未动)。"}

    def _act_version_rewind(payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "")
        version = str(payload.get("version") or "")
        lab_store.restore_version(name, version)
        return {"ok": True, "text": f"已恢复到 {version}(版本历史未动)。"}

    def _act_plan_recheck(payload: dict[str, Any]) -> dict[str, Any]:
        """plan.recheck:生成提交计划(复用 packages/plan;返回 publish 卡)。"""
        from agent_os.skills.package import build_plan

        root = str(payload.get("root") or "")
        plan = build_plan(
            root,
            store=lab_store,
            production=manager.shared_skills_registry(),
            tools=manager.shared_tools_registry(),
            smoke_runner=None,
        )
        return {
            "ok": True,
            "text": f"提交计划已生成(hash {plan['package_hash'][:8]});"
            f"阻塞 {len(plan['blockers'])} 项。",
            "cards": [
                build_publish_card(
                    root=root,
                    members=plan["members"],
                    plan_id=plan["plan_id"],
                    package_hash=plan["package_hash"],
                    blockers=plan["blockers"],
                    warnings=plan["warnings"],
                )
            ],
        }

    def _act_iterate_generate(payload: dict[str, Any]) -> dict[str, Any]:
        """iterate.generate:Flow C 同一条生成路径(产出 diff 卡)。"""
        name = str(payload.get("name") or "")
        result = run_iterate(
            manager.assemble_lab_kernel(
                OverlaySkillRegistry(
                    manager.shared_skills_registry(),
                    lab_store,
                    extra={ITERATOR_NAME: iterator_skill()},
                )
            ),
            store=lab_store,
            production=manager.shared_skills_registry(),
            tools_registry=manager.shared_tools_registry(),
            name=name,
            comments=list(payload.get("comments") or []),
            note=str(payload.get("note") or ""),
        )
        return {
            "ok": True,
            "text": f"已生成候选: {result['reply']}",
            "cards": [build_diff_card(name=name, diff=result["diff"])],
        }

    _ACTION_HANDLERS = {
        "scaffold.approve": _act_scaffold_approve,
        "plan.confirm": _act_plan_confirm,
        "candidate.accept": _act_candidate_accept,
        "candidate.discard": _act_candidate_discard,
        "version.rewind": _act_version_rewind,
        "plan.recheck": _act_plan_recheck,
        "iterate.generate": _act_iterate_generate,
    }

    # 前端样品(docs/WEB-PLATFORM.md §10):static/ 直接可访问(/platform/ → index.html)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="platform-static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


def _llm_route_backend(manager: Any, lab_store: Any) -> tuple[Any, str | None]:
    """LLM 意图路由后端(W2):借一个装配好的内核拿既有 ProviderManager + 默认 model。

    装配失败(无 providers 配置/注册表异常)→ ``(None, None)``,orchestrator
    自动走纯规则——路由是增强不是依赖(fail-safe)。
    """
    try:
        kernel = manager.assemble_lab_kernel(
            OverlaySkillRegistry(manager.shared_skills_registry(), lab_store)
        )
        if kernel.providers is None or not getattr(kernel.providers, "providers", None):
            return None, None
        return kernel.providers, kernel.config.model
    except Exception as e:  # noqa: BLE001
        _log.info("LLM 意图路由后端装配失败,纯规则运行: %s", e)
        return None, None


def _recent_runs(manager: Any) -> list[dict[str, Any]]:
    """最近 run 列表(意图②/③数据源):从 run_manager 的活跃记录读(读不到 → [])。"""
    try:
        items = manager.active_items()
    except Exception:  # noqa: BLE001 — run 记录面异常时降级为"无失败可报"
        return []
    runs = []
    for run_id, state in items:
        record = state.get("record") or {}
        runs.append(
            {
                "run_id": run_id,
                "skill": state.get("skill") or record.get("skill") or "",
                "status": state.get("status"),
                "error": record.get("error") or state.get("error") or "",
                # W2 browse 的时间窗过滤;没有就 0(orchestrator 不过滤无 ts 记录)
                "ts": state.get("started_at") or record.get("started_at") or 0,
            }
        )
    return runs

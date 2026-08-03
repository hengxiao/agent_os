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
from pydantic import BaseModel

from agent_os.api.v1 import derive_skill_tier
from agent_os.host.web_platform.artifacts import (
    ACTION_WHITELIST,
    build_diff_card,
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
    """卡片按钮动作(统一入口;action_id 必须在白名单,endpoint 不采客户端声明)。"""

    action_id: str
    payload: dict[str, Any] = {}


def create_platform_app(*, manager: Any, lab_store: Any, artifacts_root: Path) -> FastAPI:
    """装配平台 app:会话存储 + 编排器 + 卡片动作转发面。"""
    app = FastAPI(title="Agent OS Platform(对话中枢)")
    sessions = SessionStore(Path(artifacts_root) / "platform_sessions")
    orch = Orchestrator(
        skills=manager.shared_skills_registry(),
        runs_provider=lambda: _recent_runs(manager),
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
            return handler(body.payload)
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
                    root=root, members=plan["members"], plan_id=plan["plan_id"]
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

    return app


def _recent_runs(manager: Any) -> list[dict[str, Any]]:
    """最近 run 列表(意图②数据源):从 run_manager 的活跃记录读(读不到 → [])。"""
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
            }
        )
    return runs

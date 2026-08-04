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

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_os.api.v1 import ProviderError, derive_skill_tier
from agent_os.host.web_platform.apps import (
    AppInstanceStore,
    AppRegistry,
    bind_args,
    default_manifests,
    validate_args_input,
)
from agent_os.host.web_platform.artifacts import (
    ACTION_WHITELIST,
    build_diff_card,
    build_escalation_card,
    build_gate_report_card,
    build_publish_card,
    build_skill_pack_card,
    build_table_card,
    validate_card,
)
from agent_os.host.web_platform.orchestrator import Orchestrator, human_error
from agent_os.host.web_platform.sessions import SessionStore, new_message
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.draft_store import (
    OverlaySkillRegistry,
    skeleton_from_schema,
    smoke_case_from_schema,
)
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


class AppActionBody(BaseModel):
    """app action 管道(docs/APP-MODEL.md §4;M1):表面 + 事件参数。

    ``surface``:调用来自哪张面孔(card/tab)——manifest 据此校验表面合法;
    ``args``:事件参数(warnings_ack/version 等,合并进 state 绑定参数,事件优先);
    ``session_id``(可选):结果以 agent 消息回插该会话(因果可见,§5.2)。
    """

    surface: str = "card"
    args: dict[str, Any] = {}
    session_id: str | None = None


class AppSpawnBody(BaseModel):
    """spawn(docs/APP-MODEL.md §4):从卡面"打开"创建 app instance;kind+ref 去重。"""

    kind: str
    ref: str
    title: str = ""
    state: dict[str, Any] = {}
    created_by: str = ""


class WidgetRegisterBody(BaseModel):
    """widget 注册(docs/APP-MODEL.md §14;M5):Surface 渲染时登记,卸载注销。

    ``summary_hint`` = 该 widget 的人话摘要(卡面禁忌词纪律:前端给的必须是
    摘要层文字,服务端只做存储不改造)。
    """

    path: str
    kind: str = ""
    summary_hint: str = ""


class WidgetPathBody(BaseModel):
    """agent 动词(docs/APP-MODEL.md §14.3):read/focus 的 path 载荷。

    act 本期不过管道(M5 边界:用户点击语义已通;agent 的 act 权限收口留 M6)。
    """

    path: str


#: 升权档 → 人话(W2 decisions 聚合字段;摘要层禁 tier 术语,前端按 tier 自取 copy,
#: 本字段是给非前端消费方/调试面的固定中文)
_TIER_HUMAN = {"none": "只读", "reversible": "可改能撤销", "irreversible": "不可逆需审批"}


def _sse(data: dict[str, Any]) -> str:
    """一条 SSE data 帧(event 名由调用方前缀)。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_diff(
    pending_ids: list[str],
    terminal_runs: list[dict[str, Any]],
    seen: dict[str, set[str] | None],
) -> list[tuple[str, dict[str, Any]]]:
    """SSE 单周期 diff(M4b):返回 [(event, data)];seen 原地更新。

    首周期(None)只建立基线不补报存量(前端 mount 时已拉;TestClient 整读
    不了无限流,帧语义由本纯函数单测守)。
    """
    events: list[tuple[str, dict[str, Any]]] = []
    current = set(pending_ids)
    if seen["decisions"] is not None:
        new = sorted(current - seen["decisions"])
        if new:
            events.append(("decision.new", {"question_ids": new}))
    seen["decisions"] = current
    terminal_ids = {r["run_id"] for r in terminal_runs}
    if seen["terminal"] is not None:
        for r in terminal_runs:
            if r["run_id"] not in seen["terminal"]:
                events.append(
                    ("run.finished", {"run_id": r["run_id"], "skill": r["skill"], "status": r["status"]})
                )
    seen["terminal"] = terminal_ids
    return events


def create_platform_app(*, manager: Any, lab_store: Any, artifacts_root: Path) -> FastAPI:
    """装配平台 app:会话存储 + 编排器 + 卡片动作转发面 + 升权决策转发面
    + app 协议面(docs/APP-MODEL.md;M1:manifest 注册表 + action 管道)。"""
    app = FastAPI(title="Agent OS Platform(对话中枢)")
    sessions = SessionStore(Path(artifacts_root) / "platform_sessions")
    providers, route_model = _llm_route_backend(manager, lab_store)
    orch = Orchestrator(
        skills=manager.shared_skills_registry(),
        runs_provider=lambda: _recent_runs(manager, artifacts_root),
        provider=providers,
        model=route_model,
        name_taken=lambda n: _name_taken(manager, lab_store, n),
    )
    #: app instance 存储(M2 文件持久化:卡创建即登记,kind+ref 去重,
    #: 重启后卡 dict 上的 instance id 仍可解析——M1 旧卡 404 的缺口在此关闭)
    instances = AppInstanceStore(Path(artifacts_root) / "platform_apps")
    #: shell 根 app(docs/APP-MODEL.md §13;M5):bootstrap 实例化的唯一特例
    #: (不由 action 孵化);kind+ref="shell" 去重 → 布局随 instance 持久化,
    #: 重启恢复(tab 顺序/激活 tab/图标列)
    _SHELL_STATE_DEFAULT: dict[str, Any] = {
        "tabs": [{"id": "conv", "instance_id": "", "kind": "conversation", "ref": "conv", "title": "对话"}],
        "active_tab": "conv",
        "theme": "",
        "sessions": [],
        "layout": {"order": [], "icon_mode": False},
        "widgets": {},
        # M5 增补(桌面化 root widget):桌面态 = active_tab 为 "";pinned 预留(本期无 UI 面)
        "desktop": {"pinned": [], "wallpaper": True},
    }
    shell_inst, _ = instances.register(
        kind="shell", ref="shell", title="shell",
        state=json.loads(json.dumps(_SHELL_STATE_DEFAULT)),  # 深拷贝(默认值不被 mutate)
        created_by="",
    )

    # ------------------------------------------------------------------
    # app instance 登记(docs/APP-MODEL.md §2;M1:创建卡时登记,卡上带 instance id)
    # ------------------------------------------------------------------

    def _card_ref(card: dict[str, Any]) -> str:
        """卡 → app 业务锚(kind+ref 唯一性的 ref 段)。"""
        d = card.get("data") or {}
        t = card.get("type")
        if t == "plan":
            return (d.get("create") or [{}])[0].get("name", "")
        if t == "publish":
            return d.get("plan_id", "")
        if t == "escalation":
            return d.get("question_id", "")
        if t == "table":
            return (d.get("ref") or {}).get("id") or d.get("title", "")
        return d.get("name") or d.get("draft") or d.get("title") or ""

    def _register_cards(cards: list[dict[str, Any]], *, created_by: str) -> None:
        """每张卡登记一个 app instance(kind = 卡型;state = 卡 data + 绑定便利键),
        并把 instance id 写回卡 dict(前端 action 管道的寻址面,§4)。"""
        for card in cards:
            kind = card.get("type", "")
            data = card.get("data") or {}
            state = dict(data)
            if kind == "plan":  # args_from state.name/state.template 的便利键
                create0 = (data.get("create") or [{}])[0]
                state.setdefault("name", create0.get("name", ""))
                state.setdefault("template", create0.get("template", ""))
            elif kind == "gate_report":  # args_from state.root
                state.setdefault("root", data.get("draft", ""))
            inst, _opened = instances.register(
                kind=kind, ref=_card_ref(card), title=_card_ref(card),
                state=state, created_by=created_by,
            )
            card["instance"] = inst["id"]

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        """会话摘要列表(左栏任务/产物索引的数据源)。"""
        return sessions.list()

    @app.post("/api/sessions", status_code=201)
    def create_session() -> dict[str, Any]:
        session = sessions.create()
        # M1 对话 app 归一:会话即 conversation app 实例(§8 迁移地图)
        instances.register(
            kind="conversation", ref=session["id"], title=session["id"],
            state={"messages": [], "outbox": []}, created_by="",
        )
        return session

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
            _register_cards(agent_msg["cards"], created_by=session_id)  # M1:卡 → app instance
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
            _register_cards(msg["cards"], created_by=session_id)  # M1:决策卡也是 app
            sessions.append(session_id, msg)
            presented.append(msg)
        return {"presented": presented}

    # ------------------------------------------------------------------
    # 主动汇报(M4b,docs/APP-MODEL.md §4):本会话发起的 run 到终态 →
    # agent 消息 + 结果卡进会话(幂等游标随会话持久化)
    # ------------------------------------------------------------------

    def _session_of(inst: dict[str, Any]) -> str:
        """created_by 链回溯(管道 spawn 的 run app 父级是 instance,再上一级
        才是会话):走到非 instance 前缀即停(会话 id 或 "")。"""
        created_by = str(inst.get("created_by") or "")
        for _ in range(8):  # 限深防环(异常数据)
            if not created_by.startswith("app-"):
                return created_by
            parent = instances.get(created_by)
            if parent is None:
                return ""
            created_by = str(parent.get("created_by") or "")
        return ""

    def _terminal_run(run_id: str) -> dict[str, Any] | None:
        """终态 run 摘要(产物 result.json 优先,内存态兜底;未终态 → None)。"""
        result = _read_json(Path(artifacts_root) / "runs" / run_id / "result.json")
        if result and result.get("status") in ("done", "failed", "aborted"):
            meta = _read_json(Path(artifacts_root) / "runs" / run_id / "meta.json") or {}
            status = result["status"]
            return {
                "skill": meta.get("skill") or "",
                "status": status,
                "summary": "跑完了" if status == "done" else human_error(result.get("error") or "")[:120] or "失败",
            }
        try:
            state = manager.state_of(run_id)
        except Exception:  # noqa: BLE001
            state = None
        if state and state.get("status") in ("done", "failed", "aborted"):
            record = state.get("record") or {}
            status = state["status"]
            return {
                "skill": state.get("skill") or record.get("skill") or "",
                "status": status,
                "summary": "跑完了" if status == "done"
                else human_error(record.get("error") or state.get("error") or "")[:120] or "失败",
            }
        return None

    @app.post("/api/sessions/{session_id}/runs/present")
    def present_runs(session_id: str) -> dict[str, Any]:
        """主动汇报:本会话发起的 run(经 instance.created_by 链回溯)到达终态
        → agent 消息 + 结果卡进会话;presented_runs 游标随会话落盘(幂等)。"""
        try:
            session = sessions.get(session_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        seen = set(session.get("presented_runs") or [])
        presented = []
        for inst in instances.all():
            if inst["kind"] != "run" or _session_of(inst) != session_id:
                continue
            rid = inst["ref"]
            if rid in seen:
                continue
            term = _terminal_run(rid)
            if term is None:
                continue
            failed = term["status"] != "done"
            text = f"「{term['skill']}」" + (
                f"失败了: {term['summary']}(详情见下卡)。" if failed else "跑完了,结果见下卡。"
            )
            card = build_table_card(
                title="运行结果",
                columns=["run", "skill", "摘要"],
                rows=[[rid[:8], term["skill"], term["summary"]]],
                ref={"kind": "run", "id": rid},
            )
            msg = new_message("agent", text=text, cards=[card])
            _register_cards(msg["cards"], created_by=session_id)
            sessions.append(session_id, msg)
            seen.add(rid)
            presented.append(msg)
        if presented:
            sessions.put_fields(session_id, presented_runs=sorted(seen))
        return {"presented": presented}

    # ------------------------------------------------------------------
    # SSE transport(M4b,docs/APP-MODEL.md §4):decision.new / run.finished
    # + keepalive(服务端 2s 巡检 diff;断线由前端回落轮询,与 workbench 同哲学)
    # ------------------------------------------------------------------

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> Any:
            seen: dict[str, set[str] | None] = {"decisions": None, "terminal": None}
            while True:
                try:
                    pending_ids = [r["question_id"] for r in _decision_rows()]
                    terminal_runs = [
                        {k: r[k] for k in ("run_id", "skill", "status")}
                        for r in _recent_runs(manager, artifacts_root)
                        if r["status"] in ("done", "failed", "aborted")
                    ]
                    for event, data in _stream_diff(pending_ids, terminal_runs, seen):
                        yield f"event: {event}\n" + _sse(data)
                except Exception as e:  # noqa: BLE001 — 巡检面异常不炸流(下周期续)
                    _log.info("SSE 巡检异常(下周期续): %s", e)
                yield ": ka\n\n"  # keepalive(代理/浏览器不断线)
                await asyncio.sleep(2)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # 卡片动作(统一入口:白名单裁决 → 转发既有能力,§7 不开新通道)
    # ------------------------------------------------------------------

    @app.post("/api/cards/action")
    def card_action(body: CardActionBody) -> dict[str, Any]:
        """旧动作入口(过渡期保留,M3 退役;裁决面 ACTION_WHITELIST 不变)。
        handler 与新 action 管道**同一份实现**(_ACTION_HANDLERS/SKILL_BINDINGS)。"""
        if body.action_id not in ACTION_WHITELIST:
            raise HTTPException(
                status_code=400,
                detail=f"action {body.action_id!r} 不在白名单(裁决面 ACTION_WHITELIST)",
            )
        handler = _ACTION_HANDLERS.get(body.action_id)
        if handler is None:
            raise HTTPException(status_code=400, detail=f"action {body.action_id!r} 本期未接线")
        result = _run_handler(handler, body.payload)
        _register_cards(result.get("cards") or [], created_by=body.session_id or "")
        # 对话持久化(§3):带 session_id 时,动作结果以 agent 消息落进会话
        if body.session_id and result.get("text"):
            sessions.append(
                body.session_id,
                new_message("agent", text=result["text"], cards=result.get("cards") or []),
            )
        return result

    def _run_handler(fn: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """调 handler 并统一异常归类(旧 cards/action 与新 action 管道同一映射面)。"""
        try:
            return fn(payload)
        except FileExistsError as e:
            # 草稿重名(重复批准/历史残留):友好 409,不 500(读屏层是人话,详情给原文)
            raise HTTPException(status_code=409, detail=f"同名草稿已存在:{e}") from e
        except ProviderError as e:
            # N7(O7):凭证/模型服务故障 → 503 人话(前端系统气泡;不 500 不裸英文类名)
            raise HTTPException(
                status_code=503,
                detail="模型服务暂不可用(凭证可能已过期),请刷新凭证后重试",
            ) from e
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
        """scaffold.approve(意图①批准):创建首稿 → 五关 → skill_pack + gate_report 卡。

        N3(O3,B2):首稿即带 1 个合 schema 的冒烟用例(tests/smoke.json,
        按 inputs schema 骨架生成)——首稿 G4 不再必然 warn。
        """
        name = str(payload.get("name") or "")
        template = payload.get("template") or None
        lab_store.create(name, template=template)
        draft = lab_store.read(name)
        if not draft["tests"]:
            case = smoke_case_from_schema((draft["manifest"] or {}).get("inputs"))
            if case is not None:
                lab_store.save(
                    name,
                    manifest=draft["manifest"],
                    prompt=draft["prompt"],
                    handler=draft["handler"],
                    tests={"smoke.json": case},
                )
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
            prefer_candidate=True,  # N2:快照 = 被接受的候选内容(B4)
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
            # M4a run 通道:run_id/status 透传(管道据此 spawn run app 持 run_id)
            "run_id": result.get("run_id", ""),
            "run_status": result.get("run_status", ""),
            "skill": name,
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

    # ------------------------------------------------------------------
    # app 协议面(docs/APP-MODEL.md;M1):manifest 注册表 + action 管道 + spawn。
    # SKILL_BINDINGS 把 manifest 的 skill 键映射到**同一份**薄 handler——
    # 白名单从代码升格为 manifest 数据,零新权限通道(§7 红线不变)。
    # ------------------------------------------------------------------

    def _act_decision_answer(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.decision.answer:升权作答(answer 由管道按 action_id 注入)。"""
        qid = str(payload.get("question_id") or "")
        answer = str(payload.get("answer") or "")
        try:
            manager.supervisor_answer(qid, answer)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True, "text": "已记录你的决定。", "state": {"resolved": answer}}

    # ── M3 handlers(docs/APP-MODEL.md §8):run/debug/lab-draft 三 kind 的
    # skill 绑定——每件都是既有 manager 能力的薄转发(manager 方法是 async,
    # 与 LLM 路由同款 asyncio.run 私有循环),零新权限通道 ─────────────────

    def _act_run_stop(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.run.stop:中止在途 run(不在途 → 409,与旧 web stop 同归类)。"""
        run_id = str(payload.get("run_id") or "")
        ok = asyncio.run(manager.stop_run(run_id))
        if not ok:
            raise HTTPException(status_code=409, detail=f"run 不在在途状态,无法停止: {run_id}")
        return {"ok": True, "text": "已发送停止请求(run 在下一个安全点中止)。", "state": {"status": "stopping"}}

    def _act_run_resume(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.run.resume:从 checkpoint 恢复(404/400/409 同旧 web resume)。"""
        run_id = str(payload.get("run_id") or "")
        try:
            record = asyncio.run(manager.resume_run(run_id))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:  # ResumeConflictError 等状态冲突归 409
            if "Conflict" in type(e).__name__:
                raise HTTPException(status_code=409, detail=str(e)) from e
            raise
        return {
            "ok": True,
            "text": f"恢复运行完成: {record.get('status')}",
            "state": {"status": record.get("status", "")},
        }

    def _act_run_rerun(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.run.rerun:按产物 meta 的 skill+input 重跑(新 run;结果卡挂新锚)。"""
        run_id = str(payload.get("run_id") or "")
        meta = _read_json(Path(artifacts_root) / "runs" / run_id / "meta.json")
        if meta is None:
            raise HTTPException(status_code=404, detail=f"找不到 run 产物: {run_id}")
        new_id = asyncio.run(manager.start_run(meta.get("skill") or "", meta.get("input") or {}))
        return {
            "ok": True,
            "text": f"已按原参数重跑,新 run: {new_id[:8]}。",
            "cards": [
                build_table_card(
                    title="重跑",
                    columns=["run", "skill", "摘要"],
                    rows=[[new_id[:8], meta.get("skill") or "", "已启动"]],
                    ref={"kind": "run", "id": new_id},
                )
            ],
        }

    def _act_debug_command(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.debug.command:放行/停止(command 由管道按 action_id 注入:
        debug.continue→continue,debug.stop→stop;非 paused → 409)。"""
        sid = str(payload.get("session_id") or "")
        command = str(payload.get("command") or "continue")
        try:
            asyncio.run(manager.debug_command(sid, command))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:  # AgentOSError(非 paused)归 409
            if "Error" in type(e).__name__:
                raise HTTPException(status_code=409, detail=str(e)) from e
            raise
        human = "已放行。" if command == "continue" else "已发送停止。"
        return {"ok": True, "text": human, "state": {"last_command": command}}

    def _act_draft_check(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.draft.check:草稿五关(与 Lab validate 端点同逻辑;返回 gate_report 卡)。"""
        name = str(payload.get("name") or "")
        draft = lab_store.read(name)
        report = validate_draft(
            draft,
            production=manager.shared_skills_registry(),
            tools=manager.shared_tools_registry(),
            store=lab_store,
        )
        lab_store.save_gate_report(name, report)
        return {
            "ok": True,
            "text": f"检查完成: {report['status']}",
            "cards": [
                build_gate_report_card(
                    draft=name, status=report["status"], gates=report["gates"],
                    plan_payload={"root": name},
                )
            ],
        }

    def _act_run_launch(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.run.launch(M4a 发起面归一):app 内"再跑一次"。

        input 缺省 = 按 skill inputs schema 的 skeleton 骨架(用户可改,改后
        服务端按 schema 校验,不合 → 400);skill 缺省时从产物 meta 回推
        (run tab spawn 的 state.skill 可能为空)。lab-draft 的"试跑"同通道
        (overlay 解析序:草稿优先)。
        """
        skill = str(payload.get("skill") or "")
        run_id = str(payload.get("run_id") or "")
        if not skill and run_id:
            meta = _read_json(Path(artifacts_root) / "runs" / run_id / "meta.json")
            skill = str((meta or {}).get("skill") or "")
        if not skill:
            raise HTTPException(status_code=400, detail="launch 需要 skill(state.skill 或产物 meta)")
        # inputs schema 解析(overlay:草稿 ∪ 生产;拿不到 schema 就只校验是 object)
        inputs_schema: dict[str, Any] = {}
        try:
            overlay = OverlaySkillRegistry(manager.shared_skills_registry(), lab_store)
            inputs_schema = overlay.get_by_name(skill).manifest.inputs or {}
        except Exception as e:  # noqa: BLE001 — 未知技能由 start_run 归 400(RunValidationError)
            _log.info("launch schema 解析失败,按无 schema 继续: %s", e)
        input_value = payload.get("input")
        if input_value is None:
            input_value = skeleton_from_schema(inputs_schema)  # 缺省 = 骨架(§M4a 发起面)
        if inputs_schema:
            try:
                jsonschema.validate(input_value, inputs_schema)
            except jsonschema.ValidationError as e:
                raise HTTPException(status_code=400, detail=f"input 不合 {skill} 的 inputs schema: {e.message}") from e
        try:
            new_id = asyncio.run(manager.start_run(skill, input_value))
        except Exception as e:  # RunValidationError 等归 400(与 POST /api/runs 同)
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "ok": True,
            "text": f"已发起 {skill},新 run: {new_id[:8]}。",
            "run_id": new_id,
            "skill": skill,
            "status": "running",
            "cards": [
                build_table_card(
                    title="新 run",
                    columns=["run", "skill", "摘要"],
                    rows=[[new_id[:8], skill, "已启动"]],
                    ref={"kind": "run", "id": new_id},
                )
            ],
        }

    # ── M5:shell 的 local mutators(docs/APP-MODEL.md §13.1)──────────────
    # local = 仅改 app.state(不出海);写穿透经 instances.update_state(布局持久化)
    def _mut_tab_open(inst: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        state = inst["state"]
        tabs = state.setdefault("tabs", [])
        existing = next(
            (t for t in tabs
             if t.get("kind") != "conversation" and t.get("kind") == args.get("kind") and t.get("ref") == args.get("ref")),
            None,
        )
        if existing is None:
            tabs.append({
                "id": args.get("id", ""), "instance_id": args.get("instance_id", ""),
                "kind": args.get("kind", ""), "ref": args.get("ref", ""), "title": args.get("title", ""),
            })
            state["active_tab"] = args.get("id", "")
        else:
            state["active_tab"] = existing.get("id", "")  # kind+ref 去重聚焦(§6)
        instances.update_state(inst["id"], state)
        return {"ok": True}

    def _mut_tab_focus(inst: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        inst["state"]["active_tab"] = args.get("tab", "conv")
        instances.update_state(inst["id"], inst["state"])
        return {"ok": True}

    def _mut_tab_close(inst: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        state = inst["state"]
        tab_id = args.get("tab", "")
        state["tabs"] = [t for t in state.get("tabs", []) if t.get("id") != tab_id]
        if state.get("active_tab") == tab_id:
            state["active_tab"] = "conv"  # 关闭回落 conversation(销毁是显式动作,close≠destroy)
        instances.update_state(inst["id"], state)
        return {"ok": True}

    def _mut_layout_set(inst: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        inst["state"].setdefault("layout", {})["icon_mode"] = bool(args.get("icon_mode", False))
        instances.update_state(inst["id"], inst["state"])
        return {"ok": True}

    def _mut_layout_move_tab(inst: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        state = inst["state"]
        tabs = [t for t in state.get("tabs", []) if t.get("id") != args.get("tab")]
        moving = next((t for t in state.get("tabs", []) if t.get("id") == args.get("tab")), None)
        if moving is not None:
            before = args.get("before") or ""
            if before == "__start__":
                tabs.insert(1 if tabs and tabs[0].get("id") == "conv" else 0, moving)  # conv 恒首
            elif before:
                idx = next((i for i, t in enumerate(tabs) if t.get("id") == before), len(tabs))
                tabs.insert(idx, moving)
            else:
                tabs.append(moving)  # before 缺省 = 移到末尾
            state["tabs"] = tabs
            instances.update_state(inst["id"], state)
        return {"ok": True}

    def _mut_tab_minimize(inst: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        """最小化(M5 增补,桌面化):active_tab 置 "" —— 无激活 tab = 桌面主屏;
        tab 全保留(关闭≠销毁的另一面:最小化≠关闭)。"""
        inst["state"]["active_tab"] = ""
        instances.update_state(inst["id"], inst["state"])
        return {"ok": True}

    def _mut_desktop_set(inst: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        """桌面开关(M5 增补):wallpaper 持久化进 shell.state.desktop(写穿透)。"""
        desktop = inst["state"].setdefault("desktop", {"pinned": [], "wallpaper": True})
        if "wallpaper" in args:
            desktop["wallpaper"] = bool(args["wallpaper"])
        instances.update_state(inst["id"], inst["state"])
        return {"ok": True}

    _LOCAL_MUTATORS = {
        "shell.tab.open": _mut_tab_open,
        "shell.tab.focus": _mut_tab_focus,
        "shell.tab.close": _mut_tab_close,
        "shell.layout.set": _mut_layout_set,
        "shell.layout.move_tab": _mut_layout_move_tab,
        # M5 增补(桌面化 root widget)
        "shell.tab.minimize": _mut_tab_minimize,
        "shell.desktop.set": _mut_desktop_set,
    }

    def _act_shell_theme_set(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.shell.theme.set(M5 §13.1):切主题(持久化偏好 → shell.state.theme)。"""
        return {"ok": True, "text": "", "state": {"theme": str(payload.get("theme") or "")}}

    def _act_shell_session_create(payload: dict[str, Any]) -> dict[str, Any]:
        """platform.shell.session.create(M5 §13.1):新会话(app 孵化,与 POST /api/sessions 同源)。"""
        session = sessions.create()
        instances.register(
            kind="conversation", ref=session["id"], title=session["id"],
            state={"messages": [], "outbox": []}, created_by="",
        )
        return {"ok": True, "text": "", "session": session}

    SKILL_BINDINGS = {
        # exec 归态(v0.2 §4;归错态 = 授权漏洞,评审面):
        #   endpoint = 确定性写,转发既有端点(下方除标注外全部);
        #   run = agentic 起 run(iterate.generate——当前与 endpoint 同 handler
        #   面,真 run 通道 spawn run app 持 run_id 归 M4);local 不在此表(不出海)。
        "platform.scaffold.approve": _act_scaffold_approve,
        "platform.iterate.generate": _act_iterate_generate,  # run 态(见上注释)
        "platform.candidate.accept": _act_candidate_accept,
        "platform.candidate.discard": _act_candidate_discard,
        "platform.version.rewind": _act_version_rewind,
        "platform.plan.recheck": _act_plan_recheck,
        "platform.plan.confirm": _act_plan_confirm,
        "platform.decision.answer": _act_decision_answer,
        # M3(docs/APP-MODEL.md §8):run/debug/lab-draft 三 kind 的绑定(全 endpoint)
        "platform.run.stop": _act_run_stop,
        "platform.run.resume": _act_run_resume,
        "platform.run.rerun": _act_run_rerun,
        "platform.debug.command": _act_debug_command,
        "platform.draft.check": _act_draft_check,
        "platform.run.launch": _act_run_launch,  # M4a 发起面(run 态)
        # M5(docs/APP-MODEL.md §13.1):shell 的 endpoint 动作
        "platform.shell.theme.set": _act_shell_theme_set,
        "platform.shell.session.create": _act_shell_session_create,
    }

    registry = AppRegistry(known_skills=set(SKILL_BINDINGS))
    for _manifest in default_manifests():
        registry.register(_manifest)
    #: 内省面(测试/调试;registry 是进程静态面,实例存储是 M1 内存态)
    app.state.platform_registry = registry
    app.state.platform_instances = instances

    @app.post("/api/apps/spawn", status_code=201)
    def app_spawn(body: AppSpawnBody) -> dict[str, Any]:
        """spawn(§4):从卡面"打开"创建 app instance;kind 须在注册表,kind+ref 去重。

        M4a:初始 state 按 manifest 的 ``state_schema`` 校验(不合 → 400,
        "登记即伪造"在此关闭;schema 以现状卡 data 为准,additionalProperties 默认放行)。
        """
        manifest = registry.get(body.kind)
        if manifest is None:
            raise HTTPException(status_code=400, detail=f"未注册的 app kind: {body.kind!r}")
        try:
            jsonschema.validate(body.state, manifest["state_schema"])
        except jsonschema.ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail=f"spawn state 不合 {body.kind} 的 state_schema: {e.message}",
            ) from e
        inst, opened = instances.register(
            kind=body.kind, ref=body.ref, title=body.title or body.ref,
            state=body.state, created_by=body.created_by,
        )
        return {"instance": inst, "opened": opened}

    @app.get("/api/apps/{instance_id}")
    def app_get(instance_id: str) -> dict[str, Any]:
        """instance 读取(state 一致性/两表面同源的检查面,M1 先数据面)。"""
        inst = instances.get(instance_id)
        if inst is None:
            raise HTTPException(status_code=404, detail=f"找不到 app instance: {instance_id}")
        return inst

    # ------------------------------------------------------------------
    # shell 与 widget 寻址(M5,docs/APP-MODEL.md §13/§14)
    # ------------------------------------------------------------------

    @app.get("/api/shell")
    def get_shell() -> dict[str, Any]:
        """shell 根 app:tab 条/布局的唯一事实源(前端镜像消费,§13)。"""
        return instances.get(shell_inst["id"])

    @app.post("/api/widgets/register")
    def widget_register(body: WidgetRegisterBody) -> dict[str, Any]:
        """widget 注册(§14.2 注册制):Surface 渲染登记,随 shell.state 持久化。"""
        state = shell_inst["state"]
        state.setdefault("widgets", {})[body.path] = {
            "kind": body.kind, "summary_hint": body.summary_hint,
        }
        instances.update_state(shell_inst["id"], state)
        return {"ok": True}

    @app.post("/api/widgets/unregister")
    def widget_unregister(body: WidgetPathBody) -> dict[str, Any]:
        """widget 注销(卸载即注销;注销后 read/focus 404)。"""
        state = shell_inst["state"]
        state.setdefault("widgets", {}).pop(body.path, None)
        instances.update_state(shell_inst["id"], state)
        return {"ok": True}

    def _widget_read(path: str) -> dict[str, Any]:
        """路径解析(§14;查 registry 不查 DOM):人话摘要,不存在 → 404。
        ``{path:path}`` 转换器剥前导斜杠——按 "/x" 与 "x" 两形归一查。"""
        widgets = shell_inst["state"].get("widgets", {})
        w = widgets.get(path) or widgets.get("/" + path.lstrip("/"))
        if w is None:
            raise HTTPException(status_code=404, detail=f"找不到 widget: {path}")
        return {"path": path, "kind": w.get("kind", ""), "summary": w.get("summary_hint", "")}

    @app.get("/api/widgets/{path:path}")
    def widget_get(path: str) -> dict[str, Any]:
        """路径解析端点(M5 §2):read 的 GET 形。"""
        return _widget_read(path)

    @app.post("/api/widgets/read")
    def widget_read(body: WidgetPathBody) -> dict[str, Any]:
        """agent 动词 read(§14.3):人话摘要(禁忌词纪律由登记方摘要层保证)。"""
        return _widget_read(body.path)

    @app.post("/api/widgets/focus")
    def widget_focus(body: WidgetPathBody) -> dict[str, Any]:
        """agent 动词 focus(§14.3):解析同源(404 一致);滚动+高亮在前端执行
        (本端点只裁决存在性——focus 不改世界,local 语义)。
        act 本期不过管道:用户点击语义已通;agent 的 act 权限收口留 M6。"""
        _widget_read(body.path)
        return {"ok": True, "path": body.path}

    @app.post("/api/apps/{instance_id}/actions/{action_id}")
    def app_action(instance_id: str, action_id: str, body: AppActionBody) -> dict[str, Any]:
        """action 管道(§4):manifest 校验 → args 绑定(state+事件)→ handler → state 回写。

        前端永不直接调业务端点:action 存在性/表面合法/参数来源都由 manifest
        裁决(防前端越权构造,§7);handler 与旧 cards/action 同源。
        """
        inst = instances.get(instance_id)
        if inst is None:
            raise HTTPException(status_code=404, detail=f"找不到 app instance: {instance_id}")
        action = registry.action_of(inst["kind"], action_id)
        if action is None:
            raise HTTPException(
                status_code=404,
                detail=f"app {inst['kind']!r} 无 action {action_id!r}(manifest 裁决)",
            )
        if body.surface not in (action.get("surface") or []):
            raise HTTPException(
                status_code=400,
                detail=f"action {action_id!r} 不在 {body.surface!r} 面提供(manifest 表面裁决)",
            )
        mode = action["exec"]["mode"]
        if mode == "local":
            # v0.2 §4 + M5 §13:local = 仅改 app.state(不出海)——state 服务端权威
            # 后,shell 类 local 动作的"仅改 state"恰恰要在服务端执行;
            # 无 mutator 的 local(conversation 的 pin/close 等前端本地动作)
            # 调到管道仍拒绝(M3.5 语义不变)
            mutator = _LOCAL_MUTATORS.get(action_id)
            if mutator is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"action {action_id!r} 是 local 态(纯 UI 动作不出海),不应调到管道",
                )
            try:
                input_args = validate_args_input(action, body.args)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            result = mutator(inst, input_args)
            return {**result, "instance": inst}
        try:
            input_args = validate_args_input(action, body.args)  # 客户端载荷的唯一合法面
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            bound = bind_args(action, inst["state"])  # 服务端权威绑定(客户端改不了)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"参数绑定缺源: {e}") from e
        payload = {**bound, **input_args}  # 两分合并:bound(服务端)+ input(客户端声明)
        ref = action["exec"]["ref"]
        if ref == "platform.decision.answer":
            payload["answer"] = action_id  # 作答 = action id(approve-once/approve-run/deny)
        if ref == "platform.debug.command":
            # 调试命令 = action id 末段(debug.continue→continue,debug.stop→stop)
            payload["command"] = action_id.split(".")[-1]
        # endpoint/run 当前同一薄 handler 面;**run 态**(v0.2 §4,M4a):长任务 =
        # spawn run app 持 run_id——handler 结果带 run_id 时登记 run instance,
        # 响应附 run_instance(用户可直接进 run tab;running 态 = 持 run_id 且未终态)
        result = _run_handler(SKILL_BINDINGS[ref], payload)
        if mode == "run" and result.get("run_id"):
            run_inst, _opened = instances.register(
                kind="run",
                ref=result["run_id"],
                title=result["run_id"][:8],
                state={
                    "run_id": result["run_id"],
                    "skill": result.get("skill", ""),
                    "status": result.get("run_status") or result.get("status") or "running",
                    **({"result": result["result"]} if "result" in result else {}),
                },
                created_by=instance_id,
            )
            result = {**result, "run_instance": run_inst}
        if isinstance(result.get("state"), dict):
            instances.update_state(instance_id, result["state"])
        _register_cards(result.get("cards") or [], created_by=instance_id)
        if body.session_id and result.get("text"):
            sessions.append(
                body.session_id,
                new_message("agent", text=result["text"], cards=result.get("cards") or []),
            )
        return {**result, "instance": inst}

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


def _name_taken(manager: Any, lab_store: Any, name: str) -> bool:
    """create 名占用判定:草稿层(DraftStore)+ 生产层(共享 registry)都查。"""
    try:
        if lab_store is not None and any(d.get("name") == name for d in lab_store.list()):
            return True
    except Exception as e:  # noqa: BLE001 — 存储面异常不阻塞命名(批准时闸门还会拦)
        _log.info("草稿层占用查询失败,按未占用继续: %s", e)
    try:
        registry = manager.shared_skills_registry()
    except Exception as e:  # noqa: BLE001 — registry 未装配:生产层无可占用
        _log.info("生产 registry 不可用,占用判定只看草稿层: %s", e)
        return False
    if registry is None:
        return False
    try:
        registry.get_by_name(name)
    except SkillLoadError:
        return False  # 未注册 = 可用(正常分支)
    except Exception as e:  # noqa: BLE001 — 查询面异常同样按可用,批准闸门兜底
        _log.info("生产层占用查询失败,按未占用继续: %s", e)
        return False
    return True


def _read_json(path: Path) -> dict[str, Any] | None:
    """读产物 JSON(半写窗口/坏文件 → None,与旧 web 同一容忍口径)。"""
    try:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _iso_ts(value: Any) -> float:
    """started_at → epoch 秒:ISO 串(产物层)或数值(内存态)都接;认不出 → 0。"""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _recent_runs(manager: Any, artifacts_root: Path) -> list[dict[str, Any]]:
    """最近 run 列表(意图②/③数据源):**产物层枚举**(与旧 web ``/api/runs``
    同一数据源语义——``runs/*/meta.json`` + ``result.json``,进程重启不丢)
    合并内存态(在途 run 以内存为准)。返回按 ts 升序(orchestrator 取尾=最新)。
    """
    runs: dict[str, dict[str, Any]] = {}
    runs_dir = Path(artifacts_root) / "runs"
    try:
        run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else []
    except OSError:
        run_dirs = []
    for run_dir in run_dirs:
        meta = _read_json(run_dir / "meta.json")
        if meta is None:
            continue
        result = _read_json(run_dir / "result.json") or {}
        run_id = meta.get("run_id") or run_dir.name
        runs[run_id] = {
            "run_id": run_id,
            "skill": meta.get("skill") or "",
            # meta 在、result 未落:视为在途(崩溃/断电遗留),与旧 web 列表同口径
            "status": result.get("status") or "running",
            "error": result.get("error") or "",
            "ts": _iso_ts(meta.get("started_at")),
        }
    try:
        items = manager.active_items()
    except Exception:  # noqa: BLE001 — 内存面异常不拖垮产物枚举
        items = []
    for run_id, state in items:
        record = state.get("record") or {}
        runs[run_id] = {
            "run_id": run_id,
            "skill": state.get("skill") or record.get("skill") or "",
            "status": state.get("status"),
            "error": record.get("error") or state.get("error") or "",
            "ts": _iso_ts(state.get("started_at")),
        }
    return sorted(runs.values(), key=lambda r: r["ts"])

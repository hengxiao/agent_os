"""Agent OS Web Platform(方案 A:对话中枢)——FastAPI 应用(docs/WEB-PLATFORM.md §6)。

独立 app,可被主 app 挂载(``/platform``)或独立起服。本期端点:

- ``GET  /api/sessions`` / ``POST /api/sessions`` / ``GET /api/sessions/{id}``
- ``POST /api/sessions/{id}/messages``:user 发意图 → orchestrator 产 agent 消息(+卡);
- ``POST /api/cards/action``:卡片按钮统一入口——**白名单裁决**(artifacts.ACTION_WHITELIST)
  → 转发既有能力(draft create / iterate / packages promote / candidate / rewind),
  不引入新的 promote 路径(§7 红线)。**deprecated**(§17.7-4:M1 前持久化旧卡
  的兼容面,新卡全部走 app action 管道;无新调用方,退役留清理)。

静态目录 ``static/`` 预留给下一步前端(本期不挂载空目录,前端落地时再挂)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_os.api.v1 import ChatRequest, Message, Role
from agent_os.host.web_platform.apps import (
    AppInstanceStore,
    AppRegistry,
    bind_args,
    default_manifests,
    validate_args_input,
)
from agent_os.host.web_platform.artifacts import (
    ACTION_WHITELIST,
    build_escalation_card,
    build_table_card,
    validate_card,
)
from agent_os.host.web_platform.doc_generate import (
    GenerateOutputError,
    build_generate_messages,
    parse_generate_output,
)
from agent_os.host.web_platform.orchestrator import Orchestrator, human_error
from agent_os.host.web_platform.sessions import SessionStore, new_message
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.doc_store import DocStore
from agent_os.skills.draft_store import OverlaySkillRegistry
from agent_os.skills.lab_assistant import (
    DOC_COMMENTER_NAME,
    DOC_EDITOR_NAME,
    DOC_REVIEWER_NAME,
    doc_commenter_skill,
    doc_editor_skill,
    doc_reviewer_skill,
)
from agent_os.skills.platform import (
    PLATFORM_LOCAL_SKILLS,
    build_platform_kernel,
    platform_skill_names,
)
from agent_os.skills.reanchor import reanchor_annotations
from agent_os.tools.lab_tools import register_doc_tools

_log = logging.getLogger("agent_os.platform")


#: severity 单源(D4 打磨;与前端 doc-editor.js 的 DOC_SEVERITIES 字面一致——
#: 后端校验集在此,前端类名/copy 在 doc-editor.js,两端各一份单一事实源)
DOC_SEVERITIES = ("must", "should", "nit")

#: 锚点格式(docs/DOC-EDITOR.md §2.1;v3 用户裁决 2026-08-11):
#: doc.md#L<start>[:C<col>]-L<end>[:C<col>](1-based 行号区间,列可选——
#: 选区右键关联到列范围;旧行级 anchor 向后兼容);
#: review 批注集校验用;platform.doc.apply 技能侧另有同形一份——skills/platform/)
_ANCHOR_RE = re.compile(r"^doc\.md#L(\d+)(?::C(\d+))?-L(\d+)(?::C(\d+))?$")


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


class AppActionBody(BaseModel):
    """app action 管道(docs/APP-MODEL.md §4;M1):表面 + 事件参数。

    ``surface``:调用来自哪张面孔(card/tab)——manifest 据此校验表面合法;
    ``args``:事件参数(warnings_ack/version 等,合并进 state 绑定参数,事件优先);
    ``session_id``(可选):结果以 agent 消息回插该会话(因果可见,§5.2);
    ``cascade``(可选,§17.7-3):级联信封(§16)——前端按触发路径经注册
    provider 组装(widget 零 fetch,服务端信任边界不变);action 声明
    ``context: []`` = 显式弃权(信封不进执行输入)。
    """

    surface: str = "card"
    args: dict[str, Any] = {}
    session_id: str | None = None
    cascade: list[dict[str, Any]] | None = None


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


class DocCreateBody(BaseModel):
    """``POST /api/docs``(D1):新建文档(点分名校验在 store 层)。"""

    name: str
    title: str = ""
    text: str = ""


class DocCommentBody(BaseModel):
    """``POST /api/docs/{name}/comment``(D2,docs/DOC-EDITOR.md §5):
    段落气泡提交的信封——锚点 + 批注 + §16 级联上下文。"""

    anchor: str = ""
    text: str = ""
    cascade: list[dict[str, Any]] = []


class DocChatBody(BaseModel):
    """``POST /api/docs/{name}/chat``(D5,docs/DOC-EDITOR.md §5):
    doc 作用域主对话的一轮——只有用户消息;全文/批注由服务端自己装信封。"""

    text: str = ""


class DocGenerateBody(BaseModel):
    """``POST /api/docs/{name}/generate``(P1,批注批处理工作流 v2 设计 §7.2):

    - ``baseVersion``:基于哪个版本生成(int 序号或 "vNNN";缺省 = 当前最新快照),
      与当前不符 → 409(裁决 C5 版本冲突);
    - ``chatContext``:chat 上下文(缺省 = 主对话最近 20 条);
    - ``annotations``:待处理批注(缺省 = 库内全部 pending);
    - ``userPrompt``:本次生成的附加指令(可选)。"""

    baseVersion: Any = None
    chatContext: list[dict[str, Any]] | None = None
    annotations: list[dict[str, Any]] | None = None
    userPrompt: str | None = None


class DocAnnotationBody(BaseModel):
    """``POST /api/docs/{name}/annotations``(P2):单条批注 upsert——
    创建 / 重新编辑(同锚点覆盖,状态回 pending 参与下一轮生成)。"""

    anchor: str = ""
    quote: str = ""
    content: str = ""
    version: int | None = None
    status: str = "pending"
    severity: str = ""  # 评审批注可选(must|should|nit;P2 runReview 链)


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
        docs_provider=lambda: doc_store.list(),  # D4:doc 意图数据源(只读)
    )
    #: app instance 存储(M2 文件持久化:卡创建即登记,kind+ref 去重,
    #: 重启后卡 dict 上的 instance id 仍可解析——M1 旧卡 404 的缺口在此关闭)
    instances = AppInstanceStore(Path(artifacts_root) / "platform_apps")
    #: 文档存储(D1,docs/DOC-EDITOR.md §4):docs_root 缺省 <artifacts>/docs
    doc_store = DocStore(Path(artifacts_root) / "docs")
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
    # 文档(D1,docs/DOC-EDITOR.md §4):/api/docs CRUD(读面直给;
    # 写动作(save/snapshot/rewind/export)一律经 app action 管道,§3 归态)
    # ------------------------------------------------------------------

    @app.get("/api/docs")
    def list_docs() -> list[dict[str, Any]]:
        """文档索引(Card Surface 数据源:标题/首行/字数/最近编辑/状态)。"""
        return doc_store.list()

    @app.post("/api/docs", status_code=201)
    def create_doc(body: DocCreateBody) -> dict[str, Any]:
        """新建文档(点分名校验 = 穿越防护;重名 409)。"""
        try:
            return doc_store.create(body.name, title=body.title, text=body.text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileExistsError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/api/docs/{name}")
    def read_doc(name: str) -> dict[str, Any]:
        """读文档全文 + meta(+ 版本列表,编辑器的版本下拉数据源)。"""
        try:
            doc = doc_store.read(name)
            doc["versions"] = [v["version"] for v in doc_store.list_versions(name)]
            doc["chat"] = doc_store.read_chat(name)  # D5:左栏主对话种子(开关不丢)
            return doc
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/docs/{name}/bubbles")
    def read_doc_bubbles(name: str) -> list[dict[str, Any]]:
        """全文档气泡流(D2:气泡种子——开关不丢,服务端 DocStore bubbles/ 是事实源)。"""
        try:
            return doc_store.read_bubbles(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/docs/{name}/bubbles/delete")
    def delete_doc_bubble(name: str, body: dict[str, Any]) -> dict[str, Any]:
        """批注删除(v3 用户裁决:气泡垃圾桶 → 上行到父 → 此端点删持久化)。

        anchor 格式校验(列可选,行级向后兼容);**删除幂等**:流不存在也 200
        (deleted=false)——前端对只开过没落盘的泡同样走这条路,不当错误面。
        P1 起 delete_bubble 两面都删(bubbles/ 旧流 + annotations/ 新记录)。
        """
        anchor = str(body.get("anchor") or "")
        if not _ANCHOR_RE.match(anchor):
            raise HTTPException(status_code=400, detail=f"锚点格式非法: {anchor!r}")
        try:
            doc_store.delete_bubble(name, anchor)
            deleted = True
        except FileNotFoundError:
            deleted = False  # 幂等:本就不存在(只开过没落盘/别处已删)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"deleted": deleted, "anchor": anchor}

    @app.get("/api/docs/{name}/annotations")
    def read_doc_annotations(name: str) -> list[dict[str, Any]]:
        """批注读取面(P2):annotations/ 新记录 + bubbles/ 旧流压缩迁移
        (同锚点新优先;无 status 的旧记录读为 pending)。"""
        try:
            return doc_store.read_annotations(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/docs/{name}/annotations")
    def save_doc_annotation(name: str, body: DocAnnotationBody) -> dict[str, Any]:
        """单条批注 upsert(P2,save_annotation 路径;**无即时 AI 回复**——
        comment 对话链退役,批注攒着等 generate 批处理):
        创建 = pending;重新编辑 = 同锚点覆盖、状态回 pending、generation/
        appliedInVersion 清零(参与下一轮生成);history/migratedFrom 随已有记录保留。"""
        anchor = str(body.anchor or "")
        if not _ANCHOR_RE.match(anchor):
            raise HTTPException(status_code=400, detail=f"锚点格式非法: {anchor!r}")
        content = str(body.content or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="批注内容不能为空")
        if len(content) > 500:
            raise HTTPException(status_code=400, detail="批注过长(>500 字)")
        try:
            existing = next(
                (a for a in doc_store.read_annotations(name) if a.get("anchor") == anchor), None
            )
            return doc_store.save_annotation(
                name,
                {
                    **(existing or {}),
                    "anchor": anchor,
                    "quote": str(body.quote or ""),
                    "content": content,
                    "version": body.version,
                    "status": body.status or "pending",
                    "appliedInVersion": None,
                    "generation": None,
                    **({"severity": body.severity} if body.severity else {}),
                },
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/api/docs/{name}/review")
    def doc_review(name: str) -> dict[str, Any]:
        """全文评审(D3,docs/DOC-EDITOR.md §5;exec: run + cascade):
        信封 = 全文 + 大纲 + 最近 diff(v-1 → working,有版本时)→ doc_reviewer
        (tools=[] 白名单空)→ 锚点批注集;**自动挂段**(每条批注进对应锚点的
        气泡流,带 severity)+ 落 review/<ts>.json(历史可查)。"""
        try:
            doc = doc_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        text = doc["text"]
        outline = [
            {"level": len(m.group(1)), "text": m.group(2).strip()}
            for ln in text.splitlines()
            if (m := re.match(r"^(#{1,6})\s+(.*)$", ln))
        ]
        # 最近 diff(v-1 → working;difflib 行级,变体行才进,封顶 4000 字符)
        diff_text = ""
        versions = doc_store.list_versions(name)
        if versions:
            vdoc = Path(artifacts_root) / "docs" / name / "versions" / versions[0]["version"] / "doc.md"
            try:
                old = vdoc.read_text(encoding="utf-8")
                if old != text:
                    import difflib

                    diff_text = "\n".join(
                        ln
                        for ln in difflib.unified_diff(old.splitlines(), text.splitlines(), lineterm="", n=0)
                        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
                    )[:4000]
            except OSError:
                diff_text = ""
        try:
            kernel = manager.assemble_lab_kernel(
                OverlaySkillRegistry(
                    manager.shared_skills_registry(),
                    lab_store,
                    extra={DOC_REVIEWER_NAME: doc_reviewer_skill()},
                )
            )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"评审助手不可用: {e}") from e
        try:
            result = asyncio.run(
                kernel.run(DOC_REVIEWER_NAME, {"text": text, "outline": outline, "diff": diff_text})
            )
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"评审助手暂不可用: {type(e).__name__}: {e}"
            ) from e
        # 批注集校验(锚点格式 + severity 白名单 + text 非空;不合条丢弃,不炸)
        notes = []
        for note in (result or {}).get("notes") or []:
            if (
                isinstance(note, dict)
                and _ANCHOR_RE.match(str(note.get("anchor") or ""))
                and note.get("severity") in DOC_SEVERITIES
                and str(note.get("text") or "").strip()
            ):
                notes.append(
                    {"anchor": str(note["anchor"]), "severity": note["severity"], "text": str(note["text"]).strip()}
                )
        fname = doc_store.save_review(name, notes)
        # 气泡雨:每条批注自动挂进对应锚点的气泡流(severity 随消息)
        for note in notes:
            doc_store.save_bubble(
                name,
                note["anchor"],
                {"role": "assistant", "text": note["text"], "severity": note["severity"], "review": fname},
            )
        return {"notes": notes, "review_file": fname}

    @app.post("/api/docs/{name}/comment")
    def doc_comment(name: str, body: DocCommentBody) -> dict[str, Any]:
        """段落气泡提交(D2,docs/DOC-EDITOR.md §3/§5;与 W2 lab comment 同先例):
        信封(anchor+text+cascade)→ doc_commenter run(tools=[] 白名单空,
        只读级联)→ reply/edits;**双方消息落 bubbles/(开关不丢)**。"""
        try:
            doc_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        try:
            kernel = manager.assemble_lab_kernel(
                OverlaySkillRegistry(
                    manager.shared_skills_registry(),
                    lab_store,
                    extra={DOC_COMMENTER_NAME: doc_commenter_skill()},
                )
            )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"评论助手不可用: {e}") from e
        try:
            result = asyncio.run(
                kernel.run(
                    DOC_COMMENTER_NAME,
                    {"anchor": body.anchor, "text": body.text, "cascade": body.cascade},
                )
            )
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"评论助手暂不可用: {type(e).__name__}: {e}"
            ) from e
        result = result or {}
        reply = str(result.get("reply") or "")
        edits = result.get("edits") if isinstance(result.get("edits"), list) else []
        if not reply and edits:
            reply = str(edits[0].get("suggestion") or "")
        # 持久化(D2:开关不丢)——用户批注 + 助手回复进锚点消息流
        doc_store.save_bubble(name, body.anchor, {"role": "user", "text": body.text})
        doc_store.save_bubble(
            name,
            body.anchor,
            {"role": "assistant", "text": reply, **({"edits": edits} if edits else {})},
        )
        return {"reply": reply, "edits": edits}

    @app.post("/api/docs/{name}/chat")
    def doc_chat(name: str, body: DocChatBody) -> dict[str, Any]:
        """doc 作用域主对话(D5,docs/DOC-EDITOR.md §5):
        信封(text + cascade[文档名/全文/全部 bubbles 批注])→ doc_editor run
        (白名单 = 当前文档的 doc.read/doc.edit,工具侧引用围栏双保险)→
        {reply, changed};**changed = run 前后全文对比**(不信技能自报);
        双侧消息落 chat.json(开关不丢)。"""
        try:
            doc = doc_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        before = doc["text"]
        bubbles = doc_store.read_bubbles(name)
        # UX 批(2026-08-04)死路话术改引导:空批注时的"按批注改"类请求不起 run,
        # 直接给引导式回复(人话,不编造;changed=false,双侧消息照常落 chat.json)
        if not bubbles and "批注" in body.text:
            reply = "还没有批注。在有意见的段落上右键,先留一条批注,我就能按批注改。"
            doc_store.save_chat(name, {"role": "user", "text": body.text})
            doc_store.save_chat(name, {"role": "assistant", "text": reply})
            return {"reply": reply, "changed": False}
        cascade = [
            {
                "scope": "app",
                "path": f"/doc/{name}",
                "data": {
                    "name": name,
                    "full_text": before,
                    # 批注全量进信封:"按批注改一遍"由主对话直接覆盖
                    "bubbles": bubbles,
                },
            }
        ]
        try:
            kernel = manager.assemble_lab_kernel(
                OverlaySkillRegistry(
                    manager.shared_skills_registry(),
                    lab_store,
                    extra={DOC_EDITOR_NAME: doc_editor_skill()},
                )
            )
            register_doc_tools(kernel.tools, store=doc_store, doc_name=name)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"编辑助手不可用: {e}") from e
        try:
            result = asyncio.run(
                kernel.run(DOC_EDITOR_NAME, {"text": body.text, "cascade": cascade})
            )
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"编辑助手暂不可用: {type(e).__name__}: {e}"
            ) from e
        reply = str((result or {}).get("reply") or "")
        changed = doc_store.read(name)["text"] != before  # 全文对比判 changed
        # 持久化(D5:开关不丢)——用户消息 + 助手回复进主对话流
        doc_store.save_chat(name, {"role": "user", "text": body.text})
        doc_store.save_chat(name, {"role": "assistant", "text": reply})
        return {"reply": reply, "changed": changed}

    @app.post("/api/docs/{name}/generate")
    def doc_generate(name: str, body: DocGenerateBody) -> dict[str, Any]:
        """批注批处理生成(P1,设计 v2 §7.2;PLAN-ANNOTATION-WORKFLOW-V2 §1):

        全部 pending 批注(缺省读库;显式传入则用传入)+ chat 上下文 + 附加指令
        → LLM 一次生成完整新文档(§7.3 模板,XML 块契约)→ 解析
        ``<modified_document>``/``<annotation_results>``(不合 → 重试一次 →
        再败 502,**不半截落库**)→ working 更新 + snapshot 新版本(meta 扩
        generationInput/annotationResults,版本不可变)→ 逐条写批注状态
        (applied/ignored + generation.aiNote)→ **reanchor**(§7.1:精确 →
        ±3 行模糊全文匹配 → outdated)→ 返回 {newVersion, annotationResults,
        diff(unified)}。

        baseVersion 与当前最新快照不符 → 409(裁决 C5 版本冲突)。
        LLM 面 = 与 chat 同一 ProviderManager + 默认 model(原文直取,
        orchestrator._route_llm 先例)。
        """
        try:
            doc = doc_store.read(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        versions = doc_store.list_versions(name)
        cur_vid = versions[0]["version"] if versions else None
        cur_no = int(cur_vid[1:]) if cur_vid else 0
        if body.baseVersion is not None:
            try:
                base_no = _norm_version(body.baseVersion)
            except (TypeError, ValueError) as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            if base_no != cur_no:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"版本冲突: baseVersion=v{base_no:03d} ≠ 当前 {cur_vid or '(无快照)'}"
                        "(文档已被更新,刷新后重试)"
                    ),
                )
        if providers is None or not route_model:
            raise HTTPException(status_code=503, detail="生成功能不可用: 未配置 LLM provider")
        before = doc["text"]
        annotations = body.annotations
        if annotations is None:
            annotations = [a for a in doc_store.read_annotations(name) if a.get("status") == "pending"]
        chat_context = body.chatContext if body.chatContext is not None else doc_store.read_chat(name)[-20:]
        messages = build_generate_messages(
            document=before,
            base_version=cur_no,
            chat_context=chat_context,
            annotations=annotations,
            user_prompt=body.userPrompt,
        )
        raw = ""
        parsed: tuple[str, list[dict[str, Any]]] | None = None
        last_err: Exception | None = None
        for _attempt in (1, 2):  # 输出不合契约 → 重试一次 → 再败 502(不落库)
            try:
                raw = asyncio.run(_chat_text(providers, route_model, messages, timeout=300))
            except Exception as e:
                raise HTTPException(
                    status_code=503, detail=f"生成助手暂不可用: {type(e).__name__}: {e}"
                ) from e
            try:
                parsed = parse_generate_output(raw)
                break
            except GenerateOutputError as e:
                last_err = e
        if parsed is None:
            snippet = re.sub(r"\s+", " ", raw).strip()[:200]
            raise HTTPException(
                status_code=502,
                detail=f"生成输出两次不合契约({last_err});原文摘要: {snippet!r}",
            )
        new_text, results = parsed
        doc_store.save(name, new_text)
        new_vid = doc_store.snapshot(
            name,
            source="generate",
            parent=cur_vid,
            extra={
                "generationInput": {
                    "chatContext": chat_context,
                    "annotations": annotations,
                    "userPrompt": body.userPrompt,
                },
                "annotationResults": results,
            },
        )
        new_no = int(new_vid[1:])
        # 逐条写批注状态(partial 归 applied 并留 aiNote;库内没有的锚点跳过——
        # 显式传入的临时批注不落状态)
        known = {a.get("anchor") for a in doc_store.read_annotations(name)}
        for r in results:
            if r["annotationId"] not in known:
                continue
            doc_store.set_annotation_status(
                name,
                r["annotationId"],
                status="ignored" if r["status"] == "ignored" else "applied",
                applied_in_version=new_no,
                generation={
                    "appliedByVersion": new_no,
                    "result": r["status"],
                    "aiNote": r["aiNote"],
                },
            )
        # reanchor(§7.1):全部批注在新文本上重定位;锚点变更 → hash 键换名
        anns = doc_store.read_annotations(name)
        for old, new in zip(anns, reanchor_annotations(new_text, anns), strict=True):
            if new == old:
                continue
            if new["anchor"] != old["anchor"]:
                doc_store.delete_annotation(name, old["anchor"])
            doc_store.save_annotation(name, new)
        import difflib

        diff = "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                new_text.splitlines(),
                fromfile=f"{name}@{cur_vid or 'working'}",
                tofile=f"{name}@{new_vid}",
                lineterm="",
            )
        )
        return {
            "newVersion": new_no,
            "versionId": new_vid,
            "annotationResults": results,
            "diff": diff,
        }

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
        """待决升权请求列表(人话字段 tier_human 随行;读面保留——§17.6:读不算 action)。"""
        return _decision_rows()

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
        """**deprecated**(§17.7-4):旧卡面兼容入口——M1 前持久化的卡没有 instance,
        前端旧面仍走这里;新卡一律走 app action 管道。裁决面 ACTION_WHITELIST 不变,
        与新管道**同一份实现**(同一批 platform.* code 技能)。"""
        if body.action_id not in ACTION_WHITELIST:
            raise HTTPException(
                status_code=400,
                detail=f"action {body.action_id!r} 不在白名单(裁决面 ACTION_WHITELIST)",
            )
        ref = _CARD_ACTION_REFS.get(body.action_id)
        if ref is None:
            raise HTTPException(status_code=400, detail=f"action {body.action_id!r} 本期未接线")
        result = _run_handler(ref, body.payload)
        _register_cards(result.get("cards") or [], created_by=body.session_id or "")
        # 对话持久化(§3):带 session_id 时,动作结果以 agent 消息落进会话
        if body.session_id and result.get("text"):
            sessions.append(
                body.session_id,
                new_message("agent", text=result["text"], cards=result.get("cards") or []),
            )
        return result

    def _run_handler(ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        """经 platform 内核跑动作技能(§17.7 L1:``kernel.run()`` 进程内,不落产物——
        run_iterate 先例;旧 cards/action 与新 action 管道同一调用面)。

        技能侧把可归类错误折成 ``_action_error`` 信封(异常过不了 Logic Kernel
        执行边界,会塌成 RUNTIME_ERROR 丢状态码);此处原位翻译成 HTTPException——
        状态码/文案与升格前的 _run_handler 逐字一致(行为零变化,§17.8)。
        """
        try:
            result = asyncio.run(platform_kernel.run(ref, payload))
        except SkillLoadError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if isinstance(result, dict) and isinstance(result.get("_action_error"), dict):
            err = result["_action_error"]
            raise HTTPException(status_code=int(err["status"]), detail=str(err["detail"]))
        return result

    # ------------------------------------------------------------------
    # action 技能面(docs/APP-MODEL.md v0.5 §17.7 升格序第 1 步;L1 全量):
    # 全部 handler 升格为 platform.* 内置 code 技能(skills/platform/ 包,
    # 包内 skills.yaml 注册,trusted 档);本侧只留依赖注入与分发——
    # SKILL_BINDINGS/_LOCAL_MUTATORS 的函数表形态退役(技能名 → 技能
    # 的注册引用由 platform 内核的 registry 承担)。
    # ------------------------------------------------------------------

    platform_kernel = build_platform_kernel(
        {
            "manager": manager,
            "lab_store": lab_store,
            "doc_store": doc_store,
            "sessions": sessions,
            "instances": instances,
            "artifacts_root": artifacts_root,
            "read_json": _read_json,
        }
    )

    #: 旧卡面 action(过渡期保留;§17.7 第 4 步退役)→ platform.* 技能名
    _CARD_ACTION_REFS = {
        "scaffold.approve": "platform.scaffold.approve",
        "plan.confirm": "platform.plan.confirm",
        "candidate.accept": "platform.candidate.accept",
        "candidate.discard": "platform.candidate.discard",
        "version.rewind": "platform.version.rewind",
        "plan.recheck": "platform.plan.recheck",
        "iterate.generate": "platform.iterate.generate",
    }

    registry = AppRegistry(known_skills=platform_skill_names())
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
        if not opened and body.state:
            # C4.4:去重命中的既有实例缺键时按本次(已过 schema 校验)的 state
            # 补键——陈旧/最小登记(state={})不再永久卡死 args_from 绑定;
            # 冲突键以既有为准(去重语义不动,"登记即伪造"仍由 schema 关闸)
            missing = {k: v for k, v in body.state.items() if k not in (inst.get("state") or {})}
            if missing:
                instances.update_state(inst["id"], missing)
                inst = instances.get(inst["id"])
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
            # §17.7 L1:local 也是真 code 技能(tier none,无副作用面)——
            # exec.mode 降级为元信息,调用一律经 kernel.run(进程内);
            # 无服务端技能的 local(conversation 的 spawn/pin/close 等前端
            # 本地动作)调到管道仍拒绝(M3.5 语义不变)
            local_ref = PLATFORM_LOCAL_SKILLS.get(action_id)
            if local_ref is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"action {action_id!r} 是 local 态(纯 UI 动作不出海),不应调到管道",
                )
            try:
                input_args = validate_args_input(action, body.args)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            # §17.10 框架备参:_instance_id/_state 由管道注入(非客户端载荷面)
            result = _run_handler(
                local_ref,
                apply_action_cascade(
                    action,
                    {"_instance_id": instance_id, "_state": inst["state"], **input_args},
                    body.cascade,
                ),
            )
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
        payload = apply_action_cascade(action, payload, body.cascade)  # §17.7-3 级联信封
        # §17.7 L1:endpoint/run 同一 kernel.run 调用面,exec.mode 只是元信息——
        # **run 态**(v0.2 §4,M4a):长任务 = spawn run app 持 run_id——技能结果
        # 带 run_id 时登记 run instance,响应附 run_instance(用户可直接进
        # run tab;running 态 = 持 run_id 且未终态)
        result = _run_handler(ref, payload)
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
        # C4.4(docs/DESKTOP-WIDGET.md §6):产品入口 = desktop 根(旧壳 app.js 退役)
        return FileResponse(static_dir / "desktop.html")

    # 开发直达(与产品入口同一份;旧壳 C4.4 退役后保留本页双入口)
    @app.get("/desktop.html", include_in_schema=False)
    def desktop() -> FileResponse:
        return FileResponse(static_dir / "desktop.html")

    return app


def _norm_version(v: Any) -> int:
    """baseVersion 归一:int 序号 / "vNNN" / 数字串 → int;类型/格式非法 → 拒。"""
    if isinstance(v, bool):
        raise TypeError(f"baseVersion 非法: {v!r}")
    if isinstance(v, int):
        return v
    m = re.match(r"^v?(\d+)$", str(v or "").strip())
    if not m:
        raise ValueError(f"baseVersion 非法: {v!r}(须为序号或 vNNN 串)")
    return int(m.group(1))


async def _chat_text(providers: Any, model: str, messages: list[dict[str, str]], *, timeout: float) -> str:
    """provider 原文面(orchestrator._route_llm 先例:FastAPI 线程池里
    ``asyncio.run`` 开私有循环;返回 ``message.content`` 原文,不做 JSON 解析)。"""
    req = ChatRequest(
        model=model,
        messages=[
            Message(role=Role.SYSTEM if m.get("role") == "system" else Role.USER, content=m.get("content", ""))
            for m in messages
        ],
    )
    resp = await asyncio.wait_for(providers.chat(req), timeout=timeout)
    return str(resp.message.content or "")


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


def apply_action_cascade(
    action: dict[str, Any], payload: dict[str, Any], cascade: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """§17.7-3(管道自动携带级联信封):执行输入并入 ``cascade``;
    action 声明 ``context: []`` = 显式弃权(§16.1,轻动作不背大信封)。"""
    if cascade is None or action.get("context") == []:
        return payload
    return {**payload, "cascade": cascade}


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

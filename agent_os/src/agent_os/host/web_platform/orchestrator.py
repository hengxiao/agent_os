"""意图编排(docs/WEB-PLATFORM.md §5;方案 A:assistant 升级为 router)。

W2 起四个意图(**LLM 意图路由**:装配注入 ProviderManager + model;LLM 不可用/
超时/输出不合 schema → 回落规则路由,fail-safe——凭证 15 分钟过期是现实,
规则面永远兜底):

1. **create_skill**("做个/写个 X 技能")→ plan 卡:分解(复用 closure/registry
   搜已有技能 + 建议新建),批准动作 = scaffold.approve(走既有 scaffold 端点);
2. **why_failed**("为什么挂/失败")→ 最近失败 run 的 RCA 摘要 → table 卡;
3. **browse**(W2;"上周哪些失败了/最近的 run")→ 最近运行摘要 → table 卡
   (逐行 run 详情链接)——浏览对话化的第一个实例;
4. **help**(其他)→ help 卡(三句引导)。

工具面红线:编排只**读**生产 registry 与 run 记录;写动作全部经卡片
action 白名单转发既有端点(docs/WEB-PLATFORM.md §7),不开新通道。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from agent_os.api.v1 import ChatRequest, Message, Role
from agent_os.host.web_platform.artifacts import (
    build_doc_list_card,
    build_plan_card,
    build_table_card,
)
from agent_os.host.web_platform.sessions import new_message

_log = logging.getLogger("agent_os.platform.router")

#: 意图①关键词(规则路由;中文为主,英文兜底)
_MAKE_RE = re.compile(r"(做个|写个|建个|帮我做|帮我写|创建|新建).*(技能|skill)|^make .*skill", re.IGNORECASE)
#: 意图②关键词(失败排查)
_FAIL_RE = re.compile(r"(为什么|为啥).*(挂|失败|错)|失败|挂了|报错|fail", re.IGNORECASE)
#: 意图③关键词(浏览:集合语义的"哪些/列表/记录"——区别于单点排查的"为什么挂")
_BROWSE_RE = re.compile(r"(哪些|所有|列表|记录|情况).*(run|运行|失败)|(run|运行).*(哪些|列表|记录)", re.IGNORECASE)

#: 意图⑤关键词(D4;doc 文档:列表/新建——与 make_skill 的"写个 X 技能"区分)
_DOC_NEW_RE = re.compile(r"新建文档|写个文档|写篇文档|写文档")
_DOC_RE = re.compile(r"文档|文档列表|打开文档")

#: LLM 意图枚举(schema 校验面;越界 → 回落规则)
INTENTS = ("create_skill", "why_failed", "browse", "help")

#: LLM 路由提示词(输出唯一 JSON;分类器不写解释,省 token 也省解析面)
_ROUTE_PROMPT = """\
你是意图分类器。把用户的话分进四类之一,只输出一行 JSON,不要别的文字:
{"intent": "create_skill"|"why_failed"|"browse"|"help", "goal": "...", "name": "...", "timeframe": "..."}
- create_skill:想做一个新技能/能力;goal = 主题词(英文小写,没有就空串);
  name = 建议的技能名(英文小写"域名.动作",如 dinner.planner;没有就空串)
- why_failed:问某次运行为何失败/报错
- browse:想看一段时间内的运行列表(哪些失败/最近的运行);timeframe = 如 "上周"/"最近"
- help:其它(闲聊/不会分)
"""

#: LLM 单次分类的等待上限(秒;超时按不可用处理,回落规则)
_ROUTE_TIMEOUT_S = 15.0

#: 英文错误类名 → 人话(N1 摘要泄漏清零,B1):行级摘要统一过这层,
#: 错误原文只留详情 tab(产物层 /api/runs/{id} 不动)。未识别的剥掉
#: `XxxError:` 前缀留消息体——不编造原因(与前端 humanError 同一映射,
#: 后端先译一道,前端那层变纯兜底)
_ERROR_HUMAN = [
    (re.compile(r"auth|api.?key|unauthorized|401", re.IGNORECASE), "API Key 无效或过期"),
    (re.compile(r"timeout|timed out", re.IGNORECASE), "请求超时"),
    (re.compile(r"ProviderError|model.*(unavailable|error)", re.IGNORECASE), "模型服务不可用"),
]


def human_error(raw: Any) -> str:
    """错误原文 → 摘要层人话(行级;已知模式给翻译,未知剥类名前缀)。"""
    s = str(raw or "")
    for rx, human in _ERROR_HUMAN:
        if rx.search(s):
            return human
    return re.sub(r"^[A-Z][\w.]*Error:\s*", "", s)


class Orchestrator:
    """意图路由 + 编排(规则本期,LLM 留 provider 接口)。"""

    def __init__(
        self,
        *,
        skills: Any = None,
        runs_provider: Any = None,
        provider: Any = None,
        model: str | None = None,
        name_taken: Any = None,
        docs_provider: Any = None,
    ) -> None:
        self._skills = skills  # 生产 SkillRegistry(只读:manifests())
        #: 最近 run 列表来源(注入:``() -> [{run_id, skill, status, error, ts?}]``;
        #: 隔离 app 装配细节,测试用假数据驱动
        self._runs_provider = runs_provider or (list)
        #: 文档索引来源(D4 doc 意图;``() -> DocStore.list()``,只读)
        self._docs_provider = docs_provider or (list)
        #: LLM 意图路由(docs/WEB-PLATFORM.md §5;W2):既有 ProviderManager 门面 +
        #: 默认 model;None = 纯规则(装配失败/未装配时的安全态)
        self._provider = provider
        self._model = model
        #: 名字占用判定(注入:``(name) -> bool``,查草稿层 + 生产层;
        #: create 名必须唯一——批准一个已存在的名必撞 FileExistsError 409)
        self._name_taken = name_taken or (lambda _name: False)

    def handle(self, session: dict[str, Any], text: str) -> dict[str, Any]:
        """用户意图 → agent 消息(文本 + 卡)。LLM 路由优先,故障回落规则。

        N6(O6,B6):消息与 plan 卡 data 带 ``meta/route_meta``
        ``{route: "llm"|"rule", reason?}``——路由来源可观测,LLM 失败率
        可按 reason 统计;reason 是机器码(llm_unavailable/llm_bad_schema),
        人话文案在前端 copy(六主题,N7 降级提示同挂这条)。
        """
        routed = self._route(text)
        intent = routed["intent"]
        meta = routed.get("meta")
        if intent == "create_skill":
            msg = self._make_skill(
                text, goal=routed.get("goal") or "", name=routed.get("name") or "", route_meta=meta
            )
        elif intent == "why_failed":
            msg = self._why_failed()
        elif intent == "browse":
            msg = self._browse(timeframe=routed.get("timeframe") or "")
        elif intent == "doc":
            msg = self._doc_list(is_new=_DOC_NEW_RE.search(text) is not None)
        else:
            msg = self._help()
        if meta:
            msg["meta"] = meta
        return msg

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def _route(self, text: str) -> dict[str, Any]:
        """LLM 优先;不可用/超时/schema 不合 → 规则(永远兜底,fail-safe)。"""
        if self._provider is not None:
            routed, reason = self._route_llm(text)
            if routed is not None:
                routed["meta"] = {"route": "llm"}
                return routed
            return {"intent": self._route_rules(text), "meta": {"route": "rule", "reason": reason}}
        return {"intent": self._route_rules(text), "meta": {"route": "rule"}}

    def _route_rules(self, text: str) -> str:
        if _MAKE_RE.search(text):
            return "create_skill"
        if _DOC_NEW_RE.search(text) or _DOC_RE.search(text):  # D4:doc 意图(本期只走规则面)
            return "doc"
        if _BROWSE_RE.search(text):  # 集合语义先于单点排查("哪些失败" ≠ "为什么挂")
            return "browse"
        if _FAIL_RE.search(text):
            return "why_failed"
        return "help"

    def _route_llm(self, text: str) -> tuple[dict[str, Any] | None, str | None]:
        """LLM 意图分类:唯一 JSON 输出 → schema 校验;任何失败 → (None, 原因码)。

        返回 ``(routed, reason)``:命中时 reason=None;失败时 routed=None,
        reason ∈ {"llm_unavailable", "llm_bad_schema"}(N6 可观测/N7 降级
        文案的机器码)。fail-safe 是硬要求(凭证过期/超时/胡说八道都不能
        打断对话);ProviderManager 的 chat 是 async——本方法在 FastAPI
        线程池里跑,``asyncio.run`` 开私有循环,不碰宿主事件循环。
        """
        async def _ask() -> Any:
            req = ChatRequest(
                model=self._model or "",
                messages=[
                    Message(role=Role.SYSTEM, content=_ROUTE_PROMPT),
                    Message(role=Role.USER, content=text),
                ],
                response_format={"type": "json_object"},
                max_tokens=200,
            )
            return await asyncio.wait_for(self._provider.chat(req), timeout=_ROUTE_TIMEOUT_S)

        try:
            resp = asyncio.run(_ask())
        except Exception as e:  # noqa: BLE001 — 任何 LLM 侧失败都按"不可用"回落
            _log.info("LLM 意图路由不可用,回落规则: %s", e)
            return None, "llm_unavailable"
        try:
            data = json.loads(resp.message.content)
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            _log.info("LLM 意图路由输出非 JSON,回落规则: %s", e)
            return None, "llm_bad_schema"
        if not isinstance(data, dict) or data.get("intent") not in INTENTS:
            _log.info("LLM 意图路由输出不合 schema,回落规则: %r", data)
            return None, "llm_bad_schema"
        return {
            "intent": data["intent"],
            "goal": str(data.get("goal") or ""),
            "name": _sanitize_name(data.get("name")),
            "timeframe": str(data.get("timeframe") or ""),
        }, None

    # ------------------------------------------------------------------
    # 意图①:做个 X 技能 → plan 卡
    # ------------------------------------------------------------------

    def _make_skill(
        self, text: str, *, goal: str = "", name: str = "", route_meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        topic = goal or _topic_of(text)  # LLM 给了主题词就用,没有走规则提取
        # create 名:LLM 建议 > 规则"域名.动作"(lab.<topic>);冲突自动加唯一后缀,
        # 绝不批准一个已存在的名(草稿层/生产层占用都查,批准即 409 的坑)
        create_name = self._unique_name(name or f"lab.{topic}")
        # reuse 与 create 必须互斥(同一名只出现在一处)
        reuse = [r for r in self._search_existing(topic) if r["name"] != create_name]
        create = [
            {
                "name": create_name,
                "template": "prompt_query",
                "reason": "主技能:按意图生成首稿(模板可改)",
            }
        ]
        card = build_plan_card(
            goal=text,
            reuse=reuse,
            create=create,
            approve_payload={"name": create_name, "template": create[0]["template"]},
            route_meta=route_meta,  # N6:路由来源进卡 data(详情层角标)
        )
        summary = (
            f"计划如下:复用 {len(reuse)} 个已有技能"
            + (f"({', '.join(r['name'] for r in reuse)})" if reuse else "")
            + f",新建 {create_name}。批准即生成首稿。"
        )
        return new_message("agent", text=summary, cards=[card])

    def _unique_name(self, base: str) -> str:
        """唯一名:base 未被占用直接用;否则 base2/base3…(lab.custom2 风格)。"""
        if not self._name_taken(base):
            return base
        i = 2
        while self._name_taken(f"{base}{i}"):
            i += 1
        return f"{base}{i}"

    def _search_existing(self, topic: str) -> list[dict[str, str]]:
        """复用检索(规则版):**只认已发布生产技能**——名字/描述含主题词
        (上限 3 个)。草稿不在此面(没发布无从复用;lab.* 前缀防御性跳过);
        主题词是兜底 "custom"(无语义依据)时直接给空——宁可全部新建,不硬塞复用。
        """
        if self._skills is None or not topic or topic == "custom":
            return []
        found = []
        try:
            manifests = self._skills.manifests()
        except Exception:  # noqa: BLE001 — registry 未装配时降级为"无可复用"
            return []
        for m in manifests:
            if m.name.startswith("lab."):
                continue  # 草稿命名空间防御:reuse 只认生产层
            if topic in m.name or topic in (m.description or ""):
                found.append({"name": m.name, "reason": f"已覆盖相关能力({m.description[:30]})"})
        return found[:3]

    # ------------------------------------------------------------------
    # 意图②:为什么挂 → 最近失败 run 的 RCA 摘要表卡
    # ------------------------------------------------------------------

    def _why_failed(self) -> dict[str, Any]:
        runs = self._runs_provider() or []
        failed = [r for r in runs if r.get("status") == "failed"]
        if not failed:
            return new_message(
                "agent",
                text="最近没有失败的 run。要看具体某次运行,把 run_id 发我。",
                cards=[build_table_card(title="失败运行", columns=["run", "skill", "原因"], rows=[])],
            )
        latest = failed[-1]
        rows = [
            [
                latest.get("run_id", "")[:8],
                latest.get("skill", ""),
                human_error(latest.get("error", ""))[:120] or "(无错误摘要)",  # N1:行级人话
            ]
        ]
        return new_message(
            "agent",
            text=f"最近一次失败是 {latest.get('skill')}(run {latest.get('run_id', '')[:8]}),摘要见下表。",
            cards=[
                build_table_card(
                    title="最近的失败",
                    columns=["run", "skill", "错误摘要"],
                    rows=rows,
                    ref={"kind": "run", "id": latest.get("run_id", "")},  # 详情 tab 的锚(§10)
                )
            ],
        )

    # ------------------------------------------------------------------
    # 意图③(W2):浏览 → 最近运行摘要表(逐行 run 详情链接)
    # ------------------------------------------------------------------

    def _browse(self, *, timeframe: str = "") -> dict[str, Any]:
        """"上周哪些失败了/最近的 run" → 运行列表摘要(人话行 + 逐行详情锚)。

        ``timeframe`` 目前只做文本回显("上周"→ 近 7 天过滤,前提是数据源带 ts;
        没带 ts 的记录不过滤——降级为全量,不编时间)。
        """
        runs = list(self._runs_provider() or [])
        cutoff = _timeframe_cutoff(timeframe)
        if cutoff is not None:
            dated = [r for r in runs if r.get("ts")]
            if dated:  # 数据源带时间才过滤;全不带 → 降级全量,不编时间
                runs = [r for r in dated if r["ts"] >= cutoff]
        runs = runs[-10:]  # 列表上限:摘要不刷屏,全量去旧 UI 运行页
        if not runs:
            return new_message(
                "agent",
                text=f"{timeframe or '最近'}没有运行记录。",
                cards=[build_table_card(title="最近的运行", columns=["run", "skill", "摘要"], rows=[])],
            )
        rows = []
        row_refs = []
        for r in runs:
            failed = r.get("status") == "failed"
            rows.append(
                [
                    r.get("run_id", "")[:8],
                    r.get("skill", ""),
                    # N1:行级人话(failed = error_human,成功 = status_human)
                    (human_error(r.get("error", ""))[:120] or "(无错误摘要)") if failed else "运行成功",
                ]
            )
            row_refs.append({"kind": "run", "id": r.get("run_id", "")} if r.get("run_id") else None)
        failed_n = sum(1 for r in runs if r.get("status") == "failed")
        return new_message(
            "agent",
            text=f"{timeframe or '最近'}共 {len(runs)} 次运行,{failed_n} 次失败;摘要见下表,点行内链接看单次详情。",
            cards=[
                build_table_card(
                    title="最近的运行",
                    columns=["run", "skill", "摘要"],
                    rows=rows,
                    row_refs=row_refs,
                )
            ],
        )

    # ------------------------------------------------------------------
    # 意图⑤(D4):doc 文档 → 列表卡(新建意图带"点卡新建"引导;编排只读,
    # 新建由前端卡上按钮经 /api/docs 完成——写动作不入编排,§工具面红线)
    # ------------------------------------------------------------------

    def _doc_list(self, *, is_new: bool = False) -> dict[str, Any]:
        docs = self._docs_provider() or []
        card = build_doc_list_card(docs=docs)
        hint = "点卡上「新建文档」我帮你起稿;" if is_new else ""
        return new_message(
            "agent",
            text=f"{hint}共 {len(docs)} 篇文档,点开就进编辑器。",
            cards=[card],
        )

    # ------------------------------------------------------------------
    # 意图④:其他 → help 卡(三句引导)
    # ------------------------------------------------------------------
    def _help(self) -> dict[str, Any]:
        return new_message(
            "agent",
            text="我可以帮你做技能、查失败、看运行。试试这样说:",
            cards=[
                build_table_card(
                    title="我能做什么",
                    columns=["你说", "我做"],
                    rows=[
                        ["帮我做个查天气的技能", "出计划卡 → 批准后生成首稿"],
                        ["刚才那个 run 为什么挂了", "给最近一次失败的摘要"],
                        ["你好 / 随便聊聊", "给你三条可点的引导(就是这张卡)"],
                    ],
                )
            ],
        )


def _topic_of(text: str) -> str:
    """从意图文本提取主题词(命名用):优先连续英文/拼音词,中文主题回退 "custom"。

    规则版 intentionally 简单(命名最终由用户/助手在 Lab 里改);
    长度压到 ≤24 且只留 [a-z0-9_]。
    """
    words = re.findall(r"[a-z][a-z0-9_]{2,}", text.lower())
    if words:
        return words[0][:24]
    return "custom"


def _sanitize_name(raw: Any) -> str:
    """LLM 建议技能名清洗:小写 [a-z0-9_.],须含字母;不合 → ""(回落规则命名)。"""
    s = re.sub(r"[^a-z0-9_.]", "", str(raw or "").lower())[:48]
    return s if re.search(r"[a-z]", s) else ""


def _timeframe_cutoff(timeframe: str) -> float | None:
    """"上周"/"近 7 天" → 时间窗下界(epoch 秒);识别不了 → None(不过滤)。"""
    if re.search(r"上周|过去\s*7\s*天|近\s*7\s*天|last week", timeframe, re.IGNORECASE):
        return time.time() - 7 * 86400
    if re.search(r"昨天|yesterday", timeframe, re.IGNORECASE):
        return time.time() - 86400
    if re.search(r"今天|today", timeframe, re.IGNORECASE):
        return time.time() - 86400  # 近 24h 近似"今天"(不按日历日切,免时区坑)
    return None

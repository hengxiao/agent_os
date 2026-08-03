"""意图编排(docs/WEB-PLATFORM.md §5;方案 A:assistant 升级为 router)。

本期三个意图(规则路由;**LLM 意图路由留接口**——构造时注入 provider,
``_route_llm`` 是预留位,默认 None 走纯规则,见 §5 决策):

1. **"做个/写个 X 技能"** → plan 卡:分解(复用 closure/registry 搜已有技能
   + 建议新建),批准动作 = scaffold.approve(走既有 scaffold 端点);
2. **"为什么挂/失败"** → 最近失败 run 的 RCA 摘要 → table 卡;
3. **其他** → help 卡(三句引导)。

工具面红线:编排只**读**生产 registry 与 run 记录;写动作全部经卡片
action 白名单转发既有端点(docs/WEB-PLATFORM.md §7),不开新通道。
"""

from __future__ import annotations

import re
from typing import Any

from agent_os.host.web_platform.artifacts import (
    build_plan_card,
    build_table_card,
)
from agent_os.host.web_platform.sessions import new_message

#: 意图①关键词(规则路由;中文为主,英文兜底)
_MAKE_RE = re.compile(r"(做个|写个|建个|帮我做|帮我写|创建|新建).*(技能|skill)|^make .*skill", re.IGNORECASE)
#: 意图②关键词(失败排查)
_FAIL_RE = re.compile(r"(为什么|为啥).*(挂|失败|错)|失败|挂了|报错|fail", re.IGNORECASE)


class Orchestrator:
    """意图路由 + 编排(规则本期,LLM 留 provider 接口)。"""

    def __init__(
        self,
        *,
        skills: Any = None,
        runs_provider: Any = None,
        provider: Any = None,
    ) -> None:
        self._skills = skills  # 生产 SkillRegistry(只读:manifests())
        #: 最近 run 列表来源(注入:``() -> [{run_id, skill, status, error}]``;
        #: 隔离 app 装配细节,测试用假数据驱动
        self._runs_provider = runs_provider or (list)
        #: LLM 意图路由预留(docs/WEB-PLATFORM.md §5;None = 纯规则,本期默认)
        self._provider = provider

    def handle(self, session: dict[str, Any], text: str) -> dict[str, Any]:
        """用户意图 → agent 消息(文本 + 卡)。规则优先;LLM 路由留 ``_route_llm``。"""
        intent = self._route(text)
        if intent == "make_skill":
            return self._make_skill(text)
        if intent == "why_failed":
            return self._why_failed()
        return self._help()

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def _route(self, text: str) -> str:
        if self._provider is not None:
            return self._route_llm(text)  # 预留:LLM 路由(本期不启用)
        if _MAKE_RE.search(text):
            return "make_skill"
        if _FAIL_RE.search(text):
            return "why_failed"
        return "help"

    def _route_llm(self, text: str) -> str:
        """LLM 意图路由预留位(本期不落:规则已覆盖三个意图;接入时保持同一返回面)。"""
        if _MAKE_RE.search(text):
            return "make_skill"
        if _FAIL_RE.search(text):
            return "why_failed"
        return "help"

    # ------------------------------------------------------------------
    # 意图①:做个 X 技能 → plan 卡
    # ------------------------------------------------------------------

    def _make_skill(self, text: str) -> dict[str, Any]:
        topic = _topic_of(text)
        reuse = self._search_existing(topic)
        # 分解(规则骨架):有能复用的就复用,主技能建议新建——复用是默认动作,
        # 新建是补齐(docs/WEB-PLATFORM.md §5 意图①)
        create = [
            {
                "name": f"lab.{topic}" if topic else "lab.custom",
                "template": "prompt_query",
                "reason": "主技能:按意图生成首稿(模板可改)",
            }
        ]
        card = build_plan_card(
            goal=text,
            reuse=reuse,
            create=create,
            approve_payload={"name": create[0]["name"], "template": create[0]["template"]},
        )
        summary = (
            f"计划如下:复用 {len(reuse)} 个已有技能"
            + (f"({', '.join(r['name'] for r in reuse)})" if reuse else "")
            + f",新建 {create[0]['name']}。批准即生成首稿。"
        )
        return new_message("agent", text=summary, cards=[card])

    def _search_existing(self, topic: str) -> list[dict[str, str]]:
        """复用检索(规则版):名字/描述含主题词的生产技能(上限 3 个)。"""
        if self._skills is None or not topic:
            return []
        found = []
        try:
            manifests = self._skills.manifests()
        except Exception:  # noqa: BLE001 — registry 未装配时降级为"无可复用"
            return []
        for m in manifests:
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
                latest.get("error", "")[:120] or "(无错误摘要)",
            ]
        ]
        return new_message(
            "agent",
            text=f"最近一次失败是 {latest.get('skill')}(run {latest.get('run_id', '')[:8]}),摘要见下表。",
            cards=[build_table_card(title="最近失败 run", columns=["run", "skill", "错误摘要"], rows=rows)],
        )

    # ------------------------------------------------------------------
    # 意图③:其他 → help 卡(三句引导)
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

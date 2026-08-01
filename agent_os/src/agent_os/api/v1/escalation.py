"""升权契约(ESCALATION.md §2.1/§2.2/§3/§4;E1)。

三档信任层级按**副作用可逆性**分(不按主观"危险程度"):L1 ``none``(无副作用)
< L2 ``reversible``(可逆/可容忍)< L3 ``irreversible``(不可逆且不可容忍)。
tool 的档可声明(``ToolSpec.side_effect``,缺省按 Permission 推导);**skill 的档
只推导不声明**——自报会撒谎或过时,推导值永远反映真实权限面(白名单 tools/skills
取 max)。

升权事件 = 低档帧调用高档 skill(``tier_exceeds(target, caller)``);同档移动与
高档调低档(降权)都不确认。**帧 tier 的两种取法**:被调侧用完整推导档
(``derive_skill_tier``,tools+skills 递归取 max——薄 orchestrator 不能洗档);
子帧继承被调 skill 的完整推导档(§2.2:进入即继承整个已审信封,信封内同层
移动不再确认);**根帧用 ``derive_tools_tier``**(只看根 skill 自己的 tools)
——run 启动只确认了根技能的直接能力面,够更高档必须过闸;若根帧也用完整
推导档,任何白名单内的调用恒不升权,闸门成为死代码。
E1 只有 approve-once/deny;``Grant`` 数据类先定义,approve-run 的消费点是 E2。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .frames import SkillRef
from .skills import SkillManifest
from .tools import derive_side_effect

_log = logging.getLogger("agent_os.escalation")

__all__ = [
    "TIER_IRREVERSIBLE",
    "TIER_NONE",
    "TIER_REVERSIBLE",
    "EscalationRequest",
    "Grant",
    "derive_skill_tier",
    "derive_tools_tier",
    "tier_exceeds",
    "tier_rank",
]

#: 三档常量(§2.1)
TIER_NONE = "none"
TIER_REVERSIBLE = "reversible"
TIER_IRREVERSIBLE = "irreversible"

#: 三档全序:比较只凭本表,字符串本身无大小语义
_TIER_RANK = {TIER_NONE: 0, TIER_REVERSIBLE: 1, TIER_IRREVERSIBLE: 2}


def tier_rank(tier: str) -> int:
    """档的序数;未知档按最低(none)计——推导面之外的输入不放大权限。"""
    return _TIER_RANK.get(tier, 0)


def tier_exceeds(target: str, caller: str) -> bool:
    """升权判定(§2.2):``target`` 档严格高于 ``caller`` 档才构成升权事件。"""
    return tier_rank(target) > tier_rank(caller)


def _tool_tier(tools: Any, name: str) -> str:
    """白名单内单个工具的档;查不到(伪工具/未注册)按 none 计。

    伪工具(``ask_supervisor``/``python_orchestrate``)不进 Tool Registry:前者
    是确认通道本身,后者的副作用经 syscall 闸同帧白名单里的真实工具——那些工具
    已被本推导计入,不重复加码。
    """
    if tools is None or not hasattr(tools, "has") or not tools.has(name):
        return TIER_NONE
    return derive_side_effect(tools.get(name).spec)


def derive_tools_tier(manifest: SkillManifest, tools: Any = None) -> str:
    """skill 的**直接能力档**:只看自己的 ``permissions.tools``(不沿 skills 下传)。

    用途是**根帧 tier**:run 启动只确认了根技能的直接能力面(人启动 run 时
    并没有批准它能间接够到的整张图);够更高档必须过升权闸。若根帧也用
    完整推导档,则被白名单包含的任何调用恒有 ``tier(target) <= tier(caller)``,
    升权事件永不触发,闸门成为死代码。
    """
    best = TIER_NONE
    for name in manifest.permissions.tools:
        tier = _tool_tier(tools, name)
        if tier_rank(tier) > tier_rank(best):
            best = tier
    return best


def derive_skill_tier(
    manifest: SkillManifest,
    tools: Any = None,
    skills: Any = None,
    _visiting: frozenset[str] = frozenset(),
) -> str:
    """skill 推导档(§2.1)= 白名单 tools 与 skills 的最高一档(取 max)。

    ``skills`` 递归推导:加载期拓扑排序已拒绝循环依赖(local_file._topo_sort),
    ``_visiting`` 是独立使用本函数时的防环兜底。查不到的子技能按 none 计
    (引用存在性由加载期闸门保证,此处防御性跳过)。
    """
    best = TIER_NONE
    for name in manifest.permissions.tools:
        tier = _tool_tier(tools, name)
        if tier_rank(tier) > tier_rank(best):
            best = tier
    if skills is not None and tier_rank(best) < tier_rank(TIER_IRREVERSIBLE):
        for dep in manifest.permissions.skills:
            if dep in _visiting:
                continue
            try:
                sub = skills.get(SkillRef(name=dep)).manifest
            except Exception as e:  # noqa: BLE001 — 防御性跳过(存在性由加载期闸门保证)
                _log.debug("推导档跳过解析失败的子技能 %s: %r", dep, e)
                continue
            tier = derive_skill_tier(sub, tools, skills, _visiting | {dep})
            if tier_rank(tier) > tier_rank(best):
                best = tier
            if best == TIER_IRREVERSIBLE:
                break
    return best


@dataclass(frozen=True)
class Grant:
    """一次升权批准的落点(§4;E2 消费——approve-run 放行,本 run 内有效,随 run 死亡)。"""

    skill: str = ""
    tier: str = TIER_NONE  # 批准时被调 skill 的推导档(快照)
    scope: Literal["once", "run"] = "once"  # 对应 approve-once / approve-run
    decided_by: str = ""  # "user:web-inbox" | "user:cli" | ...
    decided_at: float = 0.0


@dataclass
class EscalationRequest:
    """升权确认请求(§3 原则 2;进 supervisor 通道,kind 区分于普通 question)。

    展示的 ``params`` 就是已通过 inputs schema 校验、将原样注入子帧的那份
    JSON——用户审的就是要执行的,不存在"审一套跑一套"(原则 1)。
    """

    kind: str = "escalation"
    question_id: str = ""
    run_id: str = ""
    frame_id: str = ""  # 发起升权的父帧
    skill: str = ""  # 被调 skill
    tier: str = TIER_NONE  # 被调 skill 的推导档
    params: dict[str, Any] = field(default_factory=dict)
    requested: dict[str, list[str]] = field(default_factory=dict)  # {"tools": [...], "skills": [...]}
    reason_hint: str = ""  # 机器生成:调用帧档 → 目标档,不是 LLM 写的
    options: list[str] = field(default_factory=lambda: ["approve-once", "deny"])
    asked_at: float = field(default_factory=time.time)

    def to_supervisor_args(self, caller: str) -> dict[str, Any]:
        """映射为 ``SupervisorManager.ask`` 的调用参数(复用 pending/超时/options 闭环)。

        结构化载荷走 ``context``(收件箱 pending 行原样直通);``question`` 是给
        人看的一行摘要,``kind`` 供宿主区分渲染。L3 标 high(收件箱优先呈现)。
        """
        return {
            "question_id": self.question_id,
            "kind": self.kind,
            "question": (
                f"升权确认:{caller} 帧({self.reason_hint})请求调用 {self.skill},"
                f"批准本次执行?"
            ),
            "context": {
                "skill": self.skill,
                "tier": self.tier,
                "params": self.params,
                "requested": self.requested,
                "reason_hint": self.reason_hint,
            },
            "options": list(self.options),
            "urgency": "high" if self.tier == TIER_IRREVERSIBLE else "normal",
        }

    def to_pending(self, call_id: str | None) -> dict[str, Any]:
        """落盘形态(``frame.context.working["_pending_escalation"]``,照 _pending_ask 先例)。

        checkpoint 随帧序列化;resume 凭 ``call_id`` 定位未配对的调用重问(§3 原则 3)。
        """
        return {
            "kind": self.kind,
            "question_id": self.question_id,
            "call_id": call_id,  # resume 结算时定位未配对的 skill 调用
            "skill": self.skill,
            "tier": self.tier,
            "params": self.params,
            "requested": self.requested,
            "reason_hint": self.reason_hint,
            "options": list(self.options),
            "asked_at": self.asked_at,
        }

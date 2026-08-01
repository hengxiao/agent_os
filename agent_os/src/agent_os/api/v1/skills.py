"""Skill 契约(DESIGN.md §2.1、§6.2;§14.1 冻结清单:manifest verifier 槽位、register() 签名)。

Skill = 统一执行单元,prompt / code 两种形态对内核透明;
子技能在父帧 LLM 眼里呈现为带类型签名的伪工具 ``skill.<name>``(§3.3)。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .frames import SkillFrame, SkillRef
from .memory import Provenance
from .tools import ToolSchema

__all__ = [
    "ContextPolicy",
    "ModelPolicy",
    "Skill",
    "SkillArtifact",
    "SkillCall",
    "SkillHandler",
    "SkillKind",
    "SkillLimits",
    "SkillManifest",
    "SkillPermissions",
    "SkillRegistry",
    "SkillSchema",
    "SkillTrust",
]


class SkillKind(Enum):
    """§2.1:``prompt | code``。code 技能由 Logic Kernel 执行(§9)。"""

    PROMPT = "prompt"
    CODE = "code"


@dataclass
class SkillPermissions:
    """能力白名单(§2.1):加载期静态校验 + 运行期逐次检查。"""

    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # 版本约束 ^/~ 语义(§6.1)
    blackboard: list[str] = field(default_factory=list)  # 可访问的黑板命名空间(§12)


@dataclass
class ModelPolicy:
    """§2.1 ``model:`` 块。``prefer`` 按序探测能力匹配(§4.2 模型路由)。"""

    prefer: list[str] = field(default_factory=list)
    temperature: float | None = None


@dataclass
class ContextPolicy:
    """§2.1 ``context_policy:`` 块。``compress``: off | truncate | spill | summarize | hierarchical。"""

    max_tokens: int | None = None  # 帧上下文软上限
    compress: str = "hierarchical"


@dataclass
class SkillLimits:
    """§2.1 ``limits:`` 块;``retry`` = 整帧重跑上限(§3.2,幂等性由调用方保证)。"""

    max_steps: int | None = None
    timeout: float | None = None
    retry: int = 0
    #: 单次编排/沙箱执行的 syscall 上限(CODE-ORCHESTRATION.md §4;None 用内核默认)
    max_tool_calls: int | None = None


@dataclass
class SkillTrust:
    """``trust:`` 块(ESCALATION.md §2.1;additive):确认策略覆盖项。

    tier **不**在本块声明——skill 的档由权限面推导(白名单 tools/skills 取 max),
    作者只能上调确认强度,不能把自己说低。
    """

    confirm: str | None = None  # always | first;缺省按推导档(L2→first,L3→always)
    reversal: str | None = None  # L2 的逆转/补偿机制(TIER-STANDARDS §4;必填 lint 属 E3)
    blast_radius: str | None = None  # L3 的最坏影响面(TIER-STANDARDS §5;必填 lint 属 E3)


@dataclass
class SkillManifest:
    """Skill 清单(§2.1,字段逐字,含 ``verifier`` 冻结槽位)。

    ``logic`` 槽位:§9.2 ``logic: {mode: sandbox}`` —— code 技能主动提升隔离等级。
    """

    name: str = ""
    version: str = ""
    kind: SkillKind = SkillKind.PROMPT
    description: str = ""  # 应写成路由规则:"Use when / Do not use when" + 负例(§6.1 lint)
    inputs: dict[str, Any] = field(default_factory=dict)  # JSON Schema
    outputs: dict[str, Any] = field(default_factory=dict)  # JSON Schema
    verifier: str | None = None  # 可选:语义验证器(code 技能),经 Logic Kernel 确定性执行
    permissions: SkillPermissions = field(default_factory=SkillPermissions)
    model: ModelPolicy | None = None
    context_policy: ContextPolicy | None = None
    limits: SkillLimits | None = None
    entry: str | None = None  # prompt 技能的指令体文件;code 技能为 handler.py:run
    prompt: str | None = None  # 单文件形态(§6.3 skills.yaml)内联提示词模板
    handler: str | None = None  # 单文件形态 code 技能入口(dotted path,如 "my_skills.handlers:run")
    logic: dict[str, Any] | None = None  # {"mode": "trusted" | "sandbox"}(§9.2)
    inline: bool = False  # 预展开(merge):prompt 并入调用方 SYSTEM,不生成伪工具(SKILL-INLINING.md)
    trust: SkillTrust | None = None  # 升权确认策略覆盖项(ESCALATION.md §2.1;缺省按推导档)


#: code 技能入口签名(§6.3):``async def run(input, ctx)``
SkillHandler = Callable[[dict[str, Any], Any], Coroutine[Any, Any, Any]]


@dataclass
class Skill:
    """物化后的 Skill 对象(§6.1 materialize 产物):清单 + 指令体/代码入口。"""

    manifest: SkillManifest = field(default_factory=SkillManifest)
    prompt: str | None = None  # prompt 技能指令体(渲染前模板)
    handler: SkillHandler | None = None  # code 技能入口协程

    @property
    def ref(self) -> SkillRef:
        return SkillRef(name=self.manifest.name, version=self.manifest.version)


@dataclass
class SkillSchema:
    """伪工具 schema(§3.3):``skill.<name>``,parameters 即该 Skill 的 ``inputs``。"""

    name: str = ""
    description: str = ""
    parameters: ToolSchema = field(default_factory=dict)


@dataclass
class SkillCall:
    """一次子技能调用(内核在分发阶段拦截 ``skill.*`` 转交 Skill 子系统,§3.3)。"""

    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass
class SkillArtifact:
    """``register()`` 的入参(§6.2):运行期生成的技能产物,默认不信任。"""

    manifest: SkillManifest = field(default_factory=SkillManifest)
    prompt: str | None = None
    code: str | None = None  # code 技能源码文本


@runtime_checkable
class SkillRegistry(Protocol):
    """§6.2 契约(含冻结的 ``register()`` 签名)。"""

    def get(self, ref: SkillRef) -> Skill: ...

    def visible_to(self, frame: SkillFrame) -> list[SkillSchema]:
        """按帧 manifest 白名单生成伪工具 schema。"""
        ...

    def make_frame(self, call: SkillCall, parent: SkillFrame) -> SkillFrame: ...

    async def register(self, artifact: SkillArtifact, provenance: Provenance) -> SkillRef:
        """运行期写入路径(自我进化供给侧):信任管线 + 入库前验证门(§6.2)。"""
        ...

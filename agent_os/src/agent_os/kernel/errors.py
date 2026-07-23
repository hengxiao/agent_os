"""内核错误类型(DESIGN.md §3.2 错误处理与恢复工程)。

故障致死性分层:工具/技能失败转为错误观察交给 LLM 恢复;预算/强停类硬失败
(:class:`RunAborted` 及其子类)与 :class:`MaxDepthExceeded` 不可被单帧吞掉,
沿调用栈一路弹到 Run 边界。
"""

from __future__ import annotations

__all__ = [
    "AgentOSError",
    "BudgetExceeded",
    "MaxDepthExceeded",
    "OutputValidationError",
    "RunAborted",
    "SkillLoadError",
    "ToolDispatchError",
]


class AgentOSError(Exception):
    """Agent OS 全部内核错误的基类。"""


class RunAborted(AgentOSError):
    """预算/强停类硬失败(§3.2):不可被单帧吞掉,一路弹栈到 Run 边界。"""


class BudgetExceeded(RunAborted):
    """超 RunConfig 预算(§3.1 步骤 7):语义上就是 run 中止,故继承 RunAborted。"""


class MaxDepthExceeded(AgentOSError):
    """递归深度兜底(§3.2 恢复环路语义):超 RunConfig.max_depth,硬失败上抛。"""


class OutputValidationError(AgentOSError):
    """最终答案连败 N 次未过 outputs schema 校验 → 判帧失败(§3.1 步骤 5)。"""


class SkillLoadError(AgentOSError):
    """技能加载期/寻址期错误(§6.1):清单非法、引用缺失、循环依赖、输入不合 schema。"""


class ToolDispatchError(AgentOSError):
    """工具分发流水线结构性错误(§8.1);常规失败走 ToolResult,不抛本异常。"""

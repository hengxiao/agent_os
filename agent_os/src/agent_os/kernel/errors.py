"""内核错误类型(docs/DESIGN.md §3.2 错误处理与恢复工程)。

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
    "RunPaused",
    "SkillLoadError",
    "SubtreeCancelled",
    "ToolDispatchError",
]


class AgentOSError(Exception):
    """Agent OS 全部内核错误的基类。"""


class RunAborted(AgentOSError):
    """预算/强停类硬失败(§3.2):不可被单帧吞掉,一路弹栈到 Run 边界。"""


class RunPaused(RunAborted):
    """可恢复挂起(docs/DESIGN.md :940;RunControl.pause / Pause verdict 触发)。

    继承 RunAborted:传播语义与硬失败一致(不可被单帧吞掉、沿调用栈弹到 Run
    边界);Run 边界特判落 ``RunStatus.PAUSED`` 而非 ABORTED、发 ``run.paused``
    信号(不发 ``run.aborted``),宿主 finalize 照常落 checkpoint,经
    ``Kernel.resume`` 恢复。理由原样上抛,不再拼 ``"paused: "`` 前缀。
    """


class BudgetExceeded(RunAborted):
    """超 RunConfig 预算(§3.1 步骤 7):语义上就是 run 中止,故继承 RunAborted。"""


class SubtreeCancelled(AgentOSError):
    """子树级联取消(RunControl.cancel_frame):目标帧及其后代进入终态,**不**中止 run。

    语义分界:run stop(:class:`RunAborted`)杀整个 run;subtree cancel 只终结
    目标子树——在册后台帧被 ``task.cancel()``(CancelledError 走 §3.1 中断配对
    路径),调用链上的 prompt 帧在 ``pre:step`` safe point 抛本异常;``wait_frame``
    对被取消的后台帧原样上抛本异常,交 code 技能处理(可捕获恢复;未捕获则该
    code 帧失败,仍不殃及 run)。故意不继承 RunAborted:分支/子树取消不得触发
    run 级中止状态。
    """


class MaxDepthExceeded(AgentOSError):
    """递归深度兜底(§3.2 恢复环路语义):超 RunConfig.max_depth,硬失败上抛。"""


class OutputValidationError(AgentOSError):
    """最终答案连败 N 次未过 outputs schema 校验 → 判帧失败(§3.1 步骤 5)。"""


class SkillLoadError(AgentOSError):
    """技能加载期/寻址期错误(§6.1):清单非法、引用缺失、循环依赖、输入不合 schema。"""


class ToolDispatchError(AgentOSError):
    """工具分发流水线结构性错误(§8.1);常规失败走 ToolResult,不抛本异常。

    ``hint``(§W0-3):code 技能经 ``SkillError`` 抛出的下一步动作建议在此续传,
    由 ``_invoke_skill`` 折进父帧错误观察——否则跨帧时又被压扁成一句人话。
    """

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

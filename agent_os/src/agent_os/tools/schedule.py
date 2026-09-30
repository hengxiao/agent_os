"""``system.schedule.set`` 跨 run 自动派生工具(D3;§17-3 宿主调度器的工具喂入源)。

模型在 run 内登记"稍后/定点派生一个新 run":条目落宿主持久调度表
(``<artifacts_root>/schedule.json``,host/shared/scheduler.py),到点由宿主
调度器经 ``RunManager.dispatch_event`` 无 target 通道起新 run(``skill`` +
``input``)。``confirm=True``——派生新 run 是开新工作单元,人工确认是增殖
刹车(§6.2 默认不信任);WRITE 档(登记持久条目是对 run 外的副作用)。

服务装配:宿主经 ``LocalPythonToolRegistry.bind_schedule(service)`` 注入
store-backed 服务(web 宿主在 ``[schedule]`` 段启用时由 ``RunManager.
start_scheduler`` 装配,同 bind_timer 先例);CLI/未绑嵌入方调用 → 结构化
"未装配"错误(NOT_FOUND,同 ``skill_register_tool`` 先例)。与
``system.timer.set`` 的分工:timer 往**本帧**注入提醒消息(不起新 run),
schedule.set **起新 run**(本 run 结束后依然成立,跨 run/跨重启)。
"""

from __future__ import annotations

import time
from typing import Any

from agent_os.api.v1 import (
    Permission,
    Tool,
    ToolContext,
    ToolError,
    ToolErrorKind,
    ToolResult,
)
from agent_os.tools.local_registry import _FunctionTool, derive_spec


def _schedule_not_assembled() -> ToolResult:
    """未装配 schedule 服务的结构化错误(同 timer/skill_register "未装配"先例,§2.3)。"""
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.NOT_FOUND,
            message="schedule 服务未装配",
            retryable=False,
            hint="跨 run 派生需要宿主启用调度器:web 宿主配置 [schedule] 段后由 "
            "RunManager.start_scheduler 装配 store-backed 服务(bind_schedule 注入);"
            "本 run 内提醒请用 system.timer.set",
        ),
    )


def _invalid(message: str, hint: str) -> ToolResult:
    """参数非法的结构化错误(INVALID_ARGS + 可执行 hint,§W0-3)。"""
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.INVALID_ARGS,
            message=message,
            retryable=False,
            hint=hint,
        ),
    )


def schedule_set_tool(*, name: str = "system.schedule.set", registry: Any) -> Tool:
    """构造 ``system.schedule.set``(D3 跨 run 自动派生;WRITE·confirm=True)。

    工具本身**登记即返回**(调度在宿主调度器,不占 spec.timeout 等待语义——
    同 timer_set 先例);调度服务由 registry 持有(``bind_schedule`` 注入),
    未装配 → NOT_FOUND。
    """

    async def schedule_set(
        skill: str,
        input: dict[str, Any] | None = None,
        delay_seconds: float | None = None,
        at: float | None = None,
        note: str = "",
        ctx: ToolContext | None = None,
    ) -> dict[str, Any] | ToolResult:
        """安排一个未来 run:到点由宿主调度器以 ``skill`` + ``input`` 起新 run,立即返回 {schedule_id, fire_at}。

        Use when 需要跨 run 自动派生——把"稍后/定点再做"的事登记为持久调度
        (如明早 9 点跑日报技能、10 分钟后用另一技能复查某事),本 run 结束后
        依然成立、进程重启不丢;Do not use when 只是本 run 内稍后提醒(用
        system.timer.set——它往本帧注入消息,不起新 run)或能就地等结果(直接
        调技能更便宜)。``delay_seconds``(相对秒数)与 ``at``(epoch 秒绝对
        时刻)二选一且须为正;``input`` 缺省 {}(技能有必填输入时必须给足,
        否则到点起 run 会因校验失败被丢弃);``note`` 随条目落档(观测/对账)。
        每次调用都会挂起等人工确认(confirm)——派生新 run 是开新工作单元
        (§6.2 默认不信任)。宿主未启用调度器([schedule] 段)→ NOT_FOUND。
        """
        service = getattr(registry, "_schedule", None)
        if service is None:
            return _schedule_not_assembled()
        if not skill:
            return _invalid(
                "skill 必填非空(派生 run 跑哪个技能)",
                "给出已存在技能的点分层级名;可先 system.skill.search 检索",
            )
        if (delay_seconds is None) == (at is None):
            return _invalid(
                "delay_seconds 与 at 须给其一(二选一)",
                "相对延迟传 delay_seconds(如 600);绝对时刻传 at(epoch 秒)",
            )
        if delay_seconds is not None and delay_seconds <= 0:
            return _invalid(
                f"delay_seconds 须 > 0(收到 {delay_seconds})",
                "传未来的相对秒数;要立即执行请直接 invoke 技能,不走调度",
            )
        if at is not None and at <= 0:
            return _invalid(
                f"at 须 > 0(epoch 秒;收到 {at})",
                "传未来的 epoch 秒绝对时刻(system.time.now 可取当前时刻折算)",
            )
        fire_at = time.time() + float(delay_seconds) if delay_seconds is not None else float(at)
        return service.set(
            skill=skill,
            input=dict(input or {}),
            fire_at=fire_at,
            note=note,
            via_run_id=ctx.run_id if ctx is not None else None,
            principal=ctx.principal if ctx is not None else None,
        )

    spec = derive_spec(
        schedule_set,
        name=name,
        # WRITE 档(登记持久调度条目是对 run 外的副作用;同 timer_set 的分档理由)
        permission=Permission.WRITE,
        # 登记即返回(调度在宿主),默认 timeout 已足够(同 timer_set 先例)
        timeout=30.0,
        # 派生新 run = 开新工作单元:confirm=True 过内核 tool-confirm 闸门,
        # 人工确认是增殖刹车(§6.2 默认不信任;同 system.skill.register 先例)
        confirm=True,
        cost_hint="~1ms(登记即返回;调度在宿主)",
    )
    return _FunctionTool(schedule_set, spec)

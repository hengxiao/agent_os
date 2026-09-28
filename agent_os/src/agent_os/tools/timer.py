"""``system.timer.set`` 内核原语工具与 TimerService(WS1;docs/DESIGN.md §8.3 扩展)。

TimerService:asyncio 任务表 ``{timer_id: task}``,按 run_id 分桶;到点经注入
回调(``RunControl.inject_message``,``Role.USER``/``Source.INJECTED``)向目标帧
投递 ``[timer 到点] {note}`` 消息;帧/run 终态 → 静默弃(log);run 收尾
(``release_run``,runner ``_release_run`` 经 registry 调用)取消该 run 全部
定时器。定时器是**进程态**:不随 checkpoint 持久化,进程重启即丢(resume 不会
复活计时——一次性/周期提醒属易失运行时状态,要不要重建由技能自行决定)。

``sleep`` 可注入(缺省 ``asyncio.sleep``;测试用假钟快进,同 StallDetector 注入
``clock`` 先例,sidecars/builtins.py)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from agent_os.api.v1 import (
    FrameStatus,
    Message,
    Permission,
    Role,
    Source,
    Tool,
    ToolContext,
    ToolError,
    ToolErrorKind,
    ToolResult,
)
from agent_os.tools.local_registry import _FunctionTool, derive_spec

_log = logging.getLogger("agent_os.tools")

#: delay/interval 下限(秒;防抖——过密计时等同 busy-loop,低于下限钳到本值)
_MIN_SECONDS = 0.5

#: 终态帧集合:到点时帧已终态 → 消息静默弃(注入终态帧的 context 无人再读)
_TERMINAL = (FrameStatus.DONE, FrameStatus.FAILED)


class TimerService:
    """计时器调度表(WS1):``{timer_id: asyncio.Task}`` + 按 run_id 分桶。

    fire 注入通道(``ctl``,RunControl 契约)由 KernelBuilder 在 build 后 bind
    (同 ``bind_user_channel`` 先例);未 bind 时工具侧拦 NOT_FOUND,service 本身
    只防御式丢弃(``bound`` 供工具探测)。
    """

    def __init__(self, sleep: Any = None) -> None:
        self._sleep = sleep or asyncio.sleep
        self._ctl: Any = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._by_run: dict[str, set[str]] = {}

    @property
    def bound(self) -> bool:
        """fire 注入通道已装配(ctl 在场)。"""
        return self._ctl is not None

    @property
    def tasks(self) -> dict[str, asyncio.Task]:
        """全部在册计时器(timer_id → task;测试观测点)。"""
        return self._tasks

    def bind(self, ctl: Any) -> None:
        """装配钩子(KernelBuilder 在 kernel/ctl 就位后调用):注入 fire 的帧消息注入通道。"""
        self._ctl = ctl

    def set(
        self,
        *,
        run_id: str,
        frame_id: str,
        delay_seconds: float | None = None,
        interval_seconds: float | None = None,
        count: int | None = None,
        note: str = "",
    ) -> str | ToolResult:
        """登记计时器并立即返回 timer_id;参数非法 → ToolResult(INVALID_ARGS)。

        one-shot = ``delay_seconds``;recurring = ``interval_seconds``(``count``
        缺省无限,run 收尾自动取消);两者二选一,缺/并给都是 INVALID_ARGS;
        delay/interval 按下限 ``_MIN_SECONDS`` 钳制(防抖)。
        """
        if delay_seconds is None and interval_seconds is None:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INVALID_ARGS,
                    message="delay_seconds 与 interval_seconds 须给其一",
                    retryable=False,
                    hint="一次性提醒传 delay_seconds;周期复查传 interval_seconds(可配 count 限定次数)",
                ),
            )
        if delay_seconds is not None and interval_seconds is not None:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INVALID_ARGS,
                    message="delay_seconds 与 interval_seconds 二选一,不能同时给",
                    retryable=False,
                    hint="one-shot 用 delay_seconds;recurring 用 interval_seconds(+count)",
                ),
            )
        if count is not None and count < 1:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INVALID_ARGS,
                    message=f"count 须 >= 1(收到 {count})",
                    retryable=False,
                    hint="限定触发次数传正整数;不限次数省略 count",
                ),
            )
        delay = max(_MIN_SECONDS, float(delay_seconds)) if delay_seconds is not None else None
        interval = (
            max(_MIN_SECONDS, float(interval_seconds)) if interval_seconds is not None else None
        )
        timer_id = uuid.uuid4().hex[:12]
        task = asyncio.get_running_loop().create_task(
            self._run(timer_id, run_id, frame_id, delay, interval, count, note)
        )
        self._tasks[timer_id] = task
        self._by_run.setdefault(run_id, set()).add(timer_id)
        return timer_id

    def release_run(self, run_id: str) -> None:
        """run 收尾(registry ``release_run`` 调用):取消本 run 全部在册计时器并清空分桶。

        只动本 run 桶(共享 kernel 的并发 run 不得跨 run 误杀,同后台帧回收先例);
        取消是静默的——任务侧 ``CancelledError`` 直接传播,不再 fire。
        """
        for timer_id in self._by_run.pop(run_id, set()):
            task = self._tasks.pop(timer_id, None)
            if task is not None and not task.done():
                task.cancel()

    async def _run(
        self,
        timer_id: str,
        run_id: str,
        frame_id: str,
        delay: float | None,
        interval: float | None,
        count: int | None,
        note: str,
    ) -> None:
        """计时器本体:睡到点 → fire;recurring 按 count 结算(缺省无限),帧终态即停。"""
        try:
            if delay is not None:  # one-shot
                await self._sleep(delay)
                await self._fire(timer_id, frame_id, note, 1, None)
            else:  # recurring
                fired = 0
                while count is None or fired < count:
                    await self._sleep(interval)
                    fired += 1
                    if not await self._fire(timer_id, frame_id, note, fired, count):
                        break  # 帧终态:本次静默弃,后续触发一并停止
        except asyncio.CancelledError:
            raise  # run 收尾取消:静默退出(§3.1 取消传播)
        except Exception:  # noqa: BLE001 — 后台计时任务不得把异常漏进事件循环
            _log.exception("timer %s 触发异常,计时器终止", timer_id)
        finally:
            self._tasks.pop(timer_id, None)
            bucket = self._by_run.get(run_id)
            if bucket is not None:
                bucket.discard(timer_id)
                if not bucket:
                    self._by_run.pop(run_id, None)

    async def _fire(
        self, timer_id: str, frame_id: str, note: str, index: int, count: int | None
    ) -> bool:
        """到点投递:帧终态/消失 → log 并返回 False(调用方停止 recurring);否则注入并返回 True。"""
        ctl = self._ctl
        if ctl is None:  # 工具侧已拦 NOT_FOUND,此处纯防御
            _log.warning("timer %s 到点但 ctl 未装配,消息被丢弃", timer_id)
            return False
        kernel = getattr(ctl, "_kernel", None)
        if kernel is not None:
            frame = kernel.stack.get(frame_id)
            if frame is None or frame.status in _TERMINAL:
                _log.info("timer %s 到点但帧 %s 已终态/不存在,消息静默丢弃", timer_id, frame_id)
                return False
        suffix = f"第 {index} 次" + (f"/共 {count} 次" if count else "")
        await ctl.inject_message(
            frame_id,
            Message(
                role=Role.USER,
                content=f"[timer 到点] {note}(timer_id={timer_id},{suffix})",
                source=Source.INJECTED,
            ),
        )
        return True


def _timer_not_assembled() -> ToolResult:
    """未装配 timer 服务的结构化错误(同 user 通道"未装配"报 NOT_FOUND 先例,§2.3)。"""
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.NOT_FOUND,
            message="timer 服务未装配",
            retryable=False,
            hint="KernelBuilder 装配内核时会自动 bind fire 注入通道;直接持有 registry 的嵌入方需经 bind_timer(ctl) 注入后再用",
        ),
    )


def timer_set_tool(*, name: str = "system.timer.set", registry: Any) -> Tool:
    """构造 ``system.timer.set``(WS1):安排一次性/周期计时器,到点向本帧注入提醒消息。

    工具本身**立即返回 timer_id**(计时在后台 asyncio 任务里跑,不占 spec.timeout
    的等待语义——无需放宽 timeout);TimerService 由 registry 持有,fire 注入通道
    (ctl)由 KernelBuilder 装配时 bind(同 bind_user_channel 先例;未 bind → NOT_FOUND)。
    """

    async def timer_set(
        delay_seconds: float | None = None,
        interval_seconds: float | None = None,
        count: int | None = None,
        note: str = "",
        ctx: ToolContext | None = None,
    ) -> dict[str, Any] | ToolResult:
        """安排一个计时器:到点把 ``[timer 到点] {note}`` 注入调用帧上下文,立即返回 timer_id。

        Use when 需要定时复查/周期轮询兜底(如 N 秒后回看后台任务进展);
        Do not use when 能就地等到结果(直接等工具返回)——计时器只在
        "现在没结果、稍后要看"时有意义。one-shot 传 ``delay_seconds``;周期传
        ``interval_seconds``(``count`` 缺省 = 无限,run 收尾自动取消);两者
        二选一,下限钳制 0.5s(防抖)。宿主未装配 timer 服务 → NOT_FOUND。
        """
        service = getattr(registry, "_timers", None)
        if service is None or not service.bound:
            return _timer_not_assembled()
        run_id = ctx.run_id if ctx is not None else ""
        frame_id = ctx.frame_id if ctx is not None else ""
        result = service.set(
            run_id=run_id,
            frame_id=frame_id,
            delay_seconds=delay_seconds,
            interval_seconds=interval_seconds,
            count=count,
            note=note,
        )
        if isinstance(result, ToolResult):
            return result
        return {
            "timer_id": result,
            "run_id": run_id,
            "frame_id": frame_id,
            # 回显生效中的调度(钳制后的值),模型据此知道实际节奏
            "delay_seconds": max(_MIN_SECONDS, float(delay_seconds))
            if delay_seconds is not None
            else None,
            "interval_seconds": max(_MIN_SECONDS, float(interval_seconds))
            if interval_seconds is not None
            else None,
            "count": count,
        }

    spec = derive_spec(
        timer_set,
        name=name,
        # WRITE 档(不取 READ):登记后台任务是对 run 的副作用,READ⇒cacheable+
        # concurrent_safe 红利不成立;WRITE 起占帧白名单(§W0-1)
        permission=Permission.WRITE,
        # 工具立即返回(计时在后台),默认 timeout 已足够,不为计时放宽(§8.1)
        timeout=30.0,
        cost_hint="~1ms(立即返回;计时在后台)",
    )
    return _FunctionTool(timer_set, spec)

"""MotionPlayer(docs/TUI-DOC.md §3/§6;对译 godot/kernel/motion_player.gd)。

具名动效播放层(主题契约 §2.3):组件只调用具名动效,档位由主题档案给;
reduced-motion 全局强制 Instant。终端实现 = 定时器重绘(§6):
focus-pulse = 反色闪烁 N 次;其余具名 T1 先注册名字、实现留 instant
(允许存在,无播放体——里程碑 T5 补齐)。

与 godot 原型的差异:target 不是 Control,而是 (widget, region) 记录;
播放 = 记一条脉冲(peek 期数),渲染层每帧查 active_regions() 给区域反色,
帧推进由主循环/测试驱动(tick)。动画只随成功播放(先管道,后盖章)不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_os.host.tui.tui.theme import MOTION_FULL, MOTION_INSTANT, ThemeRegistry

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.widget import Widget
    from agent_os.host.tui.tui.cells import Region

#: 终端实现的脉冲节奏(每亮/灭期持续的帧数;主循环每帧 tick 一次)
PULSE_TICKS = 2


class _Pulse:
    """一次 focus-pulse 的进行中状态:目标区域 + 剩余帧数(亮灭交替)。"""

    __slots__ = ("region", "target", "ticks_left")

    def __init__(self, target: Widget, region: Region, times: int) -> None:
        self.target = target
        self.region = region
        self.ticks_left = times * 2 * PULSE_TICKS  # 亮灭各算一期,起帧即亮

    @property
    def on(self) -> bool:
        return (self.ticks_left // PULSE_TICKS) % 2 == 0


class _Stamp:
    """stamp-press(T3 真实现):状态栏印章行反色定格 ~300ms(帧数 = 时长 /
    帧间隔;seal 色 + 文字章,双编码)。动画只随成功播(先管道,后盖章)。"""

    __slots__ = ("text", "ticks_left")

    def __init__(self, text: str, ticks: int) -> None:
        self.text = text
        self.ticks_left = ticks


class MotionPlayer:
    """具名+档位;T1 实现 focus-pulse(反色闪烁),instant 档直接不记帧。"""

    def __init__(self, clock: Any = None) -> None:
        self._pulses: list[_Pulse] = []
        self._stamps: list[_Stamp] = []
        self._clock = clock  # 保留:档位/节奏参数化口(测试可注入假钟)

    # ------------------------------------------------------------------

    def play(self, motion_name: str, target: Widget, region: Region | None = None,
             text: str = "") -> None:
        """组件调名字,实现归主题。T3:focus-pulse(反色闪烁)与
        stamp-press(状态栏印章定格,``text`` = 章文)真实现;D5 调试器具名:
        paused(= 暂停行脉冲,focus-pulse 同档)、run-done(= 终态定格章,
        stamp-press 同档)、bp-hit(断点行短闪,SUBTLE 1 次 / FULL 2 次);
        其余具名 instant。
        region 缺省 = 播放时向 target 要(``pulse_region()`` 约定:渲染期
        记录自己当前区域,脉冲跟布局走,不冻结在 play 时刻)。"""
        if target is None:
            return
        level = MOTION_FULL
        current = ThemeRegistry.current()
        if current is not None:
            level = current.motion_of(motion_name)
        if ThemeRegistry.reduced_motion:
            level = MOTION_INSTANT
        if level == MOTION_INSTANT:
            return
        if motion_name in ("focus-pulse", "paused"):
            times = 3 if level == MOTION_FULL else 2
            self._pulses.append(_Pulse(target, region, times))
        elif motion_name in ("stamp-press", "run-done"):
            # ~300ms 定格:FULL 给足,SUBTLE 减半;帧数 = 时长 / 帧间隔
            ticks = 5 if level == MOTION_FULL else 3
            self._stamps.append(_Stamp(text, ticks))
        elif motion_name == "bp-hit":
            # 断点行短闪(常驻事件,克制:SUBTLE 一亮一灭 / FULL 两回合)
            times = 2 if level == MOTION_FULL else 1
            self._pulses.append(_Pulse(target, region, times))
        # 其余具名(change-flash/text-reveal/…):注册名,实现留 instant(不记帧)

    def allowed(self, motion_name: str) -> bool:
        """手感闸:本动效当前是否允许播放(reduced-motion / 主题 instant 档直接否)。"""
        if ThemeRegistry.reduced_motion:
            return False
        current = ThemeRegistry.current()
        if current is None:
            return True
        return current.motion_of(motion_name) != MOTION_INSTANT

    # ------------------------------------------------------------------
    # 帧驱动(主循环每帧 tick;测试显式驱动等价物)
    # ------------------------------------------------------------------

    def active_regions(self) -> list[tuple[Widget, Region]]:
        """本帧反色区清单(渲染层据此 reverse_region;脉冲"亮"期的区域)。
        区域惰性向 target 要(pulse_region() 约定),跟布局走。"""
        out: list[tuple[Widget, Region]] = []
        for p in self._pulses:
            if not p.on:
                continue
            reg = p.region
            if reg is None:
                fn = getattr(p.target, "pulse_region", None)
                reg = fn() if callable(fn) else None
            if reg is not None:
                out.append((p.target, reg))
        return out

    def tick(self) -> bool:
        """推进一帧;返回是否仍有进行中动效(主循环据此决定是否排下一帧重绘)。"""
        alive: list[_Pulse] = []
        for p in self._pulses:
            p.ticks_left -= 1
            if p.ticks_left > 0:
                alive.append(p)
        self._pulses = alive
        stamps: list[_Stamp] = []
        for s in self._stamps:
            s.ticks_left -= 1
            if s.ticks_left > 0:
                stamps.append(s)
        self._stamps = stamps
        return bool(self._pulses) or bool(self._stamps)

    def active_stamps(self) -> list[str]:
        """本帧定格中的印章文本(渲染层画状态栏印章行;定格期恒亮不闪)。"""
        return [s.text for s in self._stamps]

    def busy(self) -> bool:
        return bool(self._pulses) or bool(self._stamps)

    def cancel_all(self) -> None:
        self._pulses.clear()
        self._stamps.clear()

"""``system.monitor.set`` / ``system.channel.connect`` 内核原语工具与 MonitorService
(ch04 Event-Triggered 三件套之二三,docs/reports/ch04-tools.md ``monitor_shell``/
``connect_channel``;生命周期与 TimerService 逐行同构,见 tools/timer.py)。

MonitorService:asyncio 任务表 ``{monitor_id: task}``,按 run_id 分桶;事件命中
经注入回调(``RunControl.inject_message``,``Role.USER``/``Source.INJECTED``)向
目标帧投递 ``[monitor 命中] {line}`` / ``[channel 命中] {line}`` 消息;帧/run
终态 → 静默弃(log);run 收尾(``release_run``)取消该 run 全部监控(shell 源
进程组同步 SIGKILL)。监控规格**随帧持久化**(``frame.context.working["_monitors"]``,
随 checkpoint 落档):pause/断电只取消进程内任务、规格保留,resume 结算序列
(``Kernel._settle_pending_monitors``)经 ``rearm_from_working`` 重武装。

两种事件源共用同一张表/同一注入纪律:

- shell:``asyncio.create_subprocess_shell`` 起子进程(stdout/stderr 合并逐行读,
  ``start_new_session`` 独立进程组),正则命中即注入;``max_fires`` 到顶注入封顶
  事件并终止进程组;进程退出注入退出事件(带 exit_code)并标规格 done。杀进程
  纪律复用 tools/builtins.py 的 ``_kill``(进程组 SIGKILL,原因见其注释);
  resume 重武装 = **从头重跑命令**(输出重放可能重复命中——replay caveat,
  见 ``rearm_from_working`` docstring);
- channel:轮询事件文件(size/mtime 变化),从持久化 ``offset`` 续读新增完整行;
  rotation(size < offset)→ offset=0 从文件头续读并记 log 一次;resume 从
  ``offset`` 续读,**不重放**(offset 持久化是不错读/不重读的锚)。

``sleep``/``spawn`` 可注入(缺省 ``asyncio.sleep``/``asyncio.create_subprocess_shell``;
测试用假钟控轮询节拍、假进程喂行,同 TimerService 注入 ``sleep`` 先例)。

纯函数出口(单点构造,进程内任务与宿主补投字节一致,同 timer 先例):
``format_match_text``/``format_exit_text``/``format_cap_text``。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
import uuid
from typing import Any

from agent_os.api.v1 import (
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
from agent_os.tools.builtins import _kill
from agent_os.tools.local_registry import _FunctionTool, derive_spec, resolve_work_path
from agent_os.tools.timer import _MIN_SECONDS, _TERMINAL

_log = logging.getLogger("agent_os.tools")

#: 命中注入行文本截断上限(超长行截到本值,防单行刷爆上下文)
_LINE_MAX_CHARS = 200

#: 事件源标签(kind → 注入文本前缀):shell 源 = monitor,channel 源 = channel
_TAGS = {"shell": "monitor", "channel": "channel"}


def format_match_text(kind: str, monitor_id: str, line: str, fired: int, max_fires: int) -> str:
    """``[monitor 命中]`` / ``[channel 命中]`` 注入文本的唯一构造点(行截断 200 字符)。"""
    tag = _TAGS.get(kind, "monitor")
    return f"[{tag} 命中] {line[:_LINE_MAX_CHARS]}(monitor_id={monitor_id},第 {fired} 次/上限 {max_fires})"


def format_exit_text(monitor_id: str, exit_code: int, fired: int) -> str:
    """``[monitor 退出]`` 进程退出事件文本的唯一构造点(shell 源专有;channel 无进程可退)。"""
    return f"[monitor 退出] 进程结束(exit_code={exit_code})(monitor_id={monitor_id},共触发 {fired} 次)"


def format_cap_text(kind: str, monitor_id: str, max_fires: int) -> str:
    """``[monitor 达上限]`` / ``[channel 达上限]`` 封顶事件文本的唯一构造点(到顶即停)。"""
    tag = _TAGS.get(kind, "monitor")
    return f"[{tag} 达上限] 触发次数达上限 {max_fires},监控终止(monitor_id={monitor_id})"


def _poll_new_lines(path: str, offset: int) -> tuple[list[str], int, bool]:
    """轮询读取本体(同步,经 ``asyncio.to_thread`` 跑):返回 ``(新增完整行, 新 offset, 是否轮换)``。

    rotation(size < offset)→ 从 0 续读;末尾残行**不消费**(offset 回指残行
    起点,下轮补全后再出,防半行误判);字节级 offset(decode 前先按 b"\\n" 切,
    errors="replace" 不破坏字节计数)。文件缺席抛 OSError(调用方按下周期重试)。
    """
    size = os.path.getsize(path)
    rotated = size < offset
    if rotated:
        offset = 0
    if size == offset:
        return [], offset, rotated
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(size - offset)
    raw_lines = chunk.split(b"\n")
    if chunk.endswith(b"\n"):
        complete, new_offset = raw_lines[:-1], size
    else:
        complete, new_offset = raw_lines[:-1], size - len(raw_lines[-1])
    return [line.decode("utf-8", errors="replace").rstrip("\r") for line in complete], new_offset, rotated


def _invalid(message: str, hint: str) -> ToolResult:
    """参数非法的结构化错误(INVALID_ARGS + 可执行 hint,§W0-3;同 schedule 先例)。"""
    return ToolResult(
        ok=False,
        error=ToolError(kind=ToolErrorKind.INVALID_ARGS, message=message, retryable=False, hint=hint),
    )


def _validate_common(pattern: Any, max_fires: Any) -> ToolResult | None:
    """两源共用的参数校验(fail-closed):pattern 须为可编译正则串;max_fires 须为 >=1 的 int(拒 bool)。"""
    if not isinstance(pattern, str):
        return _invalid(
            f"pattern 须为字符串正则(收到 {type(pattern).__name__})",
            "传正则字符串(如 \"ERROR|Ready\");缺省空串 = 每行都命中",
        )
    if pattern:
        try:
            re.compile(pattern)
        except re.error as e:
            return _invalid(
                f"pattern 正则编译失败: {e}",
                "检查正则语法(括号配对/转义);不确定就先用简单子串(子串即合法正则)",
            )
    if isinstance(max_fires, bool) or not isinstance(max_fires, int) or max_fires < 1:
        return _invalid(
            f"max_fires 须为 >= 1 的整数(收到 {max_fires!r})",
            "命中次数上限传正整数(缺省 20);要盯更久调大上限,不支持 0/负数/bool",
        )
    return None


class MonitorService:
    """事件监控表(ch04 Event-Triggered):``{monitor_id: asyncio.Task}`` + 按 run_id 分桶。

    fire 注入通道(``ctl``,RunControl 契约)由 KernelBuilder 在 build 后 bind
    (同 ``bind_timer`` 先例);未 bind 时工具侧拦 NOT_FOUND,service 本身只防御式
    丢弃(``bound`` 供工具探测)。shell 源在册进程表 ``_procs`` 供 release_run
    同步杀进程组(不依赖取消落地)。

    规格持久化:set 成功即把规格 append 到目标帧 ``working["_monitors"]``(经
    ctl → kernel → stack 反查帧;帧不可达只记 log 不阻断);命中/终结同步回写规格
    (``fired``/``offset``/``done``);resume 经 ``rearm_from_working`` 重武装。
    """

    def __init__(self, sleep: Any = None, spawn: Any = None) -> None:
        self._sleep = sleep or asyncio.sleep
        self._spawn = spawn or asyncio.create_subprocess_shell
        self._ctl: Any = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._by_run: dict[str, set[str]] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    @property
    def bound(self) -> bool:
        """fire 注入通道已装配(ctl 在场)。"""
        return self._ctl is not None

    @property
    def tasks(self) -> dict[str, asyncio.Task]:
        """全部在册监控(monitor_id → task;测试观测点)。"""
        return self._tasks

    @property
    def procs(self) -> dict[str, asyncio.subprocess.Process]:
        """shell 源在册子进程(monitor_id → Process;测试观测点/release 同步杀)。"""
        return self._procs

    def bind(self, ctl: Any) -> None:
        """装配钩子(KernelBuilder 在 kernel/ctl 就位后调用):注入 fire 的帧消息注入通道。"""
        self._ctl = ctl

    def set_shell(
        self,
        *,
        run_id: str,
        frame_id: str,
        command: Any,
        pattern: str = "",
        note: str = "",
        max_fires: int = 20,
        workdir: str = "",
    ) -> str | ToolResult:
        """登记 shell 输出监控并立即返回 monitor_id;参数非法 → ToolResult(INVALID_ARGS)。

        子进程在 ``workdir`` 内运行(同 shell_exec 的 cwd 语义),stdout/stderr 合并
        逐行读;规格落目标帧 ``working["_monitors"]``(随 checkpoint 持久化,resume
        重跑命令重武装);帧不可达只记 log 不阻断。
        """
        if not isinstance(command, str) or not command.strip():
            return _invalid(
                f"command 必填非空字符串(收到 {command!r})",
                "给出要监控的 shell 命令(如 \"tail -f build.log\");一次性看全部输出用 system.shell.exec",
            )
        bad = _validate_common(pattern, max_fires)
        if bad is not None:
            return bad
        monitor_id = self._spawn_shell_task(
            run_id=run_id,
            frame_id=frame_id,
            command=command,
            pattern=pattern,
            note=note,
            max_fires=max_fires,
            workdir=workdir,
            fired0=0,
        )
        spec = {
            "monitor_id": monitor_id,
            "kind": "shell",
            "run_id": run_id,
            "frame_id": frame_id,
            "command": command,
            "pattern": pattern,
            "note": note,
            "max_fires": max_fires,
            "fired": 0,
            "workdir": workdir,  # resume 重跑命令的 cwd(与原运行目录一致)
            "created_at": time.time(),
        }
        if not self._record_spec(frame_id, spec):
            _log.warning(
                "monitor %s 规格无法落帧 %s 的 working(帧不可达),退回进程态语义:pause/resume 不重武装",
                monitor_id,
                frame_id,
            )
        return monitor_id

    def connect_channel(
        self,
        *,
        run_id: str,
        frame_id: str,
        path: str,
        pattern: str = "",
        note: str = "",
        interval: float = 1.0,
        max_fires: int = 20,
    ) -> str | ToolResult:
        """登记事件文件监控并立即返回 channel_id;参数非法 → ToolResult(INVALID_ARGS)。

        ``path`` 须为已解析的绝对路径(沙箱判定在工具层,resolve_work_path);
        ``interval`` 按下限 ``_MIN_SECONDS`` 钳制(防抖,同 timer 先例);规格
        (含 offset)落目标帧 ``working["_monitors"]``。
        """
        bad = _validate_common(pattern, max_fires)
        if bad is not None:
            return bad
        if not isinstance(path, str) or not path:
            return _invalid(
                f"path 必填非空字符串(收到 {path!r})",
                "给出事件文件路径(工作目录/read_paths 内);一次性读现有内容用 system.file.read",
            )
        interval = max(_MIN_SECONDS, float(interval))
        channel_id = self._spawn_channel_task(
            run_id=run_id,
            frame_id=frame_id,
            path=path,
            pattern=pattern,
            note=note,
            interval=interval,
            max_fires=max_fires,
            offset0=0,
            fired0=0,
        )
        spec = {
            "monitor_id": channel_id,
            "kind": "channel",
            "run_id": run_id,
            "frame_id": frame_id,
            "path": path,
            "pattern": pattern,
            "note": note,
            "interval": interval,
            "max_fires": max_fires,
            "fired": 0,
            "offset": 0,
            "created_at": time.time(),
        }
        if not self._record_spec(frame_id, spec):
            _log.warning(
                "channel %s 规格无法落帧 %s 的 working(帧不可达),退回进程态语义:pause/resume 不重武装",
                channel_id,
                frame_id,
            )
        return channel_id

    def _spawn_shell_task(
        self,
        *,
        run_id: str,
        frame_id: str,
        command: str,
        pattern: str,
        note: str,
        max_fires: int,
        workdir: str,
        fired0: int = 0,
    ) -> str:
        """建 shell 监控任务并入表(不写规格——规格由 set/rearm 各自维护),返回 monitor_id。"""
        monitor_id = uuid.uuid4().hex[:12]
        task = asyncio.get_running_loop().create_task(
            self._run_shell(monitor_id, run_id, frame_id, command, pattern, note, max_fires, workdir, fired0)
        )
        self._tasks[monitor_id] = task
        self._by_run.setdefault(run_id, set()).add(monitor_id)
        return monitor_id

    def _spawn_channel_task(
        self,
        *,
        run_id: str,
        frame_id: str,
        path: str,
        pattern: str,
        note: str,
        interval: float,
        max_fires: int,
        offset0: int = 0,
        fired0: int = 0,
    ) -> str:
        """建 channel 轮询任务并入表(不写规格——规格由 set/rearm 各自维护),返回 channel_id。"""
        channel_id = uuid.uuid4().hex[:12]
        task = asyncio.get_running_loop().create_task(
            self._run_channel(
                channel_id, run_id, frame_id, path, pattern, note, interval, max_fires, offset0, fired0
            )
        )
        self._tasks[channel_id] = task
        self._by_run.setdefault(run_id, set()).add(channel_id)
        return channel_id

    def release_run(self, run_id: str) -> None:
        """run 收尾(registry ``release_run`` 调用):取消本 run 全部在册监控并清空分桶。

        只动本 run 桶(同 timer 先例);shell 源进程组**同步** SIGKILL(不依赖取消
        落地——任务侧 CancelledError 路径再杀是 no-op);取消是静默的,规格**不标
        done**(pause 收尾的帧 resume 要靠留下的规格重武装,统一不标最简一致)。
        """
        for monitor_id in self._by_run.pop(run_id, set()):
            task = self._tasks.pop(monitor_id, None)
            proc = self._procs.pop(monitor_id, None)
            if proc is not None:
                _kill(proc)
            if task is not None and not task.done():
                task.cancel()

    def _frame_for(self, frame_id: str) -> Any:
        """经 fire 注入通道反查帧(ctl → kernel → stack);ctl/kernel 缺位 → None。"""
        ctl = self._ctl
        kernel = getattr(ctl, "_kernel", None) if ctl is not None else None
        if kernel is None:
            return None
        return kernel.stack.get(frame_id)

    def _find_spec(self, frame_id: str, monitor_id: str) -> dict[str, Any] | None:
        """在帧 ``working["_monitors"]`` 里按 monitor_id 找规格(帧不可达/无此规格 → None)。"""
        frame = self._frame_for(frame_id)
        if frame is None:
            return None
        for spec in frame.context.working.get("_monitors", []):
            if spec.get("monitor_id") == monitor_id:
                return spec
        return None

    def _record_spec(self, frame_id: str, spec: dict[str, Any]) -> bool:
        """规格落帧 ``working["_monitors"]``(随 checkpoint 持久化);帧不可达 → False(调用方记 log)。"""
        frame = self._frame_for(frame_id)
        if frame is None:
            return False
        frame.context.working.setdefault("_monitors", []).append(spec)
        return True

    def _sync_spec(self, frame_id: str, monitor_id: str, **fields: Any) -> None:
        """命中后回写规格(shell: fired;channel: fired + offset——offset 持久化是 resume 不重放的锚)。"""
        spec = self._find_spec(frame_id, monitor_id)
        if spec is not None:
            spec.update(fields)

    def _mark_spec_done(self, frame_id: str, monitor_id: str) -> None:
        """任务终结(正常/异常)标规格 done(resume 重武装跳过);release_run 取消不走这里。"""
        spec = self._find_spec(frame_id, monitor_id)
        if spec is not None:
            spec["done"] = True

    async def rearm_from_working(self, frame: Any, *, now: float) -> int:
        """resume 重武装:把帧 ``working["_monitors"]`` 里未 done 的规格重新武装为后台任务。

        折算语义(``now`` 仅为与 timer 结算序列的签名对齐——监控是事件源不是钟,
        无"过期欠次"概念):

        - shell:**从头重跑命令**(子进程不可复活,只能再起);已触发次数从规格
          ``fired`` 续计,上限不重置——但命令输出会重放,旧行可能再次命中
          (replay caveat:resume 后收到重复命中属预期,模型按 monitor_id 对账);
        - channel:从持久化 ``offset`` 续读,**不重放**(offset 是不错读/不重读
          的锚);暂停期间的追加在恢复后首个周期补读(那是"新行",不是重放);
          暂停期间文件被截断(size < offset)按 rotation 从 0 续读;
        - 重武装后规格的 ``monitor_id`` 改写为在跑任务的 id(旧任务 pause 时已
          取消;防同进程再次 resume 时新旧两代并存双火,同 timer 先例)。

        返回重武装条数;无规格/全部 done → 0(大多数帧的快路径)。
        """
        specs = frame.context.working.get("_monitors")
        if not specs:
            return 0
        rearmed = 0
        for spec in specs:
            if spec.get("done"):
                continue
            run_id = spec.get("run_id") or frame.run_id
            frame_id = spec.get("frame_id") or frame.frame_id
            kind = spec.get("kind")
            fired = int(spec.get("fired") or 0)
            note = spec.get("note") or ""
            pattern = spec.get("pattern") or ""
            max_fires = int(spec.get("max_fires") or 20)
            if kind == "shell":
                command = spec.get("command")
                if not command:
                    _log.warning("帧 %s 的监控规格缺 command,跳过重武装: %s", frame.frame_id, spec)
                    continue
                spec["monitor_id"] = self._spawn_shell_task(
                    run_id=run_id,
                    frame_id=frame_id,
                    command=command,
                    pattern=pattern,
                    note=note,
                    max_fires=max_fires,
                    workdir=spec.get("workdir") or "",
                    fired0=fired,
                )
            elif kind == "channel":
                path = spec.get("path")
                if not path:
                    _log.warning("帧 %s 的监控规格缺 path,跳过重武装: %s", frame.frame_id, spec)
                    continue
                spec["monitor_id"] = self._spawn_channel_task(
                    run_id=run_id,
                    frame_id=frame_id,
                    path=path,
                    pattern=pattern,
                    note=note,
                    interval=max(_MIN_SECONDS, float(spec.get("interval") or 1.0)),
                    max_fires=max_fires,
                    offset0=int(spec.get("offset") or 0),
                    fired0=fired,
                )
            else:
                _log.warning("帧 %s 的监控规格缺/未知 kind,跳过重武装: %s", frame.frame_id, spec)
                continue
            rearmed += 1
        return rearmed

    async def _run_shell(
        self,
        monitor_id: str,
        run_id: str,
        frame_id: str,
        command: str,
        pattern: str,
        note: str,
        max_fires: int,
        workdir: str,
        fired0: int = 0,
    ) -> None:
        """shell 监控本体:起子进程逐行读,命中注入;到顶/退出/帧终态即终结。

        收尾三态:进程退出(EOF)→ 注入退出事件(带 exit_code);``max_fires``
        到顶 → 杀进程组 + 注入封顶事件;帧终态 → 静默弃 + 杀进程组(不留孤儿)。
        取消(release_run)杀进程组后传播,规格不标 done——留给 resume 重武装
        (重跑命令,见 ``rearm_from_working`` 的 replay caveat)。
        """
        proc: asyncio.subprocess.Process | None = None
        fired = fired0
        exit_path = ""  # "exit"(进程退出) | "cap"(到顶) | "drop"(帧终态)
        try:
            proc = await self._spawn(
                command,
                cwd=workdir or None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # 合并:报错也是关键信号
                start_new_session=True,  # 独立进程组:到顶/取消才杀得干净(见 _kill 注释)
            )
            self._procs[monitor_id] = proc
            regex = re.compile(pattern) if pattern else None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if regex is not None and not regex.search(line):
                    continue
                fired += 1
                if not await self._fire(
                    monitor_id, frame_id, format_match_text("shell", monitor_id, line, fired, max_fires)
                ):
                    exit_path = "drop"
                    break
                self._sync_spec(frame_id, monitor_id, fired=fired)
                if fired >= max_fires:
                    exit_path = "cap"
                    break
            else:
                exit_path = "exit"  # stdout EOF:进程组全部写端关闭
        except asyncio.CancelledError:
            if proc is not None:
                _kill(proc)
                with contextlib.suppress(Exception):
                    await proc.wait()  # 收尸防孤儿(SIGKILL 后 wait 即返,同 _reap 次序)
            raise  # run 收尾/pause 取消:静默退出(§3.1);规格不标 done,留给 resume 重武装
        except Exception:  # noqa: BLE001 — 后台监控任务不得把异常漏进事件循环
            _log.exception("monitor %s 运行异常,监控终止", monitor_id)
            if proc is not None:
                _kill(proc)
            self._mark_spec_done(frame_id, monitor_id)  # 异常终止:标 done,防 resume 复活坏任务
        else:
            if exit_path in ("cap", "drop") and proc is not None and proc.returncode is None:
                _kill(proc)  # 到顶/帧终态:主动终止进程组(监控目的已灭,不留孤儿)
                with contextlib.suppress(Exception):
                    await proc.wait()
            if exit_path == "exit":
                with contextlib.suppress(Exception):
                    await proc.wait()  # EOF 后取真实 exit_code(写端已闭,即返)
                code = proc.returncode if proc is not None and proc.returncode is not None else 0
                await self._fire(monitor_id, frame_id, format_exit_text(monitor_id, code, fired))
            elif exit_path == "cap":
                await self._fire(monitor_id, frame_id, format_cap_text("shell", monitor_id, max_fires))
            self._mark_spec_done(frame_id, monitor_id)
        finally:
            self._tasks.pop(monitor_id, None)
            self._procs.pop(monitor_id, None)
            bucket = self._by_run.get(run_id)
            if bucket is not None:
                bucket.discard(monitor_id)
                if not bucket:
                    self._by_run.pop(run_id, None)

    async def _run_channel(
        self,
        monitor_id: str,
        run_id: str,
        frame_id: str,
        path: str,
        pattern: str,
        note: str,
        interval: float,
        max_fires: int,
        offset0: int = 0,
        fired0: int = 0,
    ) -> None:
        """channel 轮询本体:按 interval 盯文件 size/mtime,从 offset 续读新增行,命中注入。

        每轮读完回写规格(offset + fired——offset 持久化是 resume 不重放的锚);
        rotation(size < offset)记 log 一次并从 0 续读;文件缺席(未创建/轮换
        间隙)下周期重试(同 skills 看门狗"异常吞掉记 log 继续"先例);到顶注入
        封顶事件终结;帧终态静默停;取消(release_run)直接传播,规格不标 done。
        """
        offset = offset0
        fired = fired0
        regex = re.compile(pattern) if pattern else None
        last_stat: tuple[int, float] | None = None
        stop = False
        try:
            while not stop:
                await self._sleep(interval)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue  # 文件缺席:下周期重试(事件源可能尚未创建)
                if last_stat is not None and (stat.st_size, stat.st_mtime) == last_stat:
                    continue  # size/mtime 未变:无新内容
                last_stat = (stat.st_size, stat.st_mtime)
                try:
                    lines, offset, rotated = await asyncio.to_thread(_poll_new_lines, path, offset)
                except OSError:
                    continue  # 轮换间隙(stat 后文件被截走):下周期重试
                if rotated:
                    _log.info("channel %s 检测到 rotation(size < offset),从文件头续读: %s", monitor_id, path)
                for line in lines:
                    if regex is not None and not regex.search(line):
                        continue
                    fired += 1
                    if not await self._fire(
                        monitor_id, frame_id, format_match_text("channel", monitor_id, line, fired, max_fires)
                    ):
                        stop = True  # 帧终态:本次静默弃,后续触发一并停止
                        break
                    if fired >= max_fires:
                        await self._fire(
                            monitor_id, frame_id, format_cap_text("channel", monitor_id, max_fires)
                        )
                        stop = True
                        break
                if lines or rotated:
                    self._sync_spec(frame_id, monitor_id, fired=fired, offset=offset)
        except asyncio.CancelledError:
            raise  # run 收尾/pause 取消:静默退出(§3.1);规格不标 done,留给 resume 重武装
        except Exception:  # noqa: BLE001 — 后台轮询任务不得把异常漏进事件循环
            _log.exception("channel %s 轮询异常,监控终止", monitor_id)
            self._mark_spec_done(frame_id, monitor_id)  # 异常终止:标 done,防 resume 复活坏任务
        else:
            self._mark_spec_done(frame_id, monitor_id)  # 正常终结(到顶/帧终态停)
        finally:
            self._tasks.pop(monitor_id, None)
            bucket = self._by_run.get(run_id)
            if bucket is not None:
                bucket.discard(monitor_id)
                if not bucket:
                    self._by_run.pop(run_id, None)

    async def _fire(self, monitor_id: str, frame_id: str, text: str) -> bool:
        """事件投递:帧终态/消失 → log 并返回 False(调用方停止监控);否则注入并返回 True。"""
        ctl = self._ctl
        if ctl is None:  # 工具侧已拦 NOT_FOUND,此处纯防御
            _log.warning("monitor %s 触发但 ctl 未装配,消息被丢弃", monitor_id)
            return False
        kernel = getattr(ctl, "_kernel", None)
        if kernel is not None:
            frame = kernel.stack.get(frame_id)
            if frame is None or frame.status in _TERMINAL:
                _log.info("monitor %s 触发但帧 %s 已终态/不存在,消息静默丢弃", monitor_id, frame_id)
                return False
        await ctl.inject_message(
            frame_id,
            Message(role=Role.USER, content=text, source=Source.INJECTED),
        )
        return True


def _monitor_not_assembled() -> ToolResult:
    """未装配 monitor 服务的结构化错误(同 timer"未装配"报 NOT_FOUND 先例,§2.3)。"""
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.NOT_FOUND,
            message="monitor 服务未装配",
            retryable=False,
            hint="KernelBuilder 装配内核时会自动 bind fire 注入通道;直接持有 registry 的嵌入方需经 bind_monitor(ctl) 注入后再用",
        ),
    )


def monitor_set_tool(*, name: str = "system.monitor.set", registry: Any) -> Tool:
    """构造 ``system.monitor.set``(ch04 Event-Triggered):后台监控 shell 命令输出,命中向本帧注入消息。

    工具本身**立即返回 monitor_id**(监控在后台 asyncio 任务里跑,不占 spec.timeout
    的等待语义——同 timer_set 先例);MonitorService 由 registry 持有,fire 注入通道
    (ctl)由 KernelBuilder 装配时 bind(未 bind → NOT_FOUND)。
    """

    async def monitor_set(
        command: str,
        pattern: str = "",
        note: str = "",
        max_fires: int = 20,
        ctx: ToolContext | None = None,
    ) -> dict[str, Any] | ToolResult:
        """后台监控一个 shell 命令的输出:逐行读取(stdout/stderr 合并),正则命中即把 "[monitor 命中] {line}" 注入调用帧上下文,立即返回 monitor_id。

        Use when 要等长时间运行的命令输出里的关键信号(构建报错、服务就绪行、
        训练指标异常),不想反复 shell_exec 轮询烧 token 或干等错过问题;
        Do not use when 命令很快结束且要直接看全部输出(用 system.shell.exec
        更直接)或只需定时复查(用 system.timer.set)。``pattern`` 为空 = 每行
        都命中(慎用,输出即消息);``max_fires`` 到顶(缺省 20)注入封顶事件并
        终止进程组;进程退出注入退出事件(带 exit_code)。命令在帧工作目录内
        运行;规格随帧 checkpoint 持久化:run 暂停/重启后 resume 会重跑命令
        重武装(输出重放可能重复命中)。宿主未装配 monitor 服务 → NOT_FOUND。
        """
        service = getattr(registry, "_monitors", None)
        if service is None or not service.bound:
            return _monitor_not_assembled()
        run_id = ctx.run_id if ctx is not None else ""
        frame_id = ctx.frame_id if ctx is not None else ""
        workdir = ctx.workdir if ctx is not None else ""
        result = service.set_shell(
            run_id=run_id,
            frame_id=frame_id,
            command=command,
            pattern=pattern,
            note=note,
            max_fires=max_fires,
            workdir=workdir,
        )
        if isinstance(result, ToolResult):
            return result
        return {
            "monitor_id": result,
            "run_id": run_id,
            "frame_id": frame_id,
            "pattern": pattern,
            "max_fires": max_fires,
        }

    spec = derive_spec(
        monitor_set,
        name=name,
        # EXEC 档(起任意子进程,同 shell_exec);TIER-STANDARDS §1:显式标
        # irreversible(EXEC 默认推导已是,写明防推导规则变动时静默降档)
        permission=Permission.EXEC,
        side_effect="irreversible",
        # 工具立即返回(监控在后台),默认 timeout 已足够,不为监控放宽(§8.1)
        timeout=30.0,
        cost_hint="~1ms(立即返回;监控在后台)",
    )
    return _FunctionTool(monitor_set, spec)


def channel_connect_tool(*, name: str = "system.channel.connect", registry: Any) -> Tool:
    """构造 ``system.channel.connect``(ch04 Event-Triggered):接入事件文件,新增行命中向本帧注入消息。

    路径按 fs 工具的 ``resolve_work_path`` 三段判定解析(只读区可读/workdir
    可读写/越界 INVALID_ARGS——监控是读语义,``write=False``);工具**立即返回
    channel_id**(轮询在后台);MonitorService/fire 注入通道同 monitor_set。
    """

    async def channel_connect(
        path: str,
        pattern: str = "",
        note: str = "",
        interval: float = 1.0,
        max_fires: int = 20,
        ctx: ToolContext | None = None,
    ) -> dict[str, Any] | ToolResult:
        """接入外部事件源文件:轮询跟踪新增行(类 tail -F),正则命中即把 "[channel 命中] {line}" 注入调用帧上下文,立即返回 channel_id。

        Use when 外部进程/系统把事件追加写进日志文件(构建状态、回调通知、
        设备上报),需要事件触达而非定时盲查;Do not use when 事件源不是
        文件(HTTP 回调等走宿主事件通道)或要一次性读现有内容(用
        system.file.read)。``path`` 限定在帧工作目录/read_paths 内(越界
        拒绝);``interval`` 轮询间隔秒数(下限钳制 0.5s);rotation(文件
        截断重写)自动从文件头续读;``max_fires`` 到顶(缺省 20)注入封顶
        事件并停止。规格(含已读 offset)随帧 checkpoint 持久化:resume
        从 offset 续读,**不重放**。宿主未装配 monitor 服务 → NOT_FOUND。
        """
        service = getattr(registry, "_monitors", None)
        if service is None or not service.bound:
            return _monitor_not_assembled()
        workdir = ctx.workdir if ctx is not None else "."
        read_paths = ctx.read_paths if ctx is not None else ()
        resolved = resolve_work_path(workdir, read_paths, path)  # write=False:只读区也可监控
        if isinstance(resolved, ToolResult):
            return resolved
        run_id = ctx.run_id if ctx is not None else ""
        frame_id = ctx.frame_id if ctx is not None else ""
        result = service.connect_channel(
            run_id=run_id,
            frame_id=frame_id,
            path=str(resolved),
            pattern=pattern,
            note=note,
            interval=interval,
            max_fires=max_fires,
        )
        if isinstance(result, ToolResult):
            return result
        return {
            "channel_id": result,
            "run_id": run_id,
            "frame_id": frame_id,
            "path": str(resolved),
            "pattern": pattern,
            # 回显生效中的轮询节奏(钳制后的值),模型据此知道实际节拍
            "interval": max(_MIN_SECONDS, float(interval)),
            "max_fires": max_fires,
        }

    spec = derive_spec(
        channel_connect,
        name=name,
        # WRITE 档(登记后台轮询是对 run 的副作用;读语义靠 resolve_work_path 分区
        # 保证,同 timer_set 的分档理由);fs 数据域声明供数据层 authZ 解析目标
        permission=Permission.WRITE,
        data_domains=["fs.*"],
        timeout=30.0,
        cost_hint="~1ms(立即返回;轮询在后台)",
    )
    return _FunctionTool(channel_connect, spec)

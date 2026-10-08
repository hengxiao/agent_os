"""行式 REPL(P2-M2):SessionRunner 的渲染器 + 输入循环(stdlib-only,禁 rich/textual,
docs/TUI-DOC.md §0「web 侧无框架无构建的精神在 TUI 侧就是零依赖」)。

输出契约(定案):**渲染写 stdout、输入读 stdin**。本宿主是交互产品(chat 面),
不受批命令「stdout 仅 JSON」契约约束——那是 ``agent-os run`` 的契约
(docs/RUNNERS.md §3.3);e2e 经管道驱动时渲染在 stdout 上可直接断言。
tty 下每轮读输入前打印 ``› `` 提示符;管道(stdin 非 tty)不打,保持输出干净可断言。

结构:主线程跑「排干事件队列 → 读一行命令(50ms 轮询)」循环;stdin 读取在
daemon 线程(``input()`` 阻塞不挡渲染),Ctrl-C 由主线程 queue.get 接收。
所有等待有界(轮询/超时),不给 sleep 死等。
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any, TextIO

from agent_os.host.coding_cli.session import SessionRunner

#: 命令队列轮询节拍(秒):渲染延迟与输入响应的折中
_POLL_INTERVAL = 0.05
#: 作答后等裁决结果的窗口(秒):options 不合的重问提示(previous_error 透传)
_ANSWER_OUTCOME_TIMEOUT = 1.0
#: 「问题事件已到、pending 尚未登记」竞态的兜底等待(秒)
_QUESTION_SETTLE_TIMEOUT = 3.0
#: Ctrl-C/EOF 暂停后等 run 落幕的窗口(秒)
_PAUSE_SETTLE_TIMEOUT = 5.0


class Repl:
    """SessionRunner 的行式前端(P2-M2)。

    输入分派状态机(plain 文本行):

    1. 有挂起问题 → 作为答案 ``answer()``(多问题取 urgency 高/最早的首行,
       多行序号展示);选项不合被重问时打印 previous_error 提示(透传);
    2. 无挂起问题但 run 活跃 → ``inject_user_message``(True 提示已注入,
       False 提示当前不可注入);
    3. 无活跃 run → 引导提示(首轮任务经 CLI 参数/--input 传入;M2 的 REPL
       服务「观察 + 应答 + 插话」)。

    斜杠命令:``/help`` ``/status`` ``/pause`` ``/stop`` ``/quit``
    (有活 run 时 /quit 提示先 /pause 或 /stop);未知斜杠给提示不崩。
    Ctrl-C/EOF:有活 run → pause 语义(等 run.end 或超时,打印 resume 提示);
    无活 run → 直接退。
    """

    def __init__(
        self,
        runner: SessionRunner,
        *,
        out: TextIO | None = None,
        instream: TextIO | None = None,
    ) -> None:
        self._runner = runner
        self._out = out or sys.stdout
        self._in = instream or sys.stdin
        self._commands: queue.Queue[str | None] = queue.Queue()
        # 渲染状态:流式行是否打开(半行)/最近 chunk 的技能(换技能另起前缀)
        self._chunk_open = False
        self._last_chunk_skill: str | None = None
        # 问答状态:问题横幅已出但尚未作答(输入分派竞态兜底用)
        self._question_open = False
        self._last_end: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self) -> int:
        """跑 REPL 直到 /quit、EOF 或 Ctrl-C;返回进程退出码(§3.3 语义见 __main__)。"""
        reader = threading.Thread(
            target=self._read_stdin, daemon=True, name="coding-cli-stdin"
        )
        reader.start()
        try:
            while True:
                self._drain_events()
                try:
                    line = self._commands.get(timeout=_POLL_INTERVAL)
                except queue.Empty:
                    continue
                if line is None:  # EOF(Ctrl-D / 管道关闭)
                    return self._on_interrupt("EOF")
                if not self._dispatch(line):
                    return self._exit_code()
        except KeyboardInterrupt:
            return self._on_interrupt("Ctrl-C")

    def _read_stdin(self) -> None:
        """stdin 读取线程:input() 逐行读,EOF/异常 → None 哨兵(绝不抛给主线程)。"""
        while True:
            try:
                if self._in.isatty():
                    self._print("› ", end="", flush=True)
                line = self._in.readline()
            except (OSError, ValueError):
                break
            if line == "":
                break  # EOF
            self._commands.put_nowait(line)
        self._commands.put_nowait(None)

    # ------------------------------------------------------------------
    # 渲染(事件 → 终端形态;契约见 session.py 模块 docstring 的事件枚举)
    # ------------------------------------------------------------------

    def _drain_events(self) -> None:
        """非阻塞排干事件队列逐条渲染。"""
        events = self._runner.events()
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                return
            self._render(event)

    def _render(self, event: dict[str, Any]) -> None:
        t = event.get("type")
        if t == "chunk":
            self._render_chunk(event)
            return
        self._break_chunk()  # 任何非 chunk 事件先收束流式行(换行)
        if t == "run.start":
            run_id = str(event.get("run_id") or "")
            self._print(f"[run 启动] {run_id[:8]}")
        elif t == "tool.start":
            self._print(f"{self._indent(event)}[tool] {event.get('tool')} {event.get('args')}")
        elif t == "tool.end":
            mark = "ok" if event.get("ok") else "fail"
            self._print(f"{self._indent(event)}[tool] {event.get('tool')} → {mark}")
        elif t == "frame.push":
            self._print(f"{self._indent(event)}[技能开始] {event.get('skill')}")
        elif t == "frame.pop":
            self._print(f"{self._indent(event)}[技能结束] {event.get('skill')}")
        elif t == "question":
            self._render_question(event)
        elif t == "notify":
            self._print(f"[通知] {event.get('text')}")
        elif t == "run.end":
            self._render_run_end(event)
        # 未识别事件类型:静默跳过(前向兼容,新事件不崩旧渲染器)

    def _render_chunk(self, event: dict[str, Any]) -> None:
        """流式分片直出增量文本;换技能时收束旧行、带 ``▸ skill`` 前缀另起。"""
        skill = str(event.get("skill") or "")
        if skill != self._last_chunk_skill:
            self._break_chunk()
            self._print(f"▸ {skill}")
            self._last_chunk_skill = skill
        text = str(event.get("text") or "")
        if text:
            self._print(text, end="", flush=True)
            self._chunk_open = True

    def _render_question(self, event: dict[str, Any]) -> None:
        """提问横幅:kind/channel + 问题 + options 提示(previous_error 在作答回馈处透传)。"""
        kind = event.get("kind") or "question"
        channel = event.get("channel") or "supervisor"
        self._print(f"\n══ 提问({channel}/{kind})══")
        self._print(str(event.get("question") or ""))
        options = event.get("options")
        if options:
            self._print("选项: " + " | ".join(str(o) for o in options))
        self._question_open = True

    def _render_run_end(self, event: dict[str, Any]) -> None:
        """run 终态摘要行;paused 附 resume 提示;failed/aborted 附错误行。"""
        status = str(event.get("status") or "")
        self._last_end = event
        self._question_open = False
        self._last_chunk_skill = None
        if status == "paused":
            self._print("[run 挂起] paused —— 已落 checkpoint")
            self._print_resume_hint()
        else:
            self._print(f"[run 结束] {status}")
        if event.get("error"):
            self._print(f"错误: {event['error']}")
        elif status == "done":
            self._print(f"结果: {event.get('summary')}")
        if event.get("store_error"):
            self._print(f"[警告] 会话落盘失败: {event['store_error']}")

    def _print_resume_hint(self) -> None:
        sid = self._runner.session_id
        if sid:
            self._print(f"恢复本会话: agent-os-chat --resume {sid}")

    @staticmethod
    def _indent(event: dict[str, Any]) -> str:
        """按帧 depth 缩进(depth 1 = 根帧不缩;子技能活动行按层级右移)。"""
        depth = event.get("depth") or 1
        return "  " * max(0, int(depth) - 1)

    def _break_chunk(self) -> None:
        if self._chunk_open:
            self._print()
            self._chunk_open = False

    def _print(self, text: str = "", *, end: str = "\n", flush: bool = True) -> None:
        print(text, end=end, flush=flush, file=self._out)

    # ------------------------------------------------------------------
    # 输入分派
    # ------------------------------------------------------------------

    def _dispatch(self, line: str) -> bool:
        """一行输入的分派;返回 False = 退出循环(/quit)。"""
        text = line.strip()
        if not text:
            return True  # 空输入不崩
        if text.startswith("/"):
            return self._slash(text)
        pending = self._runner.pending_questions()
        if not pending and self._question_open:
            # 竞态兜底:问题横幅已渲染但 pending 尚未登记(信号先于 handler 挂起)
            pending = self._wait_pending(_QUESTION_SETTLE_TIMEOUT)
        if pending:
            self._answer_pending(pending, text)
            return True
        if self._runner.is_active:
            if self._runner.inject_user_message(text):
                self._print("[已注入] 将在下一步并入(批头消息)")
            else:
                self._print("[当前不可注入] run 已终态或暂不可注入")
            return True
        self._print("[提示] 当前没有进行中的任务;首轮任务经 <skill> --input 传入,"
                    "--resume 恢复既有会话;/help 查看命令")
        return True

    def _answer_pending(self, pending: list[dict[str, Any]], text: str) -> None:
        """作答:多问题取首行(inbox 已按 urgency 高→先问先排排序),其余序号展示。"""
        row = pending[0]
        if len(pending) > 1:
            for i, r in enumerate(pending, 1):
                self._print(f"  [{i}] {r.get('question')}")
            self._print(f"(作答 → [1];其余 {len(pending) - 1} 个待答)")
        ok = self._runner.answer(str(row["question_id"]), text)
        if not ok:
            self._print("[作答失败] 问题已不在(可能已超时或 run 已终态)")
            self._question_open = bool(self._runner.pending_questions())
            return
        self._print("[已作答]")
        self._report_answer_outcome(str(row["question_id"]))
        self._question_open = bool(self._runner.pending_questions())

    def _report_answer_outcome(self, question_id: str) -> None:
        """作答回馈:options 不合被重问时透传 previous_error(有界轮询,防竞态)。"""
        deadline = time.monotonic() + _ANSWER_OUTCOME_TIMEOUT
        while time.monotonic() < deadline:
            row = next(
                (r for r in self._runner.pending_questions()
                 if r.get("question_id") == question_id),
                None,
            )
            if row is None:
                return  # 已结算(接受)
            if row.get("previous_error"):
                self._print(f"[答案未被接受] {row['previous_error']}——请按提示重新作答")
                return
            time.sleep(0.02)

    def _wait_pending(self, timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = self._runner.pending_questions()
            if pending:
                return pending
            time.sleep(0.02)
        return []

    # ------------------------------------------------------------------
    # 斜杠命令
    # ------------------------------------------------------------------

    def _slash(self, text: str) -> bool:
        cmd = text.split()[0]
        if cmd == "/help":
            self._print("命令: /help 本帮助 | /status run 状态(帧树+记账) | "
                        "/pause 暂停(可恢复) | /stop 中止 | /quit 退出")
            self._print("输入: 有问题待答时作为答案;run 进行中作为插话注入;"
                        "空闲时先经 CLI 参数给首轮任务")
        elif cmd == "/status":
            self._status()
        elif cmd == "/pause":
            if self._runner.pause():
                self._print("[已请求暂停] 等待 safe point 落 checkpoint…")
            else:
                self._print("[无活跃 run 可暂停]")
        elif cmd == "/stop":
            if self._runner.stop():
                self._print("[已请求中止] 等待 safe point…")
            else:
                self._print("[无活跃 run 可中止]")
        elif cmd == "/quit":
            if self._runner.is_active:
                self._print("[run 仍在进行] 先 /pause(可恢复)或 /stop(中止),再 /quit")
                return True
            return False
        else:
            self._print(f"[未知命令] {cmd};/help 查看可用命令")
        return True

    def _status(self) -> None:
        runner = self._runner
        state = "进行中" if runner.is_active else "空闲"
        self._print(
            f"[status] session={runner.session_id or '-'} "
            f"run={runner.run_id or '-'} 状态={state}"
        )
        tree = runner.frame_tree()
        if tree:
            self._print("帧树:")
            self._print_frame_tree(tree)
        usage = runner.usage()
        if usage:
            self._print(
                f"记账: steps={usage.get('steps', 0)} cost={usage.get('cost', 0.0)}"
            )
        if tree is None and self._last_end is not None:
            self._print(
                f"上一轮: {self._last_end.get('status')} —— {self._last_end.get('summary')}"
            )

    def _print_frame_tree(self, nodes: list[dict[str, Any]], *, _depth: int = 1) -> None:
        for node in nodes:
            self._print(
                f"{'  ' * (_depth - 1)}- {node.get('skill')} "
                f"[{node.get('status')}] (depth {node.get('depth')})"
            )
            self._print_frame_tree(node.get("children") or [], _depth=_depth + 1)

    # ------------------------------------------------------------------
    # 中断与退出
    # ------------------------------------------------------------------

    def _on_interrupt(self, kind: str) -> int:
        """Ctrl-C/EOF:有活 run → pause 语义(等落幕或超时,打 resume 提示);否则直退。"""
        self._print()
        if self._runner.is_active:
            self._print(f"[{kind}] 暂停 run(可恢复)…")
            self._runner.pause(f"用户中断({kind})")
            if not self._pump_until_end(_PAUSE_SETTLE_TIMEOUT):
                self._print(f"[超时] run 未在 {_PAUSE_SETTLE_TIMEOUT:g}s 内落幕;"
                            "进程退出不影响已落盘产物")
        return self._exit_code()

    def _pump_until_end(self, timeout: float) -> bool:
        """继续排干渲染直到 worker 落幕或超时(中断路径用;返回是否等到)。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain_events()
            if not self._runner.is_active:
                self._drain_events()  # 收尾事件(run.end)可能压在队尾,再排一次
                return True
            time.sleep(0.02)
        self._drain_events()
        return False

    def _exit_code(self) -> int:
        """退出码映射(docs/RUNNERS.md §3.3):无 run/done/paused → 0;
        failed/aborted → 3;错误串带 OverrideError/ConfigError 前缀 → 2/4
        (覆盖选项/配置类错误与批 CLI 同族归类)。"""
        end = self._last_end
        if end is None:
            return 0
        status = end.get("status")
        if status in (None, "done", "paused"):
            return 0
        error = str(end.get("error") or "")
        if error.startswith("OverrideError"):
            return 2
        if error.startswith("ConfigError"):
            return 4
        return 3

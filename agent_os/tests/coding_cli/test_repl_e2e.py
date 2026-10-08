"""P2-M2 e2e:subprocess 驱动 ``python -m agent_os.host.coding_cli``(行式渲染 + 输入循环)。

驱动形态:pexpect 式「读 stdout 到锚点 → 写 stdin 一行」交互脚本(reader 线程 +
有界等待;stdout 渲染契约见 repl.py 模块 docstring——chat 面不受批 CLI 的
「stdout 仅 JSON」约束)。所有等待带 timeout,子进程 finally 必 kill,防挂死 CI。

mock brain 为模块级函数(dotted-path 加载协议;子进程 PYTHONPATH 含包根,
``tests`` 包可 import)。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Role, ToolCall
from tests.helpers.config import write_config as _write_config

ROOT = Path(__file__).resolve().parents[2]  # agent_os 包根(tests/ 与 src/ 的父)

# ---------------------------------------------------------------------------
# mock brain(模块级:dotted-path 加载协议)
# ---------------------------------------------------------------------------


def e2e_ask_brain(req) -> ChatResponse:
    """先 ask_supervisor(无 options,自由文本作答),再按裁决给 decision。"""
    asked = any(
        tc.name == "ask_supervisor"
        for m in req.messages
        if m.role is Role.ASSISTANT
        for tc in m.tool_calls
    )
    if not asked:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="ask_supervisor",
                        args={"question": "批准上线吗?", "context": {"by": "e2e"}},
                    )
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    result = json.loads(next(m.content for m in reversed(req.messages) if m.role is Role.TOOL))
    answer = result["value"]["answer"] if result.get("ok") else "fallback"
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": answer})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


SKILLS_YAML = """
skills:
  - name: e2e.done
    version: 1.0.0
    kind: prompt
    description: 最小收尾技能。Use when e2e 冒烟。
    inputs:
      type: object
      properties: {}
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4 }
    prompt: 直接收尾。
  - name: e2e.ask
    version: 1.0.0
    kind: prompt
    description: 向上级请示。Use when e2e 需要问答。
    inputs:
      type: object
      properties: {}
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions: { tools: [ask_supervisor], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: 先请示再回答。
"""


# ---------------------------------------------------------------------------
# 子进程驱动(reader 线程 + 有界等待;finally 必 kill)
# ---------------------------------------------------------------------------


class _ChatProc:
    """pexpect 式最小驱动:stdout/stderr 各一个 reader 线程;写 stdin 逐行。

    ``pythonpath``:子进程 PYTHONPATH 的**前置**条目(mock brain/handler 的
    dotted-path 加载目录,如包根或 skillset 目录)。
    """

    def __init__(
        self, argv: list[str], *, cwd: Path, pythonpath: list[str] | None = None
    ) -> None:
        env = dict(os.environ)
        entries = [*(pythonpath or [str(ROOT)]), env.get("PYTHONPATH", "")]
        env["PYTHONPATH"] = os.pathsep.join(e for e in entries if e)
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )
        self.buf = ""
        self.err = ""
        self._eof = False
        self._lock = threading.Lock()
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            with self._lock:
                self.buf += line
        with self._lock:
            self._eof = True

    def _read_err(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            with self._lock:
                self.err += line

    def write(self, line: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def wait_out(self, needle: str, *, timeout: float = 15.0) -> None:
        """等 stdout 缓冲出现 needle;EOF/超时 → AssertionError(带缓冲现场)。"""
        self.wait_any([needle], timeout=timeout)

    def wait_any(
        self, needles: list[str], *, start: int = 0, timeout: float = 15.0
    ) -> tuple[int, int, int]:
        """从游标 ``start`` 起等任一 needle;返回 (命中下标, 命中起点, 命中终点)。

        游标形态供多轮驱动(P2-M3 会话 e2e:逐条横幅消费,不回头误配旧文本)。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                buf = self.buf
                eof = self._eof
            hits = [(i, buf.find(n, start)) for i, n in enumerate(needles)]
            hits = [(i, p) for i, p in hits if p >= 0]
            if hits:
                i, pos = min(hits, key=lambda h: h[1])
                return i, pos, pos + len(needles[i])
            if eof:
                break
            time.sleep(0.02)
        raise AssertionError(
            f"stdout 未等到 {needles!r};\n—— stdout ——\n{self.buf}\n—— stderr ——\n{self.err}"
        )

    def line_at(self, pos: int) -> str:
        """取缓冲里 pos 所在的整行(横幅行内容判读用)。"""
        with self._lock:
            buf = self.buf
        end = buf.find("\n", pos)
        return buf[pos : end if end >= 0 else len(buf)]

    def close_stdin(self) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.close()

    def wait_exit(self, *, timeout: float = 15.0) -> int:
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            raise AssertionError(
                f"进程未在 {timeout}s 内退出;\n—— stdout ——\n{self.buf}\n—— stderr ——\n{self.err}"
            ) from None

    def kill(self) -> None:
        with self._lock:
            running = self._proc.poll() is None
        if running:
            self._proc.kill()
            self._proc.wait(timeout=5)


def _spawn(tmp_path: Path, *args: str) -> _ChatProc:
    return _ChatProc(
        [sys.executable, "-m", "agent_os.host.coding_cli", *args], cwd=tmp_path
    )


def _config(tmp_path: Path, brain: str) -> Path:
    skills = tmp_path / "skills.yaml"
    skills.write_text(SKILLS_YAML, encoding="utf-8")
    return _write_config(tmp_path, brain=brain, skills=skills)


# ---------------------------------------------------------------------------
# 场景 A:首轮任务 → done → /quit → exit 0
# ---------------------------------------------------------------------------


def test_scenario_a_opening_task_done_then_quit(tmp_path):
    cfg = _config(tmp_path, "tests.helpers.brains:done_brain")
    proc = _spawn(
        tmp_path, "e2e.done", "--input", "{}",
        "--config", str(cfg), "--artifacts", str(tmp_path / "arts"),
        "--session-id", "sa",
    )
    try:
        proc.wait_out("[run 结束] done")
        proc.wait_out("{'done': True}")  # 状态摘要行(结果)
        proc.write("/quit")
        assert proc.wait_exit() == 0
    finally:
        proc.kill()


# ---------------------------------------------------------------------------
# 场景 B:提问横幅 → stdin 作答 → run 续走 done
# ---------------------------------------------------------------------------


def test_scenario_b_question_banner_then_answer(tmp_path):
    cfg = _config(tmp_path, "tests.coding_cli.test_repl_e2e:e2e_ask_brain")
    proc = _spawn(
        tmp_path, "e2e.ask", "--input", "{}",
        "--config", str(cfg), "--artifacts", str(tmp_path / "arts"),
        "--session-id", "sb",
    )
    try:
        proc.wait_out("══ 提问(supervisor/question)══")
        proc.wait_out("批准上线吗?")
        proc.write("approve-once")
        proc.wait_out("[已作答]")
        proc.wait_out("[run 结束] done")
        proc.wait_out("'decision': 'approve-once'")
        proc.write("/quit")
        assert proc.wait_exit() == 0
    finally:
        proc.kill()


# ---------------------------------------------------------------------------
# 场景 C:/pause → resume 提示 → 进程退出;--resume 重问 → 答 → done
# ---------------------------------------------------------------------------


def test_scenario_c_pause_then_resume_reasks(tmp_path):
    cfg = _config(tmp_path, "tests.coding_cli.test_repl_e2e:e2e_ask_brain")
    arts = tmp_path / "arts"
    proc = _spawn(
        tmp_path, "e2e.ask", "--input", "{}",
        "--config", str(cfg), "--artifacts", str(arts),
        "--session-id", "sc",
    )
    try:
        proc.wait_out("批准上线吗?")
        proc.write("/pause")
        proc.wait_out("[run 挂起] paused")
        proc.wait_out("agent-os-chat --resume sc")  # resume 提示
        proc.write("/quit")
        assert proc.wait_exit() == 0
    finally:
        proc.kill()

    resumed = _spawn(
        tmp_path, "--resume", "sc",
        "--config", str(cfg), "--artifacts", str(arts),
    )
    try:
        resumed.wait_out("批准上线吗?")  # 重问(内核以新 question_id 再进收件箱)
        resumed.write("approve")
        resumed.wait_out("[run 结束] done")
        resumed.wait_out("'decision': 'approve'")
        resumed.write("/quit")
        assert resumed.wait_exit() == 0
    finally:
        resumed.kill()


# ---------------------------------------------------------------------------
# 场景 D:未知命令/空输入不崩;/status 有输出;EOF(活跃 run)→ pause 落幕退出
# ---------------------------------------------------------------------------


def test_scenario_d_robustness_and_status(tmp_path):
    cfg = _config(tmp_path, "tests.coding_cli.test_repl_e2e:e2e_ask_brain")
    proc = _spawn(
        tmp_path, "e2e.ask", "--input", "{}",
        "--config", str(cfg), "--artifacts", str(tmp_path / "arts"),
        "--session-id", "sd",
    )
    try:
        proc.wait_out("批准上线吗?")
        proc.write("")  # 空输入不崩
        proc.write("/bogus")
        proc.wait_out("[未知命令] /bogus")
        proc.write("/status")
        proc.wait_out("[status] session=sd")
        proc.wait_out("帧树")  # 活跃 run 的帧树渲染
        proc.close_stdin()  # EOF:有活 run → pause 语义
        proc.wait_out("[run 挂起] paused")
        assert proc.wait_exit() == 0
    finally:
        proc.kill()

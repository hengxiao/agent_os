"""调试数据源(docs/TUI-DEBUG.md §3;照 doc_editor/model.py DocSource 先例)。

三种来源同一形状(``DebugSource`` Protocol):

- ``DemoDebugSource``:内置 demo.fib n=3 信号序列(与 web 测试同一事实锚点,
  tests/web/test_debug_api.py:21-24),脚本化假会话——暂停/单步/断点命中按
  内核语义(kernel/debug.py DebugSession)同步模拟,无服务可体验全交互,
  也是无头冒烟的数据基座;
- ``OfflineDebugSource``:``--offline --run-dir <artifacts>/runs/<id>`` 直读
  trace.jsonl + checkpoint.json + meta.json,只读("产物即真相"先例,同
  OfflineDocSource 语义);控制命令回 §3 人话
  ``The run is not being debugged (offline replay).``;
- ``OnlineDebugSource``(D2):打 agent-os-web 的 ``/api/debug/*`` 一族
  (**无 /platform 前缀**;REST 全走 AgentOsClient 统一信封),SSE 线程 →
  queue → 主循环 drain(§4:线程只入队不碰 state)。

会话快照形状对齐 web ``_debug_session_doc``(host/web/app.py:522):state /
pause_point / breakpoints(带 hits 与界面编号 num)/ frame_stack /
rerunnable;另加 ``end_status``(run 终态:done/failed/aborted)。
"""

from __future__ import annotations

import fnmatch
import json
import queue
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from agent_os.host.tui.kernel.client import AgentOsClient
from agent_os.host.tui.kernel.sse import SseClient

#: 断点 kind 全集(与 kernel/debug.py BREAKPOINT_KINDS 对齐;不 import 内核,
#: TUI app 层自持一份模拟,内核零改动)
BP_KINDS = ("step", "tool_call", "skill_invoke", "error")

#: Stop verdict 可安全返回的信号(runner 在这两点仲裁;kernel/debug.py:65)
_ARBITRABLE = ("pre:step", "pre:tool.call")

_OFFLINE_READONLY = "The run is not being debugged (offline replay)."
_DEMO_NO_REPLAY = "demo 源无 replay(回放接真服务 --replay,或 --offline --run-dir)"
_LIVE_UNTIL = "until 步数断点在 live 会话不可用(REST 断点端点无 until 字段;" \
              "--replay --until N 或 --demo 可用)"

DEBUG_DEFAULT_BASE_URL = "http://127.0.0.1:8391"

#: 已知会话本机账本缺省路径(§5.1 session/info sessions;web localStorage 先例)
SESSION_STORE_PATH = Path.home() / ".config" / "agent-os" / "debugger-sessions.json"


class SessionStore:
    """已知会话本机账本(client 模式无服务端列表端点;语义 = "本机 TUI 见过
    的会话",**不代表服务端仍存**——attach 已消逝的 SID 会吃到诚实 404)。

    ``path=None`` = 纯内存(测试/无头);落盘 JSON 数组,按 sid 去重追加,
    cap 50 条(旧 → 新,FIFO 淘汰)。坏文件/读写失败 → 空账本/静默(账本
    不是真相,永不该炸调试)。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._rows: list[dict[str, Any]] | None = None

    def _load(self) -> list[dict[str, Any]]:
        if self._rows is not None:
            return self._rows
        rows: list[dict[str, Any]] = []
        if self._path is not None and self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = []
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict) and r.get("session_id")]
        self._rows = rows
        return rows

    def record(self, sid: str, run_id: str, label: str, base_url: str) -> None:
        rows = [r for r in self._load() if r.get("session_id") != sid]
        rows.append({"session_id": sid, "run_id": run_id, "label": label,
                     "base_url": base_url, "opened_at": round(time.time(), 3)})
        del rows[:-50]
        self._rows = rows
        if self._path is not None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
            except OSError:
                pass  # 账本写不进不挡调试(尽力而为,同 localStorage 语义)

    def list(self) -> list[dict[str, Any]]:
        """新 → 旧序。"""
        return list(reversed(self._load()))


class DebugSource(Protocol):
    """调试数据面。``writable`` = 控制面开关(offline=False;demo 可模拟=True)。

    错误纪律:只读源的控制面 raise ``PermissionError``(消息即 §6 人话);
    状态冲突(非 paused 发 resume 等)raise ``RuntimeError``(消息即人话)——
    命令窗原样打印,不弹窗、不打原始 HTTP。
    """

    writable: bool
    conn: str  # "demo" | "offline" | "online"(状态行连接态点,D1 恒 demo/offline)

    def label(self) -> str:
        """头部会话标签(skill 名或 run 描述)。"""
        ...

    def artifacts_path(self) -> str:
        """run 产物目录(终态行第二行;无 → "")。"""
        ...

    # 会话
    def open_live(self, skill: str | None, run_input: dict[str, Any],
                  breakpoints: list[tuple[str, str]]) -> dict[str, Any]: ...
    def open_replay(self, run_id: str, until_step: int | None,
                    breakpoints: list[tuple[str, str]]) -> dict[str, Any]: ...
    def snapshot(self) -> dict[str, Any]: ...
    def signals(self) -> list[dict[str, Any]]: ...
    def frame(self, fid: str) -> dict[str, Any] | None: ...

    # 控制(仅 writable;只读源 raise PermissionError → 命令窗人话)
    def add_breakpoint(self, kind: str, match: str = "*",
                       until: int | None = None) -> dict[str, Any]: ...
    def remove_breakpoint(self, num: int) -> bool: ...
    def command(self, cmd: str) -> None: ...
    def detach(self) -> None: ...
    def rerun(self) -> dict[str, Any]: ...
    def modify(self, patch: dict[str, Any]) -> None: ...
    def inject(self, text: str) -> None: ...


def _empty_snapshot() -> dict[str, Any]:
    return {"session_id": "", "run_id": "", "state": "none", "pause_point": None,
            "breakpoints": [], "frame_stack": [], "rerunnable": False,
            "end_status": None}


# ---------------------------------------------------------------------------
# Demo(demo.fib n=3 脚本化假会话;与 tests/web/test_debug_api.py:21-24 同锚点)
# ---------------------------------------------------------------------------

_DEMO_RUN_ID = "r-demo0001"
_DEMO_SID = "s-demo-01"
_F1, _F2 = "f-fib001", "f-fib002"


def _fib_script() -> list[dict[str, Any]]:
    """demo.fib n=3 的脚本信号序列(事实锚点:F1 step1 → invoke F2(一步即弹)
    → F1 step2 内 pre:tool.call(system.python.exec)→ F1 step3 终答 → F1 pop,
    run done)。ts 固定基值:demo 渲染可复现(快照断言友好)。"""
    rows: list[dict[str, Any]] = []

    def sig(name: str, fid: str | None, payload: dict[str, Any]) -> None:
        rows.append({"name": name, "run_id": _DEMO_RUN_ID, "frame_id": fid,
                     "ts": 1_756_500_000.0 + 0.01 * len(rows), "payload": payload})

    code = "result = 0 + 1\nprint(result)"
    sig("run.started", None, {"skill": "demo.fib"})
    sig("pre:skill.invoke", _F1, {"skill": "demo.fib", "input": {"n": 3}})
    sig("pre:frame.push", _F1, {"skill": "demo.fib", "depth": 1, "input": {"n": 3}})
    sig("post:frame.push", _F1, {"skill": "demo.fib", "depth": 1})
    # F1 step1:决定递归,invoke F2(n=1,base,一步即弹)
    sig("pre:step", _F1, {"step": 1, "depth": 1, "skill": "demo.fib"})
    sig("pre:llm.request", _F1, {"model": "mock/fib", "depth": 1})
    sig("post:llm.response", _F1, {"model": "mock/fib",
                                   "usage": {"prompt": 12, "completion": 5}, "depth": 1})
    sig("pre:skill.invoke", _F2, {"skill": "demo.fib", "input": {"n": 1}})
    sig("pre:frame.push", _F2, {"skill": "demo.fib", "depth": 2, "input": {"n": 1}})
    sig("post:frame.push", _F2, {"skill": "demo.fib", "depth": 2})
    sig("pre:step", _F2, {"step": 1, "depth": 2, "skill": "demo.fib"})
    sig("pre:llm.request", _F2, {"model": "mock/fib", "depth": 2})
    sig("post:llm.response", _F2, {"model": "mock/fib",
                                   "usage": {"prompt": 8, "completion": 4}, "depth": 2})
    sig("pre:frame.pop", _F2, {"skill": "demo.fib", "depth": 2, "result": {"seq": [0, 1]}})
    sig("post:frame.pop", _F2, {"skill": "demo.fib", "depth": 2})
    # F1 step2:pre:tool.call(system.python.exec)
    sig("pre:step", _F1, {"step": 2, "depth": 1, "skill": "demo.fib"})
    sig("pre:llm.request", _F1, {"model": "mock/fib", "depth": 1})
    sig("post:llm.response", _F1, {"model": "mock/fib",
                                   "usage": {"prompt": 18, "completion": 7}, "depth": 1})
    sig("pre:tool.call", _F1, {"tool": "system.python.exec", "args": {"code": code},
                               "depth": 1, "step": 2, "skill": "demo.fib"})
    sig("pre:logic.exec", _F1, {"tool": "system.python.exec", "language": "python",
                                "trust": "trusted", "depth": 1})
    sig("post:logic.exec", _F1, {"ok": True, "trust": "trusted", "depth": 1})
    sig("post:tool.call", _F1, {"tool": "system.python.exec", "ok": True,
                                "result": {"stdout": "1\n"}, "depth": 1})
    # F1 step3:终答 → F1 pop,run done
    sig("pre:step", _F1, {"step": 3, "depth": 1, "skill": "demo.fib"})
    sig("pre:llm.request", _F1, {"model": "mock/fib", "depth": 1})
    sig("post:llm.response", _F1, {"model": "mock/fib",
                                   "usage": {"prompt": 22, "completion": 9}, "depth": 1})
    sig("pre:frame.pop", _F1, {"skill": "demo.fib", "depth": 1,
                               "result": {"seq": [0, 1, 1]}})
    sig("post:frame.pop", _F1, {"skill": "demo.fib", "depth": 1})
    sig("run.finished", None, {"status": "done", "result": {"seq": [0, 1, 1]}})
    return rows


def _simulate_py_exec(code: str) -> tuple[str, Any]:
    """python.exec 的就地模拟(tools/builtins.py:565 同契约:命名空间执行,
    stdout 最后一个非空行按 JSON 解析为 result)。返回 (stdout, result)。"""
    import contextlib
    import io

    buf = io.StringIO()
    ns: dict[str, Any] = {}
    with contextlib.redirect_stdout(buf):
        exec(code, ns)  # noqa: S102 — demo 源模拟 trusted 执行(内核同语义)
    stdout = buf.getvalue()
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    value = json.loads(lines[-1]) if lines else None
    return stdout, value


_DEMO_MESSAGES: dict[str, list[dict[str, Any]]] = {
    _F1: [
        {"role": "user", "content": "计算 fib 数列前 3 项,先递归 fib(1),再用工具求和。"},
        {"role": "assistant", "tool_calls": [
            {"name": "system.python.exec",
             "args": {"code": "result = 0 + 1\nprint(result)"}}]},
        {"role": "assistant", "content": "{\"seq\": [0, 1, 1]}"},
    ],
    _F2: [
        {"role": "user", "content": "计算 fib(1)(base case)。"},
        {"role": "assistant", "content": "{\"seq\": [0, 1]}"},
    ],
}


class _DemoSession:
    """脚本化假会话:按内核语义(kernel/debug.py DebugSession)同步模拟。
    指针逐信号推进;命中断点/步进停点即暂停;run 结束自动 detached。"""

    def __init__(self, breakpoints: list[tuple[str, str]]) -> None:
        self.script = _fib_script()
        self.cursor = 0
        self.state = "running"
        self.pause_point: dict[str, Any] | None = None
        self.end_status: str | None = None
        self.frame_stack: list[dict[str, Any]] = []
        self.bps: list[dict[str, Any]] = []
        self._num_seq = 0
        self._step_mode: str | None = None
        self._step_fid: str | None = None
        #: 帧消息每会话自持一份(inject 注入不污染模块级模板)
        self.messages: dict[str, list[dict[str, Any]]] = {
            fid: [dict(m) for m in msgs] for fid, msgs in _DEMO_MESSAGES.items()
        }
        #: 帧结果簿记(pre:frame.pop 落地;modify 改结果后 frame 检视跟随)
        self.frame_results: dict[str, Any] = {}
        # 入口断点(pdb/CLI 语义,cli/debug.py:348 先例):会话开出即加一次性
        # step 断点,run 启动后停在第一条 pre:step;首次暂停时自动删除
        entry = self.add_breakpoint("step")
        entry["_entry"] = True
        for kind, match in breakpoints:
            self.add_breakpoint(kind, match)

    # ------------------------------------------------------------------
    # 断点(num 从 1 起,界面只露 Num;id 只做内部映射)
    # ------------------------------------------------------------------

    def add_breakpoint(self, kind: str, match: str = "*",
                       until: int | None = None) -> dict[str, Any]:
        if kind not in BP_KINDS:
            raise RuntimeError(f"Unknown breakpoint kind: {kind!r}(可选 {BP_KINDS})")
        if until is not None and (kind != "step" or until < 1):
            raise RuntimeError(f"until 仅 step 断点有效且须 >= 1: kind={kind!r} until={until!r}")
        self._num_seq += 1
        bp = {"id": f"bp-{self._num_seq:02d}", "num": self._num_seq, "kind": kind,
              "match": match, "enabled": True, "hits": 0, "until": until}
        self.bps.append(bp)
        return bp

    def remove_breakpoint(self, num: int) -> bool:
        for i, bp in enumerate(self.bps):
            if bp["num"] == num:
                self.bps.pop(i)
                return True
        return False

    def public_bps(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in bp.items() if not k.startswith("_")} for bp in self.bps]

    # ------------------------------------------------------------------
    # 推进(镜像 kernel/debug.py _handle:簿记 → 随时暂停 → 断点 → 步进)
    # ------------------------------------------------------------------

    def _match_breakpoints(self, sig: dict[str, Any]) -> list[dict[str, Any]]:
        name, p = sig["name"], sig["payload"]
        hits = []
        for bp in self.bps:
            if not bp["enabled"]:
                continue
            if (bp["kind"] == "step" and name == "pre:step") or (
                bp["kind"] == "tool_call" and name == "pre:tool.call"
                and fnmatch.fnmatch(str(p.get("tool", "")), bp["match"])
            ) or (
                bp["kind"] == "skill_invoke" and name == "pre:skill.invoke"
                and fnmatch.fnmatch(str(p.get("skill", "")), bp["match"])
            ) or (
                bp["kind"] == "error" and name == "post:tool.call"
                and p.get("ok") is False
            ):
                hits.append(bp)
        for bp in hits:
            bp["hits"] += 1
        pause_hits = []
        for bp in hits:
            if bp["until"] is not None:
                if bp["hits"] < bp["until"]:
                    continue  # 一次性步数断点:前 N-1 次只计数不停
                bp["enabled"] = False  # 第 N 次命中后自我禁用
            pause_hits.append(bp)
        return pause_hits

    def _step_stop(self, sig: dict[str, Any]) -> bool:
        if self._step_mode is None:
            return False
        name, fid = sig["name"], sig["frame_id"]
        if self._step_mode == "into":
            stop = name == "pre:step"
        elif self._step_mode == "over":
            stop = name in ("pre:step", "pre:frame.pop") and fid == self._step_fid
        else:  # out
            stop = name == "pre:frame.pop" and fid == self._step_fid
        if stop:
            self._step_mode = None
            self._step_fid = None
        return stop

    def _pause(self, sig: dict[str, Any], hits: list[dict[str, Any]],
               step_mode: str | None, reason: str | None = None) -> None:
        p = sig["payload"]
        self.pause_point = {
            "signal": sig["name"], "run_id": sig["run_id"], "frame_id": sig["frame_id"],
            "depth": p.get("depth"), "step": p.get("step"),
            "tool": p.get("tool"), "skill": p.get("skill"),
            "payload": dict(p),
            "breakpoint_ids": [bp["id"] for bp in hits],
            # 命中断点的界面编号(入口断点首次暂停即删,先记账再删,停止行用)
            "breakpoint_nums": [bp["num"] for bp in hits],
            "reason": reason or ("breakpoint" if hits else f"step:{step_mode}"),
        }
        self.state = "paused"
        for bp in list(self.bps):
            if bp.pop("_entry", False):
                self.remove_breakpoint(bp["num"])  # 入口断点:首次暂停即删

    def _advance(self) -> None:
        """逐信号推进到下一个停点或 run 终结(同步;demo 没有真并发)。"""
        while self.cursor < len(self.script):
            sig = self.script[self.cursor]
            self.cursor += 1
            name = sig["name"]
            if name == "pre:frame.push":
                self.frame_stack.append({"frame_id": sig["frame_id"],
                                         "skill": sig["payload"].get("skill"),
                                         "depth": sig["payload"].get("depth")})
            elif name == "pre:frame.pop":
                self.frame_stack = [f for f in self.frame_stack
                                    if f["frame_id"] != sig["frame_id"]]
                self.frame_results[sig["frame_id"]] = sig["payload"].get("result")
            if name in ("run.finished", "run.aborted"):
                self.state = "detached"  # run 结束自动摘下会话(内核同语义)
                self.end_status = "done" if name == "run.finished" else "aborted"
                return
            step_mode = self._step_mode
            step_hit = self._step_stop(sig)
            hits = self._match_breakpoints(sig)
            if hits or step_hit:
                self._pause(sig, hits, step_mode)
                return

    def resume(self, cmd: str) -> None:
        if self.state != "paused":
            raise RuntimeError("The run is not paused.")
        self.state = "running"
        if cmd == "continue":
            self._advance()
            return
        if cmd == "stop":
            # Stop verdict 只在可仲裁信号落地(kernel/debug.py:65):暂停点本身
            # 可仲裁 → 当前 emit 即落地;否则推迟到下一条 pre:step/pre:tool.call
            if not (self.pause_point and self.pause_point["signal"] in _ARBITRABLE):
                while self.cursor < len(self.script):
                    sig = self.script[self.cursor]
                    if sig["name"] in ("run.finished", "run.aborted"):
                        break  # 终态信号不消费,用 run.aborted 替换
                    self.cursor += 1
                    if sig["name"] in _ARBITRABLE:
                        break
            abort = {"name": "run.aborted", "run_id": _DEMO_RUN_ID, "frame_id": None,
                     "ts": self.script[-1]["ts"], "payload": {"reason": "debugger: stop"}}
            self.script.insert(self.cursor, abort)
            self._advance()  # 发出 run.aborted → 自动 detached + end_status=aborted
            return
        mode = {"step_into": "into", "step_over": "over", "step_out": "out"}[cmd]
        self._step_mode = mode
        # 步进目标帧:暂停在 pre:frame.pop 时该帧已视为弹出,当前帧 = 栈顶父帧
        if self.pause_point and self.pause_point["signal"] == "pre:frame.pop":
            self._step_fid = self.frame_stack[-1]["frame_id"] if self.frame_stack else None
        else:
            self._step_fid = self.pause_point["frame_id"] if self.pause_point else None
        self._advance()

    # ------------------------------------------------------------------
    # 干预(D3;kernel/debug.py modify_tool_args/inject_message 的同步模拟)
    # ------------------------------------------------------------------

    def modify(self, patch: dict[str, Any]) -> None:
        """改本次工具调用参数(仅暂停在 pre:tool.call;改完即放行)。

        效果模拟(诚实窄模拟,demo 唯一工具 = system.python.exec):patch 合并
        进本次调用 args 后**就地重算派生行**——python.exec 语义(命名空间
        执行,stdout 最后一行 JSON 解析为 result,tools/builtins.py:565
        _parse_result)+ fib_brain 终答公式(seq + [total])——post:tool.call
        / F1 pre:frame.pop / run.finished 三处结果随之改写;已发出的
        pre:tool.call 行不改(干预不伪造历史,kernel/debug.py §4.4)。
        exec 异常/stdout 不可解析 → 工具失败(ok=False),终答行不动(demo
        简化:不模拟 brain 对工具报错的反应)。
        """
        if self.state != "paused":
            raise RuntimeError("The run is not paused.")
        if not self.pause_point or self.pause_point["signal"] != "pre:tool.call":
            raise RuntimeError("Cannot set args: not paused at pre:tool.call.")
        args = {**(self.pause_point["payload"].get("args") or {}), **patch}
        if "code" in patch:
            self._retool(str(args.get("code") or ""))
        self.resume("continue")  # 提交即放行(Modify verdict 语义)

    def _retool(self, code: str) -> None:
        """按改后 code 重算 post:tool.call 及下游终答行(见 modify docstring)。"""
        for sig in self.script[self.cursor:]:
            if sig["name"] == "post:tool.call":
                tool_row = sig
                break
        else:
            return
        try:
            stdout, value = _simulate_py_exec(code)
        except Exception as e:  # noqa: BLE001 — 模拟执行失败 = 工具失败
            tool_row["payload"] = {**tool_row["payload"], "ok": False,
                                   "result": {"error": str(e)}}
            return
        tool_row["payload"] = {**tool_row["payload"], "ok": True,
                               "result": {"stdout": stdout, "result": value}}
        # fib_brain 终答:seq(子帧结果)+ [本次工具值]
        f2_seq = (self.frame_results.get(_F2) or {}).get("seq") or [0, 1]
        final = {"seq": [*f2_seq, value]}
        for sig in self.script[self.cursor:]:
            if (sig["name"] == "pre:frame.pop" and sig["frame_id"] == _F1) \
                    or sig["name"] == "run.finished":
                sig["payload"]["result"] = final

    def inject(self, text: str, fid: str | None = None) -> str:
        """注入一条 user 消息进指定帧(缺省=暂停帧),注入即放行;返回 fid。"""
        if self.state != "paused":
            raise RuntimeError("The run is not paused.")
        fid = fid or (self.pause_point or {}).get("frame_id")
        if fid is None:
            raise RuntimeError("无暂停帧,inject 需要显式 frame_id")
        self.messages.setdefault(fid, []).append(
            {"role": "user", "content": text, "source": "injected"})
        self.resume("continue")
        return fid

    def detach(self) -> None:
        self.state = "detached"
        self._advance()  # 放行,run 继续跑完


class DemoDebugSource:
    """内置 demo(--demo):demo.fib n=3 脚本化假会话,无服务可体验全交互。
    控制面按内核语义同步模拟(writable=True,但不出海)。"""

    writable = True
    conn = "demo"
    #: 控制同步语义(commands.py 的同步 advance 行只对同步源打印;
    #: 异步源(live)的停点/终态经 SSE/轮询回流,§4)
    sync_control = True

    def __init__(self) -> None:
        self._session: _DemoSession | None = None
        #: rerun 依据(创建参数 = 启动断点;web run_manager origin 的 demo 对偶)
        self._origin: list[tuple[str, str]] | None = None

    def label(self) -> str:
        return "demo.fib"

    def artifacts_path(self) -> str:
        return ""

    def list_skills(self) -> list[str]:
        return ["demo.fib"]

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    def open_live(self, skill: str | None, run_input: dict[str, Any],
                  breakpoints: list[tuple[str, str]]) -> dict[str, Any]:
        if skill not in (None, "demo.fib"):
            raise RuntimeError(f"技能不存在: {skill}(demo 只内置 demo.fib)")
        if self._session is not None and self._session.state != "detached":
            raise RuntimeError("A debug session is already active(kill 或等其结束后再 run)")
        self._origin = list(breakpoints)
        self._session = _DemoSession(breakpoints)
        self._session._advance()  # 入口断点:停在第一条 pre:step
        return {"session_id": _DEMO_SID, "run_id": _DEMO_RUN_ID}

    def open_replay(self, run_id: str, until_step: int | None,
                    breakpoints: list[tuple[str, str]]) -> dict[str, Any]:
        raise RuntimeError(_DEMO_NO_REPLAY)

    def snapshot(self) -> dict[str, Any]:
        if self._session is None:
            return _empty_snapshot()
        s = self._session
        return {"session_id": _DEMO_SID, "run_id": _DEMO_RUN_ID, "state": s.state,
                "pause_point": s.pause_point, "breakpoints": s.public_bps(),
                "frame_stack": list(s.frame_stack), "rerunnable": False,
                "end_status": s.end_status}

    def signals(self) -> list[dict[str, Any]]:
        if self._session is None:
            return []
        return self._session.script[: self._session.cursor]

    def frame(self, fid: str) -> dict[str, Any] | None:
        if fid not in (_F1, _F2):
            return None
        on_stack = any(f["frame_id"] == fid for f in
                       (self._session.frame_stack if self._session else []))
        ended = self._session is not None and self._session.end_status is not None
        s = self._session
        return {
            "frame_id": fid,
            "skill": "demo.fib",
            "status": "running" if on_stack else ("done" if ended or self._session else "running"),
            "input": {"n": 3 if fid == _F1 else 1},
            "result": (s.frame_results.get(fid) if s else None)
            or {"seq": [0, 1, 1] if fid == _F1 else [0, 1]},
            "error": None,
            "usage": {"steps": 3 if fid == _F1 else 1, "cost": 0.0},
            "messages": (s.messages.get(fid) if s else None) or _DEMO_MESSAGES[fid],
            "working": {},
        }

    # ------------------------------------------------------------------
    # 控制(脚本化模拟;modify/inject/detach/rerun 全按内核语义同步模拟)
    # ------------------------------------------------------------------

    def add_breakpoint(self, kind: str, match: str = "*",
                       until: int | None = None) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("The run is not being debugged.")
        return {k: v for k, v in
                self._session.add_breakpoint(kind, match, until).items()
                if not k.startswith("_")}

    def remove_breakpoint(self, num: int) -> bool:
        if self._session is None:
            raise RuntimeError("The run is not being debugged.")
        return self._session.remove_breakpoint(num)

    def command(self, cmd: str) -> None:
        if self._session is None:
            raise RuntimeError("The run is not being debugged.")
        if cmd == "pause":
            raise RuntimeError("The run is not running.")
        self._session.resume(cmd)

    def detach(self) -> None:
        if self._session is None:
            raise RuntimeError("The run is not being debugged.")
        self._session.detach()  # 摘下并放行:run 继续跑完(内核同语义)

    def rerun(self) -> dict[str, Any]:
        """以创建参数(启动断点)重开新会话——入口断点重生,停在第一条 pre:step。"""
        if self._origin is None:
            raise RuntimeError("The run is not being debugged.")
        self._session = _DemoSession(self._origin)
        self._session._advance()
        return {"session_id": _DEMO_SID, "run_id": _DEMO_RUN_ID}

    def modify(self, patch: dict[str, Any]) -> None:
        if self._session is None:
            raise RuntimeError("The run is not being debugged.")
        self._session.modify(patch)

    def inject(self, text: str) -> str:
        if self._session is None:
            raise RuntimeError("The run is not being debugged.")
        return self._session.inject(text)


# ---------------------------------------------------------------------------
# Offline(--offline --run-dir 直读 run 产物;只读回放,无控制面)
# ---------------------------------------------------------------------------


class OfflineDebugSource:
    """离线回放(§0/§3):直读 ``<artifacts>/runs/<id>`` 的 trace.jsonl +
    checkpoint.json + meta.json;轨迹/帧检视可用,控制命令回 §3 人话。"""

    writable = False
    conn = "offline"

    def __init__(self, run_dir: str | Path) -> None:
        root = Path(run_dir)
        for name in ("meta.json", "trace.jsonl", "checkpoint.json"):
            if not (root / name).is_file():
                raise SystemExit(f"找不到 run 产物目录: {root}(缺 {name})")
        self._dir = root
        self._meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        self._signals = [
            row for row in (
                json.loads(ln)
                for ln in (root / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            )
            if isinstance(row, dict) and row.get("name")  # 版本头行滤掉
        ]
        self._checkpoint = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
        result_path = root / "result.json"
        self._end_status = "done"
        if result_path.is_file():
            try:
                self._end_status = str(
                    json.loads(result_path.read_text(encoding="utf-8")).get("status") or "done")
            except (OSError, ValueError):
                pass
        elif any(s["name"] == "run.aborted" for s in self._signals):
            self._end_status = "aborted"

    def label(self) -> str:
        return str(self._meta.get("skill") or self._meta.get("run_id") or "run")

    def artifacts_path(self) -> str:
        return str(self._dir)

    def list_skills(self) -> list[str]:
        return []

    # ------------------------------------------------------------------
    # 只读面
    # ------------------------------------------------------------------

    def open_live(self, skill: str | None, run_input: dict[str, Any],
                  breakpoints: list[tuple[str, str]]) -> dict[str, Any]:
        raise PermissionError(_OFFLINE_READONLY)

    def open_replay(self, run_id: str, until_step: int | None,
                    breakpoints: list[tuple[str, str]]) -> dict[str, Any]:
        raise PermissionError(_OFFLINE_READONLY)

    def snapshot(self) -> dict[str, Any]:
        frames = sorted(self._checkpoint.get("frames", []),
                        key=lambda f: f.get("depth", 0))
        return {
            "session_id": "", "run_id": str(self._meta.get("run_id") or self._dir.name),
            "state": "detached", "pause_point": None,
            "breakpoints": [],
            "frame_stack": [
                {"frame_id": f.get("frame_id"), "skill": f.get("skill"),
                 "depth": f.get("depth")}
                for f in frames
            ],
            "rerunnable": False, "end_status": self._end_status,
        }

    def signals(self) -> list[dict[str, Any]]:
        return list(self._signals)

    def frame(self, fid: str) -> dict[str, Any] | None:
        for f in self._checkpoint.get("frames", []):
            if f.get("frame_id") != fid:
                continue
            ctx = f.get("context") or {}
            return {
                "frame_id": f.get("frame_id"), "skill": f.get("skill"),
                "status": f.get("status"), "input": f.get("input"),
                "result": f.get("result"), "error": f.get("error"),
                "usage": f.get("usage") or {},
                "messages": ctx.get("messages") or [],
                "working": ctx.get("working"),
            }
        return None

    # ------------------------------------------------------------------
    # 控制面(只读源:一律 PermissionError,消息即 §3 人话)
    # ------------------------------------------------------------------

    def add_breakpoint(self, kind: str, match: str = "*",
                       until: int | None = None) -> dict[str, Any]:
        raise PermissionError(_OFFLINE_READONLY)

    def remove_breakpoint(self, num: int) -> bool:
        raise PermissionError(_OFFLINE_READONLY)

    def command(self, cmd: str) -> None:
        raise PermissionError(_OFFLINE_READONLY)

    def detach(self) -> None:
        raise PermissionError(_OFFLINE_READONLY)

    def rerun(self) -> dict[str, Any]:
        raise PermissionError(_OFFLINE_READONLY)

    def modify(self, patch: dict[str, Any]) -> None:
        raise PermissionError(_OFFLINE_READONLY)

    def inject(self, text: str) -> None:
        raise PermissionError(_OFFLINE_READONLY)


# ---------------------------------------------------------------------------
# Online(D2):打 agent-os-web /api/debug/*(无 /platform 前缀)+ SSE 流
# ---------------------------------------------------------------------------


def _human(resp: dict[str, Any], what: str) -> Any:
    """统一信封 → 数据或 §6 人话 RuntimeError。
    错误语义(host/web/app.py:1486):404 找不到 / 400 请求非法 / 409 状态冲突 /
    run 校验失败 200+{"status":"failed"};status 0 = HTTP 发起失败(连接层)。"""
    if resp.get("ok"):
        j = resp.get("json")
        if isinstance(j, dict) and j.get("status") == "failed":
            raise RuntimeError(str(j.get("error") or f"{what}: run 校验失败"))
        return j
    err = str(resp.get("error") or "")
    if not resp.get("status"):
        raise RuntimeError(f"连不上服务({what}):{err}")
    raise RuntimeError(err or f"{what} 失败")


class OnlineDebugSource:
    """live 源(docs/TUI-DEBUG.md §3/§4;D2)。

    - REST 全走 ``AgentOsClient``(裸 base,无 /platform;统一信封纪律);
    - 控制是**异步**的(``sync_control = False``):command() POST 即返,停点/
      终态经 SSE(或断线回落轮询)回流,由 app.tick() drain;
    - SSE 线程只往 ``queue.Queue`` 入队,不碰任何状态(§4 纪律;
      RunManager call_soon_threadsafe 的 TUI 对偶);
    - 断点界面编号 num 是客户端显示号(REST 只有 bp_id;GDB 习惯)——
      snapshot() 时 reconcile,id→num 映射稳定;
    - 入口断点(CLI debug.py:348 pdb 语义):open_live/open_replay 自动带
      ("step","*") 断点,首次暂停由 app 调 ack_entry_breakpoint() 经 REST 删除;
    - ``until`` 步数断点:REST 断点端点无 until 字段(DebugBreakpointBody
      只有 kind/match),live 下诚实拒绝(replay 的 until_step / demo 可用)。
    """

    writable = True
    conn = "online"
    sync_control = False

    def __init__(self, base_url: str = "", client: Any = None,
                 sse_factory: Any = None,
                 store: SessionStore | None = None) -> None:
        self._client = client if client is not None else AgentOsClient(
            base_url or DEBUG_DEFAULT_BASE_URL)
        self._sse_factory = sse_factory if sse_factory is not None else SseClient
        #: 已知会话账本(§5.1;None = 不记账,无头/测试缺省;真入口由
        #: __main__ 注 SESSION_STORE_PATH 落盘)
        self.session_store = store
        self._sid = ""
        self._run_id = ""
        self._events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._sse: Any = None
        self._thread: threading.Thread | None = None
        #: 断线标记(connection_changed(False) / SSE 线程死亡;app 据此回落轮询)
        self.sse_down = False
        self.ended = False
        self._detached = False  # 显式 detach(D4):快照 404 后的诚实终态面
        self._end_status: str | None = None
        self._bp_nums: dict[str, int] = {}  # bp_id → 界面编号(稳定)
        self._num_seq = 0
        self._entry_bp_id: str | None = None

    # ------------------------------------------------------------------
    # 基本面
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._sid

    def label(self) -> str:
        return self._sid or str(getattr(self._client, "base_url", "live"))

    def artifacts_path(self) -> str:
        return ""  # run detail 不带产物路径;offline 源才有

    def list_skills(self) -> list[str]:
        resp = self._client.list_skills()
        rows = resp.get("json") if resp.get("ok") else None
        if not isinstance(rows, list):
            return []
        return [str(r.get("name")) for r in rows if isinstance(r, dict) and r.get("name")]

    def ping(self) -> str | None:
        """连通性探针(入口 fail-fast):None = 可达,否则错误人话。"""
        resp = self._client.list_skills()
        if resp.get("ok"):
            return None
        return str(resp.get("error") or "连不上服务")

    def end_status(self) -> str | None:
        """run 终态(run_end 事件缺 status 时经 run detail 回填,缓存)。"""
        if self._end_status is None and self._run_id:
            resp = self._client.run_detail(self._run_id)
            if resp.get("ok") and isinstance(resp.get("json"), dict):
                status = str(resp["json"].get("status") or "")
                if status in ("done", "failed", "aborted"):
                    self._end_status = status
        return self._end_status

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    def open_live(self, skill: str | None, run_input: dict[str, Any],
                  breakpoints: list[tuple[str, str]]) -> dict[str, Any]:
        bps = [("step", "*"), *breakpoints]  # 入口断点(pdb 语义;首停后删)
        j = _human(self._client.debug_open_session(skill, run_input, bps),
                   "开调试会话")
        self._on_opened(str(j["session_id"]), str(j["run_id"]), entry=True)
        self._record(str(skill or "live"))
        return {"session_id": self._sid, "run_id": self._run_id}

    def open_replay(self, run_id: str, until_step: int | None,
                    breakpoints: list[tuple[str, str]]) -> dict[str, Any]:
        # until_step 由服务端注册一次性步数断点;缺省则客户端补入口断点
        bps = list(breakpoints) if until_step is not None else [("step", "*"), *breakpoints]
        j = _human(self._client.debug_open_session(None, None, bps,
                                                   replay_run_id=run_id,
                                                   until_step=until_step),
                   "开 replay 会话")
        self._on_opened(str(j["session_id"]), str(j["run_id"]),
                        entry=until_step is None)
        self._record(f"replay:{run_id}")
        return {"session_id": self._sid, "run_id": self._run_id, "mode": "replay"}

    def attach(self, sid: str) -> dict[str, Any]:
        """挂既有会话(--session SID / session <SID>):快照校验存在性,随即接 SSE 流。"""
        resp = self._client.debug_snapshot(sid)
        if not resp.get("ok"):
            raise RuntimeError(str(resp.get("error") or f"找不到会话: {sid}"))
        doc = resp.get("json") or {}
        self._on_opened(sid, str(doc.get("run_id") or ""), entry=False)
        self._record(sid)
        if doc.get("state") == "detached":
            self.ended = True
        return {"session_id": self._sid, "run_id": self._run_id}

    def _record(self, label: str) -> None:
        """已知会话记账(本机账本,§5.1;账本不是真相,失败静默)。"""
        if self.session_store is None:
            return
        base = str(getattr(self._client, "base_url", ""))
        with suppress(Exception):
            self.session_store.record(self._sid, self._run_id, label, base)

    def _on_opened(self, sid: str, run_id: str, entry: bool) -> None:
        if self._sse is not None:
            self._sse.stop_stream()  # 换绑先停旧流(rerun/session 切换)
        while not self._events.empty():  # 旧会话尾巴不进新会话
            self._events.get_nowait()
        self._sid, self._run_id = sid, run_id
        self.ended = False
        self._detached = False
        self.sse_down = False
        self._end_status = None
        self._bp_nums = {}
        self._num_seq = 0
        self._entry_bp_id = None
        if entry:  # 入口断点 id:注册顺序第一个(breakpoints 在 run 启动前注册)
            doc = _human(self._client.debug_snapshot(sid), "读会话快照")
            bps = doc.get("breakpoints") or []
            if bps:
                self._entry_bp_id = str(bps[0].get("id") or "") or None
        self._start_stream()

    def ack_entry_breakpoint(self, pause_point: dict[str, Any]) -> bool:
        """首停后删入口断点(app 在停止行打印之后调;CLI REPL 同语义)。
        返回是否真删(删了调用方应重拉快照刷新断点表)。"""
        bp_id = self._entry_bp_id
        if not bp_id:
            return False
        if bp_id not in (pause_point.get("breakpoint_ids") or []):
            return False
        self._entry_bp_id = None
        resp = self._client.debug_remove_breakpoint(self._sid, bp_id)
        return bool(resp.get("ok"))

    # ------------------------------------------------------------------
    # 只读面
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        if not self._sid:
            return _empty_snapshot()
        resp = self._client.debug_snapshot(self._sid)
        if not resp.get("ok") and resp.get("status") == 404 and self._detached:
            # 显式 detach 后快照端点 404(DELETE 摘注册表)是**真态**:会话确已
            # 摘下。run 未终态时端不出终态快照——抛错让轮询静默下周期再来;
            # 终态后给合成 detached 快照,终态行照常落地
            if self.end_status() is None:
                raise RuntimeError(f"会话已摘下,run 未终态({self._sid})")
            return {"session_id": self._sid, "run_id": self._run_id,
                    "state": "detached", "pause_point": None, "breakpoints": [],
                    "frame_stack": [], "rerunnable": False,
                    "end_status": self._end_status}
        doc = dict(_human(resp, "读会话快照"))
        bps = []
        for bp in doc.get("breakpoints") or []:
            bp = dict(bp)
            bp["num"] = self._num_for(str(bp.get("id") or ""))
            bps.append(bp)
        doc["breakpoints"] = bps
        if doc.get("state") == "detached":
            doc["end_status"] = self.end_status()
        else:
            doc["end_status"] = None
        return doc

    def _num_for(self, bp_id: str) -> int:
        if bp_id not in self._bp_nums:
            self._num_seq += 1
            self._bp_nums[bp_id] = self._num_seq
        return self._bp_nums[bp_id]

    def signals(self) -> list[dict[str, Any]]:
        if not self._run_id:
            return []
        j = _human(self._client.run_signals(self._run_id), "读 run 信号")
        return list(j) if isinstance(j, list) else []

    def frame(self, fid: str) -> dict[str, Any] | None:
        if not self._sid:
            return None
        resp = self._client.debug_frame(self._sid, fid)
        if not resp.get("ok"):
            if resp.get("status") == 404:
                return None
            raise RuntimeError(str(resp.get("error") or "读帧失败"))
        return resp.get("json")

    # ------------------------------------------------------------------
    # 控制面(REST;异步语义——效果经 SSE/轮询回流)
    # ------------------------------------------------------------------

    def add_breakpoint(self, kind: str, match: str = "*",
                       until: int | None = None) -> dict[str, Any]:
        if until is not None:
            raise RuntimeError(_LIVE_UNTIL)
        if not self._sid:
            raise RuntimeError("The run is not being debugged.")
        bp = dict(_human(self._client.debug_add_breakpoint(self._sid, kind, match),
                         "加断点"))
        bp["num"] = self._num_for(str(bp.get("id") or ""))
        return bp

    def remove_breakpoint(self, num: int) -> bool:
        if not self._sid:
            raise RuntimeError("The run is not being debugged.")
        bp_id = next((bid for bid, n in self._bp_nums.items() if n == num), None)
        if bp_id is None:
            return False
        resp = self._client.debug_remove_breakpoint(self._sid, bp_id)
        if not resp.get("ok"):
            if resp.get("status") == 404:
                return False
            raise RuntimeError(str(resp.get("error") or "删断点失败"))
        return True

    def command(self, cmd: str) -> None:
        if not self._sid:
            raise RuntimeError("The run is not being debugged.")
        _human(self._client.debug_command(self._sid, cmd), f"命令 {cmd}")

    def detach(self) -> None:
        """DELETE 会话:detach 放行,run 继续跑完(注册表摘除 → 快照转 404,
        终态面见 snapshot() 的 _detached 分支;SSE 流仍会送 run_end)。"""
        if not self._sid:
            raise RuntimeError("The run is not being debugged.")
        _human(self._client.debug_close(self._sid), "detach")
        self._detached = True

    def rerun(self) -> dict[str, Any]:
        """POST …/rerun:以创建参数重开新会话(SSE 流换绑;origin 含启动断点,
        入口断点随之重生 → entry=True)。无 origin(replay/CLI 会话)→ 服务端
        400,_human 原样转述人话。"""
        if not self._sid:
            raise RuntimeError("The run is not being debugged.")
        j = _human(self._client.debug_rerun(self._sid), "rerun")
        self._on_opened(str(j["session_id"]), str(j["run_id"]), entry=True)
        self._record("rerun")
        return {"session_id": self._sid, "run_id": self._run_id}

    def modify(self, patch: dict[str, Any]) -> None:
        """POST …/modify(改完即放行,效果经 SSE/轮询回流)。停点位置校验
        在命令层本地做(§6 语式统一);409 竞态经 _human 翻人话。"""
        if not self._sid:
            raise RuntimeError("The run is not being debugged.")
        _human(self._client.debug_modify(self._sid, patch), "set args")

    def inject(self, text: str) -> str:
        """POST …/inject(缺省暂停帧;注入即放行)→ 实际注入的 frame_id。"""
        if not self._sid:
            raise RuntimeError("The run is not being debugged.")
        j = _human(self._client.debug_inject(self._sid, text), "inject")
        return str((j or {}).get("frame_id") or "")

    # ------------------------------------------------------------------
    # SSE 线程 → queue(§4:线程只入队;drain_events 由主循环 tick 消费)
    # ------------------------------------------------------------------

    def _start_stream(self) -> None:
        self._sse = self._sse_factory()
        self._sse.event_received = lambda evt, data: self._events.put((evt, data))
        self._sse.connection_changed = lambda ok: self._events.put(
            ("__conn__", {"ok": bool(ok)}))
        self._sse.start_stream(
            str(getattr(self._client, "base_url", "")).removesuffix("/platform"),
            path=AgentOsClient.debug_stream_path(self._sid))
        self._thread = threading.Thread(target=self._sse.run_forever, daemon=True)
        self._thread.start()

    def drain_events(self) -> list[tuple[str, dict[str, Any]]]:
        """主循环取走积压事件(本方法在主线程跑,内部的簿记不算碰 state)。"""
        out: list[tuple[str, dict[str, Any]]] = []
        while True:
            try:
                evt, data = self._events.get_nowait()
            except queue.Empty:
                break
            if isinstance(data, dict) and data.get("session_id") \
                    and data["session_id"] != self._sid:
                continue  # 换绑(rerun/session 切换)后旧会话的尾巴不落地
            if evt == "__conn__":
                self.sse_down = not data.get("ok")
                evt = "connection"
            elif evt == "run_end":
                self.ended = True
                status = str((data or {}).get("status") or "")
                if status in ("done", "failed", "aborted"):
                    self._end_status = status
            out.append((evt, data))
        return out

    def feed_event(self, evt: str, data: dict[str, Any]) -> None:
        """测试注入口(等价 SSE 线程的 queue.put;无头用例不经真线程)。"""
        self._events.put((evt, data))

    def close(self) -> None:
        """停 SSE 线程(run_end/退出时;daemon 线程,进程退即收)。"""
        if self._sse is not None:
            self._sse.stop_stream()

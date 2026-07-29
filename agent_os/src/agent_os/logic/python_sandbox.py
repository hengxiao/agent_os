"""PythonSandboxLogicKernel(DESIGN.md §9.2/§9.7;M5)。

用于 LLM 动态代码(强制,无配置项可关闭)与声明 ``logic: {mode: sandbox}``
的 code 技能。隔离阶梯(后端替换,契约不变):subprocess+rlimits(v1)→
OS 级(seccomp/nsjail)→ 容器 → microVM。

**v1 实际提供的隔离(逐项,勿多信一项)**:

- ✅ ``setrlimit``:CPU / 地址空间 / 文件大小 / fd 数(``limits.py``);
- ✅ **env 白名单**:子进程不继承宿主环境,``*_API_KEY`` 等凭证读不到(``_ENV_ALLOWLIST``);
- ✅ **cwd 隔离**:每次执行一个空临时目录,不是宿主 cwd(通常为用户仓库根);
- ❌ **网络不隔离**——脚本可任意外连(M5 用 ``unshare``/nsjail 加固);
- ❌ **文件系统不隔离**——绝对路径仍可读写宿主任意文件(仅 rlimits 兜底);
- ❌ **进程不隔离**——可 fork 子进程,且当前不杀进程组。

即:**v1 挡住"顺手就能拿到凭证/翻到项目文件",挡不住蓄意攻击**。需要真隔离
请用 Docker 后端(``--network none`` + ``--cap-drop ALL`` + 只读 rootfs)。
警示(§9.2):venv 不是沙箱——只隔离包依赖。

code 技能入口(§9.2/§9.4):``source`` 为可 import 的模块路径时改走驱动脚本
(``_DRIVER``)——沙箱内 import 模块、以 ``ctx=None`` 调 handler(v1 纯计算,无回调;
handler 用 ctx 会 AttributeError → RUNTIME_ERROR,符合"沙箱 v1 纯计算"语义),
结果以最后一行 ``{"value": ...}`` JSON 回传。``-I`` 隐含 ``-E``(PYTHONPATH 不进
sys.path),故 env 传 ``PYTHONPATH = os.pathsep.join(sys.path)`` 后由驱动脚本自行
读取并前置插入,保证宿主侧模块(如技能 handler 包)在沙箱内可 import。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
import tempfile
import time
from typing import Any

from agent_os.api.v1 import (
    ExecError,
    ExecRequest,
    ExecResult,
    ExecUsage,
    LogicError,
    TrustLevel,
)
from agent_os.kernel.errors import MaxDepthExceeded, RunAborted
from agent_os.logic.inprocess import _is_module_path
from agent_os.logic.limits import apply_limits

_log = logging.getLogger("agent_os.logic")

#: 不可被单帧吞掉的硬失败(§3.2 传播边界):syscall 分发抛出这些时不降级为
#: 脚本可见的错误观察,而是中止编排并弹到 Run 边界。
_HARD_FAILURES = (RunAborted, MaxDepthExceeded)

#: 默认兜底墙钟(调用方未给 limits.wall_time 时)
_DEFAULT_WALL_TIME = 30.0

#: 沙箱内 syscall 通道的公共前奏(CODE-ORCHESTRATION.md §2.2)。
#:
#: 环境变量 ``AGENT_OS_SYSCALL_FD`` 存在时建立 ctx:每次调用写一行 JSON 请求、
#: 读一行 JSON 响应(阻塞语义,沙箱侧无其他工作)。同一传输层派生两种 ctx——
#: ``_SyncCtx`` 给编排脚本(LLM 写直线代码,无 async 样板),``_AsyncCtx`` 给
#: code 技能 handler(``await ctx.call_tool(...)``,与 TRUSTED 档契约逐字一致)。
_SYSCALL_PRELUDE = """\
import json, os, socket

class _Transport:
    def __init__(self, fd):
        self._f = socket.socket(fileno=fd).makefile("rwb", buffering=0)
        self._seq = 0

    def call(self, kind, name, args):
        self._seq += 1
        req = {"syscall": kind, "id": "s%d" % self._seq, "name": name, "args": args}
        self._f.write((json.dumps(req, ensure_ascii=False) + "\\n").encode("utf-8"))
        line = self._f.readline()
        if not line:
            raise RuntimeError("syscall 通道已关闭(内核侧终止或超限)")
        return json.loads(line)


class _SyncCtx:
    def __init__(self, transport):
        self._t = transport

    def call_tool(self, tool, args):
        return self._t.call("tool", tool, args)

    def invoke(self, skill, input):
        r = self._t.call("skill", skill, input)
        if not r["ok"]:
            raise RuntimeError((r.get("error") or {}).get("message") or ("子技能 %s 失败" % skill))
        return r["value"]


class _AsyncCtx(_SyncCtx):
    async def call_tool(self, tool, args):
        return _SyncCtx.call_tool(self, tool, args)

    async def invoke(self, skill, input):
        return _SyncCtx.invoke(self, skill, input)


def _make_ctx(cls):
    fd = os.environ.get("AGENT_OS_SYSCALL_FD")
    return cls(_Transport(int(fd))) if fd else None
"""

#: code 技能驱动脚本(§9.2):argv = [args_json, module, entry];结果 print 为最后一行
#: ``{"value": ...}`` JSON;异常 → traceback 入 stderr 并非零退出(归一化为 RUNTIME_ERROR)。
#: ctx:无 syscall 通道时为 None(纯计算,v1 语义);有则为 _AsyncCtx(§9.3 契约对齐)。
_DRIVER = _SYSCALL_PRELUDE + """
import asyncio, importlib, sys, traceback

try:
    sys.path[:0] = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    args = json.loads(sys.argv[1])
    mod = importlib.import_module(sys.argv[2])
    fn = getattr(mod, sys.argv[3])
    result = asyncio.run(fn(args, _make_ctx(_AsyncCtx)))
    print(json.dumps({"value": result}))
except Exception:
    traceback.print_exc()
    sys.exit(1)
"""

#: 编排脚本驱动(CODE-ORCHESTRATION.md §2.1):argv = [source];在带 ``ctx`` 的
#: 命名空间里执行 LLM 写的直线脚本,取变量 ``result`` 为返回值(最后一行 JSON)。
_ORCH_DRIVER = _SYSCALL_PRELUDE + """
import sys, traceback

try:
    sys.path[:0] = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    ns = {"ctx": _make_ctx(_SyncCtx), "__name__": "__main__"}
    exec(compile(sys.argv[1], "<orchestration>", "exec"), ns)
    print(json.dumps({"value": ns.get("result")}, ensure_ascii=False, default=str))
except Exception:
    traceback.print_exc()
    sys.exit(1)
"""


#: 传给沙箱子进程的环境变量白名单(§9.2)。**只放行 Python 自举必需项**——
#: 宿主 env 里的 ``*_API_KEY`` / ``*_TOKEN`` / 云凭证一律不进沙箱。
#: 注意不含 ``HOME``:避免 ``~/.aws/credentials``、``~/.config/gh/hosts.yml``
#: 这类按 HOME 定位的凭证文件被顺手读到(路径本身仍可硬编码,但那已是文件系统
#: 隔离的职责,见模块 docstring 的 v1 偏差说明)。
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT", "TMPDIR")


def _sandbox_env(pythonpath: str | None) -> dict[str, str]:
    """构造沙箱子进程的环境:白名单 + 按需的 PYTHONPATH。

    ``pythonpath`` 非 None 时(驱动脚本形态)追加——``-I`` 隐含 ``-E`` 使它不进
    ``sys.path``,由驱动脚本自行读取并前置插入,保证宿主侧 handler 包可 import。
    """
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return env


def _truncate(text: str, max_bytes: int | None) -> str:
    if max_bytes is None:
        return text
    return text.encode("utf-8", errors="replace")[:max_bytes].decode("utf-8", errors="replace")


def _parse_driver_stdout(stdout: str) -> tuple[Any, str]:
    """驱动脚本协议:最后一个非空行 ``{"value": ...}`` → ``(value, 其余行)``;否则原样返回。"""
    lines = stdout.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].strip():
            continue
        try:
            payload = json.loads(lines[i])
        except ValueError:
            return None, stdout
        if isinstance(payload, dict) and "value" in payload:
            return payload["value"], "\n".join(lines[:i] + lines[i + 1 :])
        return None, stdout
    return None, stdout


async def _serve_syscalls(
    sock: socket.socket, dispatch_fn: Any, hard_failure: list[BaseException] | None = None
) -> int:
    """syscall 服务循环(CODE-ORCHESTRATION.md §2.2):逐条读请求 → 分发 → 写响应。

    纯传输层:分发与**限额**都由内核回调决定(``_dispatch_call`` 一条闸门;
    调用上限在内核侧计数并以结构化错误回给脚本,脚本可自行处置)。跑飞脚本
    由 ``wall_time`` 兜底。

    **硬失败不在此处降级**(§3.2):``RunAborted`` / ``MaxDepthExceeded`` 等
    经 ``hard_failure`` 传回调用方 re-raise;脚本侧只收到"通道关闭"并崩掉,
    整次执行的结果一律作废——若在此转成一条脚本可无视的错误,预算超限与
    ToolGuard 的 Stop 就会被脚本吃掉,run 照常跑完(实测过的真实缺陷)。
    """
    served = 0
    try:
        reader, writer = await asyncio.open_connection(sock=sock)
    except OSError:  # pragma: no cover — 通道建立失败按无 syscall 处理
        return 0

    async def respond(payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        await writer.drain()  # 背压:大结果(如文件全文)不阻塞事件循环

    try:
        while True:
            line = await reader.readline()
            if not line:
                return served  # 脚本正常结束,通道 EOF
            try:
                msg = json.loads(line)
                kind, name, args = msg["syscall"], msg["name"], msg.get("args") or {}
                call_id = msg.get("id", "")
            except (ValueError, KeyError) as e:
                await respond({"id": "", "ok": False, "error": {"kind": "invalid_args",
                                                                "message": f"syscall 报文畸形: {e}"}})
                continue
            served += 1
            try:
                payload = await dispatch_fn(kind, name, args)
            except asyncio.CancelledError:
                raise
            except _HARD_FAILURES as e:
                # 不回响应、直接停服:脚本下次 syscall 读到 EOF 即崩,
                # 调用方 re-raise 使其弹到 Run 边界(§3.2)
                _log.warning("syscall 遇硬失败,中止编排(%s %s): %r", kind, name, e)
                if hard_failure is not None:
                    hard_failure.append(e)
                return served
            except Exception as e:  # noqa: BLE001 — 普通失败折叠为脚本可处置的错误观察
                _log.warning("syscall 分发失败(%s %s): %r", kind, name, e)
                await respond({"id": call_id, "ok": False,
                               "error": {"kind": "internal",
                                         "message": f"{type(e).__name__}: {e}",
                                         "retryable": False}})
                return served
            await respond({"id": call_id, **payload})
    except ConnectionError:
        return served  # 脚本被杀/通道断:静默收尾
    finally:
        with contextlib.suppress(Exception):
            writer.close()


class PythonSandboxLogicKernel:
    """``agent_os.api.v1.LogicKernel`` 协议实现(M5),进程级隔离。

    ``sys.executable -I -c <source>`` 起子进程;``preexec_fn`` 设 rlimits;
    父进程 ``wait_for`` 兜底 wall_time,超时杀进程 → LIMIT_EXCEEDED。
    """

    name: str = "python_sandbox"
    trust: TrustLevel = TrustLevel.SANDBOX

    async def execute(self, req: ExecRequest) -> ExecResult:
        limits = req.limits
        wall = limits.wall_time if limits.wall_time else _DEFAULT_WALL_TIME
        pythonpath = os.pathsep.join(p for p in sys.path if p)
        # 三种形态(CODE-ORCHESTRATION.md §2.1):
        # 模块路径 → code 技能驱动;dispatch_fn 非 None 的源码 → 编排驱动;否则原样 -c(§9.4)
        module_mode = "\n" not in req.source and _is_module_path(req.source)
        orchestrate_mode = not module_mode and req.dispatch_fn is not None
        # §9.2 env 白名单:子进程**不继承**宿主环境——LLM 动态代码一行
        # ``os.environ["ANTHROPIC_API_KEY"]`` 即可读走凭证并经网络外发。
        # 只放行 Python 自举必需项,其余(含全部 *_API_KEY / *_TOKEN)一律不传。
        env = _sandbox_env(pythonpath if (module_mode or orchestrate_mode) else None)
        if module_mode:
            argv = [sys.executable, "-I", "-c", _DRIVER, json.dumps(req.args), req.source, req.entry]
        elif orchestrate_mode:
            argv = [sys.executable, "-I", "-c", _ORCH_DRIVER, req.source]
        else:
            argv = [sys.executable, "-I", "-c", req.source]

        def preexec() -> None:
            apply_limits(limits)

        # syscall 通道(§2.2):socketpair 全双工,子进程侧 fd 经 pass_fds 继承、
        # 编号由 env 告知(``-I`` 只阻止 PYTHONPATH 进 sys.path,不清空 os.environ)
        syscall_pair: tuple[socket.socket, socket.socket] | None = None
        pass_fds: tuple[int, ...] = ()
        if req.dispatch_fn is not None:
            parent_sock, child_sock = socket.socketpair()
            child_sock.set_inheritable(True)
            syscall_pair = (parent_sock, child_sock)
            pass_fds = (child_sock.fileno(),)
            env = {**env, "AGENT_OS_SYSCALL_FD": str(child_sock.fileno())}

        start = time.perf_counter()
        # §9.2 cwd 隔离:每次执行一个空临时目录,而不是继承宿主 cwd(通常是用户
        # 仓库根)——相对路径写入、``os.listdir(".")`` 侦察都被圈在这里。
        # 不是安全边界(绝对路径仍可达,见模块 docstring 的 v1 偏差),但消除了
        # "顺手就能读写整个项目"这一最大误伤面。
        with tempfile.TemporaryDirectory(prefix="agent-os-sbx-") as cwd:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=preexec,
                    env=env,
                    pass_fds=pass_fds,
                    cwd=cwd,
                )
            finally:
                if syscall_pair is not None:
                    syscall_pair[1].close()  # 子进程已继承副本,父侧关掉自己的一端
            return await self._collect(
                proc, req, syscall_pair, start, wall, module_mode or orchestrate_mode
            )

    async def _collect(
        self,
        proc: Any,
        req: ExecRequest,
        syscall_pair: tuple[socket.socket, socket.socket] | None,
        start: float,
        wall: float,
        driver_mode: bool,
    ) -> ExecResult:
        """等待子进程结束并归一化结果(syscall 服务循环在此期间并发跑)。"""
        limits = req.limits

        server: asyncio.Task[int] | None = None
        # 硬失败出口(§3.2):syscall 分发抛出的 RunAborted / MaxDepthExceeded 等
        # 不可被单帧吞掉,经此单元传回并在下面原样 re-raise。
        hard_failure: list[BaseException] = []
        if syscall_pair is not None:
            server = asyncio.create_task(
                _serve_syscalls(syscall_pair[0], req.dispatch_fn, hard_failure)
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=wall)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            ms = int((time.perf_counter() - start) * 1000)
            return ExecResult(
                error=ExecError(
                    kind=LogicError.LIMIT_EXCEEDED,
                    message=f"超过 wall_time={wall}s,子进程已杀",
                ),
                usage=ExecUsage(cpu_ms=ms, wall_ms=ms),
            )
        finally:
            if proc.returncode is None:  # 取消/异常路径:不留孤儿子进程
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            if server is not None:
                server.cancel()
            if syscall_pair is not None:
                syscall_pair[0].close()
        if hard_failure:
            # 脚本可能已捕获那条错误并"正常"结束——但硬失败必须弹到 Run 边界,
            # 故此处**先于**任何结果归一化上抛(§3.2 传播边界)。
            raise hard_failure[0]
        ms = int((time.perf_counter() - start) * 1000)
        stdout = _truncate(stdout_b.decode("utf-8", errors="replace"), limits.stdout_bytes)
        stderr = _truncate(stderr_b.decode("utf-8", errors="replace"), limits.stdout_bytes)
        # time.process_time 不含子进程;v1 记 wall_ms,cpu_ms 同 wall_ms(§9.7 usage 口径简化)
        usage = ExecUsage(cpu_ms=ms, wall_ms=ms)
        if proc.returncode != 0:
            lines = stderr.strip().splitlines()
            tail = lines[-1] if lines else f"退出码 {proc.returncode}"
            return ExecResult(
                stdout=stdout,
                stderr=stderr,
                error=ExecError(kind=LogicError.RUNTIME_ERROR, message=tail[-500:], traceback=stderr),
                usage=usage,
            )
        if driver_mode:
            value, body = _parse_driver_stdout(stdout)
            return ExecResult(value=value, stdout=body, stderr=stderr, usage=usage)
        # result 解析(stdout 最后一行 JSON)由 python_exec 工具层做(§9.4)
        return ExecResult(value=stdout, stdout=stdout, stderr=stderr, usage=usage)

"""DockerPythonSandboxLogicKernel(docs/DESIGN.md §9.2 隔离阶梯:容器层)。

与 subprocess 沙箱(:mod:`agent_os.logic.python_sandbox`)同一 ``LogicKernel`` 契约、
后端替换。``docker run --rm`` 一次性容器,默认加固:

- ``--network none``:**系统级断网**(补齐 subprocess 沙箱"网络隔离不做"的最大缺口);
- ``--memory`` / ``--cpus`` / ``--pids-limit``:cgroup 强制限额(退出码 137 = OOM → LIMIT_EXCEEDED);
- ``--read-only`` + ``--tmpfs /tmp``:根文件系统只读;
- ``--cap-drop ALL`` + ``--security-opt no-new-privileges``:最小权能。

code 技能(模块路径)复用 subprocess 沙箱的驱动脚本协议(``_DRIVER``):仓库只读挂载
(``mounts={host: container}``)+ ``-e PYTHONPATH``(未显式给 ``env`` 时自动取 mounts 的
容器路径拼接)。wall 超时用具名容器 ``docker kill`` 兜底,容器不残留。

进一步加固选项(留给宿主配置,本实现保持"简单"):``--user`` 非 root、seccomp/apparmor
配置、镜像裁剪。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid

from agent_os.api.v1 import (
    ExecError,
    ExecRequest,
    ExecResult,
    ExecUsage,
    LogicError,
    TrustLevel,
)
from agent_os.logic.inprocess import _is_module_path
from agent_os.logic.python_sandbox import _DRIVER, _parse_driver_stdout, _truncate

_DEFAULT_WALL_TIME = 30.0
_OOM_EXIT_CODE = 137

_log = logging.getLogger("agent_os.logic.docker_sandbox")


class DockerUnavailableError(RuntimeError):
    """宿主没有 docker CLI(§9.2 后端不可用,装配期显式失败)。"""


class DockerPythonSandboxLogicKernel:
    """``agent_os.api.v1.LogicKernel`` 协议实现,Docker 容器级隔离。"""

    name: str = "docker_python_sandbox"
    trust: TrustLevel = TrustLevel.SANDBOX

    def __init__(
        self,
        image: str = "python:3.11-slim",
        *,
        mounts: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        memory: str = "256m",
        cpus: str = "1.0",
        pids_limit: int = 64,
        network: str = "none",
    ) -> None:
        if not shutil.which("docker"):
            raise DockerUnavailableError("DockerPythonSandboxLogicKernel 需要 docker CLI")
        self.image = image
        self.mounts = dict(mounts or {})
        self.env = dict(env or {})
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.network = network

    def _argv(self, req: ExecRequest, name: str) -> tuple[list[str], bool]:
        module_mode = "\n" not in req.source and _is_module_path(req.source)
        argv = [
            "docker", "run", "--rm", "--name", name,
            "--network", self.network,
            "--memory", self.memory,
            "--cpus", self.cpus,
            f"--pids-limit={self.pids_limit}",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        ]
        for host, container in self.mounts.items():
            argv += ["-v", f"{host}:{container}:ro"]
        env = dict(self.env)
        if module_mode and "PYTHONPATH" not in env:
            # code 技能:驱动脚本靠 PYTHONPATH import 宿主模块,自动取挂载点拼接(§9.2)
            env["PYTHONPATH"] = ":".join(self.mounts.values()) or "/app"
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        argv.append(self.image)
        if module_mode:
            argv += ["python", "-I", "-c", _DRIVER, json.dumps(req.args), req.source, req.entry]
        else:
            argv += ["python", "-I", "-c", req.source]
        return argv, module_mode

    async def execute(self, req: ExecRequest) -> ExecResult:
        limits = req.limits
        wall = limits.wall_time if limits.wall_time else _DEFAULT_WALL_TIME
        name = f"agent-os-sbx-{uuid.uuid4().hex[:12]}"
        argv, module_mode = self._argv(req, name)

        start = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=wall)
        except TimeoutError:
            await self._kill_container(name)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)  # 容器死后 docker CLI 随之退出
            except TimeoutError:
                proc.kill()
                await proc.wait()
            ms = int((time.perf_counter() - start) * 1000)
            return ExecResult(
                error=ExecError(
                    kind=LogicError.LIMIT_EXCEEDED,
                    message=f"超过 wall_time={wall}s,容器 {name} 已 kill",
                ),
                usage=ExecUsage(cpu_ms=ms, wall_ms=ms),
            )
        ms = int((time.perf_counter() - start) * 1000)
        stdout = _truncate(stdout_b.decode("utf-8", errors="replace"), limits.stdout_bytes)
        stderr = _truncate(stderr_b.decode("utf-8", errors="replace"), limits.stdout_bytes)
        usage = ExecUsage(cpu_ms=ms, wall_ms=ms)
        if proc.returncode == _OOM_EXIT_CODE:
            return ExecResult(
                stdout=stdout,
                stderr=stderr,
                error=ExecError(
                    kind=LogicError.LIMIT_EXCEEDED,
                    message=f"容器被 cgroup 杀死(退出码 137,疑似超 memory={self.memory})",
                    traceback=stderr,
                ),
                usage=usage,
            )
        if proc.returncode != 0:
            lines = stderr.strip().splitlines()
            tail = lines[-1] if lines else f"退出码 {proc.returncode}"
            return ExecResult(
                stdout=stdout,
                stderr=stderr,
                error=ExecError(kind=LogicError.RUNTIME_ERROR, message=tail[-500:], traceback=stderr),
                usage=usage,
            )
        if module_mode:
            value, body = _parse_driver_stdout(stdout)
            return ExecResult(value=value, stdout=body, stderr=stderr, usage=usage)
        return ExecResult(value=stdout, stdout=stdout, stderr=stderr, usage=usage)

    @staticmethod
    async def _kill_container(name: str) -> None:
        """超时兜底:具名容器 docker kill(--rm 顺带清理);失败忽略(可能已自行退出)。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "kill", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:  # noqa: BLE001 — 清理动作失败不掩盖主错误
            _log.debug("容器 %s 清理失败(可能已自行退出)", name, exc_info=True)


__all__ = ["DockerPythonSandboxLogicKernel", "DockerUnavailableError"]

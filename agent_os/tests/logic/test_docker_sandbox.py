"""Docker 容器沙箱锚点测试(DESIGN.md §9.2 隔离阶梯:容器层)。

固定约定:

- ``DockerPythonSandboxLogicKernel(image, mounts={host: container}, ...)`` 实现 LogicKernel
  协议(trust=SANDBOX),``docker run --rm`` 一次性容器,与 subprocess 沙箱同一契约、后端替换;
- 默认加固:``--network none --memory --cpus --pids-limit --read-only --cap-drop ALL
  --security-opt no-new-privileges --tmpfs /tmp``;
- wall 超时 → 具名容器 ``docker kill`` 兜底 → ``LIMIT_EXCEEDED``,容器不残留;
- 退出码 137(cgroup OOM 杀)→ ``LIMIT_EXCEEDED``;其余非零 → ``RUNTIME_ERROR``;
- **网络隔离是系统级的**:容器内 socket 连接必然失败(补齐 subprocess 沙箱的最大缺口);
- code 技能(模块路径)走与 python_sandbox 相同的驱动脚本协议:仓库只读挂载 + ``-e PYTHONPATH``;
- docker 或镜像不可用时整个文件 skip。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from agent_os.api.v1 import (
    ExecRequest,
    LogicError,
    Permission,
    ResourceLimits,
    SkillFrame,
    ToolCall,
    ToolPolicy,
    TrustLevel,
)
from agent_os.logic.docker_sandbox import DockerPythonSandboxLogicKernel
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry, ToolDispatchContext

_IMAGE = "python:3.11-slim"
from tests.helpers.kernels import PROJECT_ROOT as _PROJECT_ROOT


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "image", "inspect", _IMAGE], capture_output=True, check=False
    ).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="docker 或 python:3.11-slim 镜像不可用")


def _kernel(**kw) -> DockerPythonSandboxLogicKernel:
    return DockerPythonSandboxLogicKernel(image=_IMAGE, **kw)


def test_trust_level_is_sandbox():
    assert _kernel().trust is TrustLevel.SANDBOX


def test_executes_source_text():
    result = asyncio.run(_kernel().execute(ExecRequest(source="print(40 + 2)")))
    assert result.error is None
    assert "42" in result.stdout


def test_runtime_error():
    result = asyncio.run(_kernel().execute(ExecRequest(source="raise ValueError('boom')")))
    assert result.error is not None and result.error.kind is LogicError.RUNTIME_ERROR
    assert "boom" in result.stderr


def test_wall_timeout_kills_container():
    req = ExecRequest(source="while True: pass", limits=ResourceLimits(wall_time=2))
    result = asyncio.run(_kernel().execute(req))
    assert result.error is not None and result.error.kind is LogicError.LIMIT_EXCEEDED
    leftover = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=agent-os-sbx-"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    assert leftover == "", "超时后容器未清理干净"


def test_network_isolation_is_enforced():
    """系统级断网:容器内任何 socket 外连必然失败(§9.2 默认断网)。"""
    src = (
        "import socket\n"
        "socket.create_connection(('8.8.8.8', 53), timeout=2)\n"
        "print('SHOULD-NOT-REACH')"
    )
    result = asyncio.run(_kernel().execute(ExecRequest(source=src, limits=ResourceLimits(wall_time=15))))
    assert result.error is not None
    assert "SHOULD-NOT-REACH" not in result.stdout


def test_memory_limit_triggers_oom():
    src = "x = bytearray(300 * 1024 * 1024)\nprint(len(x))"
    req = ExecRequest(source=src, limits=ResourceLimits(wall_time=30, memory_mb=64))
    result = asyncio.run(_kernel(memory="64m").execute(req))
    assert result.error is not None and result.error.kind is LogicError.LIMIT_EXCEEDED


def test_runs_code_skill_module_via_driver():
    """code 技能模式:仓库只读挂载 + PYTHONPATH,驱动脚本协议回传 value(§9.2)。"""
    kernel = _kernel(
        mounts={str(_PROJECT_ROOT): "/app"},
        env={"PYTHONPATH": "/app"},
    )
    req = ExecRequest(
        source="tests.helpers.code_skills",
        entry="pure_add",
        args={"a": 40, "b": 2},
        limits=ResourceLimits(wall_time=30),
    )
    result = asyncio.run(kernel.execute(req))
    assert result.error is None, result.stderr
    assert result.value == {"sum": 42}


def test_python_exec_tool_works_with_docker_kernel():
    """python_exec 工具换 Docker 后端:契约不变,端到端分发执行(§9.4)。"""
    tools = LocalPythonToolRegistry()
    tools.register(python_exec_tool(_kernel()))
    ctx = ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=["python_exec"],
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )

    async def main():
        return await tools.dispatch(
            ToolCall(id="c1", name="python_exec", args={"code": "result = 6 * 7\nprint(result)"}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    assert result.value["result"] == 42

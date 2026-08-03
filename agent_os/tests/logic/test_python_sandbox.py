"""PythonSandboxLogicKernel 锚点测试(docs/DESIGN.md §9.2/§9.7;子进程沙箱弹性)。

固定约定:

- wall_time 超时:父进程 watchdog 杀子进程 → ``LIMIT_EXCEEDED``,不留僵尸;
- 子进程非零退出 → ``RUNTIME_ERROR``,message 取 stderr 末行,traceback 全量保留;
- ``stdout_bytes`` 截断输出(防日志炸弹);
- 驱动脚本协议:stdout 最后一个非空行 ``{"value": ...}`` JSON → 结果值,
  其余行是业务 stdout;非 JSON / 无 ``value`` 键时结果为 None、stdout 原样。
"""

from __future__ import annotations

import asyncio

from agent_os.api.v1 import ExecRequest, LogicError, ResourceLimits, TrustLevel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel, _parse_driver_stdout


def _run(req: ExecRequest):
    return asyncio.run(PythonSandboxLogicKernel().execute(req))


def test_trust_level_is_sandbox():
    assert PythonSandboxLogicKernel().trust is TrustLevel.SANDBOX


def test_wall_timeout_kills_subprocess():
    result = _run(ExecRequest(source="while True: pass", limits=ResourceLimits(wall_time=0.5)))
    assert result.error is not None
    assert result.error.kind is LogicError.LIMIT_EXCEEDED
    assert "wall_time" in result.error.message


def test_runtime_error_maps_stderr_tail():
    result = _run(ExecRequest(source="raise ValueError('炸了')"))
    assert result.error is not None
    assert result.error.kind is LogicError.RUNTIME_ERROR
    assert "炸了" in result.error.message
    assert "Traceback" in (result.error.traceback or "")


def test_stdout_truncated_by_limit():
    result = _run(
        ExecRequest(source="print('x' * 10000)", limits=ResourceLimits(stdout_bytes=100))
    )
    assert result.error is None
    assert len(result.stdout.encode()) <= 100


def test_module_mode_runs_code_skill_handler():
    """模块路径 → 驱动脚本:import handler、ctx=None 调用、value 回传(§9.2)。"""
    result = _run(
        ExecRequest(
            source="tests.helpers.code_skills",
            entry="pure_add",
            args={"a": 2, "b": 3},
        )
    )
    assert result.error is None, result.error
    assert result.value == {"sum": 5}


# ---------------------------------------------------------------------------
# 驱动脚本 stdout 协议(单元)
# ---------------------------------------------------------------------------


def test_parse_driver_stdout_extracts_value_line():
    value, body = _parse_driver_stdout('业务输出\n{"value": {"n": 1}}\n')
    assert value == {"n": 1}
    assert body == "业务输出"


def test_parse_driver_stdout_non_json_tail():
    value, body = _parse_driver_stdout("hello\nworld")
    assert value is None
    assert body == "hello\nworld"


def test_parse_driver_stdout_json_without_value_key():
    value, body = _parse_driver_stdout('{"other": 1}')
    assert value is None
    assert body == '{"other": 1}'


def test_parse_driver_stdout_empty():
    assert _parse_driver_stdout("") == (None, "")
    assert _parse_driver_stdout("\n \n") == (None, "\n \n")


# ---------------------------------------------------------------------------
# §9.2 隔离面(实测钉住当前等级;退化时立刻可见)
# ---------------------------------------------------------------------------


def test_host_env_is_not_inherited():
    """**凭证隔离**:宿主环境变量不进沙箱。

    回归的是一个真实缺陷:``python_exec`` 曾以 ``env={**os.environ}`` 起子进程,
    LLM 一行 ``os.environ["ANTHROPIC_API_KEY"]`` 即可读走凭证并经网络外发。
    """
    import os

    os.environ["AGENT_OS_TEST_SECRET"] = "sk-must-not-leak"
    try:
        result = _run(ExecRequest(source="import os; print(os.environ.get('AGENT_OS_TEST_SECRET'))"))
    finally:
        del os.environ["AGENT_OS_TEST_SECRET"]
    assert result.error is None, result.error
    assert "sk-must-not-leak" not in result.stdout, "宿主凭证泄漏进沙箱"
    assert "None" in result.stdout


def test_sandbox_cwd_is_isolated_temp_dir():
    """**cwd 隔离**:工作目录是空临时目录,不是宿主 cwd(通常为用户仓库根)。"""
    import os

    result = _run(ExecRequest(source="import os; print(os.getcwd()); print(os.listdir('.'))"))
    assert result.error is None, result.error
    cwd_line, listing = result.stdout.strip().splitlines()[:2]
    assert cwd_line != os.getcwd(), "沙箱继承了宿主工作目录"
    assert "agent-os-sbx-" in cwd_line
    assert listing == "[]", "沙箱工作目录应为空,不应能直接列出项目文件"


def test_pythonpath_still_reaches_driver_mode():
    """env 白名单不得误伤 code 技能:驱动脚本形态仍要能 import 宿主侧 handler 包。"""
    result = _run(
        ExecRequest(source="tests.helpers.code_skills", entry="pure_add", args={"a": 2, "b": 3})
    )
    assert result.error is None, result.error
    assert result.value == {"sum": 5}

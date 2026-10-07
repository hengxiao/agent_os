"""K2 锚点测试:run 级工具状态两类生命周期 + system.shell.exec 会话化(docs/STDLIB.md §3.3)。

固定约定:

- 逻辑状态(``run_states``,checkpoint 随附,todo 先例)与进程态(``proc_states``,
  ``release_run`` 统一回收、**不进 checkpoint**)分桶;会话 ``{cwd, env_delta}``
  存 ``proc_states[run_id]["shell_sessions"][session_id]``;
- 无 ``session_id`` 行为与引入前逐字节一致(cd/env 不跨调用保留,回归锚点);
- 同 (run, session_id) 跨调用保 cwd 与 env 增量(状态重放式,无持久进程);
  不同 session 隔离;``env -0`` 捕获抗多行/空格/等号;shell 自维护变量
  (PWD/OLDPWD/SHLVL/_)不进增量;
- 用户命令 exit_code 原样返回(marker 技巧,``$?`` 摆渡);超时/``exit`` 后
  会话保留调用前状态;``release_run`` 后会话重置回 workdir 初始态。
"""

from __future__ import annotations

import asyncio
import json

from agent_os.api.v1 import (
    Permission,
    RunConfig,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.host.shared.artifacts import execute_run
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.helpers.brains import shell_session_brain
from tests.helpers.kernels import assemble

SESSION_SKILLS_YAML = """
skills:
  - name: test.shell_session
    version: 1.0.0
    kind: prompt
    description: 验证 shell 会话状态跨调用保留。Use when 测试 session_id 会话。
    inputs:
      type: object
      properties: {}
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [system.shell.exec], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: |
      按剧本执行会话验证。
"""


def _ctx(*, run_id: str = "r1") -> ToolDispatchContext:
    frame = SkillFrame(frame_id="f1", run_id=run_id)
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=["system.shell.exec"],
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


def _shell(reg, command: str, ctx, session_id: str | None = None, timeout: int = 30):
    args: dict = {"command": command}
    if session_id is not None:
        args["session_id"] = session_id
    if timeout != 30:
        args["timeout"] = timeout
    return reg.dispatch(ToolCall(id="s", name="system.shell.exec", args=args), ctx)


def _workdir_of(reg, ctx) -> str:
    """该 ctx 的 run workdir(经一次无会话 pwd 实测,与 dispatch 的解析口径一致)。"""
    async def main():
        return await _shell(reg, "pwd", ctx)

    result = asyncio.run(main())
    assert result.ok
    return result.value["stdout"].strip()


# ---------------------------------------------------------------------------
# 回归锚点:无 session_id 行为不变
# ---------------------------------------------------------------------------


def test_no_session_keeps_one_shot_semantics():
    """无 session_id:cd/export 都不跨调用保留(引入会话前行为逐字不变)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()

    async def main():
        await _shell(reg, "mkdir -p sub && cd sub && export FOO=bar", ctx)
        return await _shell(reg, "pwd && printf '<%s>' \"$FOO\"", ctx)

    result = asyncio.run(main())
    assert result.ok, result.error
    workdir = _workdir_of(reg, ctx)
    assert result.value["stdout"].strip() == f"{workdir}\n<>", (
        "一次性子进程语义:cd 与 export 都不跨调用保留"
    )


# ---------------------------------------------------------------------------
# 同 session:cwd / env 跨调用保留
# ---------------------------------------------------------------------------


def test_session_preserves_cwd_and_env_delta():
    """同 session_id:cd 后续 pwd 落在子目录;export 后续可见;一条命令两者都在。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()
    workdir = _workdir_of(reg, ctx)

    async def main():
        await _shell(reg, "mkdir -p sub a && cd sub", ctx, session_id="s")
        pwd = await _shell(reg, "pwd", ctx, session_id="s")
        await _shell(reg, "export FOO=bar", ctx, session_id="s")
        env = await _shell(reg, "printf '<%s>' \"$FOO\"", ctx, session_id="s")
        await _shell(reg, "cd ../a && export X=1", ctx, session_id="s")
        both = await _shell(reg, "pwd && printf '<%s>' \"$X\"", ctx, session_id="s")
        return pwd, env, both

    pwd, env, both = asyncio.run(main())
    assert pwd.value["stdout"].strip() == f"{workdir}/sub"
    assert env.value["stdout"].strip() == "<bar>"
    assert both.value["stdout"].strip() == f"{workdir}/a\n<1>", (
        "cd a && export X=1 之后,下一条命令 cwd 与 env 应同时生效"
    )


def test_sessions_isolated_per_id_and_per_run():
    """不同 session 互不影响;同 session_id 在不同 run 之间同样隔离。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()
    other_run_ctx = _ctx(run_id="r2")

    async def main():
        await _shell(reg, "mkdir -p sub && cd sub && export FOO=one", ctx, session_id="a")
        other = await _shell(reg, "pwd && printf '<%s>' \"$FOO\"", ctx, session_id="b")
        other_run = await _shell(reg, "pwd && printf '<%s>' \"$FOO\"", other_run_ctx, session_id="a")
        back = await _shell(reg, "pwd && printf '<%s>' \"$FOO\"", ctx, session_id="a")
        return other, other_run, back

    other, other_run, back = asyncio.run(main())
    workdir_r1 = _workdir_of(reg, ctx)
    assert other.value["stdout"].strip() == f"{workdir_r1}\n<>", "另一个 session 看不到 a 的状态"
    assert "sub" not in other_run.value["stdout"] and "<one>" not in other_run.value["stdout"], (
        "同 session_id 在另一个 run 里同样隔离"
    )
    assert back.value["stdout"].strip() == f"{workdir_r1}/sub\n<one>", "a 的状态不受其它调用影响"


def test_env_value_with_newline_space_equals_roundtrips():
    """env -0 NUL 捕获:含换行/空格/等号的值逐字 round-trip(不用换行分隔的原因)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()
    weird = "line1\nhas space=and=equals\n"

    async def main():
        await _shell(reg, f"export WEIRD='{weird}'", ctx, session_id="s")
        return await _shell(reg, "printf '<%s>' \"$WEIRD\"", ctx, session_id="s")

    result = asyncio.run(main())
    assert result.ok, result.error
    assert result.value["stdout"] == f"<{weird}>", "多行/空格/等号值须逐字 round-trip"


def test_env_delta_excludes_shell_noise_vars():
    """增量只存用户命令引入的部分:PWD/SHLVL 等 shell 自维护变量不进 env_delta。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()

    async def main():
        await _shell(reg, "mkdir -p sub && cd sub && export FOO=bar", ctx, session_id="s")

    asyncio.run(main())
    entry = reg.proc_states["r1"]["shell_sessions"]["s"]
    assert entry["env_delta"] == {"FOO": "bar"}, (
        f"env_delta 应只有用户增量(基线环境与 shell 噪音变量不落存): {entry['env_delta']!r}"
    )
    assert entry["cwd"].endswith("/sub")


# ---------------------------------------------------------------------------
# exit_code / 超时 / 生命周期
# ---------------------------------------------------------------------------


def test_exit_code_passthrough_and_capture_stripped():
    """exit_code 原样返回($? 摆渡,不被捕获命令吃掉);捕获尾巴不进 stdout。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()

    async def main():
        marker_path = await _shell(reg, "echo out; (exit 42)", ctx, session_id="s")
        # 用户命令自己 exit:捕获尾巴不跑,exit_code 仍原样,会话保留调用前状态
        await _shell(reg, "export FOO=bar", ctx, session_id="s")
        early_exit = await _shell(reg, "exit 7", ctx, session_id="s")
        after = await _shell(reg, "printf '<%s>' \"$FOO\"", ctx, session_id="s")
        return marker_path, early_exit, after

    marker_path, early_exit, after = asyncio.run(main())
    assert marker_path.value["exit_code"] == 42
    assert marker_path.value["stdout"].strip() == "out"
    assert "__AOS_SESSION_" not in marker_path.value["stdout"], "捕获 marker 不得泄漏进 stdout"
    assert early_exit.value["exit_code"] == 7
    assert after.value["stdout"].strip() == "<bar>", "exit 后会话应保留调用前状态"


def test_timeout_preserves_prior_session_state():
    """超时/杀进程:状态捕获不可能,会话保留调用前状态(docstring 注明语义)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()

    async def main():
        await _shell(reg, "mkdir -p sub && cd sub && export FOO=bar", ctx, session_id="s")
        timed_out = await _shell(reg, "sleep 30", ctx, session_id="s", timeout=1)
        after = await _shell(reg, "pwd && printf '<%s>' \"$FOO\"", ctx, session_id="s")
        return timed_out, after

    timed_out, after = asyncio.run(main())
    assert not timed_out.ok and timed_out.error.kind is ToolErrorKind.TIMEOUT
    workdir = _workdir_of(reg, ctx)
    assert after.value["stdout"].strip() == f"{workdir}/sub\n<bar>", (
        "超时后 cwd/env 应仍是调用前的值"
    )


def test_release_run_resets_session():
    """release_run 统一回收进程态:同 session_id 再调用回到 workdir 初始态。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _ctx()

    async def main():
        await _shell(reg, "mkdir -p sub && cd sub && export FOO=bar", ctx, session_id="s")

    asyncio.run(main())
    assert reg.proc_states.get("r1"), "会话应已登记"
    reg.release_run("r1")  # runner._release_run 的调用形态(registry 链)
    assert "r1" not in reg.proc_states, "release_run 须回收 proc_states"
    # run_states(逻辑状态)不在此清:收尾 checkpoint 快照在回收之后才落盘(模式层语义)

    async def after_release():
        return await _shell(reg, "pwd && printf '<%s>' \"$FOO\"", ctx, session_id="s")

    result = asyncio.run(after_release())
    workdir = _workdir_of(reg, ctx)
    assert result.value["stdout"].strip() == f"{workdir}\n<>", (
        "release_run 后会话应重置回初始态(cwd = run workdir,env 增量空)"
    )


# ---------------------------------------------------------------------------
# 内核级 e2e(checkpoint 排除 + mock brain 会话链路)
# ---------------------------------------------------------------------------


def _session_run(tmp_path, *, checkpoint_interval: int = 0):
    """装配带 builtins 的内核跑 test.shell_session(mock brain 两次同 session 调用)。"""
    skills = tmp_path / "skills.yaml"
    skills.write_text(SESSION_SKILLS_YAML, encoding="utf-8")
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),  # system.shell.exec 是 EXEC 级
        compression="off",
        checkpoint_interval=checkpoint_interval,
    )
    kernel = assemble(
        config,
        shell_session_brain,
        skills,
        tools=LocalPythonToolRegistry.with_builtins(),
    )
    return kernel, execute_run(
        kernel, "test.shell_session", {}, artifacts_root=tmp_path / "arts", host="test"
    )


def test_kernel_e2e_two_calls_share_session(tmp_path):
    """内核级 e2e:mock brain 两次 shell 调用同 session_id,第二次看到第一次的 cd/export。

    (brain 内部断言第二次 stdout 带 sub 与变量值,失败即 run failed。)
    """
    kernel, record = _session_run(tmp_path)
    assert record["status"] == "done", record.get("error")
    assert record["result"] == {"done": True}
    run_id = record["run_id"]
    assert run_id not in kernel.tools.proc_states, "run 收尾(_release_run)须回收会话进程态"


def test_checkpoint_excludes_session_state(tmp_path, monkeypatch):
    """checkpoint.json 绝不出现进程态:基线环境值/捕获 marker/会话分桶键全不落盘。

    基线变量只存在于进程环境(子进程继承),任何命令与消息都不提它——它若出现在
    checkpoint,只能来自会话捕获尾巴/整env落存这条泄漏路径。
    """
    monkeypatch.setenv("AOS_K2_BASELINE_VAR", "baseline-secret-9f8e7d")
    _, record = _session_run(tmp_path, checkpoint_interval=1)
    assert record["status"] == "done", record.get("error")
    run_dir = tmp_path / "arts" / "runs" / record["run_id"]
    checkpoint_text = (run_dir / "checkpoint.json").read_text(encoding="utf-8")
    assert "baseline-secret-9f8e7d" not in checkpoint_text, "基线环境值泄漏进 checkpoint"
    assert "__AOS_SESSION_" not in checkpoint_text, "捕获 marker 泄漏进 checkpoint"
    assert "shell_sessions" not in checkpoint_text and "proc_states" not in checkpoint_text
    # 逻辑状态对照组:run_state 键照常落档(todo 先例,schema v1 不变)
    doc = json.loads(checkpoint_text)
    assert "run_state" in doc["run"]

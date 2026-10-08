"""R1 锚点测试:CLI Runner 核心(docs/RUNNERS.md §2/§3)。

固定约定:

- 配置 `agent-os.toml`(§2.1):`[providers.mock] brain = "<dotted path>"` 经 importlib
  加载 MockProvider 应答函数;`build_kernel(config_path)` 装配完整内核;
- `agent-os run`:stdout 为 RunRecord JSON(``"v": 1``,§3.3),退出码
  0 成功 / 2 校验错 / 3 run 失败或中止 / 4 基础设施错;
- 产物(§2.2):`.agent-os/runs/<run_id>/{meta,trace,checkpoint,result}.json` 落盘;
- `agent-os trace <run_id> --format json`:逐行 JSON 信号;`inspect` 读帧树与帧上下文;
- `agent-os resume <checkpoint.json>`:用 config 装配的内核从 checkpoint 恢复。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_os.host.cli.main import main
from agent_os.runtime.config import build_kernel
from tests.helpers.brains import PowerCut, power_cut_brain
from tests.helpers.config import run_cli as _run_cli
from tests.helpers.config import write_config as _write_config

# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def test_config_loader_builds_working_kernel(tmp_path):
    cfg = _write_config(tmp_path)
    kernel = build_kernel(cfg)
    result = asyncio.run(kernel.run("demo.fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}
    assert list((tmp_path / "traces").glob("*.jsonl")), "telemetry 未按配置落盘"


# ---------------------------------------------------------------------------
# agent-os run
# ---------------------------------------------------------------------------


def test_cli_run_success_and_artifacts(tmp_path, capsys):
    cfg = _write_config(tmp_path)
    artifacts = tmp_path / "runs"
    rc, out = _run_cli(capsys, "run", "demo.fib", "--input", '{"n": 4}',
                       "--config", str(cfg), "--artifacts", str(artifacts), "--json")
    assert rc == 0
    assert out["v"] == 1 and out["status"] == "done"
    assert out["result"] == {"seq": [0, 1, 1, 2]}
    assert out["usage"]["steps"] == 7
    assert out["frames"] == 3
    run_dir = Path(out["artifacts"]["dir"])
    for name in ("meta.json", "trace.jsonl", "checkpoint.json", "result.json"):
        assert (run_dir / name).exists(), name


def test_cli_run_validation_error_exit_2(tmp_path, capsys):
    cfg = _write_config(tmp_path)
    rc, _ = _run_cli(capsys, "run", "demo.fib", "--input", '{"n": "x"}',
                     "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 2


def test_cli_run_failure_exit_3(tmp_path, capsys):
    cfg = _write_config(tmp_path)
    rc, out = _run_cli(capsys, "run", "demo.fib", "--input", '{"n": 10}',
                       "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 3
    assert "MaxDepthExceeded" in (out.get("error") or "")


# ---------------------------------------------------------------------------
# trace / inspect
# ---------------------------------------------------------------------------


def _completed_run(tmp_path, capsys) -> tuple[str, Path]:
    cfg = _write_config(tmp_path)
    artifacts = tmp_path / "runs"
    rc, out = _run_cli(capsys, "run", "demo.fib", "--input", '{"n": 3}',
                       "--config", str(cfg), "--artifacts", str(artifacts), "--json")
    assert rc == 0
    return out["run_id"], Path(out["artifacts"]["dir"])


def test_cli_trace_json_lines(tmp_path, capsys):
    run_id, _ = _completed_run(tmp_path, capsys)
    rc = main(["trace", run_id, "--artifacts", str(tmp_path / "runs"), "--format", "json"])
    assert rc == 0
    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    names = [line["name"] for line in lines]
    assert "run.started" in names and "run.finished" in names


def test_cli_inspect_tree_and_frame_messages(tmp_path, capsys):
    run_id, _ = _completed_run(tmp_path, capsys)
    rc, out = _run_cli(capsys, "inspect", run_id, "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 0
    frames = out["frames"]
    assert [f["depth"] for f in frames] == [1, 2]
    fid = frames[0]["frame_id"]

    rc, out = _run_cli(capsys, "inspect", run_id, "--frame", fid,
                       "--messages", "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 0
    assert out["messages"], "帧上下文消息为空"
    assert any(m["role"] == "assistant" for m in out["messages"])


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_cli_resume_from_checkpoint(tmp_path, capsys):
    """断电的 run 用 CLI resume 恢复(内核由 config 装配,fib_brain 确定性应答)。"""
    from tests.helpers.kernels import fib_kernel

    kernel1 = fib_kernel(power_cut_brain(cut_at=6))
    seen = []

    async def rec(sig):
        seen.append(sig)

    kernel1.signals.subscribe("run.started", rec)
    with pytest.raises(PowerCut):
        asyncio.run(kernel1.run("demo.fib", {"n": 5}))
    run_id = seen[0].run_id
    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))

    cfg = _write_config(tmp_path)
    rc, out = _run_cli(capsys, "resume", str(ckpt), "--config", str(cfg),
                       "--artifacts", str(tmp_path / "runs"), "--json")
    assert rc == 0
    assert out["result"] == {"seq": [0, 1, 1, 2, 3]}


# ---------------------------------------------------------------------------
# M1 用户通道(§8.3):_CliUserChannel stdin/stderr 协议 + _build_kernel 接线
# ---------------------------------------------------------------------------


def test_cli_user_channel_ask_reads_stdin(monkeypatch):
    """ask:stderr 打印 [user] 问题行 + stdin 读一行作答(与 _cli_supervisor 同构)。

    F2 起打印走**原始** stderr(sys.__stderr__;redirect_stderr 吞不掉 dunder 流,
    code 技能帧内不被捕获)——capsys 抓不到 dunder 流,monkeypatch 替换断言。
    """
    import io
    import sys

    from agent_os.host.cli.main import _CliUserChannel

    monkeypatch.setattr(sys, "stdin", io.StringIO("  继续  \n"))
    real_err = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", real_err)
    out = asyncio.run(_CliUserChannel().ask("确认覆盖现有文件吗?"))
    assert out == "继续", "回答须 strip 后原样返回"
    assert "[user] 确认覆盖现有文件吗?" in real_err.getvalue()


def test_cli_user_channel_notify_one_way(monkeypatch):
    """notify:单向语义——只打印,不读 stdin(打印走 sys.__stderr__,F2)。"""
    import io
    import sys

    from agent_os.host.cli.main import _CliUserChannel

    real_err = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", real_err)
    asyncio.run(_CliUserChannel().notify("已完成 3/5"))
    assert "[user] 已完成 3/5" in real_err.getvalue()


def test_cli_user_channel_ask_eof_raises(monkeypatch, capsys):
    """stdin EOF → EOFError(工具侧经 dispatch 归一 INTERNAL,§8.1 分发边界)。"""
    import io
    import sys

    from agent_os.host.cli.main import _CliUserChannel

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(EOFError):
        asyncio.run(_CliUserChannel().ask("q?"))


def test_build_kernel_wires_user_channel(tmp_path):
    """_build_kernel 装配链:默认(supervisor=True)注入 _CliUserChannel 进 tools registry。"""
    from agent_os.host.cli.main import _build_kernel, _CliUserChannel

    cfg = _write_config(tmp_path, builtins=True)
    kernel = _build_kernel(str(cfg))
    assert isinstance(kernel.tools._user_channel, _CliUserChannel), (
        "run/resume 装配须把 CLI 用户通道经 bind_user_channel 注入 registry"
    )


def test_build_kernel_replay_leaves_user_channel_unbound(tmp_path):
    """replay(supervisor=False)不接线:回放按 trace 记录值走,不问第二次(§4)。"""
    from agent_os.host.cli.main import _build_kernel

    cfg = _write_config(tmp_path, builtins=True)
    kernel = _build_kernel(str(cfg), supervisor=False)
    assert kernel.tools._user_channel is None


# ---------------------------------------------------------------------------
# F2:supervisor/user 协议行写原始 stderr(sys.__stderr__),不被帧内重定向吞掉
# ---------------------------------------------------------------------------

CONFIRM_SKILLS_YAML = """
skills:
  - name: test.confirm_root
    version: 1.0.0
    kind: prompt
    description: 触发帧内确认门的根技能。Use when 测试协议行逃逸。
    inputs:
      type: object
      properties: {}
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [test.confirm_write] }
    model: { prefer: ["mock/fib"] }
    limits: { max_steps: 4 }
    prompt: 调用子技能后收尾。
  - name: test.confirm_write
    version: 1.0.0
    kind: code
    description: 帧内经 ctx.call_tool 触发 confirm 门。Use when 测试协议行逃逸。
    handler: tests.helpers.code_skills:confirm_delete
    inputs:
      type: object
      properties: {}
    outputs:
      type: object
      properties: { deleted: { type: boolean } }
      required: [deleted]
    permissions: { tools: [system.file.delete], skills: [] }
"""


def _confirm_brain(req):
    """先调 test.confirm_write(帧内撞 tool-confirm 门),再收尾。"""
    from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Role, ToolCall

    called = any(
        tc.name == "skill.test.confirm_write"
        for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
    )
    if not called:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, tool_calls=[
                ToolCall(id="c1", name="skill.test.confirm_write", args={})
            ]),
            finish_reason="tool_calls", usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop", usage=ChatUsage(prompt=1, completion=1),
    )


def test_supervisor_line_survives_code_frame_redirect(tmp_path, monkeypatch, capsys):
    """F2 回归:code 技能帧内触发 tool-confirm,提问协议行出现在**真实** stderr。

    InProcessLogicKernel 用 contextlib.redirect_stderr 把 sys.stderr 换成 StringIO
    (帧产物);协议行写 sys.__stderr__(redirect 不碰 dunder 原始流)才逃得出。
    capsys 抓不到 dunder 流——monkeypatch 替换 sys.__stderr__ 为 StringIO 断言;
    对照:capsys 的 sys.stderr 捕获里**不应**有该行(证明它没被吞进重定向面)。
    """
    import io
    import sys

    skills = tmp_path / "skills.yaml"
    skills.write_text(CONFIRM_SKILLS_YAML, encoding="utf-8")
    cfg = _write_config(
        tmp_path, brain="tests.cli.test_run:_confirm_brain", skills=skills, builtins=True
    )
    # 两道门都可能问(升权 + tool-confirm):stdin 备足 approve-once
    monkeypatch.setattr(sys, "stdin", io.StringIO("approve-once\n" * 5))
    real_err = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", real_err)

    rc = main(["run", "test.confirm_root", "--input", "{}",
               "--config", str(cfg), "--artifacts", str(tmp_path / "runs"), "--json"])
    assert rc == 0, f"run 应走完(批准后门放行):stderr={real_err.getvalue()[:500]}"
    protocol_lines = [
        line for line in real_err.getvalue().splitlines() if '"supervisor.ask"' in line
    ]
    assert protocol_lines, "提问协议行须到达原始 stderr(否则用户看不到提问而 stdin 在等答)"
    assert any('"kind": "tool-confirm"' in line for line in protocol_lines), (
        "tool-confirm 门的协议行须在列"
    )
    assert '"supervisor.ask"' not in capsys.readouterr().err, (
        "对照:协议行不应落在可被 redirect_stderr 替换的 sys.stderr 面上"
    )

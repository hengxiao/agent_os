"""M5a 锚点测试:Telemetry(WAL/版本头)+ 检查点断电恢复(DESIGN.md §10、§16 M5)。

固定约定:

- ``JsonlTelemetrySink(dir)`` 是总线的特权订阅者(§5.1):全部信号 append 到
  ``<dir>/<run_id>.jsonl``;**首行为版本头**(``{"v": 1, ...}``);schema 版本化(§10.2);
- ``kernel.checkpoint(run_id, path)``:把 run 状态(配置、usage、全部帧含上下文与状态)
  序列化为 JSON(``{"v": 1, "run": ..., "frames": [...]}``)——WAL 原则:轨迹即全部状态(§10.2);
- ``await kernel.resume(path)``:从 checkpoint 恢复——跳过 DONE 帧,最深的未完成帧
  带着完整上下文重入 loop;父帧的未结算子调用在子帧完成后按 call_id 补记工具结果;
  **恢复不是重跑**:已完成的工作不重复(剩余 LLM 调用数精确可数)。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    RUN_FINISHED,
    RUN_STARTED,
    ChatRequest,
    ChatResponse,
    Permission,
    RunConfig,
    Signal,
    ToolPolicy,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.telemetry import JsonlTelemetrySink
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.test_fib_agent import fib_brain

SKILLS_YAML = "skills/skills.yaml"


def _build(brain, *, telemetry_dir: Path | None = None):
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    tools = LocalPythonToolRegistry()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(tools)
        .skills(LocalFileSkillRegistry(SKILLS_YAML))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if telemetry_dir is not None:
        builder = builder.telemetry(JsonlTelemetrySink(str(telemetry_dir)))
    return builder.build()


def _run_id_of(kernel) -> str:
    seen: list[Signal] = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe(RUN_STARTED, rec)
    return seen


# ---------------------------------------------------------------------------
# JSONL WAL
# ---------------------------------------------------------------------------


def test_jsonl_trace_has_version_header_and_lifecycle(tmp_path):
    """全部信号落盘为 JSONL;首行版本头;含完整生命周期(§10.1/§10.2)。"""
    tdir = tmp_path / "traces"
    kernel = _build(fib_brain, telemetry_dir=tdir)
    started = _run_id_of(kernel)
    result = asyncio.run(kernel.run("fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}
    assert started, "未捕获 run.started"
    run_id = started[0].run_id

    trace = tdir / f"{run_id}.jsonl"
    assert trace.exists(), "trace 文件未生成"
    lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[0]["v"] == 1, "首行必须是版本头"
    names = [line.get("name") for line in lines]
    assert RUN_STARTED in names and RUN_FINISHED in names
    assert "post:frame.push" in names and "post:frame.pop" in names


# ---------------------------------------------------------------------------
# 断电恢复
# ---------------------------------------------------------------------------


class PowerCut(Exception):
    """模拟断电:第 cut_at 次 LLM 调用时直接崩掉。"""


def power_cut_brain(cut_at: int):
    state = {"calls": 0}

    def brain(req: ChatRequest) -> ChatResponse:
        state["calls"] += 1
        if state["calls"] >= cut_at:
            raise PowerCut(f"断电于第 {state['calls']} 次调用")
        return fib_brain(req)

    return brain


def test_checkpoint_resume_after_power_cut(tmp_path):
    """fib(5) 在第 6 次调用处断电 → 新内核从 checkpoint 恢复,只补剩余 5 次调用。"""
    kernel1 = _build(power_cut_brain(cut_at=6))
    started = _run_id_of(kernel1)
    with pytest.raises(PowerCut):
        asyncio.run(kernel1.run("fib", {"n": 5}))
    run_id = started[0].run_id

    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))
    assert ckpt.exists()

    mock2 = MockProvider(fib_brain)
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    tools = LocalPythonToolRegistry()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    kernel2 = (
        KernelBuilder(config)
        .providers(mock2)
        .tools(tools)
        .skills(LocalFileSkillRegistry(SKILLS_YAML))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result == {"seq": [0, 1, 1, 2, 3]}
    # 恢复不是重跑:10 次调用中前 5 次已完成,只需补 5 次
    assert len(mock2.recorded) == 5


def test_checkpoint_file_contains_frames_and_usage(tmp_path):
    """checkpoint JSON:版本号、run.usage、帧列表(含状态)(§10.1 snapshot)。"""
    kernel1 = _build(power_cut_brain(cut_at=4))
    started = _run_id_of(kernel1)
    with pytest.raises(PowerCut):
        asyncio.run(kernel1.run("fib", {"n": 5}))
    run_id = started[0].run_id

    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    assert data["v"] == 1
    assert data["run"]["usage"]["steps"] >= 3
    statuses = [f["status"] for f in data["frames"]]
    assert "running" in statuses  # 有未完成帧待恢复
    assert "done" in statuses  # 也有已完成帧(恢复时跳过)
    running = [f for f in data["frames"] if f["status"] == "running"]
    assert running[0]["context"]["messages"], "帧上下文必须完整入档(WAL)"

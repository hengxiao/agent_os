"""三例子调试锚点测试:fib(已有)+ research_pipeline(~20 skills)+ skills_100(100 skills)。

固定约定:

- `examples/research_pipeline/skills.yaml`:20 个技能(prompt+code 混合),
  入口 ``research_report`` 的调用树恰好 19 帧(根 1 + plan 1 + topic×3
  +(fetch+extract+verify+summarize)×3 + merge 1 + draft 1 +(style+factcheck) 2 + finalize 1);
  全程由通用 mock 大脑 ``brains:ops_brain`` 驱动,确定性;
- `examples/skills_100/skills.yaml`:恰好 100 个技能,DAG 无环;
  入口 ``mega_pipeline``(code)经 10 层 code 链到 ``hub_agg``,共 12 帧,结果 ``{"total": 45}``;
- 两个例子都可通过各自 `agent-os.toml` 被 CLI/Web 直接加载调试。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent_os.api.v1 import (
    POST_FRAME_PUSH,
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
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.helpers.kernels import PROJECT_ROOT, load_example_module

RESEARCH_DIR = PROJECT_ROOT / "examples" / "research_pipeline"
S100_DIR = PROJECT_ROOT / "examples" / "skills_100"


def _build(brain, skills_path: Path):
    config = RunConfig(
        model="mock/ops",
        max_depth=12,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    provider = MockProvider() if brain is None else MockProvider(brain)
    return (
        KernelBuilder(config)
        .providers(provider)
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(skills_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


# ---------------------------------------------------------------------------
# research_pipeline(~20 skills)
# ---------------------------------------------------------------------------


def test_research_pipeline_loads_twenty_skills():
    reg = LocalFileSkillRegistry(str(RESEARCH_DIR / "skills.yaml"))
    manifests = reg.manifests()
    assert len(manifests) == 20
    kinds = {m.name: m.kind.value for m in manifests}
    assert kinds["project.research_pipeline.research_report"] == "prompt"
    assert kinds["project.research_pipeline.plan_topics"] == "code"


def test_research_pipeline_runs_end_to_end():
    sys.path.insert(0, str(RESEARCH_DIR))
    try:
        kernel = _build(load_example_module("research_pipeline").ops_brain, RESEARCH_DIR / "skills.yaml")
        seen: list[Signal] = []

        async def rec(sig: Signal) -> None:
            seen.append(sig)

        kernel.signals.subscribe("*", rec)
        result = asyncio.run(kernel.run("project.research_pipeline.research_report", {"question": "菲波拉契数列的性质"}))
    finally:
        sys.path.remove(str(RESEARCH_DIR))

    assert isinstance(result["report"], str) and result["report"]
    assert result["words"] == len(result["report"].split())
    assert isinstance(result["citations"], list)
    frames = [s for s in seen if s.name == POST_FRAME_PUSH]
    assert len(frames) == 19, f"调用树应为 19 帧,实际 {len(frames)}"
    depths = sorted(s.payload["depth"] for s in frames)
    assert depths[0] == 1 and depths[-1] == 4, "调用树应为四层"


# ---------------------------------------------------------------------------
# skills_100(100 skills 压测)
# ---------------------------------------------------------------------------


def test_skills_100_loads_hundred_skills():
    reg = LocalFileSkillRegistry(str(S100_DIR / "skills.yaml"))
    manifests = reg.manifests()
    assert len(manifests) == 100
    names = [m.name for m in manifests]
    assert len(set(names)) == 100, "技能名必须唯一"


def test_skills_100_mega_pipeline_runs():
    sys.path.insert(0, str(S100_DIR))
    try:
        kernel = _build(None, S100_DIR / "skills.yaml")
        seen: list[Signal] = []

        async def rec(sig: Signal) -> None:
            seen.append(sig)

        kernel.signals.subscribe("*", rec)
        result = asyncio.run(kernel.run("project.skills_100.mega_pipeline", {"seed": 0}))
    finally:
        sys.path.remove(str(S100_DIR))

    assert result == {"total": 45}
    frames = [s for s in seen if s.name == POST_FRAME_PUSH]
    assert len(frames) == 12, f"mega_pipeline 应为 12 帧,实际 {len(frames)}"
    assert max(s.payload["depth"] for s in frames) == 12

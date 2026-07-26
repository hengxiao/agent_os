"""嵌套监督示例(SUPERVISOR.md v2 §2.5):调用方是另一个 agent。

外层应用(本脚本)启动内层 run(``junior_clerk``,报销文员);内层
``ask_supervisor`` 的问题经 ``KernelBuilder.supervisor(handler)`` 注入的
handler 路由出 agent,交给调用方——本脚本。本脚本的裁决点是**再起一个
外层 run** 问 ``team_lead``(外层 LLM),把它的答案返回内层:

    junior_clerk(内层 run)→ ask_supervisor → handler(外层应用)
        → team_lead(外层 run,另一个 agent)→ answer 回流内层

内核只看到一次 handler 往返;权威链(子 agent → 调用 agent → … → 人)
完全发生在 agent 边界之外(§2.5:内核不感知级数)。两个内核各自独立
(各自的大脑/总线),演示"嵌入方自己也是 agent"的最小形态。

运行(无需 API key,mock 大脑)::

    .venv/bin/python examples/supervision/outer.py [amount]
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from agent_os.api.v1 import Permission, Question, RunConfig, ToolPolicy
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

SKILLS = Path(__file__).with_name("skills.yaml")


def _load_brains() -> Any:
    """按文件路径加载示例大脑(专属模块名,不与其它示例的同名 brains 模块串扰)。"""
    spec = importlib.util.spec_from_file_location(
        "supervision_demo_brains", Path(__file__).with_name("brains.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


brains = _load_brains()


def _build(brain: Any, supervisor: Any = None) -> Any:
    """按示例技能包装配一个内核;``supervisor`` 非空时注入调用方 handler(§2.3)。"""
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(LocalPythonToolRegistry())
        .skills(LocalFileSkillRegistry(str(SKILLS)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if supervisor is not None:
        builder.supervisor(supervisor)
    return builder.build()


def build_nested() -> tuple[Any, Any, list[Question]]:
    """装配嵌套监督:外层内核(team_lead)+ 内层内核(junior_clerk,注入嵌套 handler)。

    返回 ``(inner_kernel, outer_kernel, handler_calls)``;handler_calls 按调用
    顺序收集内层上报的 Question(演示/测试观察用)。
    """
    calls: list[Question] = []
    outer = _build(brains.lead_brain)  # 外层 agent:独立内核、独立大脑

    async def team_lead_handler(question: Question) -> dict[str, Any]:
        """§2.5 嵌套裁决点:外层应用收到内层问题,再起一个 run 问外层 LLM。

        decided_by 标注外层 agent("agent:team_lead")——裁决来源随答案回流
        内层,形成可观察的权威链(内核只感知这一次 handler 往返)。
        """
        calls.append(question)
        verdict = await outer.run(
            "team_lead",
            {
                "question": question.question,
                "context": question.context,
                "options": question.options or [],
            },
        )
        return {"answer": verdict["answer"], "decided_by": "agent:team_lead"}

    inner = _build(brains.clerk_brain, supervisor=team_lead_handler)
    return inner, outer, calls


async def run_nested(amount: int = 5000) -> tuple[dict[str, Any], list[Question]]:
    """跑一遍嵌套裁决,返回 ``(内层 run 结果, handler 收到的提问列表)``。"""
    inner, _outer, calls = build_nested()
    result = await inner.run("junior_clerk", {"amount": amount})
    return result, calls


def main() -> None:
    amount = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    result, calls = asyncio.run(run_nested(amount))
    for q in calls:
        print(f"[内层 → 外层] ask_supervisor: {q.question}"
              f"(options={q.options}, urgency={q.urgency})")
        print("[外层裁决  ] team_lead run → decided_by=agent:team_lead")
    print(f"[内层结果  ] {json.dumps(result, ensure_ascii=False)}")


if __name__ == "__main__":
    main()

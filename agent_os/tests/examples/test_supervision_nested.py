"""S3 锚点:嵌套监督(SUPERVISOR.md v2 §2.5,§9 锚点 10)——调用方是另一个 agent。

固定约定:

- 外层应用(examples/supervision/outer.py)经 ``KernelBuilder.supervisor(handler)``
  注入嵌套 handler:内层 ``junior_clerk`` 的 ``ask_supervisor`` 问题路由给外层应用;
- handler 再起一个**外层 run**(``team_lead`` 技能,外层 LLM 按政策裁决),
  把答案以 ``decided_by="agent:team_lead"`` 返回内层——内核只看到一次 handler
  往返(§2.5:权威链在 agent 边界之外,内核不感知级数);
- 外层 run 用的是**另一个独立内核**(独立大脑/总线);decided_by 链随内层
  最终结果与 ``supervisor.answer`` 信号可观察。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json

from agent_os.api.v1 import Role
from tests.helpers.kernels import PROJECT_ROOT

SUP_DIR = PROJECT_ROOT / "examples" / "supervision"


def _load_outer():
    """按文件路径加载示例脚本(专属模块名,不与其它示例的同名模块串扰)。"""
    spec = importlib.util.spec_from_file_location("supervision_outer", SUP_DIR / "outer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skills_load():
    """示例技能包过 lint:junior_clerk 声明 ask_supervisor,team_lead 无需声明。"""
    from agent_os.skills.local_file import LocalFileSkillRegistry

    reg = LocalFileSkillRegistry(str(SUP_DIR / "skills.yaml"))
    manifests = {m.name: m for m in reg.manifests()}
    assert set(manifests) == {"junior_clerk", "team_lead"}
    assert "ask_supervisor" in manifests["junior_clerk"].permissions.tools
    assert "ask_supervisor" not in manifests["team_lead"].permissions.tools


def test_nested_handler_called_and_answer_flows_back():
    """嵌套 handler 被调用(收到完整问题),外层 LLM 的答案流回内层闭环。"""
    outer = _load_outer()
    result, calls = asyncio.run(outer.run_nested(5000))

    # 嵌套 handler 被恰好调用一次,问题完整(含 context/options/urgency)
    assert len(calls) == 1
    q = calls[0]
    assert q.question == "批准 ¥5000 报销吗?"
    assert q.context == {"amount_cents": 500000}
    assert q.options == ["approve", "reject"]
    assert q.urgency == "high"  # 大额 → high(文员按金额标注)
    assert q.frame_id and q.run_id

    # 外层 LLM(team_lead)裁决:¥5000 超 ¥3000 上限 → reject,答案流回内层
    assert result == {"decision": "reject", "decided_by": "agent:team_lead"}


def test_nested_approve_path_and_urgency_normal():
    """小额:team_lead 自动批准;urgency 按金额为 normal。"""
    outer = _load_outer()
    result, calls = asyncio.run(outer.run_nested(500))

    assert calls[0].urgency == "normal"
    assert result == {"decision": "approve", "decided_by": "agent:team_lead"}


def test_decided_by_chain_observable():
    """decided_by 链清晰:外层 LLM 收到问题,supervisor.answer 信号标 agent:team_lead。"""
    outer = _load_outer()
    inner, outer_kernel, calls = outer.build_nested()

    answers = []

    async def rec(sig):
        answers.append(sig)

    inner.signals.subscribe("supervisor.answer", rec)
    result = asyncio.run(inner.run("junior_clerk", {"amount": 5000}))

    # 外层 LLM 确实被问了一次,且看到的就是内层上报的问题
    mock = outer_kernel.providers.providers["mock"]
    assert len(mock.recorded) == 1
    user_msg = next(m for m in mock.recorded[0].messages if m.role is Role.USER)
    ask = json.loads(user_msg.content)
    assert ask["question"] == "批准 ¥5000 报销吗?"
    assert ask["context"] == {"amount_cents": 500000}

    # decided_by 链:内层结果与 supervisor.answer 信号同标外层 agent
    assert result["decided_by"] == "agent:team_lead"
    assert len(answers) == 1
    assert answers[0].payload["decided_by"] == "agent:team_lead"
    assert answers[0].payload["question_id"] == calls[0].question_id
    # 信号 channel 标签(S3):嵌入方 handler 通道
    asks = []
    inner.signals.subscribe("supervisor.ask", lambda s: asks.append(s))
    asyncio.run(inner.run("junior_clerk", {"amount": 500}))
    assert asks and asks[0].payload["channel"] == "handler"

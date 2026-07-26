"""std/nlp · std/style · std/eval 边界补测(STDLIB-CATALOG §W3;非锚点)。

锚点 tests/skills/test_std_nlp.py 覆盖主路径;本文件补薄弱边界:

- calibrate_judge:部分一致(kappa 落入门禁下方)、长度不等报错、pe=1 退化档;
- pairwise_compare:两评一致时 winner 还原为原始输入值(第一轮 A=a、B=b);
- style 三件:prompt 无任何花括号(inline 纯度闸门的自检);
- 全包纪律:prompt 技能省略 model 段(回落 RunConfig)、prompt 无裸 ``{}``
  (str.format 渲染安全)、judge description 带目录规定的负例原文。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolPolicy,
)
from agent_os.kernel.errors import ToolDispatchError
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.loader import render_prompt
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

STD_DIR = Path(__file__).resolve().parents[2] / "std"

W3_PROMPT_SKILLS = (
    "summarize", "classify", "extract", "translate", "rewrite", "qa_over_text",
    "compress_context", "tone_neutral", "untrusted_content", "knowledge_linking",
    "judge", "pairwise_judge_once",
)


def always_a_brain(req: ChatRequest) -> ChatResponse:
    """恒返回 winner=A 的评审大脑 = **纯位置偏见**裁判。

    槽位标签随 a/b 对调必然翻转,故"两轮同标签"说明裁判在看位置而非内容
    → pairwise_compare 应判平(§W3-5:位置偏见防控是技能定义的一部分)。
    """
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"winner": "A", "reason": "x"})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _kernel(brain=always_a_brain):
    sys.path.insert(0, str(STD_DIR))
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(LocalPythonToolRegistry())
        .skills(LocalFileSkillRegistry(str(STD_DIR / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


def run(skill: str, input: dict, **kw):
    return asyncio.run(_kernel(**kw).run(skill, input))


# ---------------------------------------------------------------------------
# calibrate_judge 边界
# ---------------------------------------------------------------------------


def test_calibrate_partial_agreement_below_gate():
    r = run("calibrate_judge", {
        "gold": [{"item": "a", "human_score": 1}, {"item": "b", "human_score": 0},
                 {"item": "c", "human_score": 1}, {"item": "d", "human_score": 0}],
        "judge_scores": [1, 1, 1, 0],  # 3/4 一致;pe=0.5 → kappa=0.5
    })
    assert r["agreement"] == 0.75
    assert r["kappa"] == pytest.approx(0.5)
    assert r["passes"] is False, "kappa < 0.7 不得过门禁"


def test_calibrate_length_mismatch_rejected():
    with pytest.raises(ToolDispatchError, match="长度不等"):
        run("calibrate_judge", {
            "gold": [{"item": "a", "human_score": 1}],
            "judge_scores": [1, 0],
        })


def test_calibrate_degenerate_single_category():
    # pe=1(两侧边际都只剩单一类别):全一致 → kappa=1.0;全不一致 → kappa=0.0
    r = run("calibrate_judge", {
        "gold": [{"item": "a", "human_score": 1}, {"item": "b", "human_score": 1}],
        "judge_scores": [1, 1],
    })
    assert r["kappa"] == pytest.approx(1.0) and r["passes"] is True
    r = run("calibrate_judge", {
        "gold": [{"item": "a", "human_score": 1}, {"item": "b", "human_score": 1}],
        "judge_scores": [0, 0],
    })
    assert r["kappa"] == pytest.approx(0.0) and r["passes"] is False


def test_calibrate_multi_class_kappa():
    # 三类别部分一致:po=2/4=0.5;边际 human {x:.5,y:.25,z:.25} × judge 同 → pe=0.375
    r = run("calibrate_judge", {
        "gold": [{"item": i, "human_score": s}
                 for i, s in enumerate(["x", "y", "x", "z"])],
        "judge_scores": ["x", "x", "y", "z"],
    })
    assert r["agreement"] == 0.5
    assert r["kappa"] == pytest.approx((0.5 - 0.375) / 0.625)


# ---------------------------------------------------------------------------
# pairwise_compare 边界
# ---------------------------------------------------------------------------


def test_pairwise_same_slot_twice_is_position_bias_tie():
    """两评同为 A(同一槽位)= 位置偏见 → 判平,不得输出胜者。"""
    r = run("pairwise_compare", {"a": "方案甲", "b": "方案乙", "question": "哪个更简洁"})
    assert r["winner"] == "tie", "两轮同槽位说明裁判在看位置而非内容,结论不可信"
    assert r["rounds"] == 2


def test_pairwise_unrecognized_label_ties():
    def weird_brain(req: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"winner": "差不多"})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    r = run("pairwise_compare", {"a": "甲", "b": "乙", "question": "q"}, brain=weird_brain)
    assert r["winner"] == "tie", "无法识别的胜者标签按不一致处理"


# ---------------------------------------------------------------------------
# 全包纪律(manifest 静态断言)
# ---------------------------------------------------------------------------


def test_style_prompts_have_no_braces():
    reg = LocalFileSkillRegistry(str(STD_DIR / "skills.yaml"))
    for name in ("tone_neutral", "untrusted_content", "knowledge_linking"):
        m = next(mm for mm in reg.manifests() if mm.name == name)
        assert "{" not in m.prompt and "}" not in m.prompt, f"{name} prompt 不得含花括号"


def test_w3_prompt_skills_discipline():
    reg = LocalFileSkillRegistry(str(STD_DIR / "skills.yaml"))
    manifests = {m.name: m for m in reg.manifests()}
    for name in W3_PROMPT_SKILLS:
        m = manifests[name]
        assert m.model is None, f"{name} 不得硬编码模型(省略 model 段,回落 RunConfig)"
        render_prompt(m.prompt or "", {})  # 无占位符:空输入可渲染即证明无裸 {}
        assert not m.permissions.tools and not m.permissions.skills
        assert "Use when" in m.description and "Do not use when" in m.description


def test_judge_description_carries_mandated_negative_example():
    reg = LocalFileSkillRegistry(str(STD_DIR / "skills.yaml"))
    m = next(mm for mm in reg.manifests() if mm.name == "judge")
    assert "Do not use when: 作为唯一验收依据" in m.description


def test_pairwise_compare_declares_private_dependency():
    reg = LocalFileSkillRegistry(str(STD_DIR / "skills.yaml"))
    manifests = {m.name: m for m in reg.manifests()}
    assert manifests["pairwise_compare"].permissions.skills == ["pairwise_judge_once"]
    assert manifests["pairwise_judge_once"].kind.value == "prompt"

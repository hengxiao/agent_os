"""W3 锚点测试:std/nlp · std/style · std/eval(STDLIB-CATALOG §W3;STDLIB §4.2-§4.4)。

固定约定:

- nlp 七件(summarize/classify/extract/translate/rewrite/qa_over_text/compress_context):
  prompt 技能,只做一件事,inputs 显式给全部材料,outputs 结构化可校验,
  **不硬编码模型名**(RunConfig 缺省);
- `extract` 的 inputs 收一个 JSON Schema 驱动抽取;
- style 三件(tone_neutral/untrusted_content/knowledge_linking):inline: true,
  过纯度闸门,prompt ≤ 500 字符;
- judge:rubric schema 化(dimensions[].weight ∈ essential|important|optional|veto,
  scoring 4..1 档),输出 per-dimension 分数 + veto_triggered;
- calibrate_judge:code,纯计算,输出 {agreement, kappa, passes};
- pairwise_compare:内建交换顺序两评,不一致判平。
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
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

STD_DIR = Path(__file__).resolve().parents[2] / "std"


# ---------------------------------------------------------------------------
# nlp 用 mock 大脑(按 system 内容区分技能,返回各 outputs 形状)
# ---------------------------------------------------------------------------


def nlp_brain(req: ChatRequest) -> ChatResponse:
    system = req.messages[0].content if req.messages else ""
    user = next((m.content for m in req.messages if m.role is Role.USER), "{}")

    def respond(payload):
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    if "摘要" in system and "任务感知" not in system:
        return respond({"summary": "三句话摘要:递归、调用栈、折叠。"})
    if "任务感知压缩" in system or "compress" in system:
        facts = json.loads(user).get("preserve", [])
        return respond({"compressed": f"保留 {len(facts)} 项:" + ",".join(facts), "dropped_estimate": 800})
    if "分类" in system:
        return respond({"category": "refund", "confidence": 0.9})
    if "抽取" in system or "extract" in system.lower():
        return respond({"order_id": "o1", "amount": 8900})
    if "翻译" in system:
        return respond({"translation": "the Fibonacci sequence"})
    if "改写" in system:
        return respond({"rewritten": "更通顺的版本"})
    if "问答" in system or "qa" in system.lower():
        return respond({"answer": "42"})
    if "评审" in system or "judge" in system.lower():
        return respond({
            "dimensions": [
                {"name": "准确性", "score": 4, "reason": "事实吻合"},
                {"name": "安全性", "score": 1, "reason": "泄露了路径"},
            ],
            "veto_triggered": True,
        })
    if "对比" in system or "compare" in system.lower():
        return respond({"winner": "A", "reason": "更简洁"})
    return respond({"text": "ok"})


def _kernel(brain=nlp_brain, config=None):
    sys.path.insert(0, str(STD_DIR))
    config = config or RunConfig(
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
# nlp 七件
# ---------------------------------------------------------------------------


def test_summarize():
    r = run("summarize", {"text": "长文……" * 100})
    assert isinstance(r["summary"], str) and r["summary"]


def test_classify():
    r = run("classify", {"text": "我要退款", "categories": ["refund", "complaint"]})
    assert r["category"] in ("refund", "complaint")
    assert 0 <= r["confidence"] <= 1


def test_extract_schema_driven():
    r = run("extract", {
        "text": "订单 o1 金额 8900 分",
        "schema": {"type": "object",
                   "properties": {"order_id": {"type": "string"}, "amount": {"type": "integer"}},
                   "required": ["order_id", "amount"]},
    })
    assert r == {"order_id": "o1", "amount": 8900}


def test_translate_rewrite_qa():
    assert run("translate", {"text": "菲波拉契数列", "target_lang": "en"})["translation"]
    assert run("rewrite", {"text": "句子"})["rewritten"]
    assert run("qa_over_text", {"text": "6乘7是42。", "question": "6乘7是多少?"})["answer"]


def test_compress_context_is_query_aware():
    r = run("compress_context", {
        "text": "大量无关内容……" * 50,
        "query": "预算是多少",
        "preserve": ["预算 $2", "结论 A"],
    })
    assert "预算 $2" in r["compressed"]
    assert r["dropped_estimate"] > 0


# ---------------------------------------------------------------------------
# style 三件(inline merge)
# ---------------------------------------------------------------------------


def test_style_skills_are_valid_inline_merge():
    reg = LocalFileSkillRegistry(str(STD_DIR / "skills.yaml"))
    for name in ("tone_neutral", "untrusted_content", "knowledge_linking"):
        m = next(mm for mm in reg.manifests() if mm.name == name)
        assert m.inline is True, f"{name} 必须是 inline: true"
        assert m.kind.value == "prompt"
        assert not m.permissions.tools and not m.permissions.skills, f"{name} 违反纯度闸门"
        assert m.prompt and len(m.prompt) <= 500, f"{name} 超过膨胀 lint 上限"


# ---------------------------------------------------------------------------
# eval:judge / calibrate_judge / pairwise_compare
# ---------------------------------------------------------------------------


def test_judge_rubric_schema_and_veto():
    r = run("judge", {
        "subject": "某份报告……",
        "rubric": {
            "dimensions": [
                {"name": "准确性", "weight": "essential",
                 "scoring": {"4": "无错误", "3": "小错", "2": "多处错", "1": "严重错"}},
                {"name": "安全性", "weight": "veto",
                 "scoring": {"4": "无泄露", "3": "", "2": "", "1": "泄露隐私"}},
            ],
            "edge_cases": ["报告含内部路径"],
        },
    })
    assert len(r["dimensions"]) == 2
    assert r["veto_triggered"] is True, "veto 维度低分必须一票否决"


def test_calibrate_judge_kappa():
    r = run("calibrate_judge", {
        "gold": [{"item": "a", "human_score": 1}, {"item": "b", "human_score": 0},
                 {"item": "c", "human_score": 1}, {"item": "d", "human_score": 0}],
        "judge_scores": [1, 0, 1, 0],
    })
    assert r["agreement"] == 1.0
    assert r["kappa"] == pytest.approx(1.0)
    assert r["passes"] is True, "完全一致应过 0.7 门禁"


def _pairwise_brain(winner_of):
    """构造 pairwise 假裁判:``winner_of(req) -> "A"|"B"``,并记录调用次数。"""
    calls = {"n": 0}

    def brain(req: ChatRequest) -> ChatResponse:
        calls["n"] += 1
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=json.dumps({"winner": winner_of(req), "reason": "x"}),
            ),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    return brain, calls


def test_pairwise_compare_ties_on_position_bias():
    """**纯位置偏见**(两轮都选同一槽位)→ 判平。

    槽位标签随 a/b 对调必然翻转,故"两轮同标签"恰恰说明裁判在看位置而非内容。
    """
    brain, calls = _pairwise_brain(lambda req: "A")  # 恒选 A 槽

    r = asyncio.run(
        _kernel(brain=brain).run(
            "pairwise_compare", {"a": "方案甲", "b": "方案乙", "question": "哪个更简洁"}
        )
    )
    assert calls["n"] == 2, "必须内建交换顺序两评"
    assert r["winner"] == "tie", "两轮同槽位 = 位置偏见,结论不可信"


def test_pairwise_compare_picks_content_consistent_winner():
    """**无位置偏见**(始终选同一份内容)→ 标签翻转,取该内容为胜者。"""
    def pick_jia(req: ChatRequest) -> str:
        """始终选"方案甲"这份内容,不看它落在哪个槽位。"""
        slots = json.loads(req.messages[1].content)  # 帧输入以首条 USER 消息进入
        return "A" if slots["a"] == "方案甲" else "B"

    brain, calls = _pairwise_brain(pick_jia)

    r = asyncio.run(
        _kernel(brain=brain).run(
            "pairwise_compare", {"a": "方案甲", "b": "方案乙", "question": "哪个更简洁"}
        )
    )
    assert calls["n"] == 2
    assert r["winner"] == "方案甲", "同一内容两轮都赢 → 还原为原始输入值"

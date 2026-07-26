"""research_pipeline 示例的通用 mock 大脑(调试用途)。

``ops_brain`` 是一个表驱动的确定性"模型",配合 ``skills.yaml`` 把
``research_report`` 的 19 帧调用树跑通(锚点:tests/test_examples.py)。
行为约定(与 tests/test_fib_agent.py 的 fib_brain 相同的消息解析方式):

- 从 system 消息首行 ``# skill: <name>`` 解析当前帧技能名;
- 帧输入取首条可解析为 JSON 对象的 USER 消息(尾部状态元消息自动跳过);
- 编排技能(research_report / research_topic / extract_facts / draft_report)
  按拓扑顺序推进:由 assistant tool_calls 与 TOOL 结果消息推导"已执行到第几步",
  把下一个未执行的 invoke 作为 tool_call 发出;子结果齐后 final;
- 叶技能(summarize_topic / style_review / keyword_extractor / translate_en /
  risk_scan)直接 final,内容含技能名与输入摘要,便于在 CLI/Web 帧树里逐帧辨认;
- 每个 final 都严格通过对应 manifest 的 outputs schema(jsonschema 校验会拦)。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)

# ---------------------------------------------------------------------------
# 请求解析
# ---------------------------------------------------------------------------


def _skill_name(req: ChatRequest) -> str:
    """system 消息首行 ``# skill: <name>`` → 技能名。"""
    first_line = req.messages[0].content.splitlines()[0]
    return first_line.split(":", 1)[1].strip()


def _frame_input(req: ChatRequest) -> dict[str, Any]:
    """首条可解析为 JSON 对象的 USER 消息 = 帧输入(状态元消息等自动跳过)。"""
    for m in req.messages:
        if m.role is Role.USER:
            try:
                data = json.loads(m.content)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return data
    raise AssertionError("帧上下文缺少输入消息")


def _completed_results(req: ChatRequest) -> list[dict[str, Any]]:
    """按序收集已完成的调用结果(``{"ok","value","error"}`` JSON)。"""
    call_ids: set[str] = set()
    results: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                call_ids.add(tc.id)
        elif m.role is Role.TOOL and m.tool_call_id in call_ids:
            results.append(json.loads(m.content))
    for r in results:
        assert r["ok"], f"子调用失败,demo 大脑不予恢复: {r}"
    return results


# ---------------------------------------------------------------------------
# 应答构造
# ---------------------------------------------------------------------------


def _final(payload: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _call(tc: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _next(
    skill: str, steps: list[tuple[str, dict[str, Any]]], results: list[dict[str, Any]]
) -> ChatResponse | None:
    """还有未执行的编排步骤 → 发下一个 tool_call;全部完成 → None(该 final 了)。"""
    if len(results) >= len(steps):
        return None
    tool, args = steps[len(results)]
    return _call(ToolCall(id=f"call-{skill}-{len(results)}", name=tool, args=args))


# ---------------------------------------------------------------------------
# 编排技能:调用计划按拓扑顺序随已收结果逐段展开
# ---------------------------------------------------------------------------


def _research_report(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    question = inp["question"]
    steps: list[tuple[str, dict[str, Any]]] = [("skill__plan_topics", {"question": question})]
    if results:
        for topic in results[0]["value"]["topics"]:
            steps.append(("skill__research_topic", {"topic": topic}))
    if len(results) >= 4:
        summaries = [r["value"]["summary"] for r in results[1:4]]
        steps.append(("skill__merge_summaries", {"summaries": summaries}))
    if len(results) >= 5:
        merged = results[4]["value"]["merged"]
        steps.append(("skill__draft_report", {"question": question, "merged": merged}))
    if len(results) >= 6:
        out = results[5]["value"]
        steps.append(
            ("skill__finalize_report", {"draft": out["draft"], "citations": out["citations"]})
        )
    nxt = _next("research_report", steps, results)
    if nxt is not None:
        return nxt
    return _final(results[-1]["value"])  # finalize 的 {report, words, citations} 原样上抛


def _research_topic(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    topic = inp["topic"]
    steps: list[tuple[str, dict[str, Any]]] = [("skill__fetch_source", {"topic": topic})]
    if results:
        steps.append(("skill__extract_facts", {"text": results[0]["value"]["text"]}))
    nxt = _next("research_topic", steps, results)
    if nxt is not None:
        return nxt
    facts = results[-1]["value"]["facts"]
    summary = f"[research_topic] {topic}:基于 {len(facts)} 条已核实事实的综合摘要"
    return _final({"summary": summary, "facts": facts})


def _extract_facts(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    facts = _split_facts(inp["text"])
    steps = [("skill__verify_facts", {"facts": facts})]
    nxt = _next("extract_facts", steps, results)
    if nxt is not None:
        return nxt
    return _final({"facts": results[-1]["value"]["verified"]})


def _draft_report(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    question = inp["question"]
    merged = inp["merged"]
    draft = (
        f"研究问题:{question}。\n"
        f"综合发现:\n{merged}\n"
        "结论:以上主题覆盖了该问题的主要方面,详见各节。"
    )
    steps = [
        ("skill__style_review", {"draft": draft}),
        ("skill__fact_check", {"draft": draft}),
    ]
    nxt = _next("draft_report", steps, results)
    if nxt is not None:
        return nxt
    citations = [f"内部资料摘要 {i}" for i in range(1, len(merged.splitlines()) + 1)]
    return _final({"draft": draft, "citations": citations})


def _split_facts(text: str) -> list[str]:
    """确定性切事实:按中英文句读与换行切分,去空白留非空。"""
    return [p.strip() for p in re.split(r"[。!?;!?\n]+", text) if p.strip()]


_ORCHESTRATORS = {
    "research_report": _research_report,
    "research_topic": _research_topic,
    "extract_facts": _extract_facts,
    "draft_report": _draft_report,
}


# ---------------------------------------------------------------------------
# 叶技能:直接 final,内容含技能名与输入摘要(UI 调试时可逐帧辨认)
# ---------------------------------------------------------------------------


def _leaf_summarize_topic(inp: dict[str, Any]) -> dict[str, Any]:
    facts = inp["facts"]
    return {
        "summary": f"[summarize_topic] {inp['topic']}:共 {len(facts)} 条事实的摘要",
        "facts": facts,
    }


def _leaf_style_review(inp: dict[str, Any]) -> dict[str, Any]:
    draft = inp["draft"]
    return {
        "comments": [
            f"[style_review] 草稿 {len(draft)} 字符,结构完整",
            "[style_review] 语气一致,无需修改",
        ]
    }


def _leaf_keyword_extractor(inp: dict[str, Any]) -> dict[str, Any]:
    words = inp["text"].split()
    return {"keywords": words[:5] if words else [inp["text"][:8]]}


def _leaf_translate_en(inp: dict[str, Any]) -> dict[str, Any]:
    return {"translation": f"[translate_en] EN::{inp['text'][:24]}"}


def _leaf_risk_scan(inp: dict[str, Any]) -> dict[str, Any]:
    return {"risks": [] if inp["text"].strip() else ["[risk_scan] 空文本"]}


_LEAF_FINALS = {
    "summarize_topic": _leaf_summarize_topic,
    "style_review": _leaf_style_review,
    "keyword_extractor": _leaf_keyword_extractor,
    "translate_en": _leaf_translate_en,
    "risk_scan": _leaf_risk_scan,
}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def ops_brain(req: ChatRequest) -> ChatResponse:
    """通用表驱动 mock 大脑:按 system 首行的技能名分派到编排/叶行为。"""
    name = _skill_name(req)
    if name in _LEAF_FINALS:
        return _final(_LEAF_FINALS[name](_frame_input(req)))
    if name in _ORCHESTRATORS:
        return _ORCHESTRATORS[name](_frame_input(req), _completed_results(req))
    raise AssertionError(f"ops_brain 未覆盖的技能: {name}")


# ---------------------------------------------------------------------------
# support_desk 大脑代理(跨示例 sys.modules 共存桥)
# ---------------------------------------------------------------------------


def support_brain(req: ChatRequest) -> ChatResponse:
    """``examples/support_desk`` 的 mock 大脑代理(按文件路径惰性加载)。

    锚点机制约束:tests/test_examples.py 与 tests/test_support_example.py 都以
    扁平名 ``import brains`` 取各自示例的大脑,而 pytest 单进程内
    ``sys.modules["brains"]`` 先缓存者胜(test_examples 先跑)——support_desk
    的 ``brains.py`` 本体因此经本代理以独立模块名 ``support_desk_brains``
    按文件路径加载,不抢占 ``"brains"`` 命名。单独跑 test_support_example.py
    (或 CLI 直接加载 support_desk/agent-os.toml)时,本体直接被 import,
    本代理不会被用到。
    """
    module = sys.modules.get("support_desk_brains")
    if module is None:
        path = Path(__file__).resolve().parent.parent / "support_desk" / "brains.py"
        spec = importlib.util.spec_from_file_location("support_desk_brains", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["support_desk_brains"] = module
        spec.loader.exec_module(module)
    return module.support_brain(req)


def travel_brain(req: ChatRequest) -> ChatResponse:
    """``examples/travel_planner`` 的 mock 大脑代理(与 support_brain 同一共存桥机制)。

    tests/examples/test_travel_planner.py 同样以扁平名 ``import brains`` 取
    ``travel_brain``;全量 pytest 单进程内 ``sys.modules["brains"]`` 已被本模块
    (research_pipeline,字母序先跑)缓存,故 travel_planner 的 ``brains.py`` 本体
    经本代理以独立模块名 ``travel_planner_brains`` 按文件路径加载。单独跑
    test_travel_planner.py 时本体直接被 import,本代理不会被用到。
    """
    module = sys.modules.get("travel_planner_brains")
    if module is None:
        path = Path(__file__).resolve().parent.parent / "travel_planner" / "brains.py"
        spec = importlib.util.spec_from_file_location("travel_planner_brains", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["travel_planner_brains"] = module
        spec.loader.exec_module(module)
    return module.travel_brain(req)

"""travel_planner 锚点测试:现场查互联网的旅游攻略技能包(目的地无关,live-shaped)。

设计(STDLIB 文化):flow 真实、数据真实来自互联网(Wikivoyage 开放 API,零 key);
测试用 mock wiki 工具保证确定性,live smoke 验证真实联网路径。

固定约定:

- 目的地无关:`plan_trip` 接收自然语言请求 → 解析 → `wiki_search`/`wiki_fetch`
  取回目的地资料 → 从资料中**提取**事实(不是脑内硬编码)→ 路线/餐饮/住宿/预算;
- 工具面:`wiki_search(query) -> {titles}`、`wiki_fetch(title, chars_limit?) ->
  {title, text, truncated}`(真实 Wikivoyage API;超长显式截断);
- 预算为资料价目驱动的真实算术;超预算触发 ask_supervisor;
- live smoke 须网络可达 wikivoyage.org(否则 skip)。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_os.api.v1 import ChatRequest, ChatResponse, ChatUsage, Message, Permission, Role, RunConfig, ToolCall, ToolPolicy
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

PLANNER_DIR = Path(__file__).resolve().parents[2] / "examples" / "travel_planner"

REQUEST = "想带老人从上海去西安玩 3 天 2 夜,预算 3000 元,节奏别太赶"

#: mock 的 Wikivoyage 正文(西安,含真实事实,模拟联网取回形态)
CANNED_XIAN = """
Xi'an (西安) is a city in Shaanxi Province, China.
==See==
* Terracotta Army (兵马俑). Entry ¥120. Located in Lintong, 40 km east of downtown.
Allow at least 3 hours. A UNESCO site with thousands of life-sized warriors.
* Huaqing Pool (华清池). Entry ¥120. Also in Lintong, hot springs and palaces. 2 hours.
* Shaanxi History Museum (陕西历史博物馆). Free, reservation required. Closed on Mondays.
Allow 3 hours.
* Xi'an City Wall (西安城墙). Entry ¥54. 14 km around the old city; bike rental available.
* Muslim Quarter (回民街). Free. Food street with local snacks.
* Giant Wild Goose Pagoda (大雁塔). Entry ¥40.
==Eat==
* 老孙家泡馍. Lamb paomo, about ¥40 per person.
* 贾三灌汤包. Soup dumplings, about ¥35 per person.
* 永兴坊. Snack street, about ¥30 per person.
==Sleep==
* Bell Tower area hotels: mid-range ¥400-600 per night.
* Big Goose Pagoda area: ¥350-500 per night.
==Get in==
* High-speed rail from Shanghai: about 6 hours, ¥669 second class.
==Get around==
* Metro covers most sights. Taxi to Lintong about ¥120 one way.
"""


def _register_mock_wiki(tools):
    @tools.tool(permission=Permission.NET, timeout=5)
    async def wiki_search(query: str) -> dict:
        """搜索 Wikivoyage 条目(mock)。"""
        return {"titles": ["Xi'an"]}

    @tools.tool(permission=Permission.NET, timeout=5)
    async def wiki_fetch(title: str, chars_limit: int = 6000) -> dict:
        """取 Wikivoyage 正文(mock,西安)。"""
        return {"title": title, "text": CANNED_XIAN[:chars_limit], "truncated": len(CANNED_XIAN) > chars_limit}


def _build(brain, handler=None):
    sys.path.insert(0, str(PLANNER_DIR))
    tools = LocalPythonToolRegistry.with_builtins()
    _register_mock_wiki(tools)
    builder = (
        KernelBuilder(
            RunConfig(model="mock/travel", max_depth=10, tool_policy=ToolPolicy(max_permission=Permission.EXEC), compression="off")
        )
        .providers(MockProvider(brain))
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(PLANNER_DIR / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if handler is not None:
        builder = builder.supervisor(handler)
    return builder.build()


def _brain():
    sys.path.insert(0, str(PLANNER_DIR))
    try:
        import brains

        return brains.travel_brain
    finally:
        sys.path.remove(str(PLANNER_DIR))


def test_loads_skills():
    reg = LocalFileSkillRegistry(str(PLANNER_DIR / "skills.yaml"))
    assert len(reg.manifests()) >= 10


def test_itinerary_facts_come_from_fetched_content():
    """三日攻略:行程事实必须来自 wiki 取回的正文(兵马俑 ¥120、陕历博免费等),
    预算 = 各项真实算术之和且 ≤3000。"""
    result = asyncio.run(_build(_brain()).run("plan_trip", {"request": REQUEST}))

    assert result["destination"] == "西安"
    assert len(result["days"]) == 3
    spots = [s for day in result["days"] for s in day["attractions"]]
    assert any("兵马俑" in s or "Terracotta" in s for s in spots)
    for day in result["days"]:
        assert day["attractions"] and day["meals"]
    b = result["budget"]
    assert b["total"] == b["transport"] + b["hotel"] + b["tickets"] + b["meals"] + b["local"]
    assert b["total"] <= 3000
    assert result["hotel"]["price_per_night"] > 0


def test_route_clusters_lintong_together():
    """资料注明兵马俑与华清池同在临潼 → 必须同日。"""
    result = asyncio.run(_build(_brain()).run("plan_trip", {"request": REQUEST}))
    for day in result["days"]:
        spots = " ".join(day["attractions"])
        if "兵马俑" in spots or "Terracotta" in spots:
            assert "华清池" in spots or "Huaqing" in spots


def test_over_budget_triggers_supervisor():
    seen = []

    async def handler(question):
        seen.append(question)
        return {"answer": "trim", "decided_by": "test"}

    result = asyncio.run(
        _build(_brain(), handler).run("plan_trip", {"request": "想带老人从上海去西安玩 3 天 2 夜,预算 1500 元"})
    )
    assert seen, "超预算必须请求上级裁决"
    assert result["budget"]["total"] <= 1500 or result.get("over_budget_approved") is True


def test_deterministic():
    r1 = asyncio.run(_build(_brain()).run("plan_trip", {"request": REQUEST}))
    r2 = asyncio.run(_build(_brain()).run("plan_trip", {"request": REQUEST}))
    assert r1 == r2


# ---------------------------------------------------------------------------
# live smoke:真实 Wikivoyage API(网络可达才跑)
# ---------------------------------------------------------------------------


def _wikivoyage_reachable() -> bool:
    import urllib.request

    try:
        urllib.request.urlopen("https://en.wikivoyage.org/wiki/Xi%27an", timeout=8)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _wikivoyage_reachable(), reason="wikivoyage.org 不可达")
def test_live_wiki_fetch_hits_real_internet():
    """真实工具:wiki_fetch 从互联网取回 Xi'an 正文并含真实内容。"""
    sys.path.insert(0, str(PLANNER_DIR))
    try:
        import travel_tools

        tools = LocalPythonToolRegistry()
        travel_tools.register(tools)
        from agent_os.api.v1 import SkillFrame, ToolDispatchContext

        ctx = ToolDispatchContext(
            frame=SkillFrame(frame_id="f", run_id="r"),
            allowed_tools=["wiki_fetch"],
            tool_policy=ToolPolicy(max_permission=Permission.NET),
        )
        result = asyncio.run(tools.dispatch(ToolCall(id="w", name="wiki_fetch", args={"title": "Xi'an"}), ctx))
    finally:
        sys.path.remove(str(PLANNER_DIR))
    assert result.ok, result.error
    text = result.value["text"]
    assert len(text) > 1000
    assert "Terracotta" in text or "兵马俑" in text

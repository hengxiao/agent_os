"""DefaultModelRouter 锚点测试(docs/DESIGN.md §4.2;ModelRouter 契约 v1 默认实现)。

固定约定:

- 候选链 = ``[*prefer, config_model]``(去重保序,空串剔除);
- 逐候选:``ProviderManager.resolve`` 失败(前缀未注册)→ 跳过;
  ``req.tools`` 非空要求 ``supports_tools``;``req.messages`` 任一 ``m.parts``
  非空要求 ``supports_vision``;
- 首个全过 → ``(candidate, {"temperature": req.temperature})``(参数直通);
- 全不过 **fail-open**:warning + ``(链首, {...})``(现状语义:模型串原样上送,
  ProviderManager 调用期硬报错);链空 → ``("", {...})`` 不告警(裸装配现状)。
"""

from __future__ import annotations

import asyncio
import logging

from agent_os.api.v1 import (
    ChatRequest,
    ContentPart,
    Message,
    ProviderCaps,
    Role,
)
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider
from agent_os.providers.router import DefaultModelRouter


class CapsProvider:
    """只报 caps 的最小 provider(探测语义断言用;chat/stream 不应被触达)。"""

    def __init__(self, name: str, *, tools: bool = False, vision: bool = False) -> None:
        self.name = name
        self._caps = ProviderCaps(supports_tools=tools, supports_vision=vision)

    def capabilities(self) -> ProviderCaps:
        return self._caps

    async def chat(self, req: ChatRequest):
        raise NotImplementedError

    async def stream(self, req: ChatRequest):
        raise NotImplementedError


def _req(*, tools: bool = False, parts: bool = False, temperature: float | None = None) -> ChatRequest:
    return ChatRequest(
        messages=[
            Message(
                role=Role.USER,
                content="hi",
                parts=[ContentPart()] if parts else None,
            )
        ],
        tools=[{"name": "t", "description": "", "parameters": {}}] if tools else [],
        temperature=temperature,
    )


def _route(router: DefaultModelRouter, req: ChatRequest, prefer: list[str] | None = None):
    return asyncio.run(router.route(req, prefer))


# ---------------------------------------------------------------------------
# 候选链形状
# ---------------------------------------------------------------------------


def test_candidates_dedup_preserves_order_and_drops_empty():
    """候选链 = [*prefer, config_model],去重保序、空串剔除。"""
    router = DefaultModelRouter(ProviderManager(), config_model="cfg/m")
    assert router._candidates(["a/x", "a/x", "", "b/y"]) == ["a/x", "b/y", "cfg/m"]
    assert router._candidates(None) == ["cfg/m"]
    assert router._candidates([]) == ["cfg/m"]
    assert router._candidates(["cfg/m"]) == ["cfg/m"]  # 与 config_model 重复 → 去重


def test_prefer_order_first_registered_wins():
    """prefer 在前:两候选都可用时选 prefer[0](与原内联解析同序)。"""
    mgr = ProviderManager([CapsProvider("a", tools=True), CapsProvider("b", tools=True)])
    router = DefaultModelRouter(mgr, config_model="cfg/m")
    model, _ = _route(router, _req(), ["a/x", "b/y"])
    assert model == "a/x"


def test_unregistered_prefix_skipped_falls_to_config_model():
    """prefer[0] 前缀未注册 → 跳过,fail-open 到 config_model(注册即用)。"""
    mgr = ProviderManager([CapsProvider("cfg")])
    router = DefaultModelRouter(mgr, config_model="cfg/m")
    model, _ = _route(router, _req(), ["nope/x"])
    assert model == "cfg/m"


def test_config_model_fallback_when_no_prefer():
    """prefer 空/None:config_model 兜底(对应 RunConfig.model 回落)。"""
    mgr = ProviderManager([CapsProvider("cfg")])
    router = DefaultModelRouter(mgr, config_model="cfg/m")
    assert _route(router, _req(), None)[0] == "cfg/m"
    assert _route(router, _req(), [])[0] == "cfg/m"


# ---------------------------------------------------------------------------
# caps 探测
# ---------------------------------------------------------------------------


def test_tools_caps_probe_picks_second_candidate():
    """req.tools 非空:首候选 supports_tools=False → 跳过,选满足的第二候选。"""
    mgr = ProviderManager([CapsProvider("weak", tools=False), CapsProvider("strong", tools=True)])
    router = DefaultModelRouter(mgr, config_model="")
    model, _ = _route(router, _req(tools=True), ["weak/a", "strong/b"])
    assert model == "strong/b"


def test_tools_probe_passes_when_request_has_no_tools():
    """req.tools 为空:不触发 tools 门,无 tools 能力的候选照选。"""
    mgr = ProviderManager([CapsProvider("weak", tools=False)])
    router = DefaultModelRouter(mgr, config_model="")
    model, _ = _route(router, _req(tools=False), ["weak/a"])
    assert model == "weak/a"


def test_vision_caps_probe_skips_non_vision_candidates():
    """req 带 parts 消息:无 vision 能力的候选跳过,选 supports_vision 的第二候选。"""
    mgr = ProviderManager(
        [CapsProvider("novis", vision=False), MockProvider(name="vis", supports_vision=True)]
    )
    router = DefaultModelRouter(mgr, config_model="")
    model, _ = _route(router, _req(parts=True), ["novis/a", "vis/b"])
    assert model == "vis/b"


def test_vision_probe_passes_without_parts():
    """消息无 parts(None 与空表同):不触发 vision 门。"""
    mgr = ProviderManager([CapsProvider("novis", vision=False)])
    router = DefaultModelRouter(mgr, config_model="")
    assert _route(router, _req(parts=False), ["novis/a"])[0] == "novis/a"
    bare = ChatRequest(messages=[Message(role=Role.USER, content="hi", parts=[])])
    assert _route(router, bare, ["novis/a"])[0] == "novis/a"


# ---------------------------------------------------------------------------
# fail-open 与参数直通
# ---------------------------------------------------------------------------


def test_all_candidates_fail_fails_open_with_warning(caplog):
    """全不过:warning + 回落链首(现状语义——模型串原样上送,调用期才硬报错)。"""
    mgr = ProviderManager([CapsProvider("vis", vision=True)])
    router = DefaultModelRouter(mgr, config_model="alsonope/y")
    with caplog.at_level(logging.WARNING, logger="agent_os.providers.router"):
        model, params = _route(router, _req(), ["nope/x"])
    assert model == "nope/x"  # 链首(prefer[0]),不是 config_model
    assert params == {"temperature": None}
    assert "fail-open" in caplog.text


def test_empty_chain_returns_empty_model_without_warning(caplog):
    """链空(prefer 与 config_model 都缺):("", {...}) 原样上送,不告警(裸装配现状)。"""
    router = DefaultModelRouter(ProviderManager(), config_model="")
    with caplog.at_level(logging.WARNING, logger="agent_os.providers.router"):
        model, params = _route(router, _req(), None)
    assert model == ""
    assert params == {"temperature": None}
    assert "fail-open" not in caplog.text


def test_temperature_passthrough():
    """params 直通 req.temperature(v1 不改写采样参数);None 原样。"""
    mgr = ProviderManager([CapsProvider("a")])
    router = DefaultModelRouter(mgr, config_model="")
    _, params = _route(router, _req(temperature=0.7), ["a/x"])
    assert params == {"temperature": 0.7}
    _, params = _route(router, _req(temperature=None), ["a/x"])
    assert params == {"temperature": None}

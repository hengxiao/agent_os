"""成本记账锚点测试(docs/DESIGN.md §4.2;记账职责在 ProviderManager)。

固定约定:

- ``[prices]`` 给出每模型每百万 token 单价(``{input, output, cache_read?, cache_write?}``),
  键为完整 model 串或 provider 前缀(**精确优先**);
- ``ProviderManager`` 在返回响应前按单价折算 ``usage.cost``;provider 自报
  cost 时不覆盖(有些端点直接给金额);
- **无单价表则 cost 保持 0**,此时 ``max_cost`` / BudgetGuard 不会触发——
  装配期必须告警,不得静默假装有护栏。

回归的是一个真实缺陷:三个 provider 都不填 cost、manager 也不折算,
``usage.cost`` 全链路恒为 0,配了 ``max_cost`` 的用户以为有成本护栏,实际没有
(真机跑 48 万 prompt token 的 run 报出的仍是 cost 0.0)。
"""

from __future__ import annotations

import asyncio

from agent_os.api.v1 import ChatRequest, ChatResponse, ChatUsage, Message, Role
from agent_os.providers.manager import ProviderManager

PRICES = {"mock/big": {"input": 3.0, "output": 15.0}, "mock": {"input": 1.0, "output": 2.0}}


class _Provider:
    name = "mock"

    def __init__(self, usage: ChatUsage) -> None:
        self._usage = usage

    def capabilities(self):
        from agent_os.api.v1 import ProviderCaps

        return ProviderCaps(supports_tools=True)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content="ok"),
            finish_reason="stop",
            usage=self._usage,
        )

    async def stream(self, req: ChatRequest):  # pragma: no cover
        raise NotImplementedError


def _chat(usage: ChatUsage, model: str, prices=PRICES) -> ChatResponse:
    mgr = ProviderManager([_Provider(usage)], prices=prices)
    return asyncio.run(mgr.chat(ChatRequest(model=model, messages=[])))


def test_cost_computed_from_price_table():
    """1M prompt + 1M completion × (3, 15) = 18 美元。"""
    resp = _chat(ChatUsage(prompt=1_000_000, completion=1_000_000), "mock/big")
    assert resp.usage.cost == 18.0


def test_exact_model_beats_provider_prefix():
    """精确 model 串优先于 provider 前缀(同一 provider 下多档价)。"""
    big = _chat(ChatUsage(prompt=1_000_000), "mock/big")
    small = _chat(ChatUsage(prompt=1_000_000), "mock/other")
    assert big.usage.cost == 3.0, "应用 mock/big 的 input=3.0"
    assert small.usage.cost == 1.0, "无精确匹配时回落前缀 mock 的 input=1.0"


def test_cache_tokens_priced_and_default_to_input():
    """cache_read/write 未单列时按 input 价计(保守),给了则用给的。"""
    default = _chat(ChatUsage(cache_read=1_000_000), "mock/big")
    assert default.usage.cost == 3.0

    explicit = _chat(
        ChatUsage(cache_read=1_000_000),
        "mock/big",
        prices={"mock/big": {"input": 3.0, "output": 15.0, "cache_read": 0.3}},
    )
    assert explicit.usage.cost == 0.3


def test_provider_reported_cost_is_not_overwritten():
    """端点直接给金额时以它为准(不重复折算)。"""
    resp = _chat(ChatUsage(prompt=1_000_000, cost=0.42), "mock/big")
    assert resp.usage.cost == 0.42


def test_no_price_table_leaves_cost_zero():
    """无单价表 → cost 保持 0(而非猜一个);护栏失效由装配期告警负责提示。"""
    resp = _chat(ChatUsage(prompt=1_000_000), "mock/big", prices=None)
    assert resp.usage.cost == 0.0


def test_missing_prices_warns_at_assembly(caplog):
    """装配期告警:配了 max_cost 却无价格源 = 没有成本护栏,必须让人知道。"""
    import logging

    from agent_os.runtime.config import build_kernel
    from tests.helpers.kernels import FIB_SKILLS_YAML

    cfg = {
        "run": {"model": "mock/fib", "compression": "off", "max_cost": 5.0},
        "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
        "tools": {"python_exec": "subprocess"},
        "skills": {"path": str(FIB_SKILLS_YAML)},
    }
    with caplog.at_level(logging.WARNING, logger="agent_os.runtime.config"):
        build_kernel(cfg)
    assert any("不会触发" in r.getMessage() for r in caplog.records), "缺 [prices] 必须告警"


def test_cost_reaches_run_accounting(tmp_path):
    """端到端:折算后的 cost 进入 run 级记账(max_cost 因此才可能触发)。"""
    from agent_os.api.v1 import Permission, RunConfig, ToolPolicy
    from tests.helpers.brains import fib_brain
    from tests.helpers.kernels import FIB_SKILLS_YAML, assemble

    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(config, fib_brain, FIB_SKILLS_YAML)
    kernel.providers.prices = {"mock": {"input": 1000.0, "output": 1000.0}}
    asyncio.run(kernel.run("demo.fib", {"n": 3}))
    total = next(iter(kernel._runs.values())).state.usage.cost
    assert total > 0, "折算出的 cost 必须进 run 级记账,否则 max_cost 仍是死开关"

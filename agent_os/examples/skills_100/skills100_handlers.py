"""skills_100 压测示例的 code 技能 handlers(调试用途)。

``mega_pipeline`` → ``chain_00`` → … → ``chain_09`` → ``hub_agg`` 的 12 帧纯 code
深链(锚点:tests/test_examples.py::test_skills_100_mega_pipeline_runs):
每环把 ``i`` 累加进 ``acc``,终点 ``hub_agg`` 返回 ``{"total": acc}``
(0+1+…+9 = 45),depth 恰好到 12。签名约定(DESIGN.md §6.3):
``async def run(input: dict, ctx: LogicContext) -> dict``,全部经 ``ctx.invoke``
回到内核分发路径(白名单、信号、记账一样不少)。
"""

from __future__ import annotations

from typing import Any


async def mega_pipeline(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """入口:以 acc=0 启动 10 层 chain,返回 hub_agg 的汇总。"""
    return await ctx.invoke("project.skills_100.chain_00", {"acc": 0})


async def chain_00(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 0 环:累加 0 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_01", {"acc": input["acc"] + 0})


async def chain_01(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 1 环:累加 1 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_02", {"acc": input["acc"] + 1})


async def chain_02(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 2 环:累加 2 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_03", {"acc": input["acc"] + 2})


async def chain_03(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 3 环:累加 3 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_04", {"acc": input["acc"] + 3})


async def chain_04(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 4 环:累加 4 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_05", {"acc": input["acc"] + 4})


async def chain_05(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 5 环:累加 5 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_06", {"acc": input["acc"] + 5})


async def chain_06(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 6 环:累加 6 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_07", {"acc": input["acc"] + 6})


async def chain_07(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 7 环:累加 7 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_08", {"acc": input["acc"] + 7})


async def chain_08(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 8 环:累加 8 后交下一环。"""
    return await ctx.invoke("project.skills_100.chain_09", {"acc": input["acc"] + 8})


async def chain_09(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """第 9 环:累加 9 后交 hub_agg 汇总。"""
    return await ctx.invoke("project.skills_100.hub_agg", {"acc": input["acc"] + 9})


async def hub_agg(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """终点:返回 {"total": acc}。"""
    return {"total": input["acc"]}

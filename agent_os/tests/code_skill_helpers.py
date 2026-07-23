"""M2 锚点测试用 code 技能 handlers(dotted path 惰性加载,§6.3)。

签名约定(§6.3):``async def run(input: dict, ctx: LogicContext) -> dict``。
``ctx.invoke`` / ``ctx.call_tool`` 全部回到内核分发路径(白名单、信号、记账一样不少,§9.3)。
"""

from __future__ import annotations

from typing import Any


async def fib_pair(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """编排者(§9.3):确定性控制流 + 按需调 LLM 技能。拼接 fib(a) 与 fib(b)。"""
    a = await ctx.invoke("fib", {"n": input["a"]})
    b = await ctx.invoke("fib", {"n": input["b"]})
    return {"combined": a["seq"] + b["seq"]}


async def double_it(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """经 ctx.call_tool 调 python_exec(Logic Kernel 沙箱)计算。"""
    r = await ctx.call_tool("python_exec", {"code": f"result = {input['x']} * 2\nprint(result)"})
    assert r["ok"], r
    return {"doubled": r["value"]["result"]}


async def pure_add(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """纯计算(无回调),沙箱可执行。"""
    return {"sum": input["a"] + input["b"]}


async def naughty(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """调用白名单外的子技能(用于权限拒绝测试)。"""
    await ctx.invoke("fib", {"n": 3})
    return {"never": True}


async def bad_output(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """返回不合 outputs schema 的值(用于 code 技能输出校验测试)。"""
    return {"wrong": 1}

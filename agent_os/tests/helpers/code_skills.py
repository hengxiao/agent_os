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


async def spawn_pair(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """后台帧编排(§3.4 spawn):父帧不挂起,两个子帧后台跑,wait 读终态。"""
    f1 = await ctx.spawn("fib", {"n": input["a"]})
    f2 = await ctx.spawn("fib", {"n": input["b"]})
    ra = await ctx.wait(f1)
    rb = await ctx.wait(f2)
    return {"combined": ra["seq"] + rb["seq"]}


async def spawn_naughty(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """spawn 白名单外的子技能(用于权限拒绝测试)。"""
    await ctx.spawn("fib", {"n": 2})
    return {"never": True}


async def invoke_one(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """程序化调用任意白名单子技能(inline 降级等测试用)。"""
    value = await ctx.invoke(input["skill"], dict(input.get("args") or {}))
    return {"value": value}


async def spawn_one(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """spawn 任意白名单子技能并等终态(inline 与 spawn 正交性测试用)。"""
    fid = await ctx.spawn(input["skill"], dict(input.get("args") or {}))
    return {"frame_id": fid, "value": await ctx.wait(fid)}


async def board_put_get(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """经 ctx.board 代理在白名单命名空间读写 KV(§12 内核中介仲裁)。"""
    version = await ctx.board.put("shared", "answer", input["v"])
    value, ver = await ctx.board.get("shared", "answer")
    return {"value": value, "version": ver, "put_version": version}


async def board_naughty(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """写白名单外命名空间(用于黑板权限拒绝测试)。"""
    await ctx.board.put("secret", "k", 1)
    return {"never": True}


async def board_publish(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """经 ctx.board 代理向白名单命名空间发消息(Envelope 透传)。"""
    from agent_os.api.v1 import Envelope

    await ctx.board.publish(
        "shared", Envelope(sender=ctx.frame_id, type="status_update", payload={"pct": input["pct"]})
    )
    return {"sent": True}

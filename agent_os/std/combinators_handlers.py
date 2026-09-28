"""std/combinators 技能 handler(STDLIB-CATALOG §W5-WS2+3;STDLIB §4.5)。

- ``race_first``:首个成功分支胜出。分支并发/取消/ack/幂等结算全部委托内核
  ``parallel_invoke``(first_success 模式)——经私有批帧 ``race_batch`` 间接调用,
  本技能只加 **budget 软闸**:watchdog 轮询批帧子树 usage(``ctx.frame_status``,
  W5-WS1/WS2 读面),超限时 ``ctx.cancel`` 批帧(级联取消全部在跑分支)并返回
  部分结果。批帧独立存在的理由:watchdog 需要单一的"批帧"寻址单位——子树
  usage 聚合在一帧上读、级联取消对一帧发。
- ``race_batch``:race_first 的私有依赖,把 branches 原样交 ``ctx.parallel``。
  出厂空白名单;分支技能由调用方复制专化填入(STDLIB §4.5:静态声明的少数
  条目走复制专化,运行期通配仍然拒绝)。
- ``subagent_cancel`` / ``subagent_status``:``ctx.cancel`` / ``ctx.frame_status``
  的技能面薄适配(WRITE / READ 语义;副作用限于本 run 帧树,推导档 none)。

budget 内核强制已落地(``Kernel._check_subtree_budgets``:manifest
``limits.max_cost``/``max_steps`` 挂帧,记账点沿祖先链穿透检查,超限子树级
中止/根帧炸 run;写路径不动——父帧 usage 不含子帧,检查侧读侧聚合)。本模块的
budget 软闸与之正交(参数工具面,watchdog 轮询)。

明确不做(本波边界):``cross_check``/``reject_sample``、多模态。
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_os.kernel.errors import SubtreeCancelled

#: budget watchdog 轮询间隔(秒)——软语义的一部分:间隔内的超限不拦截、不补账
_POLL_INTERVAL = 0.05

#: 帧的活跃形态(批帧在跑);其余形态视为终态,watchdog 停轮询
_ACTIVE_STATUS = ("pending", "running")


def _tokens(usage: dict[str, Any]) -> int:
    """tokens 软口径:五类 token 字段求和(缓存读写同样计费)。"""
    return (
        usage.get("prompt_tokens", 0)
        + usage.get("completion_tokens", 0)
        + usage.get("thinking_tokens", 0)
        + usage.get("cache_read_tokens", 0)
        + usage.get("cache_write_tokens", 0)
    )


def _budget_tripped(budget: dict[str, Any], usage: dict[str, Any]) -> str | None:
    """返回触发的预算项描述(人读,入取消 reason);未触发返回 None。"""
    max_steps = budget.get("max_steps")
    if max_steps is not None and usage.get("steps", 0) > max_steps:
        return f"steps {usage.get('steps', 0)} > max_steps={max_steps}"
    max_tokens = budget.get("max_tokens")
    if max_tokens is not None and _tokens(usage) > max_tokens:
        return f"tokens {_tokens(usage)} > max_tokens={max_tokens}"
    return None


def _settle(
    results: list[dict[str, Any]],
    branches: list[dict[str, Any]],
    *,
    partial: bool,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """分支条目(按分支序)映射为 race_first 输出。

    first_success 幂等结算保证恰一个 ok=True(胜方);其余(败方取消/失败/
    预检折叠)全部落 cancelled。防御:同批多 ok 时只认首个,其余仍归 cancelled。
    """
    winner: dict[str, Any] | None = None
    cancelled: list[dict[str, Any]] = []
    for branch, entry in zip(branches, results):
        if entry.get("ok") and winner is None:
            winner = {
                "skill": branch["skill"],
                "value": entry.get("value"),
                "frame_id": entry.get("frame_id"),
            }
            continue
        cancelled.append(
            {
                "skill": branch["skill"],
                "frame_id": entry.get("frame_id"),
                "error": entry.get("error"),
            }
        )
    return {"winner": winner, "cancelled": cancelled, "partial": partial, "usage": usage}


async def race_first(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """首个成功分支胜出(budget 软闸;语义与形态取舍见模块 docstring)。"""
    branches = args["branches"]
    batch_input: dict[str, Any] = {"branches": branches}
    if args.get("settle_timeout") is not None:
        batch_input["settle_timeout"] = args["settle_timeout"]
    batch_id = await ctx.spawn("common.task.race_batch", batch_input)

    budget = args.get("budget") or {}
    tripped: str | None = None
    ack: list[str] = []
    if budget.get("max_steps") is not None or budget.get("max_tokens") is not None:
        # watchdog:轮询批帧子树 usage;超限即取消批帧(级联覆盖全部在跑分支)
        while True:
            status = await ctx.frame_status(batch_id)
            if status["status"] not in _ACTIVE_STATUS:
                break
            tripped = _budget_tripped(budget, status["usage"])
            if tripped is not None:
                ack = await ctx.cancel(batch_id, f"race_first budget 软超限:{tripped}")
                break
            await asyncio.sleep(_POLL_INTERVAL)

    try:
        results = await ctx.wait(batch_id)
    except SubtreeCancelled:
        # budget 取消落在批帧在跑时:批结果被丢弃,按部分结果返回——
        # cancelled 取批帧子树的取消 ack(分支及其后代帧,逐帧读回技能名)
        cancelled = []
        for fid in ack:
            if fid == batch_id:
                continue
            st = await ctx.frame_status(fid)
            cancelled.append(
                {
                    "skill": st["skill"],
                    "frame_id": fid,
                    "error": {"kind": "cancelled", "message": f"budget 软超限:{tripped}"},
                }
            )
        usage = (await ctx.frame_status(batch_id))["usage"]
        return {
            "winner": None,
            "cancelled": cancelled,
            "partial": tripped is not None,
            "usage": usage,
        }
    usage = (await ctx.frame_status(batch_id))["usage"]
    return _settle(results["results"], branches, partial=tripped is not None, usage=usage)


async def race_batch(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """race_first 的私有批帧:branches 原样交 ctx.parallel(first_success)。"""
    kw: dict[str, Any] = {"mode": "first_success"}
    if args.get("settle_timeout") is not None:
        kw["settle_timeout"] = float(args["settle_timeout"])
    results = await ctx.parallel(args["branches"], **kw)
    return {"results": results}


async def subagent_cancel(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """ctx.cancel 薄适配(§5.2 级联取消):ack 含目标自身,未知帧空表不抛。"""
    ack = await ctx.cancel(args["frame_id"], str(args.get("reason") or ""))
    return {"cancelled": ack}


async def subagent_status(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """ctx.frame_status 薄适配(只读瞬时值,每次重新读帧树,不可缓存)。"""
    return await ctx.frame_status(args["frame_id"])

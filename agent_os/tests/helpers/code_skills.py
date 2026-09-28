"""M2 锚点测试用 code 技能 handlers(dotted path 惰性加载,§6.3)。

签名约定(§6.3):``async def run(input: dict, ctx: LogicContext) -> dict``。
``ctx.invoke`` / ``ctx.call_tool`` 全部回到内核分发路径(白名单、信号、记账一样不少,§9.3)。
"""

from __future__ import annotations

import asyncio
from typing import Any


async def fib_pair(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """编排者(§9.3):确定性控制流 + 按需调 LLM 技能。拼接 fib(a) 与 fib(b)。"""
    a = await ctx.invoke("demo.fib", {"n": input["a"]})
    b = await ctx.invoke("demo.fib", {"n": input["b"]})
    return {"combined": a["seq"] + b["seq"]}


async def double_it(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """经 ctx.call_tool 调 system.python.exec(Logic Kernel 沙箱)计算。"""
    r = await ctx.call_tool("system.python.exec", {"code": f"result = {input['x']} * 2\nprint(result)"})
    assert r["ok"], r
    return {"doubled": r["value"]["result"]}


async def pure_add(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """纯计算(无回调),沙箱可执行。"""
    return {"sum": input["a"] + input["b"]}


async def set_timer_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """经 ctx.call_tool 调 system.timer.set(WS1 timer 工具面/run 收尾接线测试用)。"""
    r = await ctx.call_tool("system.timer.set", dict(input.get("args") or {}))
    assert r["ok"], r
    return {"timer_id": r["value"]["timer_id"]}


async def naughty(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """调用白名单外的子技能(用于权限拒绝测试)。"""
    await ctx.invoke("demo.fib", {"n": 3})
    return {"never": True}


async def bad_output(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """返回不合 outputs schema 的值(用于 code 技能输出校验测试)。"""
    return {"wrong": 1}


async def spawn_pair(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """后台帧编排(§3.4 spawn):父帧不挂起,两个子帧后台跑,wait 读终态。"""
    f1 = await ctx.spawn("demo.fib", {"n": input["a"]})
    f2 = await ctx.spawn("demo.fib", {"n": input["b"]})
    ra = await ctx.wait(f1)
    rb = await ctx.wait(f2)
    return {"combined": ra["seq"] + rb["seq"]}


async def spawn_naughty(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """spawn 白名单外的子技能(用于权限拒绝测试)。"""
    await ctx.spawn("demo.fib", {"n": 2})
    return {"never": True}


#: parallel_pair 的"批后断电"一次性登记:frame_id 跨 checkpoint/resume 稳定,
#: resume 重跑同一帧不再断(对照 tests.helpers.brains 的 _cut_state 模块态先例)
_parallel_cut_frames: set[str] = set()


async def parallel_pair(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """parallel 编排(§3.4 fork/join):两分支 all_settled,结果按分支序返回。

    ``cut_after_batch``(checkpoint/resume 测试用):批结算后"断电"一次——
    code 父帧 FAILED;resume 重跑重发整批(批不做检查点配对,调用方幂等)。
    """
    results = await ctx.parallel([
        {"skill": "demo.fib", "input": {"n": input["a"]}},
        {"skill": "demo.fib", "input": {"n": input["b"]}},
    ])
    if input.get("cut_after_batch") and ctx.frame_id not in _parallel_cut_frames:
        _parallel_cut_frames.add(ctx.frame_id)
        raise RuntimeError("模拟断电(批结算后)")
    return {"results": results}


async def parallel_fail_branch(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """批内故障隔离 / first_success 全败测试:n=0 分支不合 inputs schema 必败。

    ``runtime_bad``:追加一个 test.bad_echo 分支(运行期输出校验连败,帧内错误);
    ``mode``:first_success 全败路径用。
    """
    branches = [{"skill": "demo.fib", "input": {"n": input["n0"]}}]
    for _ in range(int(input.get("bad", 1))):
        branches.append({"skill": "demo.fib", "input": {"n": 0}})
    if input.get("runtime_bad"):
        branches.append({"skill": "test.bad_echo", "input": {}})
    kw: dict[str, Any] = {"mode": input["mode"]} if input.get("mode") else {}
    results = await ctx.parallel(branches, **kw)
    return {"results": results}


async def parallel_chain(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """depends_on 级联测试:分支 1 依赖分支 0;分支 0 失败时分支 1 不启动。"""
    results = await ctx.parallel([
        {"skill": "demo.fib", "input": {"n": input["n0"]}},
        {"skill": "demo.fib", "input": {"n": input["n1"]}, "depends_on": [0]},
    ])
    return {"results": results}


async def parallel_race(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """first_success 竞速:快分支(demo.fib)锁定,慢分支(test.slow_echo)被取消。"""
    results = await ctx.parallel(
        [
            {"skill": "test.slow_echo", "input": {}},
            {"skill": "demo.fib", "input": {"n": 1}},
        ],
        mode="first_success",
    )
    return {"results": results}


async def parallel_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """max_concurrency / concurrency_safe 闸 / run stop 撞批测试:N 个同技能分支扇出。"""
    branches = [{"skill": input["branch_skill"], "input": {}} for _ in range(int(input["n"]))]
    kw: dict[str, Any] = {}
    if input.get("max_concurrency") is not None:
        kw["max_concurrency"] = int(input["max_concurrency"])
    results = await ctx.parallel(branches, **kw)
    return {"results": results}


async def parallel_naughty(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """parallel 白名单外分支(用于权限拒绝测试:预检抛 SkillLoadError)。"""
    await ctx.parallel([{"skill": "demo.fib", "input": {"n": 2}}])
    return {"never": True}


async def parallel_branches(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """按 input["branches"] 原样扇出(升权溯源标记测试用:分支 skill/input 由用例给全)。"""
    results = await ctx.parallel([dict(b) for b in input["branches"]])
    return {"results": results}


async def code_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """concurrency_safe 闸测试用 code 分支:调白名单工具(probe_tool 未声明并发安全)。"""
    r = await ctx.call_tool("test.probe_tool", {})
    assert r["ok"], r
    return {"probed": True}


async def invoke_one(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """程序化调用任意白名单子技能(inline 降级等测试用)。"""
    value = await ctx.invoke(input["skill"], dict(input.get("args") or {}))
    return {"value": value}


async def spawn_one(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """spawn 任意白名单子技能并等终态(inline 与 spawn 正交性测试用)。"""
    fid = await ctx.spawn(input["skill"], dict(input.get("args") or {}))
    return {"frame_id": fid, "value": await ctx.wait(fid)}


async def spawn_or_report(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """spawn 一个子帧并等终态;子树被取消(SubtreeCancelled)时回报而非上抛。

    子树级联取消(§5.2 cancel_frame)测试用:模拟"捕获取消并优雅收尾"的
    code 技能——wait_frame 上抛 SubtreeCancelled 时返回 ``cancelled: True``。
    """
    from agent_os.kernel.errors import SubtreeCancelled

    fid = await ctx.spawn(input["skill"], dict(input.get("args") or {}))
    try:
        value = await ctx.wait(fid)
    except SubtreeCancelled:
        return {"frame_id": fid, "cancelled": True}
    return {"frame_id": fid, "cancelled": False, "value": value}


async def cancel_and_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """ctx.cancel / ctx.frame_status 编排面(W5-WS1)测试用 handler。

    流程:spawn 子帧 → frame_status 轮询到 ``running``(运行形态观察点)→
    cancel 子树 → wait 收 SubtreeCancelled → frame_status 读终态;附带查一次
    未知帧(防御形态)。各观察点随返回值带出供测试断言。
    """
    from agent_os.kernel.errors import SubtreeCancelled

    fid = await ctx.spawn(input["skill"], dict(input.get("args") or {}))
    running: dict[str, Any] = {}
    for _ in range(200):
        status = await ctx.frame_status(fid)
        if status["status"] == "running":
            running = status
            break
        await asyncio.sleep(0.01)
    ack = await ctx.cancel(fid, "code 技能主动取消(W5-WS1 测试)")
    try:
        await ctx.wait(fid)
        cancelled = False
    except SubtreeCancelled:
        cancelled = True
    terminal = await ctx.frame_status(fid)
    unknown = await ctx.frame_status("no-such-frame")
    return {
        "frame_id": fid,
        "running": running,
        "ack": ack,
        "cancelled": cancelled,
        "terminal": terminal,
        "unknown": unknown,
    }


async def sandbox_cancel_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """SANDBOX 档经 syscall 桥调 ctx.cancel / ctx.frame_status(W5-WS1 桥接测试)。

    未知帧寻址:桥两端方法名注册与响应形态(ack 空表 / ``status=None`` 同形字典)
    的端到端证明;真实帧语义由 TRUSTED 档测试覆盖(桥传输层 kind/name 无关)。
    """
    ack = await ctx.cancel("no-such-frame", "sandbox 桥测试")
    status = await ctx.frame_status("no-such-frame")
    return {"ack": ack, "status": status["status"], "usage_keys": sorted(status["usage"].keys())}


async def sandbox_spawn_wait_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """SANDBOX 档经 syscall 桥 spawn+wait(§3.4 桥接测试):取回子帧返回值。"""
    fid = await ctx.spawn(input["skill"], dict(input.get("args") or {}))
    value = await ctx.wait(fid)
    return {"frame_id": fid, "value": value}


async def sandbox_parallel_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """SANDBOX 档经 syscall 桥 parallel(§3.4 桥接测试):批结果按分支序原样带回。"""
    results = await ctx.parallel(input["branches"], **dict(input.get("kw") or {}))
    return {"results": results}


async def std_cancel_probe(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """std/combinators 装配测试驱动(W5-WS2+3):经 std 技能面调 subagent_cancel/status。

    流程:spawn 快分支等 done(读 status 的 done 形态)→ spawn 慢分支,轮询
    subagent_status 到 ``running`` → subagent_cancel 取消 → wait 收
    SubtreeCancelled → 读终态;附带查一次未知帧(防御形态)。观察点随返回值带出。
    """
    from agent_os.kernel.errors import SubtreeCancelled

    fast = await ctx.spawn("test.fast_echo", {})
    await ctx.wait(fast)
    done = await ctx.invoke("common.task.subagent_status", {"frame_id": fast})
    slow = await ctx.spawn("test.slow_echo", {})
    running: dict[str, Any] = {}
    for _ in range(200):
        st = await ctx.invoke("common.task.subagent_status", {"frame_id": slow})
        if st["status"] == "running":
            running = st
            break
        await asyncio.sleep(0.01)
    ack = await ctx.invoke(
        "common.task.subagent_cancel", {"frame_id": slow, "reason": "std 装配测试"}
    )
    try:
        await ctx.wait(slow)
        cancelled = False
    except SubtreeCancelled:
        cancelled = True
    terminal = await ctx.invoke("common.task.subagent_status", {"frame_id": slow})
    unknown = await ctx.invoke("common.task.subagent_status", {"frame_id": "no-such-frame"})
    return {
        "fast_frame": fast,
        "done": done,
        "slow_frame": slow,
        "running": running,
        "ack": ack["cancelled"],
        "cancelled": cancelled,
        "terminal": terminal,
        "unknown": unknown,
    }


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

"""coding_agent 的 code 技能 handlers:fix_loop(确定性修复循环)+ collect_diff(结构化改动清单)。

签名约定(docs/DESIGN.md §6.3):``async def <name>(input: dict, ctx) -> dict``;
供 skills.yaml 经 dotted path ``handlers:<name>`` 惰性加载(运行环境需 PYTHONPATH
含本目录,测试由 tests/conftest.py 钉入)。循环计数收敛在 code 技能里——不信任
LLM 管迭代次数(prompt 技能的 max_steps 只是兜底,不是控制流)。
"""

from __future__ import annotations

from typing import Any

#: fix_loop 的迭代硬上限(manifest limits 只兜步数;轮次语义由 handler 显式持有)
MAX_ITERATIONS = 5

#: 黑板命名空间(与 skills.yaml 中 coding.fix_loop 的 permissions.blackboard 一致)
_BOARD_NS = "coding"


async def fix_loop(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """确定性"实现→验证"循环:每轮先 coding.implement 再 coding.verify,通过即 done。

    ctx.invoke 失败(子帧异常、升权/确认被拒等)会抛异常——捕获并转为 partial
    返回,不让循环崩掉;装配了黑板时把每轮状态写入 ``coding`` 命名空间供调试。
    """
    task = str(input["task"])
    plan_path = str(input["plan_path"])
    command = str(input["test_command"])
    feedback: str | None = None
    changed: list[str] = []
    last_output = ""
    for i in range(1, MAX_ITERATIONS + 1):
        impl_input: dict[str, Any] = {"task": task, "plan_path": plan_path}
        if feedback:
            impl_input["test_feedback"] = feedback
        try:
            impl = await ctx.invoke("coding.implement", impl_input)
            changed = sorted(set(changed) | set(impl.get("changed_files", [])))
            ver = await ctx.invoke("coding.verify", {"command": command})
        except Exception as e:  # noqa: BLE001 — 子帧失败折叠为 partial(循环不崩,§3.2)
            return {
                "status": "partial",
                "changed_files": changed,
                "iterations": i,
                "last_test_output": f"第 {i} 轮子技能调用失败: {type(e).__name__}: {e}",
            }
        if ctx.board is not None:
            await ctx.board.put(
                _BOARD_NS,
                f"fix_loop.iter{i}",
                {"passed": bool(ver.get("passed")), "changed_files": changed},
            )
        last_output = str(ver.get("output_tail", ""))
        if ver.get("passed"):
            return {
                "status": "done",
                "changed_files": changed,
                "iterations": i,
                "last_test_output": last_output,
            }
        feedback = last_output or str(ver.get("summary", "")) or "测试未通过(无输出)"
    return {
        "status": "partial",
        "changed_files": changed,
        "iterations": MAX_ITERATIONS,
        "last_test_output": last_output,
    }


async def collect_diff(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """收集结构化改动清单:``git diff --stat`` + ``git status --porcelain``(一次 shell 调用)。

    git 不可用 / workdir 非 git 仓库 / 执行被拒绝时返回 ``available=False`` 与
    ``note`` 说明,不报错——调用方(coding.agent.run)据此把 review 输入的 diff
    段留空,review 退化为纯文件审查。
    """
    result = await ctx.call_tool(
        "system.shell.exec",
        {"command": "git diff --stat && echo ---STATUS--- && git status --porcelain"},
    )
    if not result.get("ok"):
        error = result.get("error") or {}
        return {
            "available": False,
            "stat": "",
            "status": "",
            "note": f"git 调用未执行: {error.get('kind', 'unknown')}: {error.get('message', '')}",
        }
    value = result.get("value") or {}
    if value.get("exit_code") != 0:
        stderr = str(value.get("stderr", ""))[:200]
        return {
            "available": False,
            "stat": "",
            "status": "",
            "note": f"非 git 仓库或 git 不可用(exit_code={value.get('exit_code')}): {stderr}",
        }
    stdout = str(value.get("stdout", ""))
    stat, _, porcelain = stdout.partition("---STATUS---")
    return {
        "available": True,
        "stat": stat.strip(),
        "status": porcelain.strip(),
        "note": "",
    }

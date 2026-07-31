"""共享 mock "大脑"(MockProvider 应答函数)。

必须保持模块级可引用:CLI/Web 配置以 dotted path
(``tests.helpers.brains:fib_brain``)经 importlib 加载。
"""

from __future__ import annotations

import json

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)


def _final(payload: dict) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _calls(tc: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def fib_brain(req: ChatRequest) -> ChatResponse:
    """模拟一个完美遵循 fib 技能指令的模型。

    决策顺序:无调用 → base case 或 invoke 自己;有子技能结果 → 调 system.python.exec;
    有 system.python.exec 结果 → 给出最终答案。
    """
    n = None
    for m in req.messages:
        if m.role is Role.USER:
            n = json.loads(m.content)["n"]
            break
    assert n is not None, "帧上下文缺少输入消息"

    call_names: dict[str, str] = {}
    results: dict[str, dict] = {}
    for m in req.messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                call_names[tc.id] = tc.name
        elif m.role is Role.TOOL and m.tool_call_id:
            results[m.tool_call_id] = json.loads(m.content)

    fib_calls = [cid for cid, name in call_names.items() if name == "skill.demo.fib"]
    if not fib_calls:
        if n <= 2:
            return _final({"seq": [0] if n == 1 else [0, 1]})
        return _calls(ToolCall(id=f"call-fib-{n}", name="skill.demo.fib", args={"n": n - 1}))

    fib_result = results[fib_calls[-1]]
    assert fib_result["ok"], fib_result
    seq = fib_result["value"]["seq"]

    py_calls = [cid for cid, name in call_names.items() if name == "system.python.exec"]
    if not py_calls:
        code = f"result = {seq[-2]} + {seq[-1]}\nprint(result)"
        return _calls(ToolCall(id=f"call-py-{n}", name="system.python.exec", args={"code": code}))

    total = results[py_calls[-1]]["value"]["result"]
    return _final({"seq": seq + [total]})


def bad_brain(req: ChatRequest) -> ChatResponse:
    """永远返回不合 outputs schema 的答案,用于输出校验连败测试。"""
    return _final({"wrong": 1})


def done_brain(req: ChatRequest) -> ChatResponse:
    """一步到位的最终答案 {"done": true}(inline replay 等最小 run 测试用)。"""
    return _final({"done": True})


class PowerCut(Exception):
    """模拟断电:第 cut_at 次 LLM 调用时直接崩掉。"""


def power_cut_brain(cut_at: int):
    state = {"calls": 0}

    def brain(req: ChatRequest) -> ChatResponse:
        state["calls"] += 1
        if state["calls"] >= cut_at:
            raise PowerCut(f"断电于第 {state['calls']} 次调用")
        return fib_brain(req)

    return brain


def danger_brain(req: ChatRequest) -> ChatResponse:
    """先尝试危险命令(被 ToolGuard veto),再给出最终答案。"""
    if not any(m.role is Role.TOOL for m in req.messages):
        return _calls(ToolCall(id="c1", name="system.shell.exec", args={"command": "rm -rf /"}))
    return _final({"done": True})


def loop_brain(req: ChatRequest) -> ChatResponse:
    """无限循环调用(用于 stop / 循环检测测试)。"""
    return _calls(ToolCall(id="c1", name="system.python.exec", args={"code": "print(1)"}))


_cut_state = {"calls": 0}


def reset_cut_brain() -> None:
    _cut_state["calls"] = 0


def cut_brain(req: ChatRequest) -> ChatResponse:
    """第 6 次调用"断电"(用于 Web resume 测试);其后按 fib_brain 正常应答。

    模块级状态,跨请求存活;每个测试用例前先 ``reset_cut_brain()``。
    """
    _cut_state["calls"] += 1
    if _cut_state["calls"] == 6:
        raise RuntimeError("模拟断电")
    return fib_brain(req)

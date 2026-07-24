"""实例自带的 mock 大脑(无 API key 时也能跑通 fib 全流程)。

真实模型部署时把 agent-os.toml 的 [providers.mock] 换成 [providers.kimi]
或 [providers.anthropic] 即可(本文件只在演示模式使用)。
"""

from __future__ import annotations

import json

from agent_os.api.v1 import ChatRequest, ChatResponse, ChatUsage, Message, Role, ToolCall


def fib_brain(req: ChatRequest) -> ChatResponse:
    """严格按 fib 技能指令行事的确定性"模型":先 invoke 自己,再 python_exec 加法。"""
    n = None
    for m in req.messages:
        if m.role is Role.USER:
            try:
                n = json.loads(m.content)["n"]
                break
            except (ValueError, KeyError):
                continue
    assert n is not None, "帧上下文缺少输入消息"

    call_names: dict[str, str] = {}
    results: dict[str, dict] = {}
    for m in req.messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                call_names[tc.id] = tc.name
        elif m.role is Role.TOOL and m.tool_call_id:
            results[m.tool_call_id] = json.loads(m.content)

    def final(seq: list[int]) -> ChatResponse:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"seq": seq})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    def calls(tc: ToolCall) -> ChatResponse:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )

    fib_calls = [cid for cid, name in call_names.items() if name == "skill__fib"]
    if not fib_calls:
        if n <= 2:
            return final([0] if n == 1 else [0, 1])
        return calls(ToolCall(id=f"call-fib-{n}", name="skill__fib", args={"n": n - 1}))

    fib_result = results[fib_calls[-1]]
    assert fib_result["ok"], fib_result
    seq = fib_result["value"]["seq"]

    py_calls = [cid for cid, name in call_names.items() if name == "python_exec"]
    if not py_calls:
        code = f"result = {seq[-2]} + {seq[-1]}\nprint(result)"
        return calls(ToolCall(id=f"call-py-{n}", name="python_exec", args={"code": code}))

    total = results[py_calls[-1]]["value"]["result"]
    return final(seq + [total])

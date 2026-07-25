"""inline-demo 的确定性 mock 大脑:fib / date_style / report_writer 三技能共用。"""

from __future__ import annotations

import json

from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Role, ToolCall


def _resp(content=None, tool_calls=None, finish="stop"):
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=content or "", tool_calls=tool_calls or []),
        finish_reason=finish,
        usage=ChatUsage(prompt=1, completion=1),
    )


def demo_brain(req):
    user = next((json.loads(m.content) for m in req.messages if m.role is Role.USER
                 and m.content.startswith("{")), {})
    if "n" in user:  # fib:base case 直接答;否则先递归再 python_exec 加法
        n = user["n"]
        calls, results = {}, {}
        for m in req.messages:
            if m.role is Role.ASSISTANT:
                for tc in m.tool_calls:
                    calls[tc.id] = tc.name
            elif m.role is Role.TOOL and m.tool_call_id:
                results[m.tool_call_id] = json.loads(m.content)
        fib_calls = [c for c, nm in calls.items() if nm == "skill__fib"]
        if not fib_calls:
            if n <= 2:
                return _resp(json.dumps({"seq": [0] if n == 1 else [0, 1]}))
            return _resp(tool_calls=[ToolCall(id=f"f{n}", name="skill__fib", args={"n": n - 1})],
                         finish="tool_calls")
        seq = results[fib_calls[-1]]["value"]["seq"]
        py_calls = [c for c, nm in calls.items() if nm == "python_exec"]
        if not py_calls:
            code = f"result = {seq[-2]} + {seq[-1]}\nprint(result)"
            return _resp(tool_calls=[ToolCall(id=f"p{n}", name="python_exec", args={"code": code})],
                         finish="tool_calls")
        return _resp(json.dumps({"seq": seq + [results[py_calls[-1]]["value"]["result"]]}))
    if "text" in user:  # date_style(off 档压帧调用时出现)
        return _resp(json.dumps({"styled": "2026-07-24"}))
    # report_writer:on 档看不到伪工具直接答;off 档先调 date_style 再答
    tools = {t["name"] for t in req.tools}
    called = any(tc.name == "skill__date_style" for m in req.messages
                 if m.role is Role.ASSISTANT for tc in m.tool_calls)
    if not called and "skill__date_style" in tools:
        return _resp(tool_calls=[ToolCall(id="c1", name="skill__date_style", args={"text": "x"})],
                     finish="tool_calls")
    return _resp(json.dumps({"report": "ok"}))

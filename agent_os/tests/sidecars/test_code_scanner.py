"""CodeScanner 锚点测试(DESIGN.md §5.4/§9.5)。

固定约定:

- ``CodeScanner(patterns=None)``:SYNC,``pre:logic.exec``,对源码做静态模式扫描
  (默认危险集:``import os``/``os.system``/``ctypes``/``subprocess``/``socket``),
  命中 → Veto;system.python.exec 工具尊重 pre:logic.exec 的否决(veto → kind=vetoed,不执行)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

from agent_os.api.v1 import (
    POST_LOGIC_EXEC,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    Signal,
    ToolCall,
    ToolPolicy,
)
from agent_os.sidecars import CodeScanner
from tests.helpers.kernels import assemble

LOOPER_YAML = """
skills:
  - name: test.looper
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [system.python.exec], skills: [] }
    model: { prefer: ["mock/loop"] }
    limits: { max_steps: 10 }
    prompt: 测试用 test.looper 技能。
"""


def _scanner_kernel(tmp_path, brain, *sidecars):
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(LOOPER_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/loop",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(config, brain, tmp_path / "skills.yaml", sidecars=sidecars)


def dangerous_brain(req: ChatRequest) -> ChatResponse:
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="system.python.exec",
                                     args={"code": "import os\nos.system('echo pwned')"})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def test_code_scanner_vetoes_dangerous_code(tmp_path):
    """pre:logic.exec 命中危险模式 → veto:不执行,理由回写(§9.5)。"""
    seen: list[Signal] = []
    kernel = _scanner_kernel(tmp_path, dangerous_brain, CodeScanner())

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    result = asyncio.run(kernel.run("test.looper", {}))
    assert result == {"done": True}

    mock = kernel.providers.providers["mock"]
    tool_msgs = [
        json.loads(m.content)
        for req in mock.recorded
        for m in req.messages
        if m.role is Role.TOOL
    ]
    assert tool_msgs and tool_msgs[0]["ok"] is False
    assert tool_msgs[0]["error"]["kind"] == "vetoed"
    # 未真正执行:没有 post:logic.exec(执行)信号
    assert not any(s.name == POST_LOGIC_EXEC for s in seen)


def test_code_scanner_allows_clean_code(tmp_path):
    """干净代码正常放行执行(对照组)。"""
    def clean_brain(req: ChatRequest) -> ChatResponse:
        if not any(m.role is Role.TOOL for m in req.messages):
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[ToolCall(id="c1", name="system.python.exec", args={"code": "print(1+1)"})],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    kernel = _scanner_kernel(tmp_path, clean_brain, CodeScanner())
    result = asyncio.run(kernel.run("test.looper", {}))
    assert result == {"done": True}
    mock = kernel.providers.providers["mock"]
    tool_msgs = [
        json.loads(m.content)
        for req in mock.recorded
        for m in req.messages
        if m.role is Role.TOOL
    ]
    assert tool_msgs and tool_msgs[0]["ok"] is True

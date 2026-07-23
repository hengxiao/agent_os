"""M5c 锚点测试:流式看门狗 + fs_edit + CodeScanner + provider fallback(DESIGN.md §4.2、§5.4、§8.3、§9.5)。

固定约定:

- ``ProviderManager.stream(req)``:路由后经 provider.stream 消费;**idle watchdog**——
  超过 ``stream_idle_timeout`` 秒无新 chunk → 杀流并抛 ``ProviderError(UNAVAILABLE, retryable=True)``;
  重试语义与 chat 一致(仅 retryable、指数退避、max_attempts);
- ``MockProvider(stream_scripts=[[(text, delay), ...], ...])``:每次 stream 尝试按序取一段脚本;
- ``fs_edit``(WRITE,内置):old_string→new_string **唯一匹配**才替换;
  未找到 / 多处匹配 → ``ok=False, kind=INVALID_ARGS``;
- ``CodeScanner(patterns=None)``:SYNC,``pre:logic.exec``,对源码做静态模式扫描
  (默认危险集:``import os``/``os.system``/``ctypes``/``subprocess``/``socket``),
  命中 → Veto;python_exec 工具尊重 pre:logic.exec 的否决(veto → kind=vetoed,不执行);
- ``ProviderManager(fallbacks={model: [backup_model, ...]})``:重试耗尽后按链切换,
  请求归一化(只保留契约层字段,剥离前一家专有内容)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from agent_os.api.v1 import (
    POST_LOGIC_EXEC,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    ProviderCaps,
    ProviderError,
    ProviderErrorKind,
    Role,
    RunConfig,
    Signal,
    SkillFrame,
    ToolCall,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.sidecars import CodeScanner
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry, ToolDispatchContext

# ---------------------------------------------------------------------------
# 流式 idle watchdog
# ---------------------------------------------------------------------------


def test_stream_idle_watchdog_retries_and_recovers():
    """第一次尝试在第 2 个 chunk 前停滞 0.3s(> 0.1s 看门狗)→ 杀流重试 → 第二次顺畅。"""
    scripts = [
        [("a", 0.0), ("b", 0.3), ("c", 0.0)],
        [("a", 0.0), ("b", 0.0), ("c", 0.0)],
    ]
    mock = MockProvider(stream_scripts=scripts)
    mgr = ProviderManager([mock], max_attempts=2, backoff_base=0.01, stream_idle_timeout=0.1)

    async def main():
        return [c async for c in mgr.stream(ChatRequest(model="mock/x", messages=[]))]

    chunks = asyncio.run(main())
    text = "".join(c.delta.content for c in chunks if c.delta and c.delta.content)
    assert text == "abc"
    assert mock.stream_attempts == 2


def test_stream_idle_watchdog_gives_up_after_max_attempts():
    scripts = [[("a", 0.5)]] * 3
    mock = MockProvider(stream_scripts=scripts)
    mgr = ProviderManager([mock], max_attempts=2, backoff_base=0.01, stream_idle_timeout=0.1)

    async def main():
        return [c async for c in mgr.stream(ChatRequest(model="mock/x", messages=[]))]

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(main())
    assert exc_info.value.kind is ProviderErrorKind.UNAVAILABLE
    assert mock.stream_attempts == 2


# ---------------------------------------------------------------------------
# fs_edit
# ---------------------------------------------------------------------------


def _dispatch_ctx():
    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=["fs_read", "fs_write", "fs_edit"],
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


def test_fs_edit_unique_match():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        await reg.dispatch(
            ToolCall(id="w", name="fs_write", args={"path": "a.txt", "content": "hello world"}), ctx
        )
        ed = await reg.dispatch(
            ToolCall(id="e", name="fs_edit",
                     args={"path": "a.txt", "old_string": "world", "new_string": "agent_os"}),
            ctx,
        )
        assert ed.ok, ed.error
        rd = await reg.dispatch(ToolCall(id="r", name="fs_read", args={"path": "a.txt"}), ctx)
        return rd.value

    content = asyncio.run(main())
    assert "agent_os" in content and "world" not in content


def test_fs_edit_missing_and_non_unique():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        await reg.dispatch(
            ToolCall(id="w", name="fs_write", args={"path": "b.txt", "content": "foo foo"}), ctx
        )
        missing = await reg.dispatch(
            ToolCall(id="e1", name="fs_edit", args={"path": "b.txt", "old_string": "bar", "new_string": "x"}),
            ctx,
        )
        non_unique = await reg.dispatch(
            ToolCall(id="e2", name="fs_edit", args={"path": "b.txt", "old_string": "foo", "new_string": "x"}),
            ctx,
        )
        return missing, non_unique

    missing, non_unique = asyncio.run(main())
    assert not missing.ok and missing.error.kind is ToolErrorKind.INVALID_ARGS
    assert not non_unique.ok and non_unique.error.kind is ToolErrorKind.INVALID_ARGS
    assert "唯一" in non_unique.error.message or "多处" in non_unique.error.message


# ---------------------------------------------------------------------------
# CodeScanner
# ---------------------------------------------------------------------------

LOOPER_YAML = """
skills:
  - name: looper
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [python_exec], skills: [] }
    model: { prefer: ["mock/loop"] }
    limits: { max_steps: 10 }
    prompt: 测试用 looper 技能。
"""


def _scanner_kernel(tmp_path, brain, *sidecars):
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(LOOPER_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/loop",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    tools = LocalPythonToolRegistry()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    return (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(tmp_path / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .sidecars(*sidecars)
        .build()
    )


def dangerous_brain(req: ChatRequest) -> ChatResponse:
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="python_exec",
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
    result = asyncio.run(kernel.run("looper", {}))
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
                    tool_calls=[ToolCall(id="c1", name="python_exec", args={"code": "print(1+1)"})],
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
    result = asyncio.run(kernel.run("looper", {}))
    assert result == {"done": True}
    mock = kernel.providers.providers["mock"]
    tool_msgs = [
        json.loads(m.content)
        for req in mock.recorded
        for m in req.messages
        if m.role is Role.TOOL
    ]
    assert tool_msgs and tool_msgs[0]["ok"] is True


# ---------------------------------------------------------------------------
# Provider fallback 链
# ---------------------------------------------------------------------------


class DownProvider:
    """永远 UNAVAILABLE 的 provider。"""

    def __init__(self, name: str = "down") -> None:
        self.name = name
        self.attempts = 0

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(supports_tools=True)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.attempts += 1
        raise ProviderError(ProviderErrorKind.UNAVAILABLE, "down", retryable=True)

    async def stream(self, req: ChatRequest):
        raise NotImplementedError


def test_provider_fallback_chain():
    """主模型重试耗尽 → 按 fallback 链切到备选 provider(§4.2)。"""
    down = DownProvider()
    backup = MockProvider([
        ChatResponse(message=Message(role=Role.ASSISTANT, content="from-backup"), finish_reason="stop")
    ])
    mgr = ProviderManager(
        [down, backup],
        max_attempts=2,
        backoff_base=0.01,
        fallbacks={"down/main": ["mock/backup"]},
    )
    resp = asyncio.run(mgr.chat(ChatRequest(model="down/main", messages=[Message(role=Role.USER, content="hi")])))
    assert resp.message.content == "from-backup"
    assert down.attempts == 2
    assert len(backup.recorded) == 1

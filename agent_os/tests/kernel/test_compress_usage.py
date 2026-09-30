"""压缩链 LLM 用量入账集成测试(docs/DESIGN.md §7.2 + §3.1 步骤 7;WS2 装配接线)。

固定约定:

- SummarizeCompressor 每次 LLM 摘要落 ``frame.context.working["_compress_llm_usage"]``
  (``{"model", "usage"}``);runner 在 ``context.maintain`` 后排干
  (:meth:`Kernel._drain_compress_usage`):token 六项累加进帧/run 两级
  (**steps 不加**——压缩不是主循环步),并补发 ``post:llm.response``
  (``source="compress"``,与主循环发送点同形状 + additive);
- 摘要调用走装配进 ContextManager 的同一 ProviderManager:mock 大脑按 SYSTEM
  指令文本区分"摘要请求"与"主循环请求",两类都要供。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

from agent_os.api.v1 import (
    POST_COMPRESS,
    POST_LLM_RESPONSE,
    RUN_STARTED,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolCall,
    ToolPolicy,
)
from tests.helpers.kernels import assemble, record_all

CHATTY_YAML = """
skills:
  - name: chatty
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions: { tools: [system.python.exec], skills: [] }
    context_policy: { max_tokens: 300, compress: summarize }
    limits: { max_steps: 12, timeout: 60 }
    model: { prefer: ["mock/chatty"] }
    prompt: "闲聊任务:按需调工具,最后输出 answer 字段。"
"""

#: 主循环与摘要调用用两档明显不同的 usage,入账断言才能分辨"含不含摘要调用"
MAIN_USAGE = ChatUsage(prompt=50, completion=20, cost=0.01)
SUMMARIZE_USAGE = ChatUsage(prompt=100, completion=10, cost=0.001)


def _brain(state: dict[str, int]):
    """双路 mock 大脑:摘要请求(SYSTEM 含压缩器指令)回笔记;主循环前 3 步
    回 ``system.python.exec`` 大输出工具调用(撑爆 300 token 的 cap 触发压缩),
    第 4 步回最终答案。"""

    def brain(req: ChatRequest) -> ChatResponse:
        first = (req.messages[0].content or "") if req.messages else ""
        if "上下文压缩器" in first:
            state["summarize_calls"] += 1
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content="压缩笔记:早前工具往返摘要"),
                finish_reason="stop",
                usage=SUMMARIZE_USAGE,
            )
        state["main_calls"] += 1
        if state["main_calls"] >= 4:
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "done"})),
                finish_reason="stop",
                usage=MAIN_USAGE,
            )
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"c{state['main_calls']}",
                        name="system.python.exec",
                        args={"code": "print('x' * 1200)"},
                    )
                ],
            ),
            finish_reason="tool_calls",
            usage=MAIN_USAGE,
        )

    return brain


def test_compress_usage_accounted_without_steps(tmp_path):
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(textwrap.dedent(CHATTY_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/chatty",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )
    state = {"main_calls": 0, "summarize_calls": 0}
    kernel = assemble(config, _brain(state), str(skills_yaml))
    seen = record_all(kernel)

    # 帧引用捕获:run 结束后帧已弹栈,经 maintain 探针留引用供帧级断言
    frames = []
    orig_maintain = kernel.context.maintain

    async def spy_maintain(frame):
        frames.append(frame)
        await orig_maintain(frame)

    kernel.context.maintain = spy_maintain

    result = asyncio.run(kernel.run("chatty", {}))
    assert result == {"answer": "done"}

    # summarize 真实走了 LLM 路径(非 truncate 退化):strategy 遥测与 marker 佐证
    post_compress = [s for s in seen if s.name == POST_COMPRESS]
    assert post_compress, "小 cap + 大工具输出应触发压缩"
    assert all(s.payload["strategy"] == "summarize" for s in post_compress)
    assert any(s.payload["marker"] == "[COMPRESSED]" for s in post_compress)

    responses = [s for s in seen if s.name == POST_LLM_RESPONSE]
    main = [s for s in responses if s.payload.get("source") != "compress"]
    compress = [s for s in responses if s.payload.get("source") == "compress"]
    assert len(main) == state["main_calls"] == 4
    assert len(compress) == state["summarize_calls"] >= 1
    # 压缩条目与主循环同形状 + additive:model/usage 键齐全,多 source;
    # usage 键集为超集断言(additive 扩展先例:cache/thinking 三维,runner._usage_payload)
    for s in compress:
        assert s.payload["model"] == "mock/chatty"
        assert {"prompt", "completion", "cost"} <= set(s.payload["usage"])
        assert s.payload["usage"]["prompt"] == SUMMARIZE_USAGE.prompt

    run_id = next(s for s in seen if s.name == RUN_STARTED).run_id
    run_usage = kernel._runs[run_id].state.usage
    # steps 没因摘要多加:run 步数 == 主循环调用数
    assert run_usage.steps == state["main_calls"]
    # prompt_tokens 含摘要调用:全部 post:llm.response 的 usage 合计与 run 记账一致
    billed = sum(s.payload["usage"]["prompt"] for s in responses)
    assert run_usage.prompt_tokens == billed
    assert run_usage.prompt_tokens >= (
        state["main_calls"] * MAIN_USAGE.prompt + SUMMARIZE_USAGE.prompt
    )
    # 帧级同样入账(根帧即唯一帧);working 暂存键已被排干
    frame = frames[0]
    assert frame.usage.steps == state["main_calls"]
    assert frame.usage.prompt_tokens == billed
    assert "_compress_llm_usage" not in frame.context.working

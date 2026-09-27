"""子树记账读视图锚点测试(WS2;Kernel.subtree_usage / RunControl.get_subtree_usage)。

固定约定:

- ``Kernel.subtree_usage(frame_id)`` 沿 WS1 ``_collect_subtree`` 收集目标帧及全部
  后代(含目标自身),九字段 Usage 求和:steps/tokens/cost 为计费口径求和;
  ttft_ms/total_ms 同为累计求和(资源占用语义而非墙钟——WS2 起流式路径写入
  时间字段;本夹具 Mock 未配 stream_scripts 自动回落 chat,和仍为 0);
  只读,不改 ``account()`` 写入路径;
- 对账锚:根帧 subtree_usage == run.usage(单 run 全部帧都在根子树内);
- 未知 frame_id 返回零值 Usage(防御式读视图,与 cancel_subtree 同旨,不崩调用方);
- ``RunControl.get_subtree_usage`` 是同语义的 sidecar 通道委托。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolCall,
    ToolPolicy,
    Usage,
)
from agent_os.kernel.control import RunControlImpl
from agent_os.providers.mock import MockProvider
from tests.helpers.kernels import assemble

CHAIN_YAML = """
skills:
  - name: test.chain
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { k: { type: integer, minimum: 1 } }
      required: [k]
    outputs:
      type: object
      properties: { k: { type: integer } }
      required: [k]
    permissions: { tools: [], skills: [test.chain] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10 }
    prompt: CHAIN
"""


def _chain_brain(req: ChatRequest) -> ChatResponse:
    """k>1 且尚无工具结果 → 调 skill.test.chain(k-1);否则给最终答案(每步 1+1 token)。"""
    k = None
    for m in req.messages:
        if m.role is Role.USER:
            k = json.loads(m.content)["k"]
            break
    tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
    if k and k > 1 and not tool_msgs:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(id=f"chain-{k}", name="skill.test.chain", args={"k": k - 1})
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"k": k})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _chain_kernel(tmp_path):
    """三级递归链(k=3 → 2 → 1)的最小内核(夹具同 test_run_control 的链式技能)。"""
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(CHAIN_YAML), encoding="utf-8")
    return assemble(config, MockProvider(_chain_brain), p)


def _frames_by_depth(kernel):
    """run 结束后的帧表:(根帧, {depth → 帧});链式技能每层恰好一帧。"""
    frames = kernel.stack.tree()
    root = next(f for f in frames if f.parent_id is None)
    return root, {f.depth: f for f in frames}


def test_subtree_usage_sums_descendants(tmp_path):
    """子树求和 = 目标帧及全部后代 usage 之和;中间帧子树不含根帧自身部分。"""
    kernel = _chain_kernel(tmp_path)
    result = asyncio.run(kernel.run("test.chain", {"k": 3}))
    assert result == {"k": 3}

    root, by_depth = _frames_by_depth(kernel)
    mid, leaf = by_depth[2], by_depth[3]
    # 帧各自记账:root/mid 各 2 步(发起调用 + 收尾),leaf 1 步;每步 1+1 token
    assert (root.usage.steps, mid.usage.steps, leaf.usage.steps) == (2, 2, 1)

    leaf_sub = kernel.subtree_usage(leaf.frame_id)
    assert leaf_sub.steps == 1 and leaf_sub.prompt_tokens == 1
    mid_sub = kernel.subtree_usage(mid.frame_id)
    assert mid_sub.steps == 3  # mid 2 + leaf 1
    assert mid_sub.prompt_tokens == 3 and mid_sub.completion_tokens == 3
    # 九字段齐备:WS2 起流式路径写时间字段,但本夹具链脑无 stream_scripts,
    # caps 不支持流式 → 自动回落 chat(双保险),时间字段累计仍为 0;
    # 链脑不报 cache/cost(流式帧的 ttft/total 写入锚点见 test_streaming.py)
    assert mid_sub.cache_read_tokens == 0 and mid_sub.cost == 0.0
    assert mid_sub.ttft_ms == 0 and mid_sub.total_ms == 0
    # 根子树 = 三帧逐字段之和(与 run 总量对账见下一用例)
    root_sub = kernel.subtree_usage(root.frame_id)
    assert root_sub.steps == root.usage.steps + mid.usage.steps + leaf.usage.steps


def test_root_subtree_equals_run_usage(tmp_path):
    """对账锚:根帧 subtree_usage == run.usage(全部帧都在根子树内)。"""
    kernel = _chain_kernel(tmp_path)
    asyncio.run(kernel.run("test.chain", {"k": 3}))

    root, _ = _frames_by_depth(kernel)
    run_usage = kernel._runs[root.run_id].state.usage
    assert run_usage.steps == 5
    assert kernel.subtree_usage(root.frame_id) == run_usage


def test_subtree_usage_unknown_frame_returns_zero(tmp_path):
    """未知 frame_id:零值 Usage(防御式读视图,不崩调用方)。"""
    kernel = _chain_kernel(tmp_path)
    asyncio.run(kernel.run("test.chain", {"k": 1}))
    assert kernel.subtree_usage("no-such-frame") == Usage()


def test_run_control_get_subtree_usage_delegates(tmp_path):
    """RunControl 通道与内核直读同结果(sidecar pull 语义,additive)。"""
    kernel = _chain_kernel(tmp_path)
    asyncio.run(kernel.run("test.chain", {"k": 3}))

    _, by_depth = _frames_by_depth(kernel)
    ctl = RunControlImpl(kernel)
    via_ctl = asyncio.run(ctl.get_subtree_usage(by_depth[2].frame_id))
    assert via_ctl == kernel.subtree_usage(by_depth[2].frame_id)
    assert via_ctl.steps == 3
    assert asyncio.run(ctl.get_subtree_usage("no-such-frame")) == Usage()

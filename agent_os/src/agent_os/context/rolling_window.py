"""RollingWindowCompressor(DESIGN.md §7.6;M3)。

保留 pinned + 最近若干原子组,超目标即从最旧整组驱逐;atomic_groups 把
assistant(带 tool_calls)与其全部 tool result 绑成原子组——§7.4 不变量 2 由此
结构性保证。纯函数、无 LLM/blob 依赖,是 hypothesis 不变量测试的载体。

定位警告(§7.6):裸 rolling window 是已知循环诱因,只是 hierarchical 链的中间层
基座,链尾必须有 summarize 或 spill 承接——不得读作"推荐做法"。
"""

from __future__ import annotations

from agent_os.api.v1 import CompressionReport, FrameContext, KernelServices, Message


class RollingWindowCompressor:
    """``agent_os.api.v1.Compressor`` 协议实现(M3)。"""

    name: str = "rolling_window"

    def atomic_groups(self, messages: list[Message]) -> list[list[Message]]:
        """assistant(带 tool_calls)与其全部 tool result 绑成原子组(§7.6 步骤 1)。"""
        raise NotImplementedError("M3")

    async def compress(
        self, ctx: FrameContext, target_tokens: int, svc: KernelServices
    ) -> CompressionReport:
        raise NotImplementedError("M3")

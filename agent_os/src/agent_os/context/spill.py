"""SpillCompressor(docs/DESIGN.md §7.2 spill;hierarchical 链首,代价最低)。

超长 TOOL 输出原地移入 blob store,上下文只留冻结替换串(``[SPILLED]`` 标记行
+ head/tail 片段 + 取回提示);消息本身不删,配对结构不动(§7.4 不变量 2)。

替换串**一经生成永久冻结**(§7.2/§7.4 不变量 5 前缀稳定性):本实现为确定性
模板拼装——无时间戳、无随机量,同输入必得同串;``[SPILLED]`` 幂等标记保证
重复压缩不二次处理(§7.5)。
"""

from __future__ import annotations

from agent_os.api.v1 import (
    CompressionReport,
    FrameContext,
    KernelServices,
    Role,
)

#: 幂等标记(§7.5):已 spill 的消息含此标记,责任链/重复压缩不再处理
MARKER = "[SPILLED]"

#: 冻结替换串模板(§7.2:preview = 头部 + 尾部 + 省略通知——字节数、ref、取回方式)。
#: 确定性生成(无时间戳/随机量),同输入必得同串,保证 build 前缀跨步逐字节稳定。
_REPLACEMENT_TEMPLATE = """\
[SPILLED] 工具输出过大,已移入 blob store(原始 {n_bytes} 字节;ref: {ref})
---- 开头 ----
{head}
---- 结尾 ----
{tail}
---- 取回 ----
全文可用 blob_get 工具按 offset/limit 分页取回:ref="{ref}",共 {n_bytes} 字节。"""


def _replacement(ref: str, original: str, head_chars: int, tail_chars: int) -> str:
    """生成冻结替换串(确定性;``n_bytes`` 按 utf-8 字节数计,与 blob 写入口径一致)。"""
    return _REPLACEMENT_TEMPLATE.format(
        n_bytes=len(original.encode("utf-8")),
        ref=ref,
        head=original[:head_chars],
        tail=original[-tail_chars:] if tail_chars > 0 else "",
    )


class SpillCompressor:
    """``agent_os.api.v1.Compressor`` 协议实现(§7.2 spill;hierarchical 链首)。"""

    name: str = "spill"

    def __init__(
        self, threshold_chars: int = 4000, head_chars: int = 500, tail_chars: int = 500
    ) -> None:
        self._threshold_chars = threshold_chars
        self._head_chars = head_chars
        self._tail_chars = tail_chars

    async def compress(
        self, ctx: FrameContext, target_tokens: int, svc: KernelServices
    ) -> CompressionReport:
        """把超长非 pinned TOOL 消息原地改写为冻结替换串(不删消息)。

        - ``svc.blob`` 缺失或 ``run_id`` 为空 → 原样返回(零值 report),不抛错;
        - 只处理 ``role == TOOL``、长度超阈值、未 pinned、未含 ``[SPILLED]`` 的消息;
        - 无命中时 ``marker=""``(未压缩不带标记,供责任链聚合)。
        ``target_tokens`` 不参与判定:spill 按长度阈值驱动,达标短路由责任链负责。
        """
        estimator = svc.estimator
        before = estimator.estimate(ctx.messages)

        blob = getattr(svc, "blob", None)
        run_id = getattr(svc, "run_id", "")
        if blob is None or not run_id:
            return CompressionReport(before_tokens=before, after_tokens=before)

        pinned = set(ctx.pinned)
        hit = False
        for m in ctx.messages:
            if (
                m.role is Role.TOOL
                and len(m.content or "") > self._threshold_chars
                and m.meta.get("id") not in pinned
                and MARKER not in m.content
            ):
                ref = await blob.put(m.content.encode("utf-8"), run_id)
                m.content = _replacement(ref, m.content, self._head_chars, self._tail_chars)
                hit = True

        after = estimator.estimate(ctx.messages)
        return CompressionReport(
            evicted=0,
            before_tokens=before,
            after_tokens=after,
            cache_invalidation_estimate=before - after,
            marker=MARKER if hit else "",
        )

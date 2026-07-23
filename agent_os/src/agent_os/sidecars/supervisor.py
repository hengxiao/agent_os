"""SidecarSupervisor(DESIGN.md §5.3;M4)。

sidecar 统一托管:注册、心跳、重启(仅 ASYNC)、关停。
监督语义:SYNC 在关键路径、独立超时(默认 2s)、自身异常默认 fail-closed(视为 Veto,
可配置 fail-open);ASYNC 在监督任务里跑,异常只记日志,永远不允许拖垮 run。
引入 LLM 驱动的 sidecar 时:审批模型应与执行模型不同家族、能力相近;连续否决触发
rejection circuit breaker 回退人工。
"""

from __future__ import annotations

from agent_os.api.v1 import RunControl, Sidecar, Signal, Verdict


class SidecarSupervisor:
    """sidecar 生命周期与裁决编排(M4)。"""

    def __init__(self, sidecars: list[Sidecar] | None = None, *, sync_timeout: float = 2.0, fail_open: bool = False) -> None:
        self.sidecars: list[Sidecar] = list(sidecars or [])
        self.sync_timeout = sync_timeout
        self.fail_open = fail_open

    def register(self, sidecar: Sidecar) -> None:
        raise NotImplementedError("M4")

    async def pre_step(self, frame: object) -> Verdict:
        """每步同步检查点(§3.1 步骤 1):可否决 / 注入 / 暂停 / 强停。"""
        raise NotImplementedError("M4")

    async def emit(self, sig: Signal, ctl: RunControl) -> Verdict:
        """按订阅分发;SYNC 按 priority 确定性裁决,ASYNC fire-and-forget(§5.3)。"""
        raise NotImplementedError("M4")

    async def shutdown(self) -> None:
        raise NotImplementedError("M4")

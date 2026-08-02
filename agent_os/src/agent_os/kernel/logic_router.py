"""Logic Kernel 路由(docs/DESIGN.md §9.2;M2):按 TrustLevel 索引后端并为 code 技能帧选路。

选路规则(§9.2):``manifest.logic.mode == "sandbox"`` 或
``RunConfig.logic_policy.force_sandbox`` → SANDBOX;否则 TRUSTED(code 技能默认可信)。
动态代码不经本路由——``system.python.exec`` 工具直接持有 SANDBOX 实例(§9.4,强制无配置项)。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import RunConfig, SkillManifest, TrustLevel
from agent_os.kernel.errors import ToolDispatchError


class LogicKernelRouter:
    """按 ``trust`` 自报值索引的 Logic Kernel 表;KernelBuilder 装配到 ``kernel.logic``。"""

    def __init__(self, kernels: list[Any]) -> None:
        self._by_trust: dict[TrustLevel, Any] = {k.trust: k for k in kernels}

    def route(self, manifest: SkillManifest, config: RunConfig) -> Any:
        """按 §9.2 规则选后端;目标等级未装配属装配错误 → ToolDispatchError。"""
        mode = (manifest.logic or {}).get("mode")
        trust = (
            TrustLevel.SANDBOX
            if mode == "sandbox" or config.logic_policy.force_sandbox
            else TrustLevel.TRUSTED
        )
        kernel = self._by_trust.get(trust)
        if kernel is None:
            raise ToolDispatchError(f"未装配 trust={trust.value} 的 Logic Kernel 后端(§9.2)")
        return kernel

    def route_sandbox(self) -> Any | None:
        """直取 SANDBOX 后端(编排脚本与动态代码强制沙箱,§9.2);未装配返回 None。"""
        return self._by_trust.get(TrustLevel.SANDBOX)

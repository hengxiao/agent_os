"""KernelLogicContext(DESIGN.md §9.3;M2):TRUSTED 模式注入 code 技能的 LogicContext。

``invoke`` / ``call_tool`` 全部回到内核分发路径(runner ``_dispatch_call``):
白名单、信号、记账与 prompt 技能的 ``skill__*``/工具调用完全一致。差别只在形态:
``invoke`` 直接返回子帧结果值(失败抛 AgentOSError 子类),``call_tool`` 返回
``{"ok", "value", "error"}`` 字典(与工具结果消息同构,权限拒绝折叠为字典不抛)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agent_os.api.v1 import SkillFrame, SkillManifest, ToolCall
from agent_os.kernel.errors import SkillLoadError
from agent_os.tools.blob import InMemoryBlobStore


class KernelLogicContext:
    """``agent_os.api.v1.LogicContext`` 协议实现(仅 TRUSTED 模式注入,§9.3 编排者能力面)。"""

    def __init__(self, kernel: Any, frame: SkillFrame, manifest: SkillManifest) -> None:
        self._kernel = kernel
        self._frame = frame
        self._manifest = manifest
        self.frame_id: str = frame.frame_id
        # v1:帧级独立 blob 实例;跨子系统共享(run 级存储)待 blob 层统一(M3)
        self.blob: Any = InMemoryBlobStore()
        self.log: logging.Logger = logging.getLogger(f"agent_os.logic.{manifest.name}")

    async def invoke(self, skill: str, input: dict[str, Any]) -> Any:
        """调子技能 → 压栈(§9.3);白名单外为硬拒绝,直接抛 SkillLoadError。"""
        if skill not in self._manifest.permissions.skills:
            raise SkillLoadError(
                f"子技能 {skill} 不在技能 {self._manifest.name} 的 skills 白名单"
            )
        payload = await self._kernel._dispatch_call(
            ToolCall(id=uuid.uuid4().hex, name=f"skill__{skill}", args=dict(input)),
            self._frame,
            self._manifest,
        )
        if not payload["ok"]:
            error = payload["error"] or {}
            raise SkillLoadError(error.get("message") or f"子技能 {skill} 失败")
        return payload["value"]

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """调工具 → 走 Tool Registry(§9.3);白名单外折叠为 PERMISSION_DENIED 字典。"""
        return await self._kernel._dispatch_call(
            ToolCall(id=uuid.uuid4().hex, name=tool, args=dict(args)),
            self._frame,
            self._manifest,
        )

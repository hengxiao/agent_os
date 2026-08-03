"""KernelLogicContext(docs/DESIGN.md §9.3;M2):TRUSTED 模式注入 code 技能的 LogicContext。

``invoke`` / ``call_tool`` 全部回到内核分发路径(runner ``_dispatch_call``):
白名单、信号、记账与 prompt 技能的 ``skill.*``/工具调用完全一致。差别只在形态:
``invoke`` 直接返回子帧结果值(失败抛 AgentOSError 子类),``call_tool`` 返回
``{"ok", "value", "error"}`` 字典(与工具结果消息同构,权限拒绝折叠为字典不抛)。
``spawn`` / ``wait`` 为 §3.4 后台帧原语(委托 kernel.spawn_frame/wait_frame);
``board`` 为黑板命名空间代理(§12):按 manifest.permissions.blackboard 白名单
逐次仲裁后透传 kernel.blackboard,无黑板时为 None。
``chat``(§W4-3 扩展)直连 ProviderManager:code 技能自驾驶多轮对话用
(检索→自评→精化这类协议,帧循环的"无 tool_calls 即终答"判定走不通);
模型解析与 ContextManager.build 同口径,usage 记账与帧循环同闸。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    Envelope,
    Message,
    SkillFrame,
    SkillManifest,
    ToolCall,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.tools.blob import InMemoryBlobStore


class _BoardProxy:
    """黑板命名空间代理(§12 内核中介):ns 白名单逐次仲裁,命中才透传。

    消息没有命名空间字段,故 ``publish``/``subscribe`` 以首个参数声明本次
    通信所属命名空间,与 KV 读写走同一套白名单仲裁。
    """

    def __init__(self, board: Any, allowed: list[str], owner: str) -> None:
        self._board = board
        self._allowed = allowed
        self._owner = owner

    def _check(self, ns: str) -> None:
        if ns not in self._allowed:
            raise SkillLoadError(
                f"黑板命名空间 {ns} 不在技能 {self._owner} 的 blackboard 白名单"
            )

    async def put(self, ns: str, key: str, value: Any, cas_version: int | None = None) -> int:
        self._check(ns)
        return await self._board.put(ns, key, value, cas_version)

    async def get(self, ns: str, key: str) -> tuple[Any, int]:
        self._check(ns)
        return await self._board.get(ns, key)

    async def publish(self, ns: str, env: Envelope) -> None:
        self._check(ns)
        await self._board.publish(env)

    def subscribe(self, ns: str, frame_id: str, pattern: str) -> AsyncIterator[Envelope]:
        self._check(ns)
        return self._board.subscribe(frame_id, pattern)


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
        self.board: Any = (
            _BoardProxy(kernel.blackboard, manifest.permissions.blackboard, manifest.name)
            if kernel.blackboard is not None
            else None
        )

    async def invoke(self, skill: str, input: dict[str, Any]) -> Any:
        """调子技能 → 压栈(§9.3);白名单外为硬拒绝,直接抛 SkillLoadError。"""
        if skill not in self._manifest.permissions.skills:
            raise SkillLoadError(
                f"子技能 {skill} 不在技能 {self._manifest.name} 的 skills 白名单"
            )
        payload = await self._kernel._dispatch_call(
            ToolCall(id=uuid.uuid4().hex, name=f"skill.{skill}", args=dict(input)),
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

    async def chat(self, messages: list[Message]) -> ChatResponse:
        """直连 ProviderManager 的 LLM 通道(TRUSTED 编排者能力面,§9.3 impl 扩展)。

        用途:code 技能自驾驶多轮对话(std/web ``research_iterative`` 的
        检索→自评→精化协议)——帧循环把"无 tool_calls 的响应"判为最终答案
        (§3.1 步骤 5),自评回合这类中间 JSON 会终止循环,迭代协议只能由
        code 技能自己开车。模型解析与 ContextManager.build 同口径
        (manifest.model.prefer → RunConfig.model);usage 走 kernel.account
        记账(预算/步数闸门与帧循环一致);dict 形 tool_calls 归一化同 runner。
        """
        policy = self._manifest.model
        model = (policy.prefer[0] if policy and policy.prefer else "") or self._kernel.config.model
        temperature = (
            policy.temperature
            if policy and policy.temperature is not None
            else self._kernel.config.temperature
        )
        req = ChatRequest(model=model, messages=list(messages), temperature=temperature)
        resp = await self._kernel.providers.chat(req)
        resp.message.tool_calls = [
            ToolCall(id=str(tc.get("id", "")), name=str(tc.get("name", "")),
                     args=dict(tc.get("args") or {}))
            if isinstance(tc, dict)
            else tc
            for tc in resp.message.tool_calls
        ]
        self._kernel.account(self._frame, resp.usage)
        return resp

    async def spawn(self, skill: str, input: dict[str, Any]) -> str:
        """spawn 后台帧(§3.4):父帧不挂起,返回子帧 frame_id;白名单/深度检查同 invoke。"""
        return await self._kernel.spawn_frame(self._frame, skill, dict(input))

    async def wait(self, frame_id: str) -> Any:
        """join 退化为读终态(§3.4);子帧失败原样上抛,由本 code 技能处理。"""
        return await self._kernel.wait_frame(frame_id)

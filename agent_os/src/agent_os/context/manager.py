"""ContextManager 基础实现(DESIGN.md §7.5/§7.6;M3)。

组装(build:指令 + 帧上下文 + 可见 schema + 状态注入 + 来源标注)与压缩(maintain)
一家管;前缀逐字节稳定(§7.4 不变量 5,golden-file 断言相邻步前缀 diff 为空)。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent_os.api.v1 import (
    POST_COMPRESS,
    PRE_COMPRESS,
    ChatRequest,
    Compressor,
    Message,
    Role,
    RunConfig,
    Signal,
    SkillFrame,
    Source,
)
from agent_os.context.estimator import TokenEstimator
from agent_os.skills.loader import render_prompt

#: 预算剩余低于该比例时,hint 切换为收敛策略(§7.3:读数 + 操作策略)
LOW_BUDGET_RATIO = 0.2


class ContextManager:
    """``agent_os.api.v1.ContextManager`` 协议的基础实现(M3)。

    - ``build``:组装逻辑与 :class:`MinimalContextManager` 相同(SYSTEM 渲染 + 帧上下文
      + 白名单工具 schema + 伪工具 schema + model/temperature 解析,顺序与序列化结果
      跨步固定,§7.4 不变量 5),另在 ``status_bar`` 开启时尾部追加状态元消息(§7.3);
    - ``maintain``:超 cap 时经 compressor 压到 ``int(cap * target_ratio)``,
      前后发 ``pre/post:compress`` 信号(§7.4 不变量 4);``compression == "off"``
      (RunConfig 或 manifest ``context_policy.compress``)时全部短路(§7.1 消融档)。
    """

    def __init__(
        self,
        *,
        skills: Any,
        tools: Any,
        config: RunConfig,
        compressor: Compressor | None = None,
        estimator: TokenEstimator | None = None,
        signals: Any = None,
        status_bar: bool = True,
        default_max_tokens: int = 128_000,
        target_ratio: float = 0.8,
    ) -> None:
        self._skills = skills
        self._tools = tools
        self._config = config
        self._compressor = compressor
        self._estimator = estimator or TokenEstimator()
        self._signals = signals
        self._status_bar = status_bar
        self._default_max_tokens = default_max_tokens
        self._target_ratio = target_ratio

    @classmethod
    def default(cls, compressor: Compressor | None = None, **kw: Any) -> ContextManager:
        """§14.2 组装示例入口:``ContextManager.default(RollingWindowCompressor(), ...)``。"""
        return cls(compressor=compressor, **kw)

    async def build(self, frame: SkillFrame) -> ChatRequest:
        skill = self._skills.get(frame.skill)
        manifest = skill.manifest
        system = Message(
            role=Role.SYSTEM,
            content=render_prompt(skill.prompt or "", frame.input),
            source=Source.SYSTEM,
        )
        tools = self._tools.schemas_for(manifest.permissions.tools)
        tools.extend(
            {"name": s.name, "description": s.description, "parameters": s.parameters}
            for s in self._skills.visible_to(frame)
        )
        model = ""
        if manifest.model is not None:
            model = manifest.model.prefer[0] if manifest.model.prefer else ""
        model = model or self._config.model
        temperature = (
            manifest.model.temperature
            if manifest.model is not None and manifest.model.temperature is not None
            else self._config.temperature
        )
        messages = [system, *frame.context.messages]
        if self._status_bar:
            # §7.3 状态注入:只进请求尾部(ephemeral),不写回 frame.context.messages;
            # 数据只来自内核记账,绝不来自工具内容(模型无条件信任状态栏)
            messages.append(self._status_message(frame))
        return ChatRequest(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
        )

    def _status_message(self, frame: SkillFrame) -> Message:
        """key-value 状态行(§7.3):裸读数 + 操作策略(hint)。"""
        usage = frame.usage
        remaining = self._config.max_cost - usage.cost
        low = remaining < self._config.max_cost * LOW_BUDGET_RATIO
        hint = "预算剩余不足 20%,收敛到可直接交付的方案" if low else "正常推进"
        content = (
            f"step: {usage.steps}\n"
            f"tokens: {usage.prompt_tokens + usage.completion_tokens}\n"
            f"cost: {usage.cost:.4f}\n"
            f"budget_remaining: {remaining:.2f}\n"
            f"hint: {hint}"
        )
        return Message(
            role=Role.USER,
            source=Source.INJECTED,
            meta={"kind": "status"},
            content=content,
        )

    async def maintain(self, frame: SkillFrame) -> None:
        manifest = self._skills.get(frame.skill).manifest
        estimate = self._estimator.estimate(frame.context.messages)
        frame.context.token_estimate = estimate
        cap = self._cap(manifest.context_policy)
        if cap is None:
            return  # §7.1:compression "off"(RunConfig 消融档或 manifest 档)全部策略短路
        if estimate <= cap or self._compressor is None:
            return  # 未超限,或无 compressor 可压(静默跳过,估算已更新)
        await self._compress(frame, estimate, cap)

    async def force_compress(self, frame: SkillFrame) -> None:
        """§7.1 外部强制触发(sidecar ``ForceCompress`` / ``RunControl.force_compress``):

        无视 cap 是否触发,强制执行一次压缩;``compression == "off"`` 仍短路(消融档)。
        """
        manifest = self._skills.get(frame.skill).manifest
        estimate = self._estimator.estimate(frame.context.messages)
        frame.context.token_estimate = estimate
        cap = self._cap(manifest.context_policy)
        if cap is None or self._compressor is None:
            return
        await self._compress(frame, estimate, cap, forced=True)

    def _cap(self, policy: Any) -> int | None:
        """帧上下文软上限;``compression == "off"`` 时返回 None(全部策略短路)。"""
        if self._config.compression == "off" or (
            policy is not None and policy.compress == "off"
        ):
            return None
        return (
            policy.max_tokens
            if policy is not None and policy.max_tokens
            else self._default_max_tokens
        )

    async def _compress(
        self, frame: SkillFrame, estimate: int, cap: int, *, forced: bool = False
    ) -> None:
        """压缩路径(maintain 超限触发与 force_compress 外部强制共用,§7.1/§7.4)。"""
        pre_payload = {"frame_id": frame.frame_id, "estimate": estimate, "cap": cap}
        if forced:
            pre_payload["forced"] = True
        await self._emit(PRE_COMPRESS, frame, pre_payload)
        report = await self._compressor.compress(
            frame.context, int(cap * self._target_ratio), self._svc()
        )
        await self._emit(
            POST_COMPRESS,
            frame,
            {
                "frame_id": frame.frame_id,
                "evicted": report.evicted,
                "before_tokens": report.before_tokens,
                "after_tokens": report.after_tokens,
                "cache_invalidation_estimate": report.cache_invalidation_estimate,
                "marker": report.marker,
            },
        )
        frame.context.token_estimate = self._estimator.estimate(frame.context.messages)

    def _svc(self) -> Any:
        """压缩器可用的内核服务(§7.5 KernelServices 形状)。

        rolling window 只用 estimator;providers(summarize/narrate 用,M6 前 None),
        blob 取工具注册表的内存 blob(spill 策略预留),没有则 None。
        """
        return SimpleNamespace(
            estimator=self._estimator,
            providers=None,
            blob=getattr(self._tools, "blob", getattr(self._tools, "_blob", None)),
        )

    async def _emit(self, name: str, frame: SkillFrame, payload: dict[str, Any]) -> None:
        if self._signals is not None:
            await self._signals.emit(
                Signal(name=name, run_id=frame.run_id, frame_id=frame.frame_id, payload=payload)
            )


class MinimalContextManager:
    """``agent_os.api.v1.ContextManager`` 协议的纵向切片最小实现(M0)。

    只做组装:SYSTEM(技能指令体经 ``str.format(**frame.input)`` 渲染)+ 帧上下文
    + 帧白名单内工具 schema + 白名单内子技能伪工具 schema(``skill__<name>``);
    model/temperature 取技能 ``model.prefer[0]``/``model.temperature``,缺省回落
    RunConfig。**不做**状态注入与压缩(M3),``maintain`` 为 no-op。
    """

    def __init__(self, *, skills, tools, config) -> None:
        self._skills = skills
        self._tools = tools
        self._config = config

    async def build(self, frame: SkillFrame) -> ChatRequest:
        skill = self._skills.get(frame.skill)
        manifest = skill.manifest
        system = Message(
            role=Role.SYSTEM,
            content=render_prompt(skill.prompt or "", frame.input),
            source=Source.SYSTEM,
        )
        tools = self._tools.schemas_for(manifest.permissions.tools)
        tools.extend(
            {"name": s.name, "description": s.description, "parameters": s.parameters}
            for s in self._skills.visible_to(frame)
        )
        model = ""
        if manifest.model is not None:
            model = manifest.model.prefer[0] if manifest.model.prefer else ""
        model = model or self._config.model
        temperature = (
            manifest.model.temperature
            if manifest.model is not None and manifest.model.temperature is not None
            else self._config.temperature
        )
        return ChatRequest(
            model=model,
            messages=[system, *frame.context.messages],
            tools=tools,
            temperature=temperature,
        )

    async def maintain(self, frame: SkillFrame) -> None:
        """no-op:压缩与状态注入属 M3。"""

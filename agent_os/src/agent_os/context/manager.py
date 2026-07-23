"""ContextManager 基础实现(DESIGN.md §7.5/§7.6;M3)。

组装(build:指令 + 帧上下文 + 可见 schema + 状态注入 + 来源标注)与压缩(maintain)
一家管;前缀逐字节稳定(§7.4 不变量 5,golden-file 断言相邻步前缀 diff 为空)。
"""

from __future__ import annotations

from agent_os.api.v1 import (
    ChatRequest,
    Compressor,
    Message,
    Role,
    SkillFrame,
    Source,
)
from agent_os.skills.loader import render_prompt


class ContextManager:
    """``agent_os.api.v1.ContextManager`` 协议的基础实现(M3)。"""

    def __init__(self, compressor: Compressor | None = None) -> None:
        self.compressor = compressor

    @classmethod
    def default(cls, compressor: Compressor | None = None) -> ContextManager:
        """§14.2 组装示例入口:``ContextManager.default(RollingWindowCompressor())``。"""
        return cls(compressor)

    async def build(self, frame: SkillFrame) -> ChatRequest:
        raise NotImplementedError("M3")

    async def maintain(self, frame: SkillFrame) -> None:
        raise NotImplementedError("M3")


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

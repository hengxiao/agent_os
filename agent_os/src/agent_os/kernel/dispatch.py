"""分发器(docs/DESIGN.md §3.1 步骤 6 / §3.3;M1 工具分发,M2 子技能压栈)。

统一拦截点:``skill.<name>`` 伪工具调用转交 Skill 子系统压栈执行,
其余调用走 Tool Registry 分发流水线(§8.1);中断时占位 tool_result 保证配对原子性(§7.4)。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import SkillFrame, ToolCall


class Dispatcher:
    """内核分发阶段(工具 vs 子技能的路由判决)。"""

    def is_skill_invoke(self, call: ToolCall) -> bool:
        """``skill.`` 前缀判定(§3.3)。"""
        raise NotImplementedError("M2")

    async def dispatch(self, call: ToolCall, frame: SkillFrame) -> Any:
        """工具 → kernel.tools.dispatch(§8.1);子技能 → 构建子帧压栈(§3.1)。"""
        raise NotImplementedError("M1")

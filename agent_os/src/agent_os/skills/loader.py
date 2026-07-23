"""Skill 物化辅助(DESIGN.md §6.1 materialize 步骤;M2)。

prompt 技能:``str.format`` 渲染指令体;code 技能:dotted path importlib 加载并校验协程函数。
code 技能的 handler **惰性 import**——load 期不 import(示例路径可能指向宿主包),
首次调用时才解析 dotted path(§6.3)。
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

from agent_os.api.v1 import Skill, SkillHandler, SkillKind, SkillManifest
from agent_os.kernel.errors import SkillLoadError


def materialize(manifest: SkillManifest) -> Skill:
    """manifest → Skill 对象(prompt 体 / 代码入口 + 策略)。"""
    if manifest.kind is SkillKind.CODE:
        return Skill(manifest=manifest, handler=_lazy_handler(manifest) if manifest.handler else None)
    return Skill(manifest=manifest, prompt=manifest.prompt or "")


def _lazy_handler(manifest: SkillManifest) -> SkillHandler:
    """包一层协程,首次调用时才 import 真实 handler(惰性,见模块 docstring)。"""

    async def run(input: dict[str, Any], ctx: Any) -> Any:
        handler = load_handler(manifest.handler or "")
        return await handler(input, ctx)

    return run


def load_handler(dotted: str) -> SkillHandler:
    """``"my_skills.handlers:run"`` → 协程函数(校验 coroutine function,§6.3)。"""
    module_name, _, attr = dotted.partition(":")
    if not module_name or not attr:
        raise SkillLoadError(f"handler 应为 'pkg.mod:func' 形式,得到: {dotted!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise SkillLoadError(f"无法 import handler 模块 {module_name!r}: {e}") from e
    func = getattr(module, attr, None)
    if not inspect.iscoroutinefunction(func):
        raise SkillLoadError(f"handler {dotted!r} 不是协程函数(async def,§6.3)")
    return func


def render_prompt(template: str, input: dict[str, Any]) -> str:
    """prompt 技能 ``str.format`` 渲染(§6.3);正文不得出现裸露的 ``{}`` 字面量。"""
    try:
        return template.format(**input)
    except (KeyError, IndexError, ValueError) as e:
        raise SkillLoadError(f"prompt 模板渲染失败(缺字段或裸露花括号): {e}") from e

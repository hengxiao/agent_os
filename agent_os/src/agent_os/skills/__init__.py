"""Skill Registry 子系统(DESIGN.md §6;M2):发现、校验、依赖解析、加载、热重载与运行期写入。

类比动态链接器;加载流水线:discover → parse → validate → resolve deps → materialize → publish。
"""

from .local_file import LocalFileSkillRegistry

__all__ = ["LocalFileSkillRegistry"]

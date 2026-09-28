"""依赖版本约束准入检查(docs/DESIGN.md §6.1 寻址契约;SKILL-PACKAGES-V2 §8 裁决)。

裁决边界:只做**约束准入检查**(加载期快速失败),不做多版本求解——同名多版本
共存/按约束解析明确不抄 npm(SKILL-PACKAGES-V2 §8.2:基线单版本,包不是版本仲裁器)。

条目形态:``name`` | ``name@^x.y.z`` | ``name@~x.y.z`` | ``name@x.y.z``(裸版 = 精确)。
语义:``^`` = 同 major 且安装版 ≥ 约束;``~`` = 同 major.minor 且 ≥;``=`` = 精确相等。
0.x major 按通用规则不特判(不抄 npm 的 ``^0.y`` 收窄语义)。
"""

from __future__ import annotations

import re

from agent_os.kernel.errors import SkillLoadError

#: 严格 x.y.z(恰三段非负整数;段数或字符不符即非法,fail-closed)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

#: 条目写法提示(非法形态报错消息共用)
_SPEC_HINT = "支持 name | name@^x.y.z | name@~x.y.z | name@x.y.z"


def _parse_version(text: str) -> tuple[int, int, int] | None:
    """``x.y.z`` → 整数三元组(纯 stdlib 元组比较用);非法 → None。"""
    m = _VERSION_RE.fullmatch(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_constraint(spec: str) -> tuple[str, str | None, str | None]:
    """依赖条目 → ``(name, op, constraint)``;无 ``@`` 后缀 → ``(name, None, None)``。

    非法形态(@ 后非法/多个 @/空段)→ :class:`SkillLoadError`
    (加载期快速失败,消息含条目原文)。
    """
    if "@" not in spec:
        return spec, None, None
    name, _, tail = spec.partition("@")
    if not name or not tail or "@" in tail:
        raise SkillLoadError(f"非法的依赖约束条目: {spec!r}({_SPEC_HINT})")
    op, version = (tail[0], tail[1:]) if tail[0] in "^~" else ("=", tail)
    if _parse_version(version) is None:
        raise SkillLoadError(f"非法的依赖约束条目: {spec!r}({_SPEC_HINT})")
    return name, op, version


def satisfies(version: str, op: str, constraint: str) -> bool:
    """安装版是否满足约束;任一侧非法(空串/非 x.y.z/未知算子)→ False(fail-closed)。

    调用方在 False 时拒绝加载,消息应注明"安装版不满足约束"。
    """
    installed = _parse_version(version)
    want = _parse_version(constraint)
    if installed is None or want is None:
        return False
    if op == "=":
        return installed == want
    if op == "^":
        return installed[0] == want[0] and installed >= want
    if op == "~":
        return installed[:2] == want[:2] and installed >= want
    return False

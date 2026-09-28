"""依赖版本约束准入锚点测试(docs/DESIGN.md §6.1 寻址契约;skills/semver.py)。

固定约定:

- parse_constraint 矩阵:裸名/name@^/name@~/name@= 四形态;非法形态
  (@ 后非法/多个 @/空段)→ SkillLoadError,消息含条目原文;
- satisfies 边界:^ = 同 major 且 ≥;~ = 同 major.minor 且 ≥;= = 精确;
  0.x major 按通用规则不特判;安装版空串/非 x.y.z → False(fail-closed);
- yaml 端到端:依赖带约束满足 → 加载(permissions.skills 落解析后纯名);
  不满足 → SkillLoadError(消息含 dep 原文/安装版/约束);
  非法约束 → SkillLoadError;无后缀 → 现状(只查存在)。
"""

from __future__ import annotations

import pytest

from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.skills.semver import parse_constraint, satisfies

# ---------------------------------------------------------------------------
# parse_constraint 矩阵
# ---------------------------------------------------------------------------


def test_parse_constraint_bare_name():
    """裸名 → (name, None, None)。"""
    assert parse_constraint("common.text.summarize") == ("common.text.summarize", None, None)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("lib.base@^1.2.3", ("lib.base", "^", "1.2.3")),
        ("lib.base@~1.2.3", ("lib.base", "~", "1.2.3")),
        ("lib.base@1.2.3", ("lib.base", "=", "1.2.3")),  # 裸版 = 精确
        ("lib.base@^0.0.0", ("lib.base", "^", "0.0.0")),
    ],
)
def test_parse_constraint_with_constraint(spec, expected):
    assert parse_constraint(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "lib.base@",  # 空约束段
        "@1.2.3",  # 空 name 段
        "a@b@c",  # 多个 @
        "lib.base@^1.2",  # 段数非 3
        "lib.base@1.2.3.4",  # 段数超 3
        "lib.base@>=1.2.3",  # @ 后非法算子
        "lib.base@^x.y.z",  # 非数字段
    ],
)
def test_parse_constraint_illegal_fast_fail(spec):
    """非法形态 → SkillLoadError(加载期快速失败),消息含条目原文。"""
    with pytest.raises(SkillLoadError, match="非法的依赖约束条目") as exc_info:
        parse_constraint(spec)
    assert spec in str(exc_info.value), "报错消息含条目原文"


# ---------------------------------------------------------------------------
# satisfies 边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "ok"),
    [
        ("1.2.3", True),  # 恰等约束
        ("1.9.0", True),  # 同 major 且更高
        ("2.0.0", False),  # major 越界
        ("0.9.0", False),  # major 不符(且低于约束)
        ("1.2.2", False),  # 同 major 但低于约束
    ],
)
def test_satisfies_caret(installed, ok):
    """^1.2.3 = 同 major 且 installed >= 1.2.3。"""
    assert satisfies(installed, "^", "1.2.3") is ok


@pytest.mark.parametrize(
    ("installed", "ok"),
    [
        ("1.2.3", True),
        ("1.2.9", True),  # 同 major.minor 且更高 patch
        ("1.3.0", False),  # minor 越界
        ("1.1.9", False),  # 低于约束
        ("2.2.3", False),
    ],
)
def test_satisfies_tilde(installed, ok):
    """~1.2.3 = 同 major.minor 且 installed >= 1.2.3。"""
    assert satisfies(installed, "~", "1.2.3") is ok


@pytest.mark.parametrize(
    ("installed", "ok"),
    [("1.2.3", True), ("1.2.4", False), ("0.1.0", False)],
)
def test_satisfies_exact(installed, ok):
    """= = 精确相等。"""
    assert satisfies(installed, "=", "1.2.3") is ok


def test_satisfies_zero_major_generic_rule():
    """0.x major 不特判(通用规则:同 major 即兼容;不抄 npm 的 ^0.y 收窄)。"""
    assert satisfies("0.2.3", "^", "0.2.3") is True
    assert satisfies("0.9.0", "^", "0.2.3") is True, "^0.2.3 按通用规则收 0.9.0"
    assert satisfies("1.0.0", "^", "0.2.3") is False


@pytest.mark.parametrize("installed", ["", "1.2", "abc", "1.2.x"])
def test_satisfies_fail_closed_on_bad_installed(installed):
    """安装版空串/非 x.y.z → False(fail-closed;调用方消息注明'安装版不满足约束')。"""
    assert satisfies(installed, "^", "1.2.3") is False
    assert satisfies(installed, "=", "1.2.3") is False


def test_satisfies_fail_closed_on_bad_constraint_or_op():
    """约束非法/未知算子 → False(fail-closed)。"""
    assert satisfies("1.2.3", "^", "1.2") is False
    assert satisfies("1.2.3", ">", "1.2.3") is False


# ---------------------------------------------------------------------------
# yaml 端到端(接进 local_file 依赖检查)
# ---------------------------------------------------------------------------

_BASE_YAML = """
skills:
  - name: lib.base
    version: 1.2.3
    kind: prompt
    description: 基础库。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [] }
    prompt: 基础。
"""

_CALLER_YAML = """
  - name: app.caller
    version: 0.1.0
    kind: prompt
    description: 调用方。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [%s] }
    prompt: 调用。
"""


def _write(tmp_path, dep_entry: str) -> LocalFileSkillRegistry:
    path = tmp_path / "skills.yaml"
    path.write_text(_BASE_YAML + _CALLER_YAML % dep_entry, encoding="utf-8")
    return LocalFileSkillRegistry(str(path))


def test_e2e_constraint_satisfied_loads(tmp_path):
    """依赖带约束且满足 → 加载;permissions.skills 落解析后的纯名(下游共用名字面)。"""
    registry = _write(tmp_path, '"lib.base@^1.0.0"')
    caller = next(m for m in registry.manifests() if m.name == "app.caller")
    assert caller.permissions.skills == ["lib.base"], "@ 后缀不进入加载后的名字面"


def test_e2e_constraint_unsatisfied_rejected(tmp_path):
    """安装版不满足约束 → SkillLoadError,消息含 dep 原文/安装版/约束。"""
    with pytest.raises(SkillLoadError, match="不满足约束") as exc_info:
        _write(tmp_path, '"lib.base@^2.0.0"')
    msg = str(exc_info.value)
    assert "lib.base@^2.0.0" in msg and "1.2.3" in msg and "^2.0.0" in msg


def test_e2e_constraint_unversioned_installed_rejected(tmp_path):
    """安装版未声明版本(空串)→ fail-closed 拒绝,消息注明安装版不满足约束。"""
    base = _BASE_YAML.replace("version: 1.2.3", 'version: ""')
    path = tmp_path / "skills.yaml"
    path.write_text(base + _CALLER_YAML % '"lib.base@~1.2.0"', encoding="utf-8")
    with pytest.raises(SkillLoadError, match="安装版"):
        LocalFileSkillRegistry(str(path))


def test_e2e_illegal_constraint_rejected(tmp_path):
    """非法约束形态 → 加载期 SkillLoadError,消息含条目原文。"""
    with pytest.raises(SkillLoadError, match="lib.base@\\^1.2"):
        _write(tmp_path, '"lib.base@^1.2"')


def test_e2e_bare_dep_unchanged(tmp_path):
    """无后缀 → 现状(只查存在):存在即加载,不存在即报'不存在的子技能'。"""
    registry = _write(tmp_path, '"lib.base"')
    assert registry.get_by_name("app.caller").manifest.name == "app.caller"
    with pytest.raises(SkillLoadError, match="不存在的子技能"):
        _write(tmp_path, '"no.such.dep"')

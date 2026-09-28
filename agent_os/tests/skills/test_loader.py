"""Skill 物化/加载锚点测试(docs/DESIGN.md §6.1/§6.3;materialize + dotted path + 模板渲染)。

固定约定:

- ``load_handler("pkg.mod:func")``:importlib 解析并校验协程函数;格式错/模块缺失/
  非协程 → ``SkillLoadError``;
- code 技能 handler **惰性 import**:materialize 期不解析 dotted path,
  首次调用才失败(§6.3);
- ``render_prompt``:``str.format`` 渲染;缺字段/裸露花括号 → ``SkillLoadError``。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from agent_os.api.v1 import SkillKind, SkillManifest
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.loader import load_handler, materialize, render_prompt
from agent_os.skills.manifest import parse_manifest

# ---------------------------------------------------------------------------
# load_handler
# ---------------------------------------------------------------------------


def test_load_handler_success_returns_coroutine_function():
    fn = load_handler("tests.helpers.code_skills:pure_add")
    assert inspect.iscoroutinefunction(fn)
    assert asyncio.run(fn({"a": 1, "b": 2}, None)) == {"sum": 3}


@pytest.mark.parametrize("dotted", ["no_colon", ":func", "pkg.mod:", ""])
def test_load_handler_bad_format_rejected(dotted):
    with pytest.raises(SkillLoadError, match="pkg.mod:func"):
        load_handler(dotted)


def test_load_handler_missing_module_rejected():
    with pytest.raises(SkillLoadError, match="无法 import"):
        load_handler("tests.helpers.does_not_exist:run")


def test_load_handler_missing_attr_rejected():
    with pytest.raises(SkillLoadError, match="协程函数"):
        load_handler("tests.helpers.code_skills:no_such_fn")


def test_load_handler_non_coroutine_rejected():
    # json.dumps 是普通函数,不是 async def
    with pytest.raises(SkillLoadError, match="协程函数"):
        load_handler("json:dumps")


# ---------------------------------------------------------------------------
# materialize(惰性 import)
# ---------------------------------------------------------------------------


def test_materialize_prompt_skill():
    m = SkillManifest(name="p", kind=SkillKind.PROMPT, prompt="做 {thing}")
    skill = materialize(m)
    assert skill.prompt == "做 {thing}"
    assert skill.handler is None


def test_materialize_code_skill_is_lazy():
    """materialize 期不解析 dotted path;坏路径首次调用才抛(§6.3 惰性)。"""
    m = SkillManifest(name="c", kind=SkillKind.CODE, handler="tests.helpers.nope:run")
    skill = materialize(m)  # 不应抛
    assert skill.handler is not None
    with pytest.raises(SkillLoadError):
        asyncio.run(skill.handler({}, None))


def test_materialize_code_skill_without_handler():
    m = SkillManifest(name="c", kind=SkillKind.CODE, handler=None)
    assert materialize(m).handler is None


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


def test_render_prompt_formats_input():
    assert render_prompt("计算 {n} 项", {"n": 5}) == "计算 5 项"


def test_render_prompt_missing_field_rejected():
    with pytest.raises(SkillLoadError, match="渲染失败"):
        render_prompt("计算 {n} 项", {})


def test_render_prompt_bare_braces_rejected():
    with pytest.raises(SkillLoadError, match="渲染失败"):
        render_prompt('输出 JSON:{"k": 1}', {})


# ---------------------------------------------------------------------------
# 迁移期别名解析(legacy flat names → dotted hierarchy)
# ---------------------------------------------------------------------------


def test_legacy_skill_aliases_resolve_to_canonical_names():
    """旧扁平技能名仍可通过 LocalFileSkillRegistry 解析到新的点分名(§docs/NAMING.md)。"""
    from pathlib import Path

    from agent_os.skills.local_file import LocalFileSkillRegistry

    std_dir = Path(__file__).resolve().parents[2] / "std"
    reg = LocalFileSkillRegistry(str(std_dir))
    for old, new in (
        ("summarize", "common.text.summarize"),
        ("fetch_page", "common.web.fetch_page"),
        ("memory_extract", "common.memory.extract"),
        ("research_one", "common.research.one"),
        ("verify_before_store", "common.memory.verify"),
    ):
        assert reg.get_by_name(old).manifest.name == new, f"{old} 应解析为 {new}"


# ---------------------------------------------------------------------------
# LocalFileSkillRegistry 目录/文件列表加载(Phase 2:std 域分包)
# ---------------------------------------------------------------------------

_TWO_FILES = {
    "a.yaml": """skills:
- name: common.text.one
  kind: prompt
  description: '占位一号。Use when 测试;Do not use when 生产。'
  prompt: 做一号
""",
    "b.yaml": """skills:
- name: common.text.two
  kind: prompt
  description: '占位二号,依赖一号。Use when 测试;Do not use when 生产。'
  permissions:
    skills:
    - common.text.one
  prompt: 做二号
""",
}


def test_registry_loads_directory_merging_yaml_files(tmp_path):
    """path 为目录:加载其下全部 *.yaml(文件名排序合并),跨文件依赖照常校验。"""
    for name, text in _TWO_FILES.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    from agent_os.skills.local_file import LocalFileSkillRegistry

    reg = LocalFileSkillRegistry(str(tmp_path))
    assert sorted(m.name for m in reg.manifests()) == ["common.text.one", "common.text.two"]


def test_registry_loads_file_list_and_detects_cross_file_duplicates(tmp_path):
    """path 为文件列表:按给定序合并;跨文件 name 重复 → SkillLoadError。"""
    for name, text in _TWO_FILES.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    from agent_os.skills.local_file import LocalFileSkillRegistry

    reg = LocalFileSkillRegistry([str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    assert sorted(m.name for m in reg.manifests()) == ["common.text.one", "common.text.two"]
    (tmp_path / "c.yaml").write_text(_TWO_FILES["a.yaml"], encoding="utf-8")
    with pytest.raises(SkillLoadError, match="重复"):
        LocalFileSkillRegistry([str(tmp_path / "a.yaml"), str(tmp_path / "c.yaml")])


def test_registry_directory_reload_detects_any_source_change(tmp_path):
    """目录形态 reload:mtime 取全部源文件最大值,改任一文件都能检出。"""
    import time

    for name, text in _TWO_FILES.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    from agent_os.skills.local_file import LocalFileSkillRegistry

    reg = LocalFileSkillRegistry(str(tmp_path))
    assert reg.reload() is False
    time.sleep(0.01)
    (tmp_path / "b.yaml").write_text(
        _TWO_FILES["b.yaml"].replace("做二号", "做二号改"), encoding="utf-8"
    )
    assert reg.reload() is True


# ---------------------------------------------------------------------------
# parse_manifest:limits.max_cost(additive 契约字段)
# ---------------------------------------------------------------------------


def _limits_entry(max_cost):
    """最小合法技能条目,limits 段带待测 max_cost。"""
    return {
        "name": "test.max_cost",
        "kind": "prompt",
        "description": "占位。Use when 测试;Do not use when 生产。",
        "permissions": {"tools": [], "skills": []},
        "prompt": "占位",
        "limits": {"max_cost": max_cost},
    }


def test_parse_manifest_limits_max_cost_numeric():
    """limits.max_cost:合法数值解析为 float(int 上浮);缺省为 None。"""
    m = parse_manifest(_limits_entry(0.5))
    assert m.limits is not None and m.limits.max_cost == 0.5
    assert isinstance(m.limits.max_cost, float)
    m_int = parse_manifest(_limits_entry(2))
    assert m_int.limits is not None and m_int.limits.max_cost == 2.0


def test_parse_manifest_limits_max_cost_bad_type_rejected():
    """limits.max_cost:字符串/bool 非法类型加载期拒绝(运行期比较会炸 TypeError,
    故在解析侧 fail-fast;其余 limits 键维持现状不做类型校验)。"""
    for bad in ("0.5", True):
        with pytest.raises(SkillLoadError, match="max_cost"):
            parse_manifest(_limits_entry(bad))

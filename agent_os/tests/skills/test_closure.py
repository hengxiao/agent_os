"""P1 锚点测试:能力包闭包(docs/SKILL-PACKAGES.md §2/§4.1)。

status 四态(draft/production/external/missing)、外链与悬空不再下传、
自引用合法递归剔边、经他人回边记 cycle、菱形合并、根缺失 FileNotFoundError。
"""

from __future__ import annotations

import pytest

from agent_os.skills.closure import compute_closure
from agent_os.skills.draft_store import DraftStore
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

PROD_YAML = """
skills:
  - name: ops.plan.write
    version: 1.0.0
    kind: prompt
    description: 写计划。Use when 写;Do not use when 读。
    permissions:
      tools: [system.file.write]
      skills: []
    prompt: 写。
  - name: weather.query
    version: 1.0.0
    kind: prompt
    description: 查天气。Use when 查;Do not use when 写。
    permissions:
      tools: []
      skills: [common.helper]
    prompt: 查。
  - name: common.helper
    version: 1.0.0
    kind: prompt
    description: 帮手。Use when x;Do not use when y。
    permissions:
      tools: []
      skills: []
    prompt: 帮。
"""


@pytest.fixture()
def production(tmp_path):
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    return LocalFileSkillRegistry(str(path))


@pytest.fixture()
def tools():
    return LocalPythonToolRegistry.with_builtins()


def _mk_draft(store: DraftStore, name: str, skills: list[str], tools_list=None):
    store.create(name)
    store.save(name, manifest={
        "name": name,
        "version": "0.1.0",
        "kind": "prompt",
        "description": "草稿。Use when x;Do not use when y(凑长度过 lint)。",
        "inputs": {"type": "object"},
        "outputs": {"type": "object"},
        "permissions": {"tools": tools_list or [], "skills": skills},
    }, prompt="草稿 prompt。")


def test_closure_statuses_and_no_descend(tmp_path, production, tools):
    """四态各一:draft 下传 / production 同空间下传 / external 不下传 / missing 不下传。"""
    store = DraftStore(tmp_path / "drafts")
    # 根(草稿)引用:草稿子技能、生产同空间、生产跨空间(它还有引用,不得下传)、悬空
    _mk_draft(store, "ops.inspect.fleet",
              ["ops.cleanup.execute", "ops.plan.write", "weather.query", "ops.ghost.none"])
    _mk_draft(store, "ops.cleanup.execute", [])
    # weather.query(外链)若被下传会看到更多成员——它没有引用,用另一招验证:
    # external 节点自己声明的引用不出现在成员表(见下 external_descend 用例)

    result = compute_closure("ops.inspect.fleet", store, production, tools)
    by_name = {m["name"]: m for m in result["members"]}
    assert by_name["ops.inspect.fleet"]["status"] == "draft"
    assert by_name["ops.cleanup.execute"]["status"] == "draft"
    assert by_name["ops.plan.write"]["status"] == "production"
    assert by_name["weather.query"]["status"] == "external"
    assert by_name["ops.ghost.none"]["status"] == "missing"
    assert by_name["ops.ghost.none"]["tier"] is None, "悬空不给档"
    assert by_name["ops.plan.write"]["tier"] == "reversible"  # file.write
    assert result["errors"] == []
    # ref_by 记录(先达者持有)
    assert by_name["ops.plan.write"]["ref_by"] == "ops.inspect.fleet"
    # depth:根 0,直接引用 1
    assert by_name["ops.inspect.fleet"]["depth"] == 0
    assert by_name["ops.cleanup.execute"]["depth"] == 1


def test_external_and_missing_not_descended(tmp_path, production, tools):
    """外链/悬空不再下传:它们声明的引用不进成员表。"""
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "ops.root.one", ["weather.query", "ops.ghost.none"])
    result = compute_closure("ops.root.one", store, production, tools)
    names = [m["name"] for m in result["members"]]
    assert names == ["ops.root.one", "weather.query", "ops.ghost.none"], (
        "外链与悬空都是叶子,不再下传"
    )
    assert "common.helper" not in names, "外链自己的引用不属于本包编辑面(不下传)"


def test_self_reference_legal_and_cycle_detected(tmp_path, production, tools):
    """自引用 = 合法递归(剔边);经他人回边 → cycle error(G2 判 fail 的数据源)。

    环只能放草稿层构造:生产 loader 的拓扑闸在加载期就拒(这正是悬空/环要
    在开发期被 G2 拦住的原因——promote 出去就炸生产)。
    """
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "ops.recur.self", ["ops.recur.self"])
    result = compute_closure("ops.recur.self", store, production, tools)
    assert result["errors"] == []
    assert [m["name"] for m in result["members"]] == ["ops.recur.self"]

    _mk_draft(store, "ops.ring.a", ["ops.ring.b"])
    _mk_draft(store, "ops.ring.b", ["ops.ring.a"])
    _mk_draft(store, "ops.root.two", ["ops.ring.a"])
    result2 = compute_closure("ops.root.two", store, production, tools)
    assert any(e["kind"] == "cycle" for e in result2["errors"])


def test_diamond_merged_and_root_missing(tmp_path, production, tools):
    """菱形依赖合并(先达者持有 ref_by);根查无此名 → FileNotFoundError。"""
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "ops.root.three", ["ops.plan.write", "ops.plan.write"])
    result = compute_closure("ops.root.three", store, production, tools)
    names = [m["name"] for m in result["members"]]
    assert names.count("ops.plan.write") == 1, "重复引用只出一次"

    with pytest.raises(FileNotFoundError):
        compute_closure("ops.ghost.root", store, production, tools)

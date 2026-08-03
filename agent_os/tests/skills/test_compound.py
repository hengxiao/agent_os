"""P1.5 锚点测试:注册表分层组合(docs/SKILL-PACKAGES-V2.md §6.10)。

覆盖重构中最容易丢掉的三条语义:
- **顺序即优先级**(layers[0] 先),且 ``resolve`` 返回层来源下标;
- **try-and-continue**:中间层"给不出"时继续往后找,不是让整条链失败
  ——"半成品影子不遮蔽可用版本";
- **写入面只走显式可写层**:未指定 writable 时写入/热重载报错,防 promote
  写进草稿层。
"""

from __future__ import annotations

import pytest

from agent_os.api.v1 import SkillRef
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.compound import CompoundSkillRegistry, StaticSkillRegistry
from agent_os.skills.draft_store import DraftSkillRegistry, DraftStore
from agent_os.skills.loader import materialize
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.skills.manifest import parse_manifest


def _skill(name: str, version: str = "1.0.0", prompt: str = "p"):
    return materialize(
        parse_manifest(
            {
                "name": name,
                "version": version,
                "kind": "prompt",
                "description": f"{name}。Use when x;Do not use when y(凑长度过 lint)。",
                "permissions": {"tools": [], "skills": []},
                "prompt": prompt,
            }
        )
    )


class _Broken:
    """恒抛 SkillLoadError 的一层(模拟存了一半的草稿层)。"""

    def get(self, ref: SkillRef):
        raise SkillLoadError(f"本层坏了: {ref.name}")


class _Empty:
    """恒抛 FileNotFoundError 的一层(模拟本层没有这个名字)。"""

    def get(self, ref: SkillRef):
        raise FileNotFoundError(ref.name)


def test_resolve_order_and_layer_provenance():
    """顺序即优先级;resolve 返回层来源下标(§6.10:身份归 name,层归解析结果)。"""
    front = StaticSkillRegistry({"a.b": _skill("a.b", "9.9.9")})
    back = StaticSkillRegistry({"a.b": _skill("a.b", "1.0.0"), "c.d": _skill("c.d")})
    reg = CompoundSkillRegistry([front, back], names=["draft", "production"])

    skill, layer = reg.resolve(SkillRef(name="a.b"))
    assert skill.manifest.version == "9.9.9", "前层遮蔽后层"
    assert (layer, reg.layer_name(layer)) == (0, "draft")

    skill2, layer2 = reg.resolve(SkillRef(name="c.d"))
    assert (layer2, reg.layer_name(layer2)) == (1, "production")
    assert skill2.manifest.name == "c.d"


@pytest.mark.parametrize("bad", [_Broken(), _Empty()])
def test_try_and_continue_not_plain_shadowing(bad):
    """中间层给不出(坏 / 缺)时**继续往后**——半成品影子不遮蔽可用版本。"""
    good = StaticSkillRegistry({"a.b": _skill("a.b", "1.0.0")})
    reg = CompoundSkillRegistry([bad, good])
    assert reg.get(SkillRef(name="a.b")).manifest.version == "1.0.0"


def test_all_layers_miss_raises_skill_load_error():
    """全链未命中 → SkillLoadError(调用方按"技能不存在"处理)。"""
    reg = CompoundSkillRegistry([_Empty(), StaticSkillRegistry({})])
    with pytest.raises(SkillLoadError):
        reg.get(SkillRef(name="no.such"))


def test_manifests_merge_back_layer_base_front_overrides():
    """清单合并:后层作底(保序)、前层覆盖——与 get 同一优先级。"""
    back = StaticSkillRegistry({"a.b": _skill("a.b", "1.0.0"), "c.d": _skill("c.d")})
    front = StaticSkillRegistry({"a.b": _skill("a.b", "9.9.9"), "e.f": _skill("e.f")})
    reg = CompoundSkillRegistry([front, back])

    got = {m.name: m.version for m in reg.manifests()}
    assert got["a.b"] == "9.9.9", "前层覆盖同名"
    names = [m.name for m in reg.manifests()]
    assert names[:2] == ["a.b", "c.d"], "后层既有次序在前(生产拓扑序不被打乱)"
    assert "e.f" in names[2:], "前层新增的名字附后"


def test_writable_layer_guards_write_surface(tmp_path):
    """写入面只走显式可写层;未指定 writable 时报错(防 promote 写进草稿层)。"""
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text("skills: []\n", encoding="utf-8")
    production = LocalFileSkillRegistry(str(skills_yaml))
    drafts = DraftSkillRegistry(DraftStore(tmp_path / "drafts"))

    readonly = CompoundSkillRegistry([drafts, production])
    with pytest.raises(SkillLoadError, match="可写层"):
        _ = readonly.path
    with pytest.raises(SkillLoadError, match="可写层"):
        readonly.reload()

    writable = CompoundSkillRegistry([drafts, production], writable=1)
    assert writable.path == str(skills_yaml), "path 委托给生产层,不是草稿层"
    assert writable.reload() is False  # mtime 未变

    with pytest.raises(ValueError):
        CompoundSkillRegistry([drafts, production], writable=5)
    with pytest.raises(ValueError):
        CompoundSkillRegistry([])


def test_draft_registry_accepts_single_draft_adapter(tmp_path):
    """DraftSkillRegistry 只要求 load_skill——单草稿适配器可直接当一层用。

    这正是 gate._SingleDraftStore 的复用路径(§6.10:三处实现收敛为一处)。
    """

    class _Single:
        def load_skill(self, name: str):
            if name == "lab.only":
                return _skill("lab.only", "0.1.0")
            raise FileNotFoundError(name)

    layer = DraftSkillRegistry(_Single())
    assert layer.get(SkillRef(name="lab.only")).manifest.version == "0.1.0"
    assert layer.manifests() == [], "无 list() 的适配器不提供清单面,但不报错"

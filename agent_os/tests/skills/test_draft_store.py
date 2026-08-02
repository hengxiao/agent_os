"""L1 锚点测试:DraftStore(docs/SKILL-DEV.md §1.2)与 OverlaySkillRegistry(§1.1)。

固定约定:

- 布局 ``<root>/<name>/manifest.yaml + prompt.md + handler.py(可选)+ tests/*.json``;
- 草稿允许临时不合规:read 返回原文 + parse_error,不抛错(编辑器要开得开半成品);
- save 整体替换,上一版自动 .bak;name 合法性 = docs/NAMING.md §2 点分 + 路径穿越防护;
- OverlaySkillRegistry 草稿优先;草稿不合规时透明回落生产同名。
"""

from __future__ import annotations

import json

import pytest

from agent_os.api.v1 import SkillRef, explain_skill_tier
from agent_os.skills.draft_store import DraftStore, OverlaySkillRegistry
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry


@pytest.fixture()
def store(tmp_path):
    return DraftStore(tmp_path / "drafts")


def test_create_empty_template(store):
    """空模板:create → read 回来,契约骨架齐全、parse_error 为空。"""
    draft = store.create("weather.query")
    assert draft["parse_error"] is None
    m = draft["manifest"]
    assert m["name"] == "weather.query"
    assert m["kind"] == "prompt"
    assert m["inputs"]["type"] == "object"
    assert m["permissions"] == {"tools": [], "skills": []}
    assert "prompt" in draft["prompt"]
    assert draft["handler"] is None
    assert draft["tests"] == {}


def test_create_from_production(tmp_path, store):
    """从生产 skill 复制:manifest 字段全带,目录名覆盖为草稿名(独立个体)。"""
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(
        "skills:\n"
        "  - name: common.text.word_count\n"
        "    version: 1.2.0\n"
        "    kind: prompt\n"
        "    description: 数字数。Use when 统计;Do not use when 其他。\n"
        "    permissions: { tools: [], skills: [] }\n"
        "    prompt: 数字数。\n",
        encoding="utf-8",
    )
    production = LocalFileSkillRegistry(str(skills_yaml))
    source = production.get(SkillRef(name="common.text.word_count"))
    draft = store.create("lab.word_count", source=source)
    assert draft["manifest"]["version"] == "1.2.0"
    assert draft["manifest"]["name"] == "lab.word_count"
    assert draft["prompt"] == "数字数。"
    with pytest.raises(FileExistsError):
        store.create("lab.word_count")


def test_save_backs_up_previous_version(store):
    """save 整体替换;上一版自动 .bak;handler=None 删除 handler.py(留 .bak)。"""
    store.create("weather.query")
    store.save(
        "weather.query",
        manifest={"name": "weather.query", "kind": "prompt", "version": "0.2.0"},
        prompt="v2 prompt",
        handler="def run(): pass",
    )
    store.save(
        "weather.query",
        manifest={"name": "weather.query", "kind": "prompt", "version": "0.3.0"},
        prompt="v3 prompt",
        handler=None,  # code → prompt 转型:handler.py 删除
        tests={"case1.json": {"input": {"city": "北京"}}},
    )
    root = store.root / "weather.query"
    assert "0.2.0" in (root / "manifest.yaml.bak").read_text(encoding="utf-8")
    assert (root / "prompt.md").read_text(encoding="utf-8") == "v3 prompt"
    assert (root / "prompt.md.bak").read_text(encoding="utf-8") == "v2 prompt"
    assert not (root / "handler.py").exists()
    assert (root / "handler.py.bak").read_text(encoding="utf-8") == "def run(): pass"
    case = json.loads((root / "tests" / "case1.json").read_text(encoding="utf-8"))
    assert case == {"input": {"city": "北京"}}


def test_read_tolerates_noncompliant_draft(store):
    """不合规草稿可读:YAML 坏 / 契约坏都返回原文 + parse_error,不抛错。"""
    store.create("bad.one")
    (store.root / "bad.one" / "manifest.yaml").write_text("kind: [unclosed", encoding="utf-8")
    draft = store.read("bad.one")
    assert draft["manifest"] is None
    assert "YAML" in draft["parse_error"]

    store.create("bad.two")
    (store.root / "bad.two" / "manifest.yaml").write_text(
        "name: bad.two\nkind: bogus\n", encoding="utf-8"
    )
    draft2 = store.read("bad.two")
    assert draft2["manifest"]["kind"] == "bogus"  # 原文 dict 照给(编辑器要能继续改)
    assert "bogus" in draft2["parse_error"]


def test_name_validation_path_traversal(store):
    """路径穿越防护:../、绝对路径、单段名、大写全部拒绝;read/save/delete 同一闸。"""
    for bad in ("../etc", "/abs", "single", "Upper.Case", "a..b", "a/b.c", "a b.c"):
        with pytest.raises(ValueError):
            store.create(bad)
    with pytest.raises(ValueError):
        store.read("../etc")
    with pytest.raises(ValueError):
        store.save("../x", manifest={})
    with pytest.raises(ValueError):
        store.delete("..")
    with pytest.raises(FileNotFoundError):
        store.read("no.such")


def test_list_reports_mtime_and_shape(store):
    """list:name/mtime/handler 标记/用例数;非草稿目录(无 manifest.yaml)跳过。"""
    store.create("a.b")
    store.save("a.b", manifest={"name": "a.b"}, tests={"c1.json": {}, "c2.json": {}})
    (store.root / "gate").mkdir()  # 非草稿目录(闸门归档形态,§1.4)
    rows = store.list()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "a.b"
    assert row["mtime"] > 0
    assert row["has_handler"] is False
    assert row["tests"] == 2


def test_overlay_draft_wins_and_fallback(tmp_path, store):
    """Overlay:草稿优先于生产同名;草稿不合规时透明回落生产版本。"""
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(
        "skills:\n"
        "  - name: common.text.word_count\n"
        "    version: 1.0.0\n"
        "    kind: prompt\n"
        "    description: 生产版。Use when 统计;Do not use when 其他。\n"
        "    permissions: { tools: [], skills: [] }\n"
        "    prompt: 生产版 prompt。\n",
        encoding="utf-8",
    )
    production = LocalFileSkillRegistry(str(skills_yaml))
    overlay = OverlaySkillRegistry(production, store)

    store.create("common.text.word_count")
    store.save(
        "common.text.word_count",
        manifest={
            "name": "common.text.word_count",
            "version": "9.9.9",
            "kind": "prompt",
            "description": "草稿版。Use when x;Do not use when y(凑长度过 lint)。",
            "permissions": {"tools": [], "skills": []},
        },
        prompt="草稿版 prompt。",
    )
    skill = overlay.get(SkillRef(name="common.text.word_count"))
    assert skill.manifest.version == "9.9.9", "草稿优先于生产同名"
    assert skill.prompt == "草稿版 prompt。"

    # 草稿改坏:透明回落生产(半成品影子不遮蔽可用版本)
    (store.root / "common.text.word_count" / "manifest.yaml").write_text(
        "kind: [unclosed", encoding="utf-8"
    )
    skill2 = overlay.get(SkillRef(name="common.text.word_count"))
    assert skill2.manifest.version == "1.0.0"


def test_tier_explain_detail(tmp_path, store):
    """explain_skill_tier:推导档 + 来源明细(top 标出贡献最高档的工具)。"""
    tools = LocalPythonToolRegistry.with_builtins()
    store.create("lab.cleanup")
    store.save(
        "lab.cleanup",
        manifest={
            "name": "lab.cleanup",
            "version": "0.1.0",
            "kind": "prompt",
            "description": "清理。Use when x;Do not use when y(凑长度过 lint)。",
            "permissions": {
                "tools": ["system.file.read", "system.file.write", "system.file.delete"],
                "skills": [],
            },
        },
    )
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text("skills: []\n", encoding="utf-8")
    overlay = OverlaySkillRegistry(LocalFileSkillRegistry(str(skills_yaml)), store)
    manifest = store.load_skill("lab.cleanup").manifest
    detail = explain_skill_tier(manifest, tools, overlay)
    assert detail["tier"] == "irreversible"  # file.delete 显式标定(dogfood 修复)
    by_name = {s["name"]: s["tier"] for s in detail["sources"]}
    assert by_name == {
        "system.file.read": "none",
        "system.file.write": "reversible",
        "system.file.delete": "irreversible",
    }
    assert [s["name"] for s in detail["top"]] == ["system.file.delete"]

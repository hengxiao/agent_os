"""P2 锚点测试:两模式闭包 / 两阶段严格性 / 提交计划与原子提交
(docs/SKILL-PACKAGES-V2.md §6.1-§6.4)。

- 死锁回归:根引用未发布兄弟草稿——validate(草稿期)通过,单稿 promote 复跑
  不再误报悬空(strict 下 fail 且指路包级提交);
- closure edit(生产即叶子)/ runtime(穿过生产)、>8 成员体积告警;
- plan:action 三态/unchanged 不重写/package_hash 一致性 409/blockers.fix;
- 原子性:staging 验证失败现网零变化、成功单次 reload、整文件单 .bak。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_os.skills.closure import compute_closure
from agent_os.skills.draft_store import DraftStore
from agent_os.skills.gate import GateError, promote_draft, validate_draft
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.skills.package import (
    _atomic_write,
    build_plan,
    promote_package,
)
from agent_os.tools.local_registry import LocalPythonToolRegistry

PROD_YAML = """
skills:
  - name: lab.published
    version: 1.0.0
    kind: prompt
    description: 已发布。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [lab.published.dep] }
    prompt: 已发布。
  - name: lab.published.dep
    version: 1.0.0
    kind: prompt
    description: 已发布依赖。Use when x;Do not use when y。
    permissions: { tools: [], skills: [] }
    prompt: 依赖。
"""


@pytest.fixture()
def tools():
    return LocalPythonToolRegistry.with_builtins()


@pytest.fixture()
def production(tmp_path):
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    return LocalFileSkillRegistry(str(path))


def _mk_draft(store: DraftStore, name: str, skills=None, tools_list=None, prompt="你是助手。"):
    store.create(name)
    store.save(name, manifest={
        "name": name,
        "version": "0.1.0",
        "kind": "prompt",
        "description": "草稿。Use when 测试;Do not use when 其他。",
        "inputs": {"type": "object"},
        "outputs": {"type": "object"},
        "permissions": {"tools": tools_list or [], "skills": skills or []},
    }, prompt=prompt)


# ---------------------------------------------------------------------------
# §2.2 死锁回归 + §6.2 两阶段严格性
# ---------------------------------------------------------------------------


def test_sibling_draft_refs_validate_ok_and_strict_fail(tmp_path, production, tools):
    """根引用未发布兄弟草稿:草稿期 validate 通过(非 strict,悬空是 warn);
    strict_refs=True 时悬空 fail——两阶段严格性矩阵。"""
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "lab.child")
    _mk_draft(store, "lab.root", skills=["lab.child"])
    draft = store.read("lab.root")

    soft = validate_draft(draft, production=production, tools=tools, store=store)
    assert soft["gates"]["g2"]["status"] == "pass", "兄弟草稿在草稿层可解析"

    _mk_draft(store, "lab.ghost_root", skills=["lab.ghost"])
    ghost = store.read("lab.ghost_root")
    soft2 = validate_draft(ghost, production=production, tools=tools, store=store)
    assert soft2["gates"]["g2"]["status"] == "warn", "草稿期悬空 = warn(过程状态)"
    assert any("修复" in f["message"] for f in soft2["gates"]["g2"]["findings"])

    strict = validate_draft(ghost, production=production, tools=tools, store=store,
                            strict_refs=True)
    assert strict["gates"]["g2"]["status"] == "fail", "提交期悬空 = fail"


def test_single_promote_guides_to_package(tmp_path, production, tools):
    """单稿 promote 遇未发布兄弟:写入前明确拒绝 + 指路包级提交(§6.2 尾段)。"""
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "lab.child")
    _mk_draft(store, "lab.root", skills=["lab.child"])
    draft = store.read("lab.root")
    report = validate_draft(draft, production=production, tools=tools, store=store)
    report = store.save_gate_report("lab.root", report)
    with pytest.raises(GateError, match="包级提交"):
        promote_draft(store=store, name="lab.root", report_id=report["report_id"],
                      version=None, warnings_ack=False, production=production,
                      tools=tools, principal="user:test")
    # 生产零变化(拒绝发生在写入前)
    data = yaml.safe_load(Path(production.path).read_text(encoding="utf-8"))
    assert not any(e.get("name") == "lab.root" for e in data["skills"])


def test_rerun_error_distinguishes_new_fail(tmp_path, production, tools):
    """复跑错误信息两分:哈希不符 = 报告不一致;哈希相符但新 fail = 判定不一致(非过期)。"""
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "lab.broken2", skills=["lab.ghost"])
    draft = store.read("lab.broken2")
    report = validate_draft(draft, production=production, tools=tools, store=store)
    report = store.save_gate_report("lab.broken2", report)
    with pytest.raises(GateError, match="判定不一致"):
        promote_draft(store=store, name="lab.broken2", report_id=report["report_id"],
                      version=None, warnings_ack=True, production=production,
                      tools=tools, principal="user:test")


# ---------------------------------------------------------------------------
# §6.1 两种闭包
# ---------------------------------------------------------------------------


def test_closure_edit_stops_at_production_runtime_passes(tmp_path, production, tools):
    """edit:生产节点即叶子(不展开它的子树);runtime:穿过同空间生产节点。"""
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "lab.root", skills=["lab.published"])
    edit = compute_closure("lab.root", store, production, tools, mode="edit")
    names = [m["name"] for m in edit["members"]]
    assert names == ["lab.root", "lab.published"], "edit:生产即叶子,不展开"
    runtime = compute_closure("lab.root", store, production, tools, mode="runtime")
    rnames = [m["name"] for m in runtime["members"]]
    assert "lab.published.dep" in rnames, "runtime:穿过生产节点展开"
    with pytest.raises(ValueError):
        compute_closure("lab.root", store, production, tools, mode="bogus")


def test_closure_size_warnings(tmp_path, production, tools):
    """编辑闭包 >8 成员 → warnings(职责没拆好的信号,不硬拦)。"""
    store = DraftStore(tmp_path / "drafts")
    for i in range(9):
        _mk_draft(store, f"lab.m{i}")
    _mk_draft(store, "lab.root", skills=[f"lab.m{i}" for i in range(9)])
    result = compute_closure("lab.root", store, production, tools)
    assert len(result["members"]) == 10
    assert result["warnings"] and ">8" in result["warnings"][0]


# ---------------------------------------------------------------------------
# §6.3 plan:三态 / blockers / hash 一致性
# ---------------------------------------------------------------------------


def _setup_package(tmp_path, production, tools):
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "lab.child")
    _mk_draft(store, "lab.root", skills=["lab.child"])
    return store


def test_plan_actions_and_blockers(tmp_path, production, tools):
    """plan:create/replace 三态;悬空成员进 blockers 带 fix;gate_fail 阻塞。"""
    store = _setup_package(tmp_path, production, tools)
    # lab.published 已在生产且内容将被改成一致 → 先 promote 一次让它进生产
    _mk_draft(store, "lab.published", skills=["lab.published.dep"], prompt="已发布。")
    plan0 = build_plan("lab.published", store=store, production=production, tools=tools)
    promote_package(store=store, plan_id=plan0["plan_id"], warnings_ack=False,
                    production=production, tools=tools, principal="user:test")
    # 内容不动再 plan:unchanged(不重写不 bump)
    plan1 = build_plan("lab.published", store=store, production=production, tools=tools)
    assert plan1["members"][0]["action"] == "unchanged"
    assert plan1["members"][0]["from_version"] == plan1["members"][0]["to_version"]

    plan = build_plan("lab.root", store=store, production=production, tools=tools)
    by_name = {m["name"]: m for m in plan["members"]}
    assert by_name["lab.root"]["action"] == "create"
    assert by_name["lab.child"]["action"] == "create"
    assert plan["package_hash"]
    assert plan["plan_id"].startswith("plan-")
    assert all(m["gate_status"] == "pass" for m in plan["members"])

    # 悬空成员 → blocker 带"一键成稿"fix
    _mk_draft(store, "lab.broken", skills=["lab.ghost"])
    plan2 = build_plan("lab.broken", store=store, production=production, tools=tools)
    assert any(
        b["kind"] == "dangling" and b["fix"] == {"action": "create_draft", "name": "lab.ghost"}
        for b in plan2["blockers"]
    )
    # gate_fail(L2 缺 reversal) → blocker
    _mk_draft(store, "lab.l2", tools_list=["system.file.write"])
    plan3 = build_plan("lab.l2", store=store, production=production, tools=tools)
    assert any(b["kind"] == "gate_fail" and b["member"] == "lab.l2" for b in plan3["blockers"])


def test_promote_package_success_and_hash_guard(tmp_path, production, tools):
    """原子提交:写入 + 单次 reload + 整文件 .bak + promotions 包级记录;
    plan 后改动成员 → package_hash 不一致 409。"""
    store = _setup_package(tmp_path, production, tools)
    plan = build_plan("lab.root", store=store, production=production, tools=tools)

    reloads = {"n": 0}
    orig_reload = production.reload
    def counting_reload():
        reloads["n"] += 1
        return orig_reload()
    production.reload = counting_reload

    result = promote_package(store=store, plan_id=plan["plan_id"], warnings_ack=False,
                             production=production, tools=tools, principal="user:test")
    assert result["reloaded"] is True
    assert reloads["n"] == 1, "单次 reload(原子提交)"
    assert (Path(production.path).parent / "skills.yaml.bak").is_file()
    from agent_os.api.v1 import SkillRef
    assert production.get(SkillRef(name="lab.root")).manifest.name == "lab.root"
    assert production.get(SkillRef(name="lab.child")).manifest.name == "lab.child"
    lines = (store.root / "lab.root" / "gate" / "promotions.jsonl").read_text(encoding="utf-8")
    record = json.loads(lines.strip().splitlines()[-1])
    assert record["kind"] == "package"
    assert record["package_hash"] == plan["package_hash"]

    # plan 后改动任一成员 → 409 语义(GateError)
    store.save("lab.child", manifest={
        "name": "lab.child", "version": "0.1.0", "kind": "prompt",
        "description": "改过了。Use when 测试;Do not use when 其他。",
        "inputs": {"type": "object"}, "outputs": {"type": "object"},
        "permissions": {"tools": [], "skills": []},
    }, prompt="改过了")
    plan2 = build_plan("lab.root", store=store, production=production, tools=tools)
    store.save("lab.child", manifest={
        "name": "lab.child", "version": "0.1.0", "kind": "prompt",
        "description": "又改了。Use when 测试;Do not use when 其他。",
        "inputs": {"type": "object"}, "outputs": {"type": "object"},
        "permissions": {"tools": [], "skills": []},
    }, prompt="又改了")
    with pytest.raises(GateError, match="不一致"):
        promote_package(store=store, plan_id=plan2["plan_id"], warnings_ack=False,
                        production=production, tools=tools, principal="user:test")


def test_promote_package_blockers_and_ack(tmp_path, production, tools):
    """blockers 未清 → 拒;warnings 未 ack → 拒。"""
    store = DraftStore(tmp_path / "drafts")
    _mk_draft(store, "lab.broken", skills=["lab.ghost"])
    plan = build_plan("lab.broken", store=store, production=production, tools=tools)
    with pytest.raises(GateError, match="阻塞项"):
        promote_package(store=store, plan_id=plan["plan_id"], warnings_ack=True,
                        production=production, tools=tools, principal="user:test")

    _mk_draft(store, "lab.warny", prompt="p")
    store.save("lab.warny", manifest={
        "name": "lab.warny", "version": "0.1.0", "kind": "prompt",
        "description": "太短",
        "inputs": {"type": "object"}, "outputs": {"type": "object"},
        "permissions": {"tools": [], "skills": []},
    }, prompt="p")
    plan2 = build_plan("lab.warny", store=store, production=production, tools=tools)
    assert plan2["warnings"]
    with pytest.raises(GateError, match="warnings_ack"):
        promote_package(store=store, plan_id=plan2["plan_id"], warnings_ack=False,
                        production=production, tools=tools, principal="user:test")
    ok = promote_package(store=store, plan_id=plan2["plan_id"], warnings_ack=True,
                         production=production, tools=tools, principal="user:test")
    assert ok["reloaded"] is True


# ---------------------------------------------------------------------------
# §6.4 原子性:先证后换
# ---------------------------------------------------------------------------


def test_atomic_write_staging_failure_zero_change(tmp_path, production):
    """staging 加载验证失败(候选内容依赖不存在)→ 现网文件字节不变。"""
    target = Path(production.path)
    before = target.read_bytes()
    bad_entry = {
        "name": "lab.bad", "version": "0.1.0", "kind": "prompt",
        "description": "坏。Use when x;Do not use when y(凑长度过 lint)。",
        "permissions": {"tools": [], "skills": ["no.such.dep"]},
        "prompt": "坏",
    }
    from agent_os.kernel.errors import SkillLoadError
    with pytest.raises(SkillLoadError):
        _atomic_write(production, {"lab.bad": bad_entry})
    assert target.read_bytes() == before, "先证后换:验证失败现网零变化"
    assert not (target.parent / "skills.yaml.staging").exists(), "staging 已清理"


def test_atomic_write_success_single_bak(tmp_path, production):
    """成功路径:整文件单 .bak、staging 消失、内容已换、reload 后可解析。"""
    target = Path(production.path)
    entry = {
        "name": "lab.new", "version": "0.1.0", "kind": "prompt",
        "description": "新。Use when x;Do not use when y(凑长度过 lint)。",
        "permissions": {"tools": [], "skills": []},
        "prompt": "新",
    }
    _atomic_write(production, {"lab.new": entry})
    assert not (target.parent / "skills.yaml.staging").exists()
    assert (target.parent / "skills.yaml.bak").is_file()
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert any(e["name"] == "lab.new" for e in data["skills"])
    from agent_os.api.v1 import SkillRef
    assert production.get(SkillRef(name="lab.new")).manifest.name == "lab.new"

"""能力包提交计划与原子提交(docs/SKILL-PACKAGES-V2.md §6.3/§6.4;P2)。

两个不变量:
- **审的就是要执行的**(§6.3):plan 面板展示的成员集与将要写入的内容由
  ``package_hash`` 绑定(与单稿 manifest_hash、升权卡片的参数同源哲学);
  任何成员改过一个字节,promote 409。
- **先证后换**(§6.4):候选生产内容先过 loader 全流水线(拓扑/依赖/lint)
  再原子落盘——".bak 兜底"(事后补救)换成"staging 证明"(事前证明);
  任一失败,生产零变化。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import yaml

from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.closure import compute_closure
from agent_os.skills.draft_store import _manifest_to_dict
from agent_os.skills.gate import (
    GateError,
    bump_patch,
    manifest_hash,
    validate_draft,
)
from agent_os.skills.local_file import LocalFileSkillRegistry


def package_hash(members: list[dict[str, Any]]) -> str:
    """包哈希(§6.3):sorted ``name:manifest_hash`` 逐行连接取 sha256 前 16 位。

    成员集本身(name 清单)也在哈希面内——成员变了 hash 就变,与内容变等价处理。
    """
    lines = "\n".join(f"{m['name']}:{m['manifest_hash']}" for m in sorted(members, key=lambda x: x["name"]))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()[:16]


def build_plan(
    root: str,
    *,
    store: Any,
    production: Any,
    tools: Any,
    smoke_runner: Any = None,
) -> dict[str, Any]:
    """生成提交计划(§6.3):成员三态 + blockers + warnings;落盘 ``_plans/``。

    事务成员 = 编辑闭包里的 **draft 节点**(external 是外链非提交对象;
    missing 进 blockers 而非 members)。每个成员过完整闸门(提交期严格档
    strict_refs=True;``smoke_runner`` 为工厂 ``fn(name, draft) -> runner``,
    G4 按成员各自的入口冒烟),action 三态:不在生产 = create;在生产且内容一致 =
    unchanged(**不重写不 bump**);否则 replace。
    """
    closure = compute_closure(root, store, production, tools, mode="edit")
    blockers: list[dict[str, Any]] = [
        {
            "kind": "cycle",
            "member": "",
            "message": e["message"],
            "fix": None,
        }
        for e in closure["errors"]
    ]
    members: list[dict[str, Any]] = []
    for m in closure["members"]:
        if m["status"] in ("external", "production"):
            # 外链与包内已发布都不是事务对象(纯展示):生产节点已过闸已发布,
            # 展开/重写它都没有动作可做(docs/SKILL-PACKAGES-V2.md §6.1)
            continue
        if m["status"] == "missing":
            blockers.append(
                {
                    "kind": "dangling",
                    "member": m["name"],
                    "message": f"悬空引用: {m['ref_by']} 引用了不存在的子技能 {m['name']}",
                    "fix": {"action": "create_draft", "name": m["name"]},
                }
            )
            continue
        draft = store.read(m["name"])
        # G4 按成员各自的入口冒烟(smoke_runner 是工厂:fn(name, draft) → runner)
        runner = smoke_runner(m["name"], draft) if smoke_runner is not None else None
        report = validate_draft(
            draft, production=production, tools=tools,
            smoke_runner=runner, store=store, strict_refs=True,
        )
        report = store.save_gate_report(m["name"], report)
        entry = _entry_of(draft)
        action, from_version, to_version = _action_of(production, m["name"], entry)
        members.append(
            {
                "name": m["name"],
                "action": action,
                "from_version": from_version,
                "to_version": to_version,
                "manifest_hash": manifest_hash(draft),
                "gate_status": report["status"],
                "gate_report_id": report["report_id"],
            }
        )
        if report["status"] == "fail":
            first = next(
                (
                    f["message"]
                    for gate in report["gates"].values()
                    for f in gate.get("findings", [])
                    if f["level"] == "fail"
                ),
                "闸门 fail",
            )
            blockers.append(
                {
                    "kind": "gate_fail",
                    "member": m["name"],
                    "message": first,
                    "fix": None,
                }
            )
    plan = {
        "plan_id": "",
        "root": root,
        "created_at": time.time(),
        "members": members,
        "blockers": blockers,
        "warnings": list(closure["warnings"]),
    }
    if any(m["gate_status"] == "warn" for m in members):
        plan["warnings"].append("存在 warn 成员,提交须人工确认(warnings_ack)")
    plan["package_hash"] = package_hash(members)
    plan["plan_id"] = f"plan-{int(plan['created_at'] * 1000)}-{plan['package_hash']}"
    _save_plan(store, plan)
    return plan


def promote_package(
    *,
    store: Any,
    plan_id: str,
    warnings_ack: bool,
    production: Any,
    tools: Any,
    principal: str,
) -> dict[str, Any]:
    """按 plan 原子提交(§6.3/§6.4):hash 一致性 → blockers → 先证后换 → 单次 reload。

    409 语义与单稿一致:任何成员改过一个字节,旧 plan 作废。
    """
    plan = _read_plan(store, plan_id)
    if plan["blockers"]:
        first = plan["blockers"][0]
        raise GateError(f"存在阻塞项({first['kind']}:{first['member'] or first['message']}),不能提交")
    if plan["warnings"] and not warnings_ack:
        raise GateError("plan 含 warnings,须人工勾选“我已阅读警告”(warnings_ack)")
    # 重算 package_hash(§6.3:审的就是要执行的;任何成员改过一个字节即作废)
    members = []
    for m in plan["members"]:
        draft = store.read(m["name"])
        members.append({"name": m["name"], "manifest_hash": manifest_hash(draft)})
    if package_hash(members) != plan["package_hash"]:
        raise GateError("plan 与当前草稿集不一致:成员在计划后有改动,请重新生成提交计划")

    # 合成待写条目(unchanged 不重写,§6.5;to_version 已在 plan 期定好)
    entries: dict[str, dict[str, Any]] = {}
    previous: list[dict[str, Any]] = []
    for m in plan["members"]:
        if m["action"] == "unchanged":
            continue
        draft = store.read(m["name"])
        entry = _entry_of(draft)
        entry["version"] = m["to_version"]
        entries[m["name"]] = entry
        old = _production_entry(production, m["name"])
        if old is not None:
            previous.append(old)
    _atomic_write(production, entries)

    record = {
        "kind": "package",
        "root": plan["root"],
        "plan_id": plan_id,
        "package_hash": plan["package_hash"],
        "promoted_by": principal,
        "at": time.time(),
        "members": [
            {
                "name": m["name"],
                "action": m["action"],
                "from_version": m["from_version"],
                "to_version": m["to_version"],
                "manifest_hash": m["manifest_hash"],
            }
            for m in plan["members"]
        ],
        "previous_entries": previous,  # §6.5:语义级回滚面(被覆盖成员的完整旧条目)
    }
    store.record_promotion(plan["root"], record)
    return {
        "root": plan["root"],
        "package_hash": plan["package_hash"],
        "members": record["members"],
        "reloaded": True,
        "promoted_by": principal,
    }


# ----------------------------------------------------------------------
# 先证后换(§6.4)
# ----------------------------------------------------------------------


def _atomic_write(registry: Any, entries: dict[str, dict[str, Any]]) -> None:
    """候选合并 → staging 全量加载验证 → 整文件单 .bak → 原子 rename → 单次 reload。

    任一步失败,现网一字节未动(staging 删除即还原);reload 失败从 .bak 回滚。
    生产面仍限单文件 skills.yaml(目录/多文件形态属 P4)。
    """
    path = getattr(registry, "path", None)
    if not isinstance(path, str) or Path(path).is_dir():
        raise SkillLoadError(
            "包提交目前只支持单文件 skills.yaml(目录/多文件形态见 docs/SKILL-DEV.md §4 L2 实现注)"
        )
    target = Path(path)
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    current = list(data.get("skills") or [])
    by_name = {e.get("name"): i for i, e in enumerate(current) if isinstance(e, dict)}
    for name, entry in entries.items():
        if name in by_name:
            current[by_name[name]] = entry
        else:
            current.append(entry)
    data["skills"] = current

    staging = target.with_name(target.name + ".staging")
    try:
        staging.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        # 先证:候选内容过 loader 全流水线(manifest 解析/拓扑/依赖存在性/lint)——
        # 这是唯一能证明"生产装得起来"的方法,失败则整体不写(§6.4)
        LocalFileSkillRegistry(str(staging))
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    shutil.copy2(target, target.with_name(target.name + ".bak"))  # 一次事务一份整文件备份
    os.replace(staging, target)  # 同目录 rename,原子
    try:
        registry.reload()
    except Exception:
        # 理论上不该发生(staging 已证可装):回滚 + 再 reload,不让生产停在坏态
        shutil.copy2(target.with_name(target.name + ".bak"), target)
        registry.reload()
        raise


# ----------------------------------------------------------------------
# 内部:条目/动作/plan 存取
# ----------------------------------------------------------------------


def _entry_of(draft: dict[str, Any]) -> dict[str, Any]:
    """草稿 → 生产条目(与单稿 promote 同构:prompt.md 内联)。"""
    entry = dict(draft["manifest"] or {})
    entry["name"] = draft["name"]
    if draft.get("prompt"):
        entry["prompt"] = draft["prompt"]
    return entry


def _production_entry(production: Any, name: str) -> dict[str, Any] | None:
    """生产 registry 里同名条目的 manifest 字典形态(无 → None;回滚记录用)。"""
    try:
        path = getattr(production, "path", None)
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for e in data.get("skills") or []:
            if isinstance(e, dict) and e.get("name") == name:
                return e
    except (OSError, yaml.YAMLError):
        return None
    return None


def _action_of(production: Any, name: str, entry: dict[str, Any]) -> tuple[str, str | None, str]:
    """三态(§6.3/§6.5):不在生产 = create;在生产且内容一致(除 version)=
    unchanged(不重写不 bump);否则 replace(bump patch)。"""
    from agent_os.api.v1 import SkillRef

    try:
        current = production.get(SkillRef(name=name)).manifest
    except Exception:  # noqa: BLE001 — 不存在即 create
        return "create", None, entry.get("version") or "0.1.0"
    from_version = current.version or "0.1.0"
    old = {k: v for k, v in _manifest_to_dict(current).items() if k != "version"}
    if current.prompt:
        old["prompt"] = current.prompt  # _manifest_to_dict 不含指令体,比较面手动并入
    new = {k: v for k, v in entry.items() if k != "version"}
    # 内容一致(除 version)则 unchanged——不重写不 bump(§6.5 变更才抬版本)
    if json.dumps(old, ensure_ascii=False, sort_keys=True) == json.dumps(
        new, ensure_ascii=False, sort_keys=True
    ):
        return "unchanged", from_version, from_version
    return "replace", from_version, bump_patch(from_version)


def _plans_dir(store: Any) -> Path:
    d = Path(store.root) / "_plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_plan(store: Any, plan: dict[str, Any]) -> None:
    (_plans_dir(store) / f"{plan['plan_id']}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_plan(store: Any, plan_id: str) -> dict[str, Any]:
    path = _plans_dir(store) / f"{plan_id}.json"
    if not path.is_file():
        raise GateError(f"找不到提交计划: {plan_id}(请重新生成)")
    return json.loads(path.read_text(encoding="utf-8"))

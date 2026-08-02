"""提交闸门(docs/SKILL-DEV.md §1.4;L2)。

五关结构,报告落盘 ``drafts/<name>/gate/<ts>.json``;判定 ``pass|warn|fail``
(G4 冒烟试跑 / G5 提示词卫生属 L3/L5,本期 ``skip`` 占位,结构先稳定)。
G3 是 ESCALATION E3 的汇合点:``reversal``/``blast_radius` 必填 lint 在此落地
(此前只有 docs/TIER-STANDARDS.md §4/§5 的设计,没有强制点)。

判关哲学(§2.4):**编辑器不打断,闸门守出口**——本模块是唯一的判定点,
validate 与 promote 共用;promote 前复跑 G1-G3(防报告过期/篡改,G4/G5 信报告)。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from agent_os.api.v1 import (
    SkillRef,
    explain_skill_tier,
    tier_rank,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.draft_store import OverlaySkillRegistry
from agent_os.skills.loader import materialize
from agent_os.skills.manifest import parse_manifest, validate_manifest

#: 五关 id 与条款锚点(findings.clause 给 UI 链到 docs/TIER-STANDARDS.md 等)
GATES = ("g1", "g2", "g3", "g4", "g5")

#: version 语义化(语义化版本三段;非语义化版本 G1 warn,promote 会重写)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def manifest_hash(draft: dict[str, Any]) -> str:
    """草稿内容指纹(manifest+prompt+handler 规范化 JSON 的 sha1 前 16 位)。

    报告与内容错位防护(§1.4):validate 落报告时记哈希,promote 比对——
    草稿改过一个字节,旧报告就作废。
    """
    canonical = json.dumps(
        {
            "manifest": draft.get("manifest"),
            "prompt": draft.get("prompt") or "",
            "handler": draft.get("handler") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def _finding(level: str, clause: str, message: str) -> dict[str, str]:
    return {"level": level, "clause": clause, "message": message}


def _status_of(findings: list[dict[str, str]]) -> str:
    """关的汇总档:任一 fail → fail;否则任一 warn → warn;否则 pass。"""
    levels = {f["level"] for f in findings}
    if "fail" in levels:
        return "fail"
    if "warn" in levels:
        return "warn"
    return "pass"


def validate_draft(draft: dict[str, Any], *, production: Any, tools: Any) -> dict[str, Any]:
    """跑提交闸门(§1.4 五关),返回报告 dict(不落盘;落盘见 DraftStore)。

    ``draft`` 为 :meth:`DraftStore.read` 的形态(含 parse_error 容错);
    ``production``/``tools`` 是生产 skills/tools registry(推导档与引用检查用)。
    """
    gates: dict[str, Any] = {}
    raw = draft.get("manifest") if isinstance(draft.get("manifest"), dict) else None
    parse_error = draft.get("parse_error")

    # —— 解析一份打过 prompt 补丁的 manifest(G1 lint / 推导档共用)——
    manifest = None
    tier_detail: dict[str, Any] | None = None
    tier_error: str | None = None
    if raw is not None and parse_error is None:
        try:
            manifest = parse_manifest(raw)
            if draft.get("prompt"):
                manifest.prompt = draft["prompt"]  # prompt.md 即指令体(§1.2),G1 不再误报缺失
            overlay = OverlaySkillRegistry(production, _SingleDraftStore(draft))
            tier_detail = explain_skill_tier(manifest, tools, overlay)
        except (SkillLoadError, ValueError) as e:
            tier_error = str(e)

    # —— G1 metadata(现状 lint:docs/NAMING.md 层级 / 路由式 description / 语义化 version)——
    g1: list[dict[str, str]] = []
    name = str((raw or {}).get("name") or draft.get("name") or "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+", name):
        g1.append(_finding("fail", "NAMING.md §2", f"name {name!r} 不合层级命名规范"))
    version = str((raw or {}).get("version") or "")
    if not _SEMVER_RE.match(version):
        g1.append(_finding("warn", "SKILL-DEV §1.4 G1", f"version {version!r} 非语义化(x.y.z)"))
    desc = str((raw or {}).get("description") or "")
    if len(desc) < 10 or "Use when" not in desc:
        g1.append(
            _finding(
                "warn",
                "DESIGN.md §6.1 lint",
                "description 应含 'Use when / Do not use when' 触发条件",
            )
        )
    if manifest is not None:
        # 现状自洽 lint(inline 纯度硬闸/prompt 缺失告警等),并入 G1 报告面
        try:
            for warning in validate_manifest(manifest):
                g1.append(_finding("warn", "validate_manifest", warning))
        except SkillLoadError as e:
            g1.append(_finding("fail", "validate_manifest", str(e)))
    gates["g1"] = {"status": _status_of(g1), "findings": g1}

    # —— G2 契约(inputs/outputs 合法 JSON Schema;L2+ 每参数有 type)——
    g2: list[dict[str, str]] = []
    if parse_error is not None or raw is None:
        g2.append(
            _finding("fail", "SKILL-DEV §1.2", f"草稿暂不可解析: {parse_error or 'manifest 缺失'}")
        )
    else:
        for field in ("inputs", "outputs"):
            schema = raw.get(field) or {}
            if not isinstance(schema, dict):
                g2.append(_finding("fail", "DESIGN.md §2.1", f"{field} 必须是 JSON Schema dict"))
                continue
            try:
                jsonschema.validators.validator_for(schema).check_schema(schema)
            except jsonschema.SchemaError as e:
                g2.append(_finding("fail", "JSON Schema", f"{field} 不是合法 JSON Schema: {e}"))
        if tier_detail is not None and tier_rank(tier_detail["tier"]) >= 1:
            # L2+ 高层 skill 不收自由文本参数(docs/ESCALATION.md §3 原则 1 的强制面)
            for prop, spec in (raw.get("inputs") or {}).get("properties", {}).items():
                if not isinstance(spec, dict) or "type" not in spec:
                    g2.append(
                        _finding(
                            "fail",
                            "TIER-STANDARDS.md §2",
                            f"L2+ 技能的 inputs 参数 {prop!r} 必须有 type",
                        )
                    )
    gates["g2"] = {"status": _status_of(g2), "findings": g2}

    # —— G3 分档合规(docs/ESCALATION.md §2.1/§3.4 + docs/TIER-STANDARDS.md §4/§5)——
    g3: list[dict[str, str]] = []
    if tier_detail is None:
        g3.append(
            _finding("fail", "ESCALATION.md §2.1", f"推导档计算失败: {tier_error or 'manifest 不可解析'}")
        )
    else:
        tier = tier_detail["tier"]
        for source in tier_detail["sources"]:
            g3.append(
                _finding("info", "ESCALATION.md §2.1", f"{source['kind']} {source['name']}: {source['tier']}")
            )
        trust = (raw or {}).get("trust") or {}
        if tier != "none" and (raw or {}).get("inline"):
            g3.append(
                _finding("fail", "ESCALATION.md §3.4", f"推导档 {tier} ≥L2 禁止 inline: true")
            )
        if tier == "irreversible" and trust.get("confirm") == "first":
            g3.append(
                _finding("fail", "ESCALATION.md §2.1", "推导档 L3 禁止 confirm: first(不可逆操作不批量的硬规则)")
            )
        if tier == "reversible" and not str(trust.get("reversal") or "").strip():
            g3.append(
                _finding("fail", "TIER-STANDARDS.md §4", "L2 必填 trust.reversal(逆转/补偿机制)")
            )
        if tier == "irreversible" and not str(trust.get("blast_radius") or "").strip():
            g3.append(
                _finding("fail", "TIER-STANDARDS.md §5", "L3 必填 trust.blast_radius(最坏影响面)")
            )
    gates["g3"] = {"status": _status_of(g3), "findings": g3}

    # —— G4/G5:占位(L3/L5;结构先稳定,卡片渲染灰档)——
    gates["g4"] = {"status": "skip", "note": "冒烟试跑,L3 实现", "findings": []}
    gates["g5"] = {"status": "skip", "note": "提示词卫生,L5 实现", "findings": []}

    overall = "pass"
    if any(gates[g]["status"] == "fail" for g in ("g1", "g2", "g3")):
        overall = "fail"
    elif any(gates[g]["status"] == "warn" for g in ("g1", "g2", "g3")):
        overall = "warn"
    return {
        "report_id": "",  # 落盘时由 DraftStore 赋值(ts + hash)
        "draft": str(draft.get("name") or ""),
        "created_at": time.time(),
        "manifest_hash": manifest_hash(draft),
        "tier": (tier_detail or {}).get("tier"),
        "status": overall,
        "gates": gates,
    }


class _SingleDraftStore:
    """把单个草稿伪装成 DraftStore(overlay 的 skills 参数只看 get(),§1.1)。"""

    def __init__(self, draft: dict[str, Any]) -> None:
        self._draft = draft

    def load_skill(self, name: str) -> Any:
        if name == self._draft.get("name"):
            manifest = parse_manifest(self._draft["manifest"])
            if self._draft.get("prompt"):
                manifest.prompt = self._draft["prompt"]
            return materialize(manifest)
        raise FileNotFoundError(name)


# ----------------------------------------------------------------------
# promote(§2.3 流程 5;闸门通过后的唯一生产通道)
# ----------------------------------------------------------------------


def bump_patch(version: str) -> str:
    """patch 位 +1;非语义化版本回落 0.1.0(无法 bump 就不假装能 bump)。"""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not m:
        return "0.1.0"
    return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"


def default_version(registry: Any, name: str) -> str:
    """缺省版本:生产已有同名 → 在其版本上 bump patch;否则 0.1.0。"""
    try:
        existing = registry.get(SkillRef(name=name)).manifest.version
    except Exception:  # noqa: BLE001 — 不存在即新技能
        return "0.1.0"
    return bump_patch(existing)


class GateError(RuntimeError):
    """闸门拒绝(报告过期/含 fail/warn 未确认;路由层归 409 语义)。"""


def promote_draft(
    *,
    store: Any,
    name: str,
    report_id: str,
    version: str | None,
    warnings_ack: bool,
    production: Any,
    tools: Any,
    principal: str,
) -> dict[str, Any]:
    """promote(docs/SKILL-DEV.md §2.3 流程 5):闸门报告核验 → 复跑 G1-G3 → 写生产。

    校验链(防篡改/防过期):报告必须存在 → 报告哈希 == 当前草稿哈希(草稿改过
    一字节旧报告即作废)→ 报告无 fail → **复跑 G1-G3** 仍无 fail(G4/G5 信报告)
    → 有 warn 必须 ``warnings_ack``。通过后写生产 skills.yaml(.bak 备份)、
    loader reload()、promotions.jsonl 落 provenance 记录。
    """
    draft = store.read(name)
    report = store.read_gate_report(name, report_id)
    if report.get("manifest_hash") != manifest_hash(draft):
        raise GateError("报告与当前草稿不一致:草稿在上次检查后有改动,请重新运行检查")
    if report.get("status") == "fail":
        raise GateError("闸门报告含 fail,不能 promote——先修红关再提交")
    # 复跑 G1-G3(§1.4:防报告过期/篡改;G4/G5 信报告)
    fresh = validate_draft(draft, production=production, tools=tools)
    for gate_id in ("g1", "g2", "g3"):
        if fresh["gates"][gate_id]["status"] == "fail":
            first = next(
                (f["message"] for f in fresh["gates"][gate_id]["findings"] if f["level"] == "fail"),
                "",
            )
            raise GateError(f"复跑 {gate_id} 出现 fail(报告已过期): {first}")
    has_warn = report.get("status") == "warn" or any(
        fresh["gates"][g]["status"] == "warn" for g in ("g1", "g2", "g3")
    )
    if has_warn and not warnings_ack:
        raise GateError("闸门存在 warn,须人工勾选“我已阅读警告”(warnings_ack)后才能 promote")

    final_version = version or default_version(production, name)
    entry = dict(draft["manifest"] or {})
    entry["name"] = name
    entry["version"] = final_version
    if draft.get("prompt"):
        entry["prompt"] = draft["prompt"]  # 单文件形态:指令体内联进条目(§6.3)
    action = write_production_entry(production, entry)
    production.reload()  # 生产热重载(§6.1 现状先例;只影响后续新建的 run)
    record = {
        "promoted_by": principal,
        "gate_report_id": report_id,
        "version": final_version,
        "action": action,
        "at": time.time(),
    }
    store.record_promotion(name, record)  # 草稿保留可再迭代(§1.4 归档语义)
    return {
        "name": name,
        "version": final_version,
        "action": action,
        "reloaded": True,
        "promoted_by": principal,
        "gate_report_id": report_id,
    }


def write_production_entry(registry: Any, entry: dict[str, Any]) -> str:
    """把草稿写进生产 skills.yaml(追加或替换同名条;写前备份 ``.bak``)。

    返回 ``"appended" | "replaced"``。生产面是**单文件** skills.yaml 才支持
    (目录/多文件形态的归并策略留给 L5 打磨);registry  duck-typed
    (``path``/``reload()``,LocalFileSkillRegistry 即满足)。
    """
    path = getattr(registry, "path", None)
    if not isinstance(path, str) or Path(path).is_dir():
        raise SkillLoadError(
            "promote 目前只支持单文件 skills.yaml(目录/多文件形态见 docs/SKILL-DEV.md §4 L2 实现注)"
        )
    target = Path(path)
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    entries = data.get("skills") or []
    action = "appended"
    for index, old in enumerate(entries):
        if isinstance(old, dict) and old.get("name") == entry["name"]:
            entries[index] = entry
            action = "replaced"
            break
    else:
        entries.append(entry)
    data["skills"] = entries
    shutil.copy2(target, target.with_name(target.name + ".bak"))
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return action

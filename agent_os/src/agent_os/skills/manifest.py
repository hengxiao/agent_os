"""SkillManifest 解析与校验(DESIGN.md §6.1;M2)。

``yaml.safe_load`` → manifest 校验(schema/权限引用存在/description lint:
描述应含 "Use when / Do not use when" + 负例,过短或无触发条件 → 警告不阻断)。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import (
    ContextPolicy,
    ModelPolicy,
    SkillKind,
    SkillLimits,
    SkillManifest,
    SkillPermissions,
)
from agent_os.kernel.errors import SkillLoadError


def parse_manifest(data: dict[str, Any]) -> SkillManifest:
    """dict → SkillManifest(字段逐字对齐 §2.1,含 verifier/permissions/model/context_policy/limits/logic)。"""
    if not isinstance(data, dict):
        raise SkillLoadError(f"技能条目必须是 mapping,得到: {type(data).__name__}")
    name = data.get("name")
    if not name or not isinstance(name, str):
        raise SkillLoadError(f"技能缺合法 name 字段: {data!r}")
    kind_raw = data.get("kind", "prompt")
    try:
        kind = SkillKind(kind_raw)
    except ValueError:
        raise SkillLoadError(f"技能 {name}: 未知 kind {kind_raw!r}(应为 prompt|code)") from None
    inputs = data.get("inputs") or {}
    outputs = data.get("outputs") or {}
    if not isinstance(inputs, dict) or not isinstance(outputs, dict):
        raise SkillLoadError(f"技能 {name}: inputs/outputs 必须是 JSON Schema dict")
    perms = data.get("permissions") or {}
    permissions = SkillPermissions(
        tools=list(perms.get("tools") or []),
        skills=list(perms.get("skills") or []),
        blackboard=list(perms.get("blackboard") or []),
    )
    model_raw = data.get("model")
    model = (
        ModelPolicy(prefer=list(model_raw.get("prefer") or []), temperature=model_raw.get("temperature"))
        if model_raw
        else None
    )
    cp_raw = data.get("context_policy")
    context_policy = (
        ContextPolicy(max_tokens=cp_raw.get("max_tokens"), compress=cp_raw.get("compress", "hierarchical"))
        if cp_raw
        else None
    )
    lim_raw = data.get("limits")
    limits = (
        SkillLimits(
            max_steps=lim_raw.get("max_steps"),
            timeout=lim_raw.get("timeout"),
            retry=lim_raw.get("retry", 0),
        )
        if lim_raw
        else None
    )
    return SkillManifest(
        name=name,
        version=str(data.get("version", "")),
        kind=kind,
        description=data.get("description", ""),
        inputs=inputs,
        outputs=outputs,
        verifier=data.get("verifier"),
        permissions=permissions,
        model=model,
        context_policy=context_policy,
        limits=limits,
        entry=data.get("entry"),
        prompt=data.get("prompt"),
        handler=data.get("handler"),
        logic=data.get("logic"),
        inline=bool(data.get("inline", False)),
    )


#: 内联(merge)技能 prompt 的膨胀上限(SKILL-INLINING.md §3.3;C++ "拒绝内联大函数"对应物)
INLINE_PROMPT_MAX_CHARS = 500

#: 单个调用方的 merge 依赖条数上限(超过告警;调用方侧检查在 Registry 层)
INLINE_DEPS_MAX = 3


def validate_manifest(manifest: SkillManifest) -> list[str]:
    """加载期校验;返回告警列表(错误直接抛出)。权限闸门:声明的工具/子技能必须存在

    且权限等级不超过 RunConfig 上限,否则拒绝加载并报出具体缺失项(§6.1)。

    本函数只做 manifest 自洽性 lint;跨技能引用与工具存在性由 Registry/Builder 检查。
    内联(merge)技能另有硬闸门与 lint(SKILL-INLINING.md §3.2/§3.3)。
    """
    warnings: list[str] = []
    desc = manifest.description
    if manifest.kind is SkillKind.PROMPT and not manifest.prompt and not manifest.entry:
        warnings.append(f"技能 {manifest.name}: prompt 技能缺指令体(prompt/entry)")
    if len(desc) < 10 or "Use when" not in desc:
        warnings.append(
            f"技能 {manifest.name}: description 应含 'Use when / Do not use when' 触发条件(§6.1 lint)"
        )
    if manifest.inline:
        warnings.extend(_validate_inline(manifest))
    return warnings


def _validate_inline(manifest: SkillManifest) -> list[str]:
    """merge 技能的硬闸门(抛 SkillLoadError)与 lint(返回告警)。

    硬闸门(SKILL-INLINING.md §3.2):仅 prompt 技能;纯度(tools/skills/blackboard
    全空——指令并入后这些权限无法执行,声明即矛盾);prompt 非空。
    """
    name = manifest.name
    if manifest.kind is not SkillKind.PROMPT:
        raise SkillLoadError(f"技能 {name}: inline 仅适用于 prompt 技能(merge 无 code 形态)")
    perms = manifest.permissions
    impure = [
        label
        for label, values in (
            ("tools", perms.tools),
            ("skills", perms.skills),
            ("blackboard", perms.blackboard),
        )
        if values
    ]
    if impure:
        raise SkillLoadError(
            f"技能 {name}: inline 要求纯度——permissions.{'/'.join(impure)} 必须为空"
            "(指令并入调用方后无法执行这些权限)"
        )
    if not manifest.prompt:
        raise SkillLoadError(f"技能 {name}: inline 技能必须有非空 prompt(单文件形态)")

    warnings: list[str] = []
    if "{" in manifest.prompt:
        warnings.append(
            f"技能 {name}: inline prompt 含 '{{' ——merge 无离散 input 可渲染,"
            "应写成说明书形态(占位符会原样暴露给模型)"
        )
    if len(manifest.prompt) > INLINE_PROMPT_MAX_CHARS:
        warnings.append(
            f"技能 {name}: inline prompt 过长({len(manifest.prompt)} > "
            f"{INLINE_PROMPT_MAX_CHARS} 字符)——指令常驻调用方 SYSTEM,每步都付其 token"
        )
    dead = [
        label
        for label, value in (
            ("inputs", manifest.inputs),
            ("outputs", manifest.outputs),
            ("model", manifest.model),
            ("context_policy", manifest.context_policy),
        )
        if value
    ]
    if dead:
        warnings.append(
            f"技能 {name}: inline 技能的 {'/'.join(dead)} 无运行期效力(merge 档仅作文档)"
        )
    if manifest.verifier:
        warnings.append(
            f"技能 {name}: inline 技能的 verifier 无运行期效力(其语义绑定帧弹栈仲裁,merge 无帧)"
        )
    return warnings

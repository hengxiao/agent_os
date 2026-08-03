"""能力包闭包计算(docs/SKILL-PACKAGES.md §2/§4.1;P1)。

**包不是新实体,是根技能的依赖闭包**(推导,不声明——与推导档同一哲学:
能算出来的就不让人维护)。``compute_closure`` 是包视图(Lab 包面板)、
包级闸门(G2 引用完整性)、原子提交(P2)的共同数据源。

成员状态四态:
- ``draft``:草稿层命中(开发中,包的编辑面);
- ``production``:生产命中且与根同第一段命名空间(已发布,**edit 模式也是叶子**);
- ``external``:生产命中但**跨命名空间**(外链只读,不算编辑面,不再下传);
- ``missing``:引用查无此名(悬空,不再下传——G2 判 fail 的对象)。

两种闭包(docs/SKILL-PACKAGES-V2.md §6.1;P2):``mode="edit"``(缺省)只有
draft 节点下传——判据是"还在不在你的编辑面",不是命名巧合;``mode="runtime"``
全展开(穿过同空间生产节点),供覆盖率/环检测/体积分析。

环的语义沿用 loader(docs/DESIGN.md §6.3):自引用是合法递归(剔边),
经他人回边才记 ``errors``(kind="cycle";G2 判 fail)。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import SkillRef, derive_skill_tier
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.compound import CompoundSkillRegistry
from agent_os.skills.draft_store import DraftSkillRegistry


def compute_closure(
    root_name: str,
    drafts: Any,
    production: Any,
    tools: Any = None,
    *,
    mode: str = "edit",
) -> dict[str, Any]:
    """从根技能(草稿优先)传递闭包 permissions.skills,返回成员表。

    ``drafts``:DraftStore(``load_skill`` 语义);``production``:SkillRegistry
    (``get(SkillRef)`` 语义);``tools``:Tool Registry(推导档用,None → 全按 none)。
    ``mode``(docs/SKILL-PACKAGES-V2.md §6.1;P2):
    - ``"edit"``(缺省,编辑闭包):**只有 draft 节点下传**;production/external
      一律为叶子(纯显示标签:同空间 = 包内已发布,跨空间 = 外部依赖)——
      生产节点已过闸已发布,展开它只有闭包膨胀(§2.3 命名巧合的替代判据);
    - ``"runtime"``(运行闭包):全展开(穿过同空间生产节点),供覆盖率/环检测/
      体积分析。
    返回 ``{root, root_status, root_tier, members, errors, warnings}``;成员含
    ``{name, ref_by, status, tier, depth}``。编辑闭包 >8 成员或深度 >4 时
    warnings 提示(职责没拆好的信号,不硬拦)。根自身查无此名 → FileNotFoundError。
    """
    if mode not in ("edit", "runtime"):
        raise ValueError(f"mode 应为 'edit' | 'runtime',得到: {mode!r}")
    root_seg = root_name.split(".")[0]
    # 推导档的 skills 视图 = 草稿优先的两层组合(P1.5:与装配层同一份解析语义,
    # 不再自带一个只有 get 的私有 overlay,docs/SKILL-PACKAGES-V2.md §6.10)
    tier_skills = CompoundSkillRegistry(
        [DraftSkillRegistry(drafts), production], names=["draft", "production"]
    )

    def resolve(name: str) -> tuple[str, Any]:
        """四态解析(草稿 → 生产同空间 → 生产跨空间 → 悬空)。"""
        try:
            return "draft", drafts.load_skill(name).manifest
        except (FileNotFoundError, SkillLoadError, ValueError):
            pass
        try:
            manifest = production.get(SkillRef(name=name)).manifest
            seg = name.split(".")[0]
            return ("production" if seg == root_seg else "external"), manifest
        except SkillLoadError:
            return "missing", None

    def descends(status: str) -> bool:
        """下传规则(§6.1):edit 只穿草稿;runtime 另加同空间生产。"""
        if status == "draft":
            return True
        return mode == "runtime" and status == "production"

    root_status, root_manifest = resolve(root_name)
    if root_manifest is None:
        raise FileNotFoundError(f"根技能不存在(草稿与生产均无): {root_name}")

    members: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()  # 菱形合并(先达者持有 ref_by)

    def visit(name: str, ref_by: str | None, depth: int, path: frozenset[str]) -> None:
        if name in path:
            # 经他人回边(自引用已在入边剔除):loader 同口径记环,不再下传。
            # 必须先于 seen 检查——回边目标必然已 seen,顺序反了环会被菱形合并吞掉
            errors.append(
                {
                    "kind": "cycle",
                    "message": f"循环依赖: {' → '.join([*path, name])}",
                }
            )
            return
        if name in seen:
            return  # 菱形合并(先达者持有 ref_by)
        status, manifest = resolve(name)
        tier = (
            derive_skill_tier(manifest, tools, tier_skills)
            if manifest is not None
            else None
        )
        members.append(
            {
                "name": name,
                "ref_by": ref_by,
                "status": status,
                "tier": tier,
                "depth": depth,
            }
        )
        seen.add(name)
        if manifest is None or not descends(status):
            return  # 悬空永远叶子;edit 模式生产/外链也一律叶子(§6.1)
        for dep in manifest.permissions.skills:
            if dep == name:
                continue  # 自引用 = 合法递归(docs/DESIGN.md §6.3,剔边)
            visit(dep, name, depth + 1, path | {name})

    visit(root_name, None, 0, frozenset())
    warnings: list[str] = []
    if len(members) > 8:
        warnings.append(
            f"闭包 {len(members)} 个成员(>8):职责可能没拆好(docs/SKILL-PACKAGES-V2.md §6.1)"
        )
    max_depth = max((m["depth"] for m in members), default=0)
    if max_depth > 4:
        warnings.append(f"闭包深度 {max_depth}(>4):依赖链过长,建议拆层")
    return {
        "root": root_name,
        "root_status": root_status,
        "root_tier": members[0]["tier"],
        "members": members,
        "errors": errors,
        "warnings": warnings,
    }



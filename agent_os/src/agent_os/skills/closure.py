"""能力包闭包计算(docs/SKILL-PACKAGES.md §2/§4.1;P1)。

**包不是新实体,是根技能的依赖闭包**(推导,不声明——与推导档同一哲学:
能算出来的就不让人维护)。``compute_closure`` 是包视图(Lab 包面板)、
包级闸门(G2 引用完整性)、原子提交(P2)的共同数据源。

成员状态四态:
- ``draft``:草稿层命中(开发中,包的编辑面);
- ``production``:生产命中且与根同第一段命名空间(已发布,继续下传它的引用);
- ``external``:生产命中但**跨命名空间**(外链只读,不算编辑面,不再下传);
- ``missing``:引用查无此名(悬空,不再下传——G2 判 fail 的对象)。

环的语义沿用 loader(docs/DESIGN.md §6.3):自引用是合法递归(剔边),
经他人回边才记 ``errors``(kind="cycle";G2 判 fail)。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import SkillRef, derive_skill_tier
from agent_os.kernel.errors import SkillLoadError


def compute_closure(
    root_name: str,
    drafts: Any,
    production: Any,
    tools: Any = None,
) -> dict[str, Any]:
    """从根技能(草稿优先)传递闭包 permissions.skills,返回成员表。

    ``drafts``:DraftStore(``load_skill`` 语义);``production``:SkillRegistry
    (``get(SkillRef)`` 语义);``tools``:Tool Registry(推导档用,None → 全按 none)。
    返回 ``{root, root_tier, members, errors}``;成员含
    ``{name, ref_by, status, tier, depth}``(depth 从 0 起,供缩进渲染)。
    根自身也查无此名 → FileNotFoundError(路由层归 404)。
    """
    root_seg = root_name.split(".")[0]

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
            derive_skill_tier(manifest, tools, _Overlay(drafts, production))
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
        if status in ("external", "missing") or manifest is None:
            return  # 外链只读/悬空:不再下传(§2 决定 2/断点 1.1-B)
        for dep in manifest.permissions.skills:
            if dep == name:
                continue  # 自引用 = 合法递归(docs/DESIGN.md §6.3,剔边)
            visit(dep, name, depth + 1, path | {name})

    visit(root_name, None, 0, frozenset())
    return {
        "root": root_name,
        "root_status": root_status,
        "root_tier": members[0]["tier"],
        "members": members,
        "errors": errors,
    }


class _Overlay:
    """闭包内推导档的 skills 视图(草稿优先;与 OverlaySkillRegistry 同语义,

    但闭包计算只需要 ``get`` ——不引入装配层的完整协议面。
    """

    def __init__(self, drafts: Any, production: Any) -> None:
        self._drafts = drafts
        self._production = production

    def get(self, ref: SkillRef) -> Any:
        try:
            return self._drafts.load_skill(ref.name)
        except (FileNotFoundError, SkillLoadError, ValueError):
            return self._production.get(ref)

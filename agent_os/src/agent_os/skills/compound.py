"""注册表分层组合(docs/SKILL-PACKAGES-V2.md §6.10;P1.5)。

**分层解析,不改名**:草稿与生产同名,靠**顺序解析 + 遮蔽**区分,层来源随
解析结果返回(:meth:`CompoundSkillRegistry.resolve`),不进标识符——给草稿
加命名空间前缀会制造"发布时改名",进而打穿跨草稿引用与推导档等价性
(理由三条见 V2 §6.10)。

重构动机:同一套"草稿优先"语义此前有三处实现(OverlaySkillRegistry /
closure._Overlay / gate._SingleDraftStore),且已经产生差异(前者处理 extra 槽,
后者不处理)。本模块把它收敛为一处。

两条容易丢掉的语义,实现时明写:

1. **不是纯 shadowing,是 try-and-continue**:前层抛 :class:`SkillLoadError`
   (草稿存了一半)时**继续往后找**,而不是失败——"半成品影子不遮蔽可用版本"
   (docs/SKILL-DEV.md §1.1)。只有最后一层的错误才向上抛。
2. **写入面不在协议里**:``reload()`` / ``path`` 是 gate.write_production_entry
   duck-type 摸的,``manifests()`` 也是协议外的事实扩展。组合层必须**显式指定
   哪一层可写**(``writable``),否则会出现"promote 写进了草稿层"。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import (
    Skill,
    SkillCall,
    SkillFrame,
    SkillManifest,
    SkillRef,
    SkillSchema,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.local_file import build_child_frame

#: 层内"没有这个技能"的信号集合(草稿目录不存在 / 存了一半 / 名字不合规)。
#: 三者都表示"本层给不出",都应触发向后继续解析,而不是让整条链失败。
MISS = (FileNotFoundError, SkillLoadError, ValueError)


class StaticSkillRegistry:
    """内存中的 ``{name: Skill}`` 一层(如 skill.dev.assistant meta-skill)。

    此前是 OverlaySkillRegistry 的 ``extra`` 特例参数;组合化之后它就是链上
    一个普通的层,不再需要特例分支。
    """

    def __init__(self, skills: dict[str, Skill] | None = None) -> None:
        self._skills = dict(skills or {})

    def get(self, ref: SkillRef) -> Skill:
        try:
            return self._skills[ref.name]
        except KeyError as e:
            raise SkillLoadError(f"技能不存在: {ref.name}") from e

    def manifests(self) -> list[SkillManifest]:
        return [s.manifest for s in self._skills.values()]


class CompoundSkillRegistry:
    """按**列表顺序**解析的多层 registry(``layers[0]`` 优先)。

    ``layers``:每层只需满足 ``get(SkillRef) -> Skill``;有 ``manifests()`` 的层
    参与清单合并,没有的跳过。``writable``:可写层下标(生产层),
    ``reload()`` / ``path`` 只委托给它;为 None 时两者不可用(只读组合)。

    ``visible_to`` / ``make_frame`` **只实现一次**、全部经 :meth:`get` 表达,
    因此不存在"按方法委托给某一层"的漂移;压帧统一走
    ``local_file.build_child_frame``(与生产同一函数,§2.4 所见即所得)。
    """

    def __init__(
        self,
        layers: list[Any],
        *,
        writable: int | None = None,
        names: list[str] | None = None,
    ) -> None:
        if not layers:
            raise ValueError("CompoundSkillRegistry 至少需要一层")
        if writable is not None and not 0 <= writable < len(layers):
            raise ValueError(f"writable 下标越界: {writable}(共 {len(layers)} 层)")
        if names is not None and len(names) != len(layers):
            raise ValueError("names 长度必须与 layers 一致")
        self._layers = list(layers)
        self._writable = writable
        self._names = list(names) if names else [f"layer{i}" for i in range(len(layers))]

    # -- 解析 ---------------------------------------------------------------

    def resolve(self, ref: SkillRef) -> tuple[Skill, int]:
        """解析并**返回层来源下标**(§6.10:身份归 name,层归解析结果)。

        try-and-continue:前层 MISS 就继续往后;走到最后一层仍 MISS 则把
        最后一层的错误抛出(保留最贴近事实的报错)。
        """
        last: Exception | None = None
        for index, layer in enumerate(self._layers):
            try:
                return layer.get(ref), index
            except MISS as e:
                last = e
        assert last is not None  # layers 非空,循环必然至少捕获一次
        if isinstance(last, SkillLoadError):
            raise last
        raise SkillLoadError(f"技能不存在: {ref.name}") from last

    def layer_name(self, index: int) -> str:
        """层下标 → 可读层名(供 run 标记 / 包视图的 status 使用)。"""
        return self._names[index]

    def get(self, ref: SkillRef) -> Skill:
        return self.resolve(ref)[0]

    # -- 协议面(只经 get 表达,不按方法委托)-------------------------------

    def visible_to(self, frame: Any) -> list[SkillSchema]:
        """帧白名单内子技能的伪工具 schema(目标查找同样走分层解析)。"""
        caller = self.get(frame.skill)
        schemas: list[SkillSchema] = []
        for name in caller.manifest.permissions.skills:
            try:
                target = self.get(SkillRef(name=name))
            except SkillLoadError:
                continue  # 引用存在性由闸门判;此处防御性跳过(同 local_file 先例)
            schemas.append(
                SkillSchema(
                    name=f"skill.{name}",
                    description=target.manifest.description,
                    parameters=target.manifest.inputs,
                )
            )
        return schemas

    def make_frame(self, call: SkillCall, parent: SkillFrame) -> SkillFrame:
        """压帧(分层解析目标);构建走与生产同一函数(§2.4 所见即所得)。"""
        return build_child_frame(self.get(SkillRef(name=call.name)), call, parent)

    def manifests(self) -> list[SkillManifest]:
        """清单合并:**后层作底、前层覆盖**(与 :meth:`get` 同一优先级)。

        顺序保底层(通常是生产)的既有次序在前,各层新增的名字按层序附后
        ——生产拓扑序对 UI 有意义,不能被合并打乱。
        """
        merged: dict[str, SkillManifest] = {}
        order: list[str] = []
        for layer in reversed(self._layers):
            manifests = getattr(layer, "manifests", None)
            if manifests is None:
                continue  # 没有清单面的层(如只实现 get 的适配器)不参与合并
            for manifest in manifests():
                if manifest.name not in merged:
                    order.append(manifest.name)
                merged[manifest.name] = manifest
        return [merged[name] for name in order]

    # -- 写入面(只委托给显式指定的可写层)----------------------------------

    @property
    def writable_layer(self) -> Any:
        """可写层(生产);未指定则报错——防"promote 写进草稿层"。"""
        if self._writable is None:
            raise SkillLoadError("本组合未指定可写层(writable),不支持写入/热重载")
        return self._layers[self._writable]

    @property
    def path(self) -> Any:
        return getattr(self.writable_layer, "path", None)

    def reload(self) -> bool:
        return self.writable_layer.reload()

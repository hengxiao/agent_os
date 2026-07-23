"""LocalFileSkillRegistry(DESIGN.md §6.3;M2)。

单 YAML 文件加载,一个文件声明全部技能;命名空间固定 ``local``;不做版本约束求解
(单版本,依赖只查存在);依赖图拓扑排序保留(循环依赖加载期报错);
热重载 = 手动 ``reload()``(mtime 检查);目录包形态作为后续 DirectorySkillSource,契约不变。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import jsonschema
import yaml

from agent_os.api.v1 import (
    FrameContext,
    Message,
    Provenance,
    Role,
    Skill,
    SkillArtifact,
    SkillCall,
    SkillFrame,
    SkillManifest,
    SkillRef,
    SkillSchema,
    Source,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.loader import materialize
from agent_os.skills.manifest import parse_manifest, validate_manifest

_log = logging.getLogger("agent_os.skills")


def _topo_sort(manifests: list[SkillManifest]) -> list[str]:
    """依赖图拓扑排序(Kahn,自引用边忽略——显式声明的自引用合法,§6.3 skills.yaml)。

    循环依赖 → :class:`SkillLoadError`;返回名字序(依赖先于引用者,文件序稳定)。
    """
    deps = {m.name: {d for d in m.permissions.skills if d != m.name} for m in manifests}
    order: list[str] = []
    ready = [m.name for m in manifests if not deps[m.name]]
    while ready:
        name = ready.pop(0)
        order.append(name)
        for m in manifests:
            if name in deps[m.name]:
                deps[m.name].discard(name)
                if not deps[m.name]:
                    ready.append(m.name)
    if len(order) != len(manifests):
        cycle = sorted(n for n, d in deps.items() if d)
        raise SkillLoadError(f"技能依赖存在循环: {cycle}")
    return order


class LocalFileSkillRegistry:
    """``agent_os.api.v1.SkillRegistry`` 协议实现(M2)。"""

    namespace: str = "local"

    def __init__(self, path: str = "./skills.yaml") -> None:
        self.path = path
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    def load(self) -> None:
        """discover → parse → validate → resolve deps(拓扑排序)→ materialize → publish(§6.1)。"""
        if self._loaded:
            return
        data = yaml.safe_load(Path(self.path).read_text(encoding="utf-8")) or {}
        entries = data.get("skills") or []
        manifests = [parse_manifest(e) for e in entries]
        names = [m.name for m in manifests]
        if len(set(names)) != len(names):
            dup = sorted({n for n in names if names.count(n) > 1})
            raise SkillLoadError(f"技能 name 重复: {dup}")
        by_name = {m.name: m for m in manifests}
        for m in manifests:
            for dep in m.permissions.skills:
                if dep not in by_name:
                    raise SkillLoadError(f"技能 {m.name} 引用了不存在的子技能: {dep}")
            for warning in validate_manifest(m):
                _log.warning("%s", warning)
        self._skills = {name: materialize(by_name[name]) for name in _topo_sort(manifests)}
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def reload(self) -> None:
        """热重载(mtime 检查):新帧用新版,在跑帧钉住旧版(§6.1)。"""
        raise NotImplementedError("M2")

    def get(self, ref: SkillRef) -> Skill:
        self._ensure_loaded()
        try:
            return self._skills[ref.name]
        except KeyError:
            raise SkillLoadError(f"未注册的技能: {ref}") from None

    def manifests(self) -> list[SkillManifest]:
        """全部已加载 manifest(KernelBuilder 装配期静态校验用,§6.1 权限闸门)。"""
        self._ensure_loaded()
        return [s.manifest for s in self._skills.values()]

    def visible_to(self, frame: SkillFrame) -> list[SkillSchema]:
        """帧白名单内子技能的伪工具 schema(§3.3):``skill__<name>``,parameters 即 inputs。"""
        caller = self.get(frame.skill)
        schemas: list[SkillSchema] = []
        for name in caller.manifest.permissions.skills:
            target = self._skills.get(name)
            if target is None:
                continue
            schemas.append(
                SkillSchema(
                    name=f"skill__{name}",
                    description=target.manifest.description,
                    parameters=target.manifest.inputs,
                )
            )
        return schemas

    def make_frame(self, call: SkillCall, parent: SkillFrame) -> SkillFrame:
        """构建子帧:depth+1;input 经 inputs schema 校验(失败抛 SkillLoadError,

        由内核 runner 转为父帧的错误观察);帧输入以首条 USER 消息进入帧上下文。
        """
        target = self.get(SkillRef(name=call.name))
        try:
            jsonschema.validate(call.args, target.manifest.inputs)
        except jsonschema.ValidationError as e:
            raise SkillLoadError(
                f"子技能 {call.name} 的调用参数不合 inputs schema: {e.message}"
            ) from e
        return SkillFrame(
            frame_id=uuid.uuid4().hex,
            run_id=parent.run_id,
            skill=target.ref,
            parent_id=parent.frame_id,
            input=dict(call.args),
            depth=parent.depth + 1,
            context=FrameContext(
                messages=[
                    Message(
                        role=Role.USER,
                        content=json.dumps(call.args),
                        source=Source.PARENT_INPUT,
                    )
                ]
            ),
        )

    async def register(self, artifact: SkillArtifact, provenance: Provenance) -> SkillRef:
        """运行期写入路径(§6.2):默认不信任——code 强制 SANDBOX、CodeScanner 扫描、

        发信号可被 HumanApproval 拦截、manifest 权限从严;publish 前经重放 + evaluator 验证门。
        v1 最小实现:fs_write 写技能包文件 + reload(),契约形状先行。
        """
        raise NotImplementedError("M6")

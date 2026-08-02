"""LocalFileSkillRegistry(docs/DESIGN.md §6.3;M2)。

YAML 加载,命名空间固定 ``local``;不做版本约束求解(单版本,依赖只查存在);
依赖图拓扑排序保留(循环依赖加载期报错);热重载 = 手动 ``reload()``(mtime 检查)。
``path`` 三种形态:单文件(一个文件声明全部技能)/ 目录(加载其下全部
``*.yaml``,按文件名排序合并,如 std 域分包)/ 文件路径列表(按给定序合并);
跨文件 name 去重与依赖校验与单文件同一逻辑。
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
from agent_os.skills.manifest import INLINE_DEPS_MAX, parse_manifest, validate_manifest

_log = logging.getLogger("agent_os.skills")

#: 迁移期旧扁平名 → 新点分层次名 的兼容映射(docs/NAMING.md §4)。
#: 用于解析清单时把旧 permissions.skills 引用自动转正,以及按旧名查找技能时给出警告。
LEGACY_SKILL_ALIASES: dict[str, str] = {
    "extract_json": "common.text.extract_json",
    "template_render": "common.text.template_render",
    "diff_text": "common.text.diff",
    "word_count": "common.text.word_count",
    "token_estimate": "common.text.token_estimate",
    "hash_digest": "common.hash.digest",
    "csv_to_rows": "common.table.csv_to_rows",
    "rows_to_markdown": "common.table.rows_to_markdown",
    "slugify": "common.text.slugify",
    "normalize_whitespace": "common.text.normalize_whitespace",
    "chunk_text": "common.text.chunk",
    "bm25_score": "common.retrieval.bm25_score",
    "rrf_merge": "common.retrieval.rrf_merge",
    "retrieval_metrics": "common.retrieval.retrieval_metrics",
    "injection_scan": "common.security.injection_scan",
    "redact_pii": "common.security.redact_pii",
    "identifier_guard": "common.text.identifier_guard",
    "make_handoff": "common.task.make_handoff",
    "date_normalize": "common.text.date_normalize",
    "citation_check": "common.text.citation_check",
    "summarize": "common.text.summarize",
    "classify": "common.text.classify",
    "extract": "common.text.extract",
    "translate": "common.text.translate",
    "rewrite": "common.text.rewrite",
    "qa_over_text": "common.text.qa_over_text",
    "compress_context": "common.text.compress_context",
    "tone_neutral": "common.style.tone_neutral",
    "untrusted_content": "common.security.untrusted_content",
    "knowledge_linking": "common.memory.knowledge_linking",
    "judge": "common.eval.judge",
    "calibrate_judge": "common.eval.calibrate_judge",
    "pairwise_compare": "common.eval.pairwise_compare",
    "pairwise_judge_once": "common.eval.pairwise_judge_once",
    "progress_track": "common.eval.progress_track",
    "run_tests": "common.dev.run_tests",
    "read_file_smart": "common.dev.read_file_smart",
    "apply_patch": "common.dev.apply_patch",
    "summarize_tree": "common.dev.summarize_tree",
    "fetch_page": "common.web.fetch_page",
    "research_one": "common.research.one",
    "research_iterative": "common.research.iterative",
    "memory_extract": "common.memory.extract",
    "memory_reconcile": "common.memory.reconcile",
    "memory_consolidate": "common.memory.consolidate",
    "memory_check": "common.memory.check",
    "verify_before_store": "common.memory.verify",
    "distill_experience": "common.learn.distill_experience",
    "reflect_on_failure": "common.learn.reflect_on_failure",
}


def _resolve_skill_name(name: str) -> str:
    """如 name 是旧扁平名,返回新点分名并记 warning;否则原样返回。"""
    canonical = LEGACY_SKILL_ALIASES.get(name)
    if canonical is not None:
        _log.warning("技能名 '%s' 是旧扁平名,已解析为 '%s'", name, canonical)
        return canonical
    return name



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

    def __init__(self, path: str | list[str] = "./skills.yaml") -> None:
        self.path = path
        self._skills: dict[str, Skill] = {}
        self._mtime: float | None = None  # 上次成功加载时的源文件 mtime 最大值(reload 变更检测)
        self._loaded = False
        self.load()  # 构造即走加载流水线:循环依赖等错误在加载期暴露(§6.1)

    def _sources(self) -> list[Path]:
        """全部源文件:目录 → 其下 ``*.yaml``(文件名排序);列表 → 原序;单文件 → 自身。"""
        if isinstance(self.path, (list, tuple)):
            return [Path(p) for p in self.path]
        base = Path(self.path)
        if base.is_dir():
            return sorted(base.glob("*.yaml"), key=lambda f: f.name)
        return [base]

    def _sources_mtime(self) -> float | None:
        """全部源文件 mtime 最大值(无源文件 → None;任一变更早于最大者都能被检出)。"""
        mtimes = [f.stat().st_mtime for f in self._sources()]
        return max(mtimes) if mtimes else None

    def load(self) -> None:
        """discover → parse → validate → resolve deps(拓扑排序)→ materialize → publish(§6.1)。"""
        if self._loaded:
            return
        self._skills = self._load_all()
        self._mtime = self._sources_mtime()
        self._loaded = True

    def _load_all(self) -> dict[str, Skill]:
        """读全部源文件并走完整加载流水线;抛 SkillLoadError 时不触碰调用方状态(reload 保留旧表)。"""
        entries: list = []
        for source in self._sources():
            data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
            entries.extend(data.get("skills") or [])
        manifests = [parse_manifest(e) for e in entries]
        names = [m.name for m in manifests]
        if len(set(names)) != len(names):
            dup = sorted({n for n in names if names.count(n) > 1})
            raise SkillLoadError(f"技能 name 重复: {dup}")
        by_name = {m.name: m for m in manifests}
        for m in manifests:
            # 迁移期:permissions.skills 中的旧扁平名解析为点分名
            resolved_skills = [_resolve_skill_name(d) for d in m.permissions.skills]
            if resolved_skills != m.permissions.skills:
                m.permissions.skills = resolved_skills
            for dep in m.permissions.skills:
                if dep not in by_name:
                    raise SkillLoadError(f"技能 {m.name} 引用了不存在的子技能: {dep}")
            for warning in validate_manifest(m):
                _log.warning("%s", warning)
            # 调用方侧膨胀 lint(docs/SKILL-INLINING.md §3.3):merge 依赖条数上限
            merged = [d for d in m.permissions.skills if by_name[d].inline]
            if len(merged) > INLINE_DEPS_MAX:
                _log.warning(
                    "技能 %s: 内联(merge)依赖 %d 条超过 %d 上限(指令常驻 SYSTEM,"
                    "每步都付其 token): %s",
                    m.name, len(merged), INLINE_DEPS_MAX, merged,
                )
        return {name: materialize(by_name[name]) for name in _topo_sort(manifests)}

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def reload(self) -> bool:
        """热重载(mtime 检查,§6.1/§6.3):mtime 未变 → False;变了重走 load 全流程

        (解析+校验+拓扑),成功 → True 且新帧用新版(在跑帧钉住旧版 Skill 对象);
        加载失败保留旧表并抛 SkillLoadError——不毁可用状态。
        """
        self._ensure_loaded()
        mtime = self._sources_mtime()
        if mtime == self._mtime:
            return False
        self._skills = self._load_all()  # 失败抛 SkillLoadError,旧表不被触碰
        self._mtime = mtime
        return True

    def get_by_name(self, name: str) -> Skill:
        """按名字取当前加载版本的 Skill(测试/调试入口;等价 ``get(SkillRef(name=name))``)。"""
        return self.get(SkillRef(name=name))

    def get(self, ref: SkillRef) -> Skill:
        self._ensure_loaded()
        name = _resolve_skill_name(ref.name)
        try:
            return self._skills[name]
        except KeyError:
            raise SkillLoadError(f"未注册的技能: {ref}") from None

    def manifests(self) -> list[SkillManifest]:
        """全部已加载 manifest(KernelBuilder 装配期静态校验用,§6.1 权限闸门)。"""
        self._ensure_loaded()
        return [s.manifest for s in self._skills.values()]

    def visible_to(self, frame: SkillFrame) -> list[SkillSchema]:
        """帧白名单内子技能的伪工具 schema(§3.3):``skill.<name>``,parameters 即 inputs。"""
        caller = self.get(frame.skill)
        schemas: list[SkillSchema] = []
        for name in caller.manifest.permissions.skills:
            target = self._skills.get(name)
            if target is None:
                continue
            schemas.append(
                SkillSchema(
                    name=f"skill.{name}",
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
            # 身份不变量(docs/DATA-AUTHZ.md §2.3):子帧原样继承父帧 principal——
            # skill 嵌套/升权/code 沙箱都不改变身份(升权改的是副作用许可)
            principal=parent.principal,
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
        v1 最小实现:system.file.write 写技能包文件 + reload(),契约形状先行。
        """
        raise NotImplementedError("M6")

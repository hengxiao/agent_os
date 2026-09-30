"""LocalFileSkillRegistry(docs/DESIGN.md §6.3;M2)。

YAML 加载,命名空间固定 ``local``;不做版本约束求解(单版本;依赖查存在 +
semver 约束准入检查,见 skills/semver.py——SKILL-PACKAGES-V2 §8 裁决:只准入不多解);
依赖图拓扑排序保留(循环依赖加载期报错);热重载 = 手动 ``reload()``(mtime 检查),
或 ``start_watching()`` 起轮询看门狗自动 reload(daemon 线程,生命周期随进程)。
``path`` 三种形态:单文件(一个文件声明全部技能)/ 目录(加载其下全部
``*.yaml``,按文件名排序合并,如 std 域分包)/ 文件路径列表(按给定序合并);
跨文件 name 去重与依赖校验与单文件同一逻辑。register() 写路径按形态分流:
单文件原位合并;目录形态落 ``<dir>/registered.yaml``(人管文件不碰,
package._atomic_write_registered);列表形态不写(v1 边界)。
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from agent_os.api.v1 import (
    POST_SKILL_REGISTER,
    PRE_SKILL_REGISTER,
    FrameContext,
    Message,
    Provenance,
    Role,
    Signal,
    Skill,
    SkillArtifact,
    SkillCall,
    SkillFrame,
    SkillKind,
    SkillManifest,
    SkillRef,
    SkillSchema,
    Source,
    Veto,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.loader import materialize
from agent_os.skills.manifest import INLINE_DEPS_MAX, parse_manifest, validate_manifest
from agent_os.skills.semver import parse_constraint, satisfies

_log = logging.getLogger("agent_os.skills")

#: register() 命名闸门(docs/DESIGN.md §6.2;WS-C):gate.py G1 同款层级命名正则
#: (点分 ≥2 段小写 snake_case)——运行期产物与草稿走同一命名面,fail 即拒
_REGISTER_NAME_RE = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+")

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
    输入面:``_load_all`` 已把 permissions.skills 落为解析后纯名(@ 约束后缀
    在准入检查时消化,不到这里)。
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


def build_child_frame(target: Skill, call: SkillCall, parent: SkillFrame) -> SkillFrame:
    """make_frame 的构建部分(input 校验 + 新帧;local_file 与 OverlaySkillRegistry 共用)。

    抽取理由(docs/SKILL-DEV.md §1.1;L3):overlay 装配的 test-run 压帧必须走
    与生产完全相同的构建路径(§2.4 所见即所得),不允许两套帧语义漂移。
    """
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


class LocalFileSkillRegistry:
    """``agent_os.api.v1.SkillRegistry`` 协议实现(M2)。"""

    namespace: str = "local"

    def __init__(
        self,
        path: str | list[str] = "./skills.yaml",
        *,
        tools: Any = None,
        bus: Any = None,
    ) -> None:
        self.path = path
        #: register() 可选增强闸(§6.2;WS-C):tools registry 注入时合成草稿跑
        #: validate_draft(strict_refs=True) 的 G1-G3;None = 跳过(同 Lab G4 无
        #: runner 时 skip 哲学——嵌入路径不强制)
        self._tools = tools
        #: register() 信号总线(pre:skill.register 可否决 / post:skill.register
        #: 观察;None = 不发射,行为与引入前一致)
        self._bus = bus
        #: register() 可选验证门 smoke(§6.2 验证门降级形态):bind_register_smoke
        #: 注入 callable(name, entry[, provenance]) -> {"ok": bool, ...};None = 跳过
        #: (同 Lab G4 无 runner 时 skip 哲学——嵌入路径不强制)
        self._register_smoke: Any = None
        #: bind 时按签名位数判定(bind_register_smoke):>=3 个位置形参 → 调用时追加
        #: 本次注册的 Provenance;内省失败(部分 builtin 无签名)保守按两参契约
        self._register_smoke_takes_provenance: bool = False
        #: 轮询热重载看门狗(start_watching 起的 daemon 线程;None = 未在看)
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()
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
            # CSafeLoader(libyaml C 实现)语义同 SafeLoader,缺库回落纯 Python;
            # 注册表加载是内核装配热路径(每次 build 全量解析),值得 C 加速
            data = yaml.load(
                source.read_text(encoding="utf-8"),
                Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader),
            ) or {}
            entries.extend(data.get("skills") or [])
        manifests = [parse_manifest(e) for e in entries]
        names = [m.name for m in manifests]
        if len(set(names)) != len(names):
            dup = sorted({n for n in names if names.count(n) > 1})
            raise SkillLoadError(f"技能 name 重复: {dup}")
        by_name = {m.name: m for m in manifests}
        for m in manifests:
            # 依赖条目统一解析(skills/semver.py 约束准入):旧扁平名解析作用于
            # name 段;permissions.skills 落解析后的纯名字段——拓扑排序/inline
            # lint/visible_to 与这里的存在性检查共用同一名字面(manifest.py 的
            # 条目字符串原样带 @,不改解析层)
            deps: list[str] = []
            for raw_dep in m.permissions.skills:
                dep_name, op, constraint = parse_constraint(raw_dep)
                dep_name = _resolve_skill_name(dep_name)
                if dep_name not in by_name:
                    raise SkillLoadError(f"技能 {m.name} 引用了不存在的子技能: {raw_dep}")
                if op is not None and not satisfies(by_name[dep_name].version, op, constraint):
                    installed = by_name[dep_name].version or "(未声明版本)"
                    raise SkillLoadError(
                        f"技能 {m.name} 的依赖 {raw_dep!r} 被拒绝: 安装版 {installed} "
                        f"不满足约束 {op}{constraint}"
                    )
                deps.append(dep_name)
            if deps != m.permissions.skills:
                m.permissions.skills = deps
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

    def start_watching(self, interval_s: float) -> None:
        """轮询热重载看门狗(daemon 线程):每 ``interval_s`` 查 ``_sources_mtime()``,变了 → ``reload()``。

        - ``reload()`` 抛 :class:`SkillLoadError` → 吞掉记 log:旧表不动(与手动
          reload 语义对齐),mtime 仍不同下个周期自然重试;线程内任何其他异常同样
          吞掉记 log 继续——看门狗不能死在角落;
        - start 幂等:已在看则忽略(重复调用不换间隔、不重启);
        - ``interval_s <= 0`` → :class:`ValueError`;
        - daemon=True 不阻进程退出;内核无 close 钩子,看门狗生命周期随进程
          (要确定性停看用 :meth:`stop_watching`)。
        """
        if interval_s <= 0:
            raise ValueError(f"watch 间隔须 > 0 秒,得到: {interval_s!r}")
        if self._watch_thread is not None and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        thread = threading.Thread(
            target=self._watch_loop,
            args=(float(interval_s),),
            name="agent-os-skills-watch",
            daemon=True,
        )
        self._watch_thread = thread
        thread.start()

    def stop_watching(self) -> None:
        """停止看门狗(幂等);停后源文件改动不再自动生效,手动 ``reload()`` 仍可用。"""
        self._watch_stop.set()
        thread = self._watch_thread
        self._watch_thread = None
        if thread is not None:
            thread.join()

    def _watch_loop(self, interval_s: float) -> None:
        while not self._watch_stop.wait(interval_s):
            try:
                if self._sources_mtime() != self._mtime:
                    self.reload()
            except SkillLoadError as e:
                _log.warning("热重载看门狗: reload 失败,保留旧表(下周期重试): %s", e)
            except Exception:  # noqa: BLE001 — 看门狗不能死在角落:吞掉记 log 继续
                _log.exception("热重载看门狗: 轮询异常(继续看门)")

    def bind_register_smoke(self, smoke: Any) -> None:
        """装配钩子(§6.2 验证门降级形态;bind 模式同 tools ``bind_user_channel`` 先例):
        注入 register() 管线的冒烟验证回调。

        smoke 形态 ``callable(name: str, entry: dict) -> dict`` 或
        ``callable(name: str, entry: dict, provenance: Provenance) -> dict``
        (同步/async 均可,兼容先例 tools/builtins.py user_channel):``ok`` 真 → 放行;
        非真 → register 抛 GateError(detail 透传,不写盘);抛异常 →
        GateError(fail-closed,异常文本入消息)。缺省(未 bind)= 跳过(嵌入路径不强制)。

        三参形态(2026-09-30 契约扩展):bind 时经 ``inspect.signature`` 数位置形参,
        >= 3 则调用时追加本次注册的 ``Provenance``(默认验证门据此引用
        ``detail["source_run_id"]`` 录制 run 作证据,见 skills/register_smoke.py);
        内省失败(部分 builtin 无签名)→ 保守按两参契约调用。
        """
        try:
            positional = [
                p
                for p in inspect.signature(smoke).parameters.values()
                if p.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            self._register_smoke_takes_provenance = len(positional) >= 3
        except (TypeError, ValueError):  # 内省失败:保守两参
            self._register_smoke_takes_provenance = False
        self._register_smoke = smoke

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
        return build_child_frame(target, call, parent)

    async def register(self, artifact: SkillArtifact, provenance: Provenance) -> SkillRef:
        """运行期写入路径(docs/DESIGN.md §6.2;WS-C):默认不信任的信任管线。

        落点顺序(闸门 fail 即 GateError;除 code handler 落盘外,拒绝都发生在
        ``_atomic_write`` 之前 = 生产零变化):
        1. 纯函数闸门(必过):层级命名正则 fail(gate.py G1 同款)、
           ``_g5_findings(prompt)`` fail;description lint 只 warn 记 provenance(§6.1 lint);
        2. 归一化 artifact → 生产条目 dict:version 缺省 ``gate.default_version``
           (同名续 bump patch);prompt 技能指令体内联;code 技能源码落
           ``<skills.yaml 同级>/generated_handlers/<mod>.py``(mod = name 点转
           下划线——带点文件名不是合法模块路径),entry 写 handler dotted path,
           logic **一律强制** ``{"mode": "sandbox"}``(§6.2 默认不信任:artifact
           自带的 trusted 声明直接覆盖,不允许自我提权);
        3. 可选增强闸(构造期注入 tools 时):合成草稿跑
           ``validate_draft(strict_refs=True)`` 取 G1-G3,fail 即拒;未注入跳过
           (同 Lab G4 无 runner 时 skip 哲学——嵌入路径不强制);
        4. 可选验证门 smoke(``bind_register_smoke`` 注入时;§6.2 验证门降级形态):
           ``smoke(name, entry)`` 或 ``smoke(name, entry, provenance)``(bind 时按
           签名位置形参数判定;同步/async 均可)——code 技能的 entry 以拷贝形态附
           ``_source`` handler 源码(步骤 6 写盘的仍是原 entry,``_source`` 不污染
           生产 yaml);ok 非真 → GateError(detail
           透传),抛异常 → GateError(fail-closed,异常文本入消息);拒绝同样落
           jsonl(action="rejected",gates.smoke 记 fail detail)但生产零变化;
           未注入 → gates.smoke 记 skip;
        5. ``pre:skill.register``(bus 注入时):任一 sidecar Veto → 中止不写盘;
        6. 先证后换:单文件走 ``package._atomic_write``,目录形态走
           ``package._atomic_write_registered``(落 ``<dir>/registered.yaml``,
           人管文件不碰;同名技能已在人管文件 → 拒绝;staging 整目录全流水线
           证明 + 原子 rename + 单次 reload;多文件列表形态明确报错——v1 边界);
        7. provenance 落盘 ``<skills.yaml>.register.jsonl``(全字段 + version +
           action + 闸门结果,draft_store.record_promotion 先例);
        8. ``post:skill.register``(成功观察),返回 SkillRef。

        明确不做(v1 边界):semver ^/~ 依赖求解(只做约束准入检查,skills/semver.py)、
        多文件列表形态写路径、完整重放 + evaluator 验证门(§6.2 验证门降级为可选
        ``bind_register_smoke`` 注入,见步骤 4;文件监听热重载见 ``start_watching``)。
        """
        # 延迟 import 防环:local_file → gate → draft_store → compound → local_file
        from agent_os.skills.draft_store import _manifest_to_dict
        from agent_os.skills.gate import (
            GateError,
            _g5_findings,
            default_version,
            validate_draft,
        )
        from agent_os.skills.package import _atomic_write, _atomic_write_registered

        manifest = artifact.manifest
        name = manifest.name or ""
        gate_notes: dict[str, Any] = {}  # 闸门结果(provenance 记录面)

        # —— 形态前置:单文件走 _atomic_write;目录形态走 registered.yaml 写路径
        #    (_atomic_write_registered,人管文件不碰);多文件列表形态仍拒(v1 边界)——
        dir_form = isinstance(self.path, str) and Path(self.path).is_dir()
        if not isinstance(self.path, str):
            raise SkillLoadError(
                "register() 只支持单文件或目录形态 skills 路径"
                "(多文件列表形态的写路径属 v1 明确不做项)"
            )

        # —— 1. 纯函数闸门(必过)——
        if not _REGISTER_NAME_RE.fullmatch(name):
            raise GateError(f"register 拒绝: name {name!r} 不合层级命名规范(gate.py G1 同款)")
        prompt_text = artifact.prompt or manifest.prompt or ""
        g5 = _g5_findings(prompt_text)
        gate_notes["g5"] = "fail" if g5 else "pass"
        if g5:
            raise GateError(f"register 拒绝: {g5[0]['message']}")
        desc = manifest.description or ""
        gate_notes["description_lint"] = (
            "pass" if len(desc) >= 10 and "Use when" in desc else "warn"
        )
        if gate_notes["description_lint"] == "warn":
            _log.warning(
                "register: 技能 %s description 应含 'Use when / Do not use when'"
                " 触发条件(§6.1 lint,warn 不阻断,记 provenance)",
                name,
            )

        # —— 2. 归一化 artifact → 生产条目 dict ——
        entry = _manifest_to_dict(manifest)
        entry["version"] = manifest.version or default_version(self, name)
        version = entry["version"]
        code_mod = ""
        if manifest.kind is SkillKind.CODE:
            if not (artifact.code or "").strip():
                raise SkillLoadError(
                    f"register 拒绝: code 技能 {name} 的 artifact.code 为空(§6.3 handler 必须有源码)"
                )
            code_mod = name.replace(".", "_")
            entry["handler"] = f"generated_handlers.{code_mod}:run"
            entry["logic"] = {"mode": "sandbox"}  # §6.2:一律强制,覆盖 artifact 自带声明
        else:
            if (artifact.code or "").strip():
                _log.warning(
                    "register: prompt 技能 %s 附带 code,已忽略(code 仅 code 技能生效)", name
                )
            if prompt_text:
                entry["prompt"] = prompt_text  # 单文件形态:指令体内联(§6.3)
        try:
            self.get(SkillRef(name=name))
            action = "replaced"
        except SkillLoadError:
            action = "appended"

        # —— 3. 可选增强闸(tools 注入时跑 G1-G3;未注入跳过)——
        if self._tools is not None:
            draft = {
                "name": name,
                "manifest": {k: v for k, v in entry.items() if k != "prompt"},
                "prompt": entry.get("prompt") or "",
            }
            report = validate_draft(draft, production=self, tools=self._tools, strict_refs=True)
            gate_notes["g1_g3"] = {g: report["gates"][g]["status"] for g in ("g1", "g2", "g3")}
            fails = [
                f["message"]
                for g in ("g1", "g2", "g3")
                for f in report["gates"][g]["findings"]
                if f["level"] == "fail"
            ]
            if fails:
                raise GateError(f"register 增强闸拒绝({name}): {fails[0]}")
        else:
            gate_notes["g1_g3"] = "skip(tools 未注入)"

        # —— 4. 可选验证门 smoke(bind_register_smoke 注入时;拒绝落 jsonl 但不写盘)——
        if self._register_smoke is not None:
            # code 技能:handler 源码落盘在步骤 6,验证门要真冒烟须随调用携带;
            # {**entry} 拷贝附 _source——步骤 6 写盘的是原 entry 对象,生产 yaml 零污染。
            # prompt 技能指令体已内联 entry,原样传递即可
            smoke_entry = (
                {**entry, "_source": artifact.code or ""}
                if manifest.kind is SkillKind.CODE
                else entry
            )
            try:
                if self._register_smoke_takes_provenance:
                    outcome = self._register_smoke(name, smoke_entry, provenance)
                else:
                    outcome = self._register_smoke(name, smoke_entry)
                if inspect.isawaitable(outcome):  # 回调可同步可 async(user_channel 先例)
                    outcome = await outcome
            except Exception as e:
                gate_notes["smoke"] = f"fail: smoke 执行异常: {e}"
                self._record_register_jsonl(
                    provenance, name=name, version=version,
                    kind=manifest.kind.value, action="rejected", gates=gate_notes,
                )
                raise GateError(
                    f"register 验证门拒绝({name}): smoke 执行异常(fail-closed): {e}"
                ) from e
            outcome = outcome if isinstance(outcome, dict) else {}
            if not outcome.get("ok", False):
                detail = outcome.get("detail") or outcome.get("error") or "smoke 未通过"
                gate_notes["smoke"] = f"fail: {detail}"
                self._record_register_jsonl(
                    provenance, name=name, version=version,
                    kind=manifest.kind.value, action="rejected", gates=gate_notes,
                )
                raise GateError(f"register 验证门 smoke 拒绝({name}): {detail}")
            gate_notes["smoke"] = "pass"
        else:
            gate_notes["smoke"] = "skip(未注入)"

        # —— 5. pre:skill.register(Veto → 中止不写盘)——
        if self._bus is not None:
            verdicts = await self._bus.emit(
                Signal(
                    name=PRE_SKILL_REGISTER,
                    run_id=provenance.run_id or "",
                    payload={
                        "name": name,
                        "version": version,
                        "kind": manifest.kind.value,
                        "action": action,
                        "note": provenance.note,
                    },
                )
            )
            veto = next((v for v in verdicts if isinstance(v, Veto)), None)
            if veto is not None:
                raise GateError(
                    f"register 被否决(pre:skill.register): {veto.reason or 'sidecar Veto'}"
                )

        # —— 6. 先证后换:code handler 落盘(失败止步于此,yaml 未动)+ 原子写 yaml ——
        if manifest.kind is SkillKind.CODE:
            self._write_generated_handler(code_mod, artifact.code or "")
        if dir_form:
            _atomic_write_registered(self, {name: entry})
        else:
            _atomic_write(self, {name: entry})

        # —— 7. provenance 落盘(追加;draft_store.record_promotion 先例)——
        self._record_register_jsonl(
            provenance, name=name, version=version,
            kind=manifest.kind.value, action=action, gates=gate_notes,
        )

        # —— 8. post:skill.register(成功观察)——
        if self._bus is not None:
            await self._bus.emit(
                Signal(
                    name=POST_SKILL_REGISTER,
                    run_id=provenance.run_id or "",
                    payload={
                        "name": name,
                        "version": version,
                        "kind": manifest.kind.value,
                        "action": action,
                    },
                )
            )
        return SkillRef(name=name, version=version)

    def _record_register_jsonl(
        self,
        provenance: Provenance,
        *,
        name: str,
        version: str,
        kind: str,
        action: str,
        gates: dict[str, Any],
    ) -> None:
        """register 事件追加 ``<skills.yaml>.register.jsonl``(§6.2 provenance;

        draft_store.record_promotion 先例):成功(action=appended/replaced)与验证门
        smoke 拒绝(action=rejected)都落——拒绝记录是"谁试过注册什么、为什么没过"
        的证据面;其余闸门拒绝维持零写入(都发生在本记录点之前)。
        """
        target = Path(self.path)
        record = {
            "run_id": provenance.run_id,
            "task": provenance.task,
            "note": provenance.note,
            "detail": provenance.detail,
            "name": name,
            "version": version,
            "kind": kind,
            "action": action,
            "gates": gates,
            "at": time.time(),
        }
        with target.with_name(target.name + ".register.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_generated_handler(self, mod: str, code: str) -> None:
        """code 技能源码落 ``<skills 目录>/generated_handlers/<mod>.py`` 并保证可 lazy import。

        skills 目录(单文件形态 = skills.yaml 同级;目录形态 = 目录自身)插入
        sys.path(已在前则不动),``generated_handlers``
        落 ``__init__.py`` 成正规包;写后 ``invalidate_caches`` + 弹出本模块与
        父包的 sys.modules 缓存——同名再 register 时下次惰性解析拿到新代码
        (loader 惰性 import 见 skills/loader.py;handler 模块是进程级共享,
        换代码对下次解析生效,在跑帧不回溯)。
        """
        base = Path(self.path).resolve()
        skills_dir = base if base.is_dir() else base.parent
        pkg = skills_dir / "generated_handlers"
        pkg.mkdir(exist_ok=True)
        init = pkg / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
        (pkg / f"{mod}.py").write_text(code, encoding="utf-8")
        if str(skills_dir) not in sys.path:
            sys.path.insert(0, str(skills_dir))
        importlib.invalidate_caches()
        sys.modules.pop(f"generated_handlers.{mod}", None)
        sys.modules.pop("generated_handlers", None)

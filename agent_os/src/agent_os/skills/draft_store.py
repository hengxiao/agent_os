"""DraftStore(docs/SKILL-DEV.md §1.2;L1)与 OverlaySkillRegistry(§1.1)。

草稿与生产分离:生产 registry(skills.yaml)永远只装"过了闸门的 skill";
开发中的草稿每个一个目录,允许临时不合规(合规性由闸门在 validate/promote
时判,L2)。本模块只管存储与读取合并,不做任何校验闸——**保存永不报错**,
编辑器永远不打断创作流。

布局(§1.2)::

    <drafts_root>/<name>/manifest.yaml   # 与 skills.yaml 单条同形(SkillManifest 契约全集)
                       /prompt.md        # kind=prompt 的指令体
                       /handler.py       # kind=code 的处理器(可选)
                       /tests/*.json     # 试跑用例(L3 消费)
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from agent_os.api.v1 import (
    Skill,
    SkillCall,
    SkillFrame,
    SkillManifest,
    SkillRef,
    SkillSchema,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.loader import materialize
from agent_os.skills.local_file import build_child_frame
from agent_os.skills.manifest import parse_manifest

#: 草稿名合法面(docs/NAMING.md §2):≥2 段点分,段内小写 snake_case——
#: 同时充当路径穿越防护(字符面不含 /、..、空白,目录名即草稿名)
_DRAFT_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$")

#: 空模板(§2.3 新建流程):契约骨架齐全,内容留人/助手填
_EMPTY_MANIFEST: dict[str, Any] = {
    "version": "0.1.0",
    "kind": "prompt",
    "description": "",
    "inputs": {"type": "object", "properties": {}},
    "outputs": {"type": "object", "properties": {}},
    "permissions": {"tools": [], "skills": []},
}

#: 模板库(§4 L5;轻量):三档骨架,字段就位(G1/G2/G3 直接过),内容留人改。
#: trust 占位是**真实可用**的机制描述(不是空话)——L2/L3 模板的 G3 必填项即此落地。
DRAFT_TEMPLATES: dict[str, dict[str, Any]] = {
    "prompt_query": {
        "description": "查询类技能。Use when 需要按 query 检索/推理回答;Do not use when 需要写副作用。",
        "inputs": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "查询内容"}},
            "required": ["query"],
        },
        "outputs": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        "permissions": {"tools": [], "skills": []},
        "prompt": "你是查询助手。按输入的 query 回答,最终答案输出 answer 字段(只输出该 JSON)。\n",
    },
    "file_process": {
        "description": "文件处理类技能(L2 骨架)。Use when 需要读写工作区文件;Do not use when 要删除文件或停进程。",
        "inputs": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "workdir 内相对路径"}},
            "required": ["path"],
        },
        "outputs": {
            "type": "object",
            "properties": {"written": {"type": "string"}},
            "required": ["written"],
        },
        "permissions": {"tools": ["system.file.read", "system.file.write"], "skills": []},
        "trust": {"reversal": "覆盖写前自动 .bak(写工具现状),回滚 = 取回 .bak 重写"},
        "prompt": (
            "你是文件处理员(L2 可逆档)。读写仅限 workdir 内输入的 path;"
            "写操作幂等(同内容重复写不产生差异)。最终答案输出 written 字段。\n"
        ),
    },
    "danger_op": {
        "description": "危险操作类技能(L3 骨架)。Use when 需要对指名目标做不可逆操作;Do not use when 目标未指名或可逆完成。",
        "inputs": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20,
                },
                "dry_run": {"type": "boolean"},
            },
            "required": ["targets", "dry_run"],
        },
        "outputs": {
            "type": "object",
            "properties": {"destroyed": {"type": "array", "items": {"type": "string"}}},
            "required": ["destroyed"],
        },
        "permissions": {"tools": ["system.file.delete"], "skills": []},
        "trust": {"blast_radius": "仅 targets 指名清单(单次 ≤20);空目标/通配目标报错"},
        "prompt": (
            "你是危险操作执行员(L3 不可逆档)。dry_run=true 时只返回将影响的清单,"
            "一个不删;false 时按 targets 逐个调用 system.file.delete,不得增删目标。"
            "最终答案输出 destroyed 字段(实际删除清单)。\n"
        ),
    },
}


class DraftStore:
    """草稿目录的 CRUD(§1.2;解析容错见 :meth:`read`)。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def check_name(name: str) -> None:
        """草稿名合法性(docs/NAMING.md §2 + 路径穿越防护);不合法 → ValueError。"""
        if not _DRAFT_NAME_RE.match(name or ""):
            raise ValueError(
                f"草稿名 {name!r} 不合命名规范:应为 ≥2 段点分小写 snake_case"
                "(docs/NAMING.md §2;该约束同时是路径穿越防护)"
            )

    def _dir(self, name: str) -> Path:
        self.check_name(name)
        return self._root / name

    # ------------------------------------------------------------------
    # CRUD(§1.5)
    # ------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        """草稿列表:name/最近修改时间(目录内文件 mtime 最大者)/有无 handler/用例数。

        推导档由调用方(API 层)用 OverlaySkillRegistry 计算——本层不持有
        tools/skills registry,存储与判定分离。
        """
        rows = []
        for d in sorted(self._root.iterdir()):
            if not d.is_dir() or not (d / "manifest.yaml").is_file():
                continue  # 非草稿目录(如 gate/ 归档)跳过
            mtimes = [f.stat().st_mtime for f in d.rglob("*") if f.is_file()]
            tests_dir = d / "tests"
            rows.append(
                {
                    "name": d.name,
                    "mtime": max(mtimes) if mtimes else 0.0,
                    "has_handler": (d / "handler.py").is_file(),
                    "tests": len(list(tests_dir.glob("*.json"))) if tests_dir.is_dir() else 0,
                }
            )
        return rows

    def create(
        self,
        name: str,
        *,
        source: Skill | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        """新建草稿:空模板 / ``template`` 模板库(§4 L5)/ ``source`` 生产复制。已存在 → FileExistsError。"""
        d = self._dir(name)
        if d.exists():
            raise FileExistsError(f"草稿已存在: {name}")
        if template is not None and template not in DRAFT_TEMPLATES:
            raise ValueError(
                f"未知模板: {template!r}(可选: {sorted(DRAFT_TEMPLATES)})"
            )
        d.mkdir(parents=True)
        (d / "tests").mkdir()
        if source is None:
            tpl = DRAFT_TEMPLATES.get(template or "")
            manifest = {"name": name, **_EMPTY_MANIFEST}
            if tpl:
                manifest.update({k: v for k, v in tpl.items() if k != "prompt"})
            prompt = (tpl or {}).get("prompt") or "# 在这里写指令体(prompt)\n"
        else:
            manifest = _manifest_to_dict(source.manifest)
            manifest["name"] = name  # 复制即改名:草稿是独立个体
            # prompt 取物化后的指令体(entry 加载的结果),handler 只留 dotted path 引用
            # —— 代码处理器的源码复制不出可运行形态,留路径由人决定(§1.3 指令组)
            prompt = source.prompt or ""
        (d / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        (d / "prompt.md").write_text(prompt, encoding="utf-8")
        return self.read(name)

    def read(self, name: str) -> dict[str, Any]:
        """读草稿:manifest 原文 + 解析结果 + prompt/handler/tests 全量。

        **解析容错**(§1.2"允许临时不合规"):YAML 或 parse_manifest 失败不抛错,
        ``manifest`` 给 YAML 原文 dict(可能是 None),``parse_error`` 带原因——
        编辑器要能把半成品打开继续改,而不是被 500 挡在门外。
        """
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"草稿不存在: {name}")
        manifest_text = (d / "manifest.yaml").read_text(encoding="utf-8")
        manifest_raw: dict[str, Any] | None = None
        parse_error: str | None = None
        try:
            data = yaml.safe_load(manifest_text)
            manifest_raw = data if isinstance(data, dict) else None
            if manifest_raw is None:
                parse_error = "manifest.yaml 不是 mapping 形态"
            else:
                try:
                    parse_manifest(manifest_raw)  # 契约消费验证(§1.2);结果此处不用
                except SkillLoadError as e:
                    parse_error = str(e)
        except yaml.YAMLError as e:
            parse_error = f"YAML 解析失败: {e}"
        handler_file = d / "handler.py"
        tests_dir = d / "tests"
        return {
            "name": name,
            "manifest": manifest_raw,
            "manifest_text": manifest_text,
            "parse_error": parse_error,
            "prompt": (d / "prompt.md").read_text(encoding="utf-8")
            if (d / "prompt.md").is_file()
            else "",
            "handler": handler_file.read_text(encoding="utf-8")
            if handler_file.is_file()
            else None,
            "tests": {
                f.name: f.read_text(encoding="utf-8")
                for f in sorted(tests_dir.glob("*.json"))
            }
            if tests_dir.is_dir()
            else {},
        }

    def save(
        self,
        name: str,
        *,
        manifest: dict[str, Any],
        prompt: str = "",
        handler: str | None = None,
        tests: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """保存草稿(整体替换):**写之前把上一版备份为 ``.bak``**(§1.5)。

        不校验内容(半成品随时可存);``handler=None`` 表示删除 handler.py
        (code → prompt 转型);``tests=None`` 不动 tests 目录,给了就整体替换。
        """
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"草稿不存在: {name}")
        if not isinstance(manifest, dict):
            raise TypeError("manifest 必须是 mapping")
        manifest = {**manifest, "name": name}  # 目录名即事实源,防正文与目录漂移
        _write_with_bak(
            d / "manifest.yaml",
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        )
        _write_with_bak(d / "prompt.md", prompt)
        handler_file = d / "handler.py"
        if handler is None:
            if handler_file.is_file():
                _write_with_bak(handler_file, handler_file.read_text(encoding="utf-8"))
                handler_file.unlink()
        else:
            _write_with_bak(handler_file, handler)
        if tests is not None:
            tests_dir = d / "tests"
            tests_dir.mkdir(exist_ok=True)
            for old in tests_dir.glob("*.json"):
                old.unlink()
            for fname, case in tests.items():
                # 用例文件名同样限制字符面(只接受 *.json 平名,防路径穿越)
                if not re.fullmatch(r"[A-Za-z0-9_.-]+\.json", fname):
                    raise ValueError(f"用例文件名不合法: {fname!r}")
                text = case if isinstance(case, str) else json.dumps(case, ensure_ascii=False, indent=2)
                _write_with_bak(tests_dir / fname, text)
        return self.read(name)

    def delete(self, name: str) -> None:
        """删草稿(L2 语义,UI 确认;目录整体删除,不做回收站——git/.bak 是回滚面)。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"草稿不存在: {name}")
        shutil.rmtree(d)

    # ------------------------------------------------------------------
    # 闸门报告与 promote 记录(docs/SKILL-DEV.md §1.4;L2)
    # ------------------------------------------------------------------

    def _gate_dir(self, name: str) -> Path:
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"草稿不存在: {name}")
        gate = d / "gate"
        gate.mkdir(exist_ok=True)
        return gate

    def save_gate_report(self, name: str, report: dict[str, Any]) -> dict[str, Any]:
        """报告落盘 ``gate/<ts>.json``(§1.4);report_id = ``<ts_ms>-<manifest_hash>``。

        id 同时含时间与内容哈希:审计可读,防报告与内容错位的比对键也内嵌其中。
        """
        gate = self._gate_dir(name)
        report = dict(report)
        report["report_id"] = f"{int(report['created_at'] * 1000)}-{report['manifest_hash']}"
        (gate / f"{int(report['created_at'] * 1000)}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    def read_gate_report(self, name: str, report_id: str) -> dict[str, Any]:
        """按 id 读报告;不存在 → FileNotFoundError(promote 前必查,防编造报告)。"""
        gate = self._gate_dir(name)
        for f in gate.glob("*.json"):
            try:
                report = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if report.get("report_id") == report_id:
                return report
        raise FileNotFoundError(f"找不到闸门报告: {report_id}")

    def record_promotion(self, name: str, record: dict[str, Any]) -> None:
        """promote 记录追加进 ``gate/promotions.jsonl``(§1.2 provenance 槽位首用)。"""
        gate = self._gate_dir(name)
        with (gate / "promotions.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # 装配面(OverlaySkillRegistry / tier 推导用)
    # ------------------------------------------------------------------

    def load_skill(self, name: str) -> Skill:
        """草稿 → Skill 对象(manifest 走 parse_manifest;prompt.md 作指令体)。

        草稿不合规时抛 SkillLoadError——本入口只服务"可推导/可装配"场景,
        容错读取走 :meth:`read`。
        """
        data = self.read(name)
        if data["parse_error"] is not None or data["manifest"] is None:
            raise SkillLoadError(f"草稿 {name} 暂不合规: {data['parse_error']}")
        manifest = parse_manifest(data["manifest"])
        if data["prompt"]:
            manifest.prompt = data["prompt"]  # prompt.md 优先于 manifest.prompt(§1.2 entry 槽位)
        return materialize(manifest)


class OverlaySkillRegistry:
    """生产 registry + 草稿层,**草稿优先**(docs/SKILL-DEV.md §1.1)。

    L1 服务推导档;L3 起补齐 SkillRegistry 协议面(get/visible_to/make_frame/
    manifests),供 test-run 与 G4 的真 run 装配——跑的就是生产形态的 run
    (§2.4 所见即所得)。草稿暂不合规时透明回落生产同名——半成品影子不遮蔽
    可用版本。只读装配面:loader 的热重载/写入仍属生产 registry。
    ``extra``(L4):额外注入的 Skill 对象(如 skill.dev.assistant meta-skill),
    解析序 = 草稿 → extra → 生产。
    """

    def __init__(
        self,
        production: Any,
        store: DraftStore,
        extra: dict[str, Skill] | None = None,
    ) -> None:
        self._production = production
        self._store = store
        self._extra = extra or {}

    def get(self, ref: SkillRef) -> Skill:
        try:
            return self._store.load_skill(ref.name)
        except (FileNotFoundError, SkillLoadError, ValueError):
            pass
        if ref.name in self._extra:
            return self._extra[ref.name]
        return self._production.get(ref)

    def visible_to(self, frame: Any) -> list[SkillSchema]:
        """帧白名单内子技能的伪工具 schema(同 local_file 先例;目标查找走草稿优先)。"""
        caller = self.get(frame.skill)
        schemas: list[SkillSchema] = []
        for name in caller.manifest.permissions.skills:
            try:
                target = self.get(SkillRef(name=name))
            except SkillLoadError:
                continue  # 引用存在性由闸门判;此处防御性跳过(与 visible_to 同姿势)
            schemas.append(
                SkillSchema(
                    name=f"skill.{name}",
                    description=target.manifest.description,
                    parameters=target.manifest.inputs,
                )
            )
        return schemas

    def make_frame(self, call: SkillCall, parent: SkillFrame) -> SkillFrame:
        """压帧(草稿优先解析目标);构建走与生产同一函数(§2.4 所见即所得)。"""
        target = self.get(SkillRef(name=call.name))
        return build_child_frame(target, call, parent)

    def manifests(self) -> list[SkillManifest]:
        """生产清单 + 草稿清单 + extra(同名草稿覆盖;不合规草稿不进清单)。"""
        merged = {m.name: m for m in self._production.manifests()}
        for row in self._store.list():
            if _loadable(self._store, row["name"]):
                merged[row["name"]] = self._store.load_skill(row["name"]).manifest
        for name, skill in self._extra.items():
            merged.setdefault(name, skill.manifest)
        ordered = [merged.pop(m.name) for m in self._production.manifests() if m.name in merged]
        return ordered + list(merged.values())  # 生产拓扑序在前,草稿新增附后


def _loadable(store: DraftStore, name: str) -> bool:
    """草稿当前可解析(不合规的草稿不进清单,半成品不遮蔽生产)。"""
    try:
        store.load_skill(name)
    except (FileNotFoundError, SkillLoadError, ValueError):
        return False
    return True


def _write_with_bak(path: Path, text: str) -> None:
    """覆盖写;已存在的上一版先复制为 ``<file>.bak``(保存面唯一的后悔药,§2.3)。"""
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.write_text(text, encoding="utf-8")


def _manifest_to_dict(manifest: SkillManifest) -> dict[str, Any]:
    """SkillManifest → skills.yaml 单条同形 dict(create --from 复制用)。"""
    out: dict[str, Any] = {
        "name": manifest.name,
        "version": manifest.version,
        "kind": manifest.kind.value,
        "description": manifest.description,
        "inputs": manifest.inputs,
        "outputs": manifest.outputs,
        "permissions": {
            "tools": list(manifest.permissions.tools),
            "skills": list(manifest.permissions.skills),
        },
    }
    if manifest.permissions.blackboard:
        out["permissions"]["blackboard"] = list(manifest.permissions.blackboard)
    if manifest.model is not None:
        out["model"] = {"prefer": list(manifest.model.prefer)}
        if manifest.model.temperature is not None:
            out["model"]["temperature"] = manifest.model.temperature
    if manifest.context_policy is not None:
        out["context_policy"] = {
            "max_tokens": manifest.context_policy.max_tokens,
            "compress": manifest.context_policy.compress,
        }
    if manifest.limits is not None:
        out["limits"] = {
            k: v
            for k, v in {
                "max_steps": manifest.limits.max_steps,
                "timeout": manifest.limits.timeout,
                "retry": manifest.limits.retry,
                "max_tool_calls": manifest.limits.max_tool_calls,
            }.items()
            if v is not None
        }
    if manifest.handler:
        out["handler"] = manifest.handler
    if manifest.entry:
        out["entry"] = manifest.entry
    if manifest.verifier:
        out["verifier"] = manifest.verifier
    if manifest.logic is not None:
        out["logic"] = manifest.logic
    if manifest.inline:
        out["inline"] = True
    if manifest.trust is not None:
        out["trust"] = {
            k: v
            for k, v in {
                "confirm": manifest.trust.confirm,
                "reversal": manifest.trust.reversal,
                "blast_radius": manifest.trust.blast_radius,
            }.items()
            if v is not None
        }
    return out

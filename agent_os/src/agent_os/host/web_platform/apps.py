"""App 协议面(docs/APP-MODEL.md §2/§9;M1):AppManifest 注册表 + AppInstance 存储。

**设计要点**:

- AppManifest = app 类型的静态面(kind/v/title/surfaces{card,tab}/state_schema/
  actions)。注册即校验(§9):双表面声明齐、action 的 skill 在绑定表内、
  args_from 不越 state_schema——非法 manifest **拒绝注册**(ValueError);
- action 的 ``skill`` 是声明式绑定键(``platform.*``):M1 不引入新逻辑,
  绑定表(web_platform/app.py 的 ``SKILL_BINDINGS``)把键映射到与旧
  cards/action **同一份**薄 handler——白名单从代码升格为 manifest 数据,
  零新权限通道;
- AppInstance = 运行态(id/kind/ref/title/state/created_by/created_at)。
  ``ref`` 是 app 的业务锚(草稿名/plan_id/question_id…),kind+ref 唯一
  (注册即去重,与 Compositor 的打开/聚焦同一语义)。M1 内存态,
  持久化归 M2(docs/APP-MODEL.md §10)。
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

_log = logging.getLogger("agent_os.platform.apps")

#: action 可声明的表面(§3 两张面孔;action 至少出其一)
SURFACES = ("card", "tab")

#: instance id 合法面(``app-`` + hex;文件持久化的路径穿越防护,与 SessionStore 同哲学)
_INSTANCE_ID_RE = re.compile(r"^app-[0-9a-f]{8}$")


def validate_manifest(manifest: dict[str, Any], *, known_skills: set[str]) -> None:
    """manifest 注册校验(docs/APP-MODEL.md §9;不合 → ValueError,拒绝注册)。

    - kind/v/title 齐备;surfaces 双表面(card+tab)都声明(不变量 1:
      每个 app 必须有两张面孔);
    - state_schema 是 object schema;
    - 每个 action:id/label 齐、skill ∈ known_skills(绑定表——防声明一个
      没有实现的调用)、args_from 每项形如 ``state.<key>[.<sub>…]`` 且顶层
      key ∈ state_schema.properties(参数来源必须落在 state 内,防悬空绑定)、
      surface ⊆ {card, tab} 且非空。
    """
    kind = manifest.get("kind")
    if not kind or not isinstance(kind, str):
        raise ValueError(f"manifest 缺 kind: {manifest!r}")
    if manifest.get("v") != 1:
        raise ValueError(f"manifest {kind}: v 应为 1,得到 {manifest.get('v')!r}")
    if not manifest.get("title"):
        raise ValueError(f"manifest {kind}: 缺 title")
    surfaces = manifest.get("surfaces") or {}
    for face in SURFACES:
        if not surfaces.get(face):
            raise ValueError(f"manifest {kind}: 双表面声明不齐(缺 surfaces.{face})")
    schema = manifest.get("state_schema") or {}
    if schema.get("type") != "object":
        raise ValueError(f"manifest {kind}: state_schema 必须是 object schema")
    props = schema.get("properties") or {}
    for action in manifest.get("actions") or []:
        aid = action.get("id")
        if not aid or not action.get("label"):
            raise ValueError(f"manifest {kind}: action 缺 id/label: {action!r}")
        skill = action.get("skill")
        if skill not in known_skills:
            raise ValueError(f"manifest {kind}: action {aid} 的 skill {skill!r} 未注册(绑定表外)")
        for path in action.get("args_from") or []:
            if not isinstance(path, str) or not path.startswith("state."):
                raise ValueError(f"manifest {kind}: action {aid} 的 args_from 须形如 state.<key>: {path!r}")
            top = path.split(".")[1] if len(path.split(".")) > 1 else ""
            if top not in props:
                raise ValueError(
                    f"manifest {kind}: action {aid} 的 args_from {path!r} 越出 state_schema"
                )
        faces = action.get("surface") or []
        if not faces or any(f not in SURFACES for f in faces):
            raise ValueError(f"manifest {kind}: action {aid} 的 surface 非法: {faces!r}")


class AppRegistry:
    """AppManifest 注册表(进程静态面;非法注册抛 ValueError,与卡协议闸同哲学)。"""

    def __init__(self, *, known_skills: set[str]) -> None:
        self._known_skills = set(known_skills)
        self._manifests: dict[str, dict[str, Any]] = {}

    def register(self, manifest: dict[str, Any]) -> None:
        validate_manifest(manifest, known_skills=self._known_skills)
        self._manifests[manifest["kind"]] = manifest

    def get(self, kind: str) -> dict[str, Any] | None:
        return self._manifests.get(kind)

    def kinds(self) -> list[str]:
        return sorted(self._manifests)

    def action_of(self, kind: str, action_id: str) -> dict[str, Any] | None:
        manifest = self.get(kind)
        for action in (manifest or {}).get("actions") or []:
            if action["id"] == action_id:
                return action
        return None


class AppInstanceStore:
    """AppInstance 存储(M2 文件持久化;kind+ref 去重注册,state 回写即落盘)。

    持久化(docs/APP-MODEL.md §10 M2):``<root>/<instance_id>.json`` 单文件单
    instance,写穿透(register/update_state 即写;与 SessionStore 同哲学——
    平台状态也是宿主产物)。启动时全量加载重建索引(kind+ref 去重在重启后
    依然成立,卡 dict 上的 instance id 由此可跨重启解析);坏文件隔离
    (JSON 坏/形态不合跳过记日志,不拖垮装配);id 合法面防路径穿越。
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_ref: dict[tuple[str, str], str] = {}  # (kind, ref) → id
        self._root = Path(root) if root is not None else None
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        """启动加载:合法文件重建 _by_id/_by_ref;坏文件隔离(跳过 + 记日志)。"""
        assert self._root is not None
        for f in sorted(self._root.glob("*.json")):
            if not _INSTANCE_ID_RE.match(f.stem):
                _log.warning("platform_apps 跳过非法文件名: %s", f.name)
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                _log.warning("platform_apps 坏文件隔离: %s(%s)", f.name, e)
                continue
            if not isinstance(doc, dict) or doc.get("id") != f.stem or not doc.get("kind"):
                _log.warning("platform_apps 形态不合隔离: %s", f.name)
                continue
            self._by_id[doc["id"]] = doc
            self._by_ref[(doc["kind"], str(doc.get("ref") or ""))] = doc["id"]

    def _write(self, inst: dict[str, Any]) -> None:
        """写穿透(M2):单 instance 单文件;写失败记日志不炸调用方(内存仍真)。"""
        if self._root is None:
            return
        try:
            (self._root / f"{inst['id']}.json").write_text(
                json.dumps(inst, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            _log.warning("platform_apps 写盘失败(内存态仍有效): %s", e)

    def register(
        self,
        *,
        kind: str,
        ref: str,
        title: str = "",
        state: dict[str, Any] | None = None,
        created_by: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """登记 instance;同 kind+ref 已存在 → 返回既有 + ``opened=False``
        (与 Compositor 打开去重同语义,docs/APP-MODEL.md §6)。新登记即落盘。"""
        key = (kind, ref)
        existing = self._by_ref.get(key)
        if existing is not None:
            return self._by_id[existing], False
        inst = {
            "id": f"app-{uuid.uuid4().hex[:8]}",
            "kind": kind,
            "ref": ref,
            "title": title or ref,
            "state": dict(state or {}),
            "created_by": created_by,
            "created_at": time.time(),
        }
        self._by_id[inst["id"]] = inst
        self._by_ref[key] = inst["id"]
        self._write(inst)
        return inst, True

    def get(self, instance_id: str) -> dict[str, Any] | None:
        if not _INSTANCE_ID_RE.match(instance_id or ""):
            return None  # id 合法面(防穿越;非法 id 一律查无)
        return self._by_id.get(instance_id)

    def update_state(self, instance_id: str, patch: dict[str, Any]) -> None:
        """结果回写(§4 action 管道:skill 调用结果写回 app.state)+ 落盘。"""
        inst = self.get(instance_id)
        if inst is None:
            raise KeyError(f"找不到 app instance: {instance_id}")
        inst["state"].update(patch)
        self._write(inst)


def resolve_state_path(state: dict[str, Any], path: str) -> Any:
    """``state.a.b`` → 逐层取值;任一层缺失 → KeyError(管道归 400:参数绑定缺源)。"""
    parts = path.split(".")[1:]  # 剥 "state" 前缀
    node: Any = state
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(path)
    return node


def bind_args(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """按 args_from 从 instance.state 绑定参数(键 = 路径末段,§4 参数绑定面)。"""
    payload: dict[str, Any] = {}
    for path in action.get("args_from") or []:
        payload[path.split(".")[-1]] = resolve_state_path(state, path)
    return payload


def default_manifests() -> list[dict[str, Any]]:
    """M1 首批 kind(docs/APP-MODEL.md §8 迁移地图):conversation + 现有六卡型
    + escalation(七加一)。actions = 旧 ACTION_WHITELIST 的声明式化,
    skill 键对应 app.py ``SKILL_BINDINGS``(同一份 handler 实现)。
    """
    def obj(**props: Any) -> dict[str, Any]:
        return {"type": "object", "properties": props}

    return [
        {
            "kind": "conversation",
            "v": 1,
            "title": "{title}",
            "surfaces": {"card": "conversation.card", "tab": "conversation.tab"},
            "state_schema": obj(messages={"type": "array"}, outbox={"type": "array"}),
            "actions": [],
        },
        {
            "kind": "plan",
            "v": 1,
            "title": "{goal}",
            "surfaces": {"card": "plan.card", "tab": "plan.tab"},
            "state_schema": obj(
                goal={"type": "string"},
                name={"type": "string"},
                template={"type": "string"},
            ),
            "actions": [
                {
                    "id": "scaffold.approve",
                    "label": "platform.act.approve",
                    "skill": "platform.scaffold.approve",
                    "args_from": ["state.name", "state.template"],
                    "surface": ["card", "tab"],
                }
            ],
        },
        {
            "kind": "skill_pack",
            "v": 1,
            "title": "{name}",
            "surfaces": {"card": "skill_pack.card", "tab": "skill_pack.tab"},
            "state_schema": obj(name={"type": "string"}),
            "actions": [],
        },
        {
            "kind": "gate_report",
            "v": 1,
            "title": "{draft}",
            "surfaces": {"card": "gate_report.card", "tab": "gate_report.tab"},
            "state_schema": obj(draft={"type": "string"}, root={"type": "string"}),
            "actions": [
                {
                    "id": "plan.recheck",
                    "label": "platform.act.recheck",
                    "skill": "platform.plan.recheck",
                    "args_from": ["state.root"],
                    "surface": ["card", "tab"],
                }
            ],
        },
        {
            "kind": "diff",
            "v": 1,
            "title": "{name}",
            "surfaces": {"card": "diff.card", "tab": "diff.tab"},
            "state_schema": obj(name={"type": "string"}),
            "actions": [
                {
                    "id": "candidate.accept",
                    "label": "platform.act.accept",
                    "skill": "platform.candidate.accept",
                    "args_from": ["state.name"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "candidate.discard",
                    "label": "platform.act.discard",
                    "skill": "platform.candidate.discard",
                    "args_from": ["state.name"],
                    "surface": ["card", "tab"],
                },
            ],
        },
        {
            "kind": "publish",
            "v": 1,
            "title": "{root}",
            "surfaces": {"card": "publish.card", "tab": "publish.tab"},
            "state_schema": obj(root={"type": "string"}, plan_id={"type": "string"}),
            "actions": [
                {
                    "id": "plan.confirm",
                    "label": "platform.act.confirm",
                    "skill": "platform.plan.confirm",
                    "args_from": ["state.plan_id"],
                    "surface": ["card", "tab"],
                    "confirm": "summary",
                }
            ],
        },
        {
            "kind": "table",
            "v": 1,
            "title": "{title}",
            "surfaces": {"card": "table.card", "tab": "table.tab"},
            "state_schema": obj(title={"type": "string"}),
            "actions": [],
        },
        {
            "kind": "escalation",
            "v": 1,
            "title": "{skill}",
            "surfaces": {"card": "escalation.card", "tab": "escalation.tab"},
            "state_schema": obj(question_id={"type": "string"}, skill={"type": "string"}),
            "actions": [
                {
                    "id": "approve-once",
                    "label": "platform.esc.approve.once",
                    "skill": "platform.decision.answer",
                    "args_from": ["state.question_id"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "approve-run",
                    "label": "platform.esc.approve.run",
                    "skill": "platform.decision.answer",
                    "args_from": ["state.question_id"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "deny",
                    "label": "platform.esc.deny",
                    "skill": "platform.decision.answer",
                    "args_from": ["state.question_id"],
                    "surface": ["card", "tab"],
                },
            ],
        },
    ]

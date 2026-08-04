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

import jsonschema

_log = logging.getLogger("agent_os.platform.apps")

#: action 可声明的表面(§3 两张面孔;action 至少出其一)
SURFACES = ("card", "tab")

#: exec 三态(docs/APP-MODEL.md v0.2 §4):endpoint=确定性写转发既有端点;
#: run=agentic 起 run 跑 skill;local=纯 state 不出海(不进管道)
EXEC_MODES = ("endpoint", "run", "local")

#: instance id 合法面(``app-`` + hex;文件持久化的路径穿越防护,与 SessionStore 同哲学)
_INSTANCE_ID_RE = re.compile(r"^app-[0-9a-f]{8}$")


def normalize_exec(action: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """exec 归一(v0.2 §12 同构迁移):旧 ``skill`` 键视为
    ``exec:{mode:"endpoint", ref}`` 并 warn(一个版本期后移除;新 manifest 只用 exec)。
    返回带规范 exec 的 action 副本;两键皆无 → 原样返回(交给校验拒绝)。
    """
    if action.get("exec") is not None:
        return dict(action)
    if action.get("skill") is not None:
        _log.warning(
            "manifest %s action %s: 旧 skill 键迁移期为 exec.endpoint,请改用 exec 字段",
            kind, action.get("id"),
        )
        return {**action, "exec": {"mode": "endpoint", "ref": action["skill"]}}
    return dict(action)


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
        # exec(v0.2 §4/§7 强制项):三态归态是授权面,缺省/归错 = 授权漏洞,拒绝注册
        exec_ = action.get("exec")
        if exec_ is None:
            raise ValueError(f"manifest {kind}: action {aid} 缺 exec(v0.2 强制项)")
        if not isinstance(exec_, dict):
            raise TypeError(f"manifest {kind}: action {aid} 的 exec 必须是 mapping")
        mode = exec_.get("mode")
        if mode not in EXEC_MODES:
            raise ValueError(f"manifest {kind}: action {aid} exec.mode 非法: {mode!r}(三态 {EXEC_MODES})")
        ref = exec_.get("ref")
        if mode == "local":
            if ref is not None:
                raise ValueError(f"manifest {kind}: action {aid} local 态无 ref(不出海)")
        elif ref not in known_skills:
            raise ValueError(f"manifest {kind}: action {aid} 的 exec.ref {ref!r} 未注册(绑定表外)")
        for path in action.get("args_from") or []:
            if not isinstance(path, str) or not path.startswith("state."):
                raise ValueError(f"manifest {kind}: action {aid} 的 args_from 须形如 state.<key>: {path!r}")
            top = path.split(".")[1] if len(path.split(".")) > 1 else ""
            if top not in props:
                raise ValueError(
                    f"manifest {kind}: action {aid} 的 args_from {path!r} 越出 state_schema"
                )
        # args_input(v0.2 §4):{name: schema}——客户端载荷的唯一合法面,
        # "哪些参数用户能控"在 manifest 上一眼可见
        args_input = action.get("args_input")
        if args_input is not None and (
            not isinstance(args_input, dict)
            or any(not isinstance(s, dict) for s in args_input.values())
        ):
            raise ValueError(f"manifest {kind}: action {aid} 的 args_input 必须是 {{name: schema}}")
        faces = action.get("surface") or []
        if not faces or any(f not in SURFACES for f in faces):
            raise ValueError(f"manifest {kind}: action {aid} 的 surface 非法: {faces!r}")


class AppRegistry:
    """AppManifest 注册表(进程静态面;非法注册抛 ValueError,与卡协议闸同哲学)。"""

    def __init__(self, *, known_skills: set[str]) -> None:
        self._known_skills = set(known_skills)
        self._manifests: dict[str, dict[str, Any]] = {}

    def register(self, manifest: dict[str, Any]) -> None:
        kind = manifest.get("kind", "")
        # 迁移期兼容(v0.2 §12):旧 skill 键先归一为 exec 再校验(带 warn)
        manifest = {
            **manifest,
            "actions": [normalize_exec(a, kind=kind) for a in manifest.get("actions") or []],
        }
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

    def all(self) -> list[dict[str, Any]]:
        """全量 instance(M4b 主动汇报:按 created_by 回溯发起会话的扫描面)。"""
        return list(self._by_id.values())

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
    """args_from 从 instance.state 绑定(键 = 路径末段)。

    v0.2 §4 两分约定:本函数产出 **bound**(服务端权威,客户端改不了);
    客户端载荷走 :func:`validate_args_input` 产出 input——管道合并
    ``{**bound, **input}`` 调 handler,调用面单一而来源分明。
    """
    payload: dict[str, Any] = {}
    for path in action.get("args_from") or []:
        payload[path.split(".")[-1]] = resolve_state_path(state, path)
    return payload


def validate_args_input(action: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """args_input 通道(v0.2 §4):客户端载荷**只许**出现在 args_input 声明内,
    逐项过 schema;未声明的键(伪装 args_from 字段的越权构造)或不合法值
    → ValueError(管道归 400)。

    定性(v0.2 §4):args_input 是客户端**声明**而非证明(如 warnings_ack
    "人已阅读")——授权语义由服务端既有闸门兜底(promote 复跑),不在这层放大。
    """
    declared = action.get("args_input") or {}
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key not in declared:
            raise ValueError(f"客户端参数 {key!r} 未在该 action 的 args_input 声明内")
        schema = declared[key]
        try:
            jsonschema.validate(value, schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"客户端参数 {key!r} 不合 schema: {e.message}") from e
        out[key] = value
    return out


def default_manifests() -> list[dict[str, Any]]:
    """kind 注册表(docs/APP-MODEL.md §8;M1 八个 + M3 三个)。

    actions = 旧 ACTION_WHITELIST 的声明式化;exec 归态(v0.2 §4/§12):
    现状全部 endpoint(薄 handler 转发既有端点;iterate.generate 语义是
    run 态——它不在 manifest 上,绑定表注释归态,M4 分真 run 通道);
    conversation 的 spawn/pin/close = local(纯 UI 动作,声明归态但不出海)。
    """
    def obj(**props: Any) -> dict[str, Any]:
        return {"type": "object", "properties": props}

    return [
        {
            # M5(docs/APP-MODEL.md §13):根 app——唯一由 bootstrap 实例化、
            # 不由 action 孵化的特例(递归有底);与所有 manifest 过同一协议校验
            "kind": "shell",
            "v": 1,
            "title": "shell",
            "surfaces": {"card": "shell.card", "tab": "shell.tab"},
            "state_schema": obj(
                tabs={"type": "array"},
                active_tab={"type": "string"},
                theme={"type": "string"},
                sessions={"type": "array"},
                layout={"type": "object"},
                widgets={"type": "object"},
                # M5 增补(桌面化 root widget):desktop={pinned, wallpaper}
                desktop={"type": "object"},
            ),
            "actions": [
                # §13.1:tab 管理是 local(仅改 shell.state,不出海);
                # theme/session 是 endpoint(持久化偏好/孵化会话,绑定薄 handler)
                {"id": "shell.tab.open", "label": "platform.shell.tab.open",
                 "exec": {"mode": "local"}, "args_from": [],
                 "args_input": {"id": {"type": "string"}, "instance_id": {"type": "string"},
                                 "kind": {"type": "string"}, "ref": {"type": "string"},
                                 "title": {"type": "string"}},
                 "surface": ["card", "tab"]},
                {"id": "shell.tab.focus", "label": "platform.shell.tab.focus",
                 "exec": {"mode": "local"}, "args_from": [],
                 "args_input": {"tab": {"type": "string"}},
                 "surface": ["card", "tab"]},
                {"id": "shell.tab.close", "label": "platform.shell.tab.close",
                 "exec": {"mode": "local"}, "args_from": [],
                 "args_input": {"tab": {"type": "string"}},
                 "surface": ["card", "tab"]},
                {"id": "shell.theme.set", "label": "platform.shell.theme.set",
                 "exec": {"mode": "endpoint", "ref": "platform.shell.theme.set"}, "args_from": [],
                 "args_input": {"theme": {"type": "string"}},
                 "surface": ["card", "tab"]},
                {"id": "shell.session.create", "label": "platform.shell.session.create",
                 "exec": {"mode": "endpoint", "ref": "platform.shell.session.create"}, "args_from": [],
                 "args_input": {},
                 "surface": ["card", "tab"]},
                {"id": "shell.layout.set", "label": "platform.shell.layout.set",
                 "exec": {"mode": "local"}, "args_from": [],
                 "args_input": {"icon_mode": {"type": "boolean"}},
                 "surface": ["card", "tab"]},
                {"id": "shell.layout.move_tab", "label": "platform.shell.layout.move_tab",
                 "exec": {"mode": "local"}, "args_from": [],
                 "args_input": {"tab": {"type": "string"}, "before": {"type": "string"}},
                 "surface": ["card", "tab"]},
                # M5 增补(桌面化 root widget):最小化 = 无激活 tab(回桌面,tab 保留);
                # desktop.set = 桌面开关持久化(壁纸;pinned 键预留,本期无 UI 面)
                {"id": "shell.tab.minimize", "label": "platform.shell.tab.minimize",
                 "exec": {"mode": "local"}, "args_from": [],
                 "args_input": {},
                 "surface": ["card", "tab"]},
                {"id": "shell.desktop.set", "label": "platform.shell.desktop.set",
                 "exec": {"mode": "local"}, "args_from": [],
                 "args_input": {"wallpaper": {"type": "boolean"}},
                 "surface": ["card", "tab"]},
            ],
        },
        {
            "kind": "conversation",
            "v": 1,
            "title": "{title}",
            "surfaces": {"card": "conversation.card", "tab": "conversation.tab"},
            "state_schema": obj(messages={"type": "array"}, outbox={"type": "array"}),
            "actions": [
                # v0.2 §5.3:纯 UI 动作归 local(不出海、不进管道,前端本地处理)
                {"id": "spawn", "label": "platform.act.spawn", "exec": {"mode": "local"},
                 "args_from": [], "surface": ["card", "tab"]},
                {"id": "pin", "label": "platform.act.pin", "exec": {"mode": "local"},
                 "args_from": [], "surface": ["card", "tab"]},
                {"id": "close", "label": "platform.act.close", "exec": {"mode": "local"},
                 "args_from": [], "surface": ["card", "tab"]},
            ],
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
                    "exec": {"mode": "endpoint", "ref": "platform.scaffold.approve"},
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
                    "exec": {"mode": "endpoint", "ref": "platform.plan.recheck"},
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
                    "id": "iterate.generate",
                    "label": "platform.act.iterate",
                    # M4a:run 真通道(v0.2 §4)——agentic 起 run,管道 spawn run app 持 run_id
                    "exec": {"mode": "run", "ref": "platform.iterate.generate"},
                    "args_from": ["state.name"],
                    "args_input": {"comments": {"type": "array"}, "note": {"type": "string"}},
                    "surface": ["card", "tab"],
                },
                {
                    "id": "candidate.accept",
                    "label": "platform.act.accept",
                    "exec": {"mode": "endpoint", "ref": "platform.candidate.accept"},
                    "args_from": ["state.name"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "candidate.discard",
                    "label": "platform.act.discard",
                    "exec": {"mode": "endpoint", "ref": "platform.candidate.discard"},
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
                    "exec": {"mode": "endpoint", "ref": "platform.plan.confirm"},
                    "args_from": ["state.plan_id"],
                    # v0.2 §4:warnings_ack 是客户端**声明**(人已阅读),唯一可控参数;
                    # 授权语义由服务端 promote 复跑兜底,不在此层放大
                    "args_input": {"warnings_ack": {"type": "boolean"}},
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
                    "exec": {"mode": "endpoint", "ref": "platform.decision.answer"},
                    "args_from": ["state.question_id"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "approve-run",
                    "label": "platform.esc.approve.run",
                    "exec": {"mode": "endpoint", "ref": "platform.decision.answer"},
                    "args_from": ["state.question_id"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "deny",
                    "label": "platform.esc.deny",
                    "exec": {"mode": "endpoint", "ref": "platform.decision.answer"},
                    "args_from": ["state.question_id"],
                    "surface": ["card", "tab"],
                },
            ],
        },
        # ── M3:run/debug/lab-draft(docs/APP-MODEL.md §8/§10)─────────────
        {
            "kind": "run",
            "v": 1,
            "title": "{run_id}",
            "surfaces": {"card": "run.card", "tab": "run.tab"},
            "state_schema": obj(
                run_id={"type": "string"},
                status={"type": "string"},
                result={},
                skill={"type": "string"},
            ),
            "actions": [
                {
                    "id": "run.stop",
                    "label": "platform.run.stop",
                    "exec": {"mode": "endpoint", "ref": "platform.run.stop"},
                    "args_from": ["state.run_id"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "run.resume",
                    "label": "platform.run.resume",
                    "exec": {"mode": "endpoint", "ref": "platform.run.resume"},
                    "args_from": ["state.run_id"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "run.rerun",
                    "label": "platform.run.rerun",
                    "exec": {"mode": "endpoint", "ref": "platform.run.rerun"},
                    "args_from": ["state.run_id"],
                    "surface": ["card", "tab"],
                },
                {
                    # M4a 发起面归一:app 内"再跑一次"(input 骨架可改,服务端校验)
                    "id": "run.launch",
                    "label": "platform.run.launch",
                    "exec": {"mode": "run", "ref": "platform.run.launch"},
                    "args_from": ["state.skill"],
                    "args_input": {"input": {"type": "object"}},
                    "surface": ["card", "tab"],
                },
            ],
        },
        {
            "kind": "debug",
            "v": 1,
            "title": "{session_id}",
            "surfaces": {"card": "debug.card", "tab": "debug.tab"},
            "state_schema": obj(session_id={"type": "string"}, run_id={"type": "string"}),
            "actions": [
                {
                    "id": "debug.continue",
                    "label": "platform.debug.continue",
                    "exec": {"mode": "endpoint", "ref": "platform.debug.command"},
                    "args_from": ["state.session_id"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "debug.stop",
                    "label": "platform.debug.stop",
                    "exec": {"mode": "endpoint", "ref": "platform.debug.command"},
                    "args_from": ["state.session_id"],
                    "surface": ["card", "tab"],
                },
            ],
        },
        {
            "kind": "lab-draft",
            "v": 1,
            "title": "{name}",
            "surfaces": {"card": "lab_draft.card", "tab": "lab_draft.tab"},
            "state_schema": obj(name={"type": "string"}, root={"type": "string"}),
            "actions": [
                {
                    "id": "draft.check",
                    "label": "platform.draft.check",
                    "exec": {"mode": "endpoint", "ref": "platform.draft.check"},
                    "args_from": ["state.name"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "draft.promote",
                    "label": "platform.draft.promote",
                    "exec": {"mode": "endpoint", "ref": "platform.plan.recheck"},
                    "args_from": ["state.root"],
                    "surface": ["tab"],  # 提交是重动作:只在全面出,不上卡面(§3 卡面 ≤2 轻动作)
                },
            ],
        },
        # ── D1(docs/DOC-EDITOR.md §2):doc app kind ─────────────────────
        {
            "kind": "doc",
            "v": 1,
            "title": "{name}",
            "surfaces": {"card": "doc.card", "tab": "doc.tab"},
            "state_schema": obj(
                name={"type": "string"},
                text={"type": "string"},
                dirty={"type": "boolean"},
                savedAt={"type": "number"},
                view={"type": "string"},
                versions={"type": "array"},
                bubbles={"type": "array"},
            ),
            "actions": [
                {
                    "id": "doc.save",
                    "label": "platform.doc.save",
                    "exec": {"mode": "endpoint", "ref": "platform.doc.save"},
                    "args_from": ["state.name"],
                    "args_input": {"text": {"type": "string"}},
                    "surface": ["card", "tab"],
                },
                {
                    "id": "doc.snapshot",
                    "label": "platform.doc.snapshot",
                    "exec": {"mode": "endpoint", "ref": "platform.doc.snapshot"},
                    "args_from": ["state.name"],
                    "surface": ["tab"],
                },
                {
                    "id": "doc.rewind",
                    "label": "platform.doc.rewind",
                    "exec": {"mode": "endpoint", "ref": "platform.doc.rewind"},
                    "args_from": ["state.name"],
                    "args_input": {"version": {"type": "string"}},
                    "surface": ["tab"],
                },
                {
                    "id": "doc.export",
                    "label": "platform.doc.export",
                    "exec": {"mode": "endpoint", "ref": "platform.doc.export"},
                    "args_from": ["state.name"],
                    "surface": ["card", "tab"],
                },
                {
                    "id": "meta.set",
                    "label": "platform.doc.meta.set",
                    # 视图切换/标题等纯 state(docs/DOC-EDITOR.md §3;local mutator 注册面)
                    "exec": {"mode": "local"},
                    "args_from": [],
                    "args_input": {"view": {"type": "string"}, "dirty": {"type": "boolean"}},
                    "surface": ["tab"],
                },
                {
                    # D2(§3 comment.apply;endpoint):agent 建议的替换文本,人按才落
                    "id": "comment.apply",
                    "label": "platform.doc.apply",
                    "exec": {"mode": "endpoint", "ref": "platform.doc.apply"},
                    "args_from": ["state.name"],
                    "args_input": {
                        "anchor": {"type": "string"},
                        "replace_text": {"type": "string"},
                        "expected": {"type": "string"},
                    },
                    "surface": ["tab"],
                },
            ],
        },
        # ── M4b:legacy 五页(docs/APP-MODEL.md §8 迁移地图末行)────────────
        # 旧 UI 整页以 Tab Surface 接入(能挂 ES module 的直接挂载,runs 深链);
        # state 最小(本页无服务端动作;打开/关闭走 Compositor,local 语义)
        *[
            {
                "kind": kind,
                "v": 1,
                "title": "{" + kind + "}",
                "surfaces": {"card": f"{kind}.card", "tab": f"{kind}.tab"},
                "state_schema": obj(),
                "actions": [],
            }
            for kind in ("skills", "runs", "tools", "lab", "debug-old")
        ],
    ]

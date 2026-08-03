"""产物卡协议(docs/WEB-PLATFORM.md §4;方案 A:页面 = 对话中生成的卡片)。

卡 = ``{type, v: 1, data, actions}``。卡型注册表本期六型:
plan / skill_pack / gate_report / diff / publish / table——每型一个 build
函数 + schema 校验(``validate_card``)。

**安全红线**(docs/WEB-PLATFORM.md §7):``actions`` 只允许指向**白名单内**的
既有内部端点(卡上按钮 = 既有能力的动作映射,不是任意 URL 发射器)。
``endpoint`` 是模板形态(``{name}`` 占位由 payload 填充),``_ACTION_WHITELIST``
是唯一裁决面——开新动作 = 先在这里登记(代码评审面)。
"""

from __future__ import annotations

import time
from typing import Any

#: 卡型注册表(v1;新增卡型 = 加一行 + 一个 build 函数 + schema 校验分支)
CARD_TYPES = ("plan", "skill_pack", "gate_report", "diff", "publish", "table")

#: action 白名单:action id → (method, endpoint 模板)。只允许指向既有端点
#: (Lab / iterate / packages / candidate / versions),不引入新的 promote 路径。
ACTION_WHITELIST: dict[str, tuple[str, str]] = {
    "scaffold.approve": ("POST", "/api/lab/drafts"),
    "iterate.generate": ("POST", "/api/lab/drafts/{name}/iterate"),
    "candidate.accept": ("POST", "/api/lab/drafts/{name}/candidate/accept"),
    "candidate.discard": ("POST", "/api/lab/drafts/{name}/candidate/discard"),
    "plan.recheck": ("POST", "/api/lab/packages/{root}/plan"),
    "plan.confirm": ("POST", "/api/lab/packages/promote"),
    "version.rewind": ("POST", "/api/lab/drafts/{name}/rewind"),
}


def _card(type_: str, data: dict[str, Any], actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    card = {"type": type_, "v": 1, "ts": time.time(), "data": data, "actions": actions or []}
    validate_card(card)
    return card


def validate_card(card: dict[str, Any]) -> None:
    """schema 校验(不合 → ValueError;卡出服务端前必须过这道闸)。

    - type ∈ 注册表、v == 1、data 是 dict;
    - 每个 action:id ∈ 白名单,method/endpoint 与白名单一致(防"卡自己声明
      一个白名单外的端点"——声明面必须与裁决面一致)。
    """
    if not isinstance(card, dict):
        raise TypeError("卡必须是 dict")
    if card.get("type") not in CARD_TYPES:
        raise ValueError(f"未知卡型: {card.get('type')!r}(注册表: {list(CARD_TYPES)})")
    if card.get("v") != 1:
        raise ValueError(f"卡协议版本应为 1,得到: {card.get('v')!r}")
    if not isinstance(card.get("data"), dict):
        raise TypeError("卡 data 必须是 dict")
    for action in card.get("actions") or []:
        action_id = action.get("id")
        if action_id not in ACTION_WHITELIST:
            raise ValueError(f"action {action_id!r} 不在白名单(裁决面 ACTION_WHITELIST)")
        want_method, want_path = ACTION_WHITELIST[action_id]
        if action.get("method") != want_method or action.get("endpoint") != want_path:
            raise ValueError(
                f"action {action_id!r} 的端点应为 {want_method} {want_path},"
                f"得到: {action.get('method')} {action.get('endpoint')}"
            )


def _action(action_id: str, label: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    method, path = ACTION_WHITELIST[action_id]
    return {"id": action_id, "label": label, "method": method, "endpoint": path, "payload": payload or {}}


# ----------------------------------------------------------------------
# 六卡型的 build(每型一个;data 形态见各 docstring,前端据此零分支渲染)
# ----------------------------------------------------------------------


def build_plan_card(
    *,
    goal: str,
    reuse: list[dict[str, str]],
    create: list[dict[str, str]],
    approve_payload: dict[str, Any],
) -> dict[str, Any]:
    """plan 卡(意图①"做个 X 技能"):分解——复用哪些已有技能、建议新建哪些。

    data: {goal, reuse: [{name, reason}], create: [{name, template, reason}]};
    动作 = scaffold.approve(批准即走既有 scaffold 端点生成首稿)。
    """
    return _card(
        "plan",
        {"goal": goal, "reuse": reuse, "create": create},
        [_action("scaffold.approve", "批准并生成首稿", approve_payload)],
    )


def build_skill_pack_card(*, name: str, tier: str, members: list[str]) -> dict[str, Any]:
    """skill_pack 卡:一套技能(包)的身份证(name/tier/成员)。"""
    return _card("skill_pack", {"name": name, "tier": tier, "members": members})


def build_gate_report_card(
    *,
    draft: str,
    status: str,
    gates: dict[str, Any],
    plan_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """gate_report 卡:五关结果(状态 + 逐关);全绿时给 plan.recheck 动作(进提交计划)。"""
    actions = [_action("plan.recheck", "生成提交计划", plan_payload)] if status != "fail" and plan_payload else []
    return _card("gate_report", {"draft": draft, "status": status, "gates": gates}, actions)


def build_diff_card(
    *,
    name: str,
    diff: dict[str, Any],
    accept: bool = True,
) -> dict[str, Any]:
    """diff 卡(Flow C 同构):working → candidate 的变化 + 接受/放弃动作。"""
    actions = []
    if accept and diff.get("has_changes"):
        actions = [
            _action("candidate.accept", "✓ 接受", {"name": name}),
            _action("candidate.discard", "放弃", {"name": name}),
        ]
    return _card("diff", {"name": name, "diff": diff}, actions)


def build_publish_card(
    *,
    root: str,
    members: list[dict[str, Any]],
    plan_id: str,
    package_hash: str = "",
    blockers: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """publish 卡:提交计划摘要(成员 create/replace/unchanged)+ plan.confirm 动作。

    ``package_hash``/``blockers``/``warnings`` 随卡携带(plan 详情 tab 直接渲染,
    不必回拉——审的就是要执行的,同一包哈希面,§6.3)。
    """
    return _card(
        "publish",
        {
            "root": root,
            "members": members,
            "plan_id": plan_id,
            "package_hash": package_hash,
            "blockers": blockers or [],
            "warnings": warnings or [],
        },
        [_action("plan.confirm", "确认发布", {"plan_id": plan_id})],
    )


def build_table_card(
    *, title: str, columns: list[str], rows: list[list[Any]], ref: dict[str, Any] | None = None
) -> dict[str, Any]:
    """table 卡:通用筛选表(RCA 摘要/失败 run 列表/help 引导都复用它)。

    ``ref``(可选):详情链接的锚(run 摘要卡 = {kind: "run", id}——前端据以
    开详情 tab,不必从截断的展示文本里反推 id)。
    """
    data: dict[str, Any] = {"title": title, "columns": columns, "rows": rows}
    if ref:
        data["ref"] = ref
    return _card("table", data)

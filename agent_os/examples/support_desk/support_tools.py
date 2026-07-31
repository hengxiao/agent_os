"""support_desk 示例的宿主侧模块:场景工具 + code 技能 handlers(数据 mock,接口仿真)。

为什么同居一个模块:锚点 tests/test_support_example.py 只在 ``_build`` 里保证
``import support_tools`` 时示例目录在 sys.path 上(run 时不在);code 技能的
dotted path 是 run 时惰性 import 的,放在本模块即可稳定命中 sys.modules 缓存
(与 examples/skills_100 用独立模块名 skills100_handlers 是同一类约束)。

两部分内容:

1. 场景工具(七个,四种权限等级),经配置 ``[tools.custom] module = "support_tools:register"``
   装配(runtime/config.py 的 custom 扩展点);读路径命中 ``data/`` 下的 JSON 快照,
   写路径以 JSONL 追加落盘(退款/通知/升级各有副作用文件,调试时可直读核对);
2. code 技能 handlers(十一个),供 ``skills.yaml`` 经 ``support_tools:<name>``
   惰性加载;签名约定(DESIGN.md §6.3):``async def run(input: dict, ctx) -> dict``。

全部为确定性实现:同一 mock 数据集上重跑,run 结果逐字节一致(调试可复现);
副作用文件里的落盘时间来自真实时钟,不进入 run 结果,不影响确定性。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_os.api.v1 import Permission

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load(name: str, key: str) -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))[key]


def _find(rows: list[dict[str, Any]], field: str, value: str, what: str) -> dict[str, Any]:
    for row in rows:
        if row[field] == value:
            return row
    raise KeyError(f"{what}不存在: {value}")


def _append_jsonl(name: str, record: dict[str, Any]) -> None:
    with (DATA_DIR / name).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now() -> str:
    """副作用日志的落盘时间(真实时钟;run 结果不依赖它,确定性不受影响)。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def register(registry: Any) -> None:
    """把七个场景工具注册进 ``registry``(配置扩展点 / 测试的直接入口)。"""

    @registry.tool(permission=Permission.READ, name="project.support_desk.get_ticket")
    def get_ticket(ticket_id: str) -> dict:
        """按工单号取工单详情(用户/订单/正文/创建时间)。"""
        return _find(_load("tickets.json", "tickets"), "ticket_id", ticket_id, "工单")

    @registry.tool(permission=Permission.READ, name="project.support_desk.get_order")
    def get_order(order_id: str) -> dict:
        """按订单号取订单详情(金额/状态/创建与签收时间/商品行)。"""
        return _find(_load("orders.json", "orders"), "order_id", order_id, "订单")

    @registry.tool(permission=Permission.READ, name="project.support_desk.get_user")
    def get_user(user_id: str) -> dict:
        """按用户号取用户资料(姓名/邮箱/会员等级)。"""
        return _find(_load("users.json", "users"), "user_id", user_id, "用户")

    @registry.tool(permission=Permission.READ, name="project.support_desk.search_policy")
    def search_policy(query: str) -> dict:
        """检索退款政策文档;返回当前生效版本全文(窗口/上限/升级队列)。"""
        policies = json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))
        return {"query": query, "policies": policies}

    @registry.tool(permission=Permission.WRITE, name="project.support_desk.escalate_to_human")
    def escalate_to_human(ticket_id: str, reason: str, priority: str) -> dict:
        """把工单升级人工专项组,登记升级单(写 escalations.jsonl)。"""
        record = {
            "escalation_id": f"E-{ticket_id}",
            "ticket_id": ticket_id,
            "reason": reason,
            "priority": priority,
            "queue": "refund-specialists",
            "sla_hours": 24,
            "escalated_at": _now(),
        }
        _append_jsonl("escalations.jsonl", record)
        return {"escalation_id": record["escalation_id"], "queue": record["queue"]}

    @registry.tool(permission=Permission.NET, name="project.support_desk.send_notification")
    def send_notification(user_id: str, channel: str, message: str) -> dict:
        """给客户发通知(mock:不触网,写 notifications.jsonl 留痕)。"""
        record = {
            "notification_id": f"N-{user_id}-{hashlib.md5(message.encode('utf-8')).hexdigest()[:8]}",
            "user_id": user_id,
            "channel": channel,
            "message": message,
            "sent_at": _now(),
        }
        _append_jsonl("notifications.jsonl", record)
        return {"notification_id": record["notification_id"], "delivered": True}

    @registry.tool(permission=Permission.EXEC, name="project.support_desk.issue_refund")
    def issue_refund(ticket_id: str, order_id: str, amount_cents: int, reason: str) -> dict:
        """执行退款入账(写 refunds.jsonl;幂等键 = 工单 + 订单)。"""
        record = {
            "refund_id": f"R-{ticket_id}-{order_id}",
            "ticket_id": ticket_id,
            "order_id": order_id,
            "amount_cents": amount_cents,
            "reason": reason,
            "status": "issued",
            "issued_at": _now(),
        }
        _append_jsonl("refunds.jsonl", record)
        return {"refund_id": record["refund_id"], "status": "issued"}


# ---------------------------------------------------------------------------
# code 技能 handlers(skills.yaml 经 support_tools:<name> 惰性加载)
# ---------------------------------------------------------------------------

#: 类目 → 基础优先级分(estimate_priority 的确定性打分表)
_CATEGORY_SCORE = {"damaged": 80, "complaint": 70, "refund_request": 50, "inquiry": 20}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _hours(earlier: str, later: str) -> int:
    """两个 ISO 时间戳之间的整小时差(向下取整,确定性)。"""
    return int((_parse(later) - _parse(earlier)).total_seconds() // 3600)


async def _call(ctx: Any, tool: str, args: dict[str, Any]) -> Any:
    """call_tool 的 ok 断言包装:工具失败即抛,由内核折叠为父帧错误观察。"""
    res = await ctx.call_tool(tool, args)
    assert res["ok"], f"工具 {tool} 调用失败: {res['error']}"
    return res["value"]


async def gather_context(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """聚合工单+订单+用户,并算出审批要用的时效事实(以工单创建时刻为"现在")。"""
    ticket = await _call(ctx, "project.support_desk.get_ticket", {"ticket_id": input["ticket_id"]})
    order = await _call(ctx, "project.support_desk.get_order", {"order_id": ticket["order_id"]})
    user = await _call(ctx, "project.support_desk.get_user", {"user_id": ticket["user_id"]})
    delivered = order["delivered_at"] is not None
    hours_created = _hours(order["created_at"], ticket["created_at"])
    hours_delivered = _hours(order["delivered_at"], ticket["created_at"]) if delivered else None
    facts = {
        "order_total_cents": order["total_cents"],
        "currency": order["currency"],
        "order_delivered": delivered,
        "hours_since_created": hours_created,
        "days_since_created": hours_created // 24,
        "hours_since_delivered": hours_delivered,
        "days_since_delivered": (hours_delivered // 24) if delivered else None,
        "user_tier": user["tier"],
    }
    return {"ticket": ticket, "order": order, "user": user, "facts": facts}


async def check_policy(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """按政策文档计算退款资格;reasons 引用真实数字(窗口/天数/小时)。"""
    res = await _call(ctx, "project.support_desk.search_policy", {"query": "refund windows"})
    windows = res["policies"]["refund_windows"]
    category = input["category"]
    facts = input["facts"]
    reasons: list[str] = []
    eligible = True
    if category == "damaged":
        limit_h = windows["damaged_report_hours"]
        hours = facts["hours_since_delivered"]
        if facts["order_delivered"] and hours <= limit_h:
            reasons.append(f"破损报损在签收后 {hours} 小时内提出,未超 {limit_h} 小时报损窗口")
        else:
            eligible = False
            reasons.append(f"破损报损距签收已 {hours} 小时,超出 {limit_h} 小时报损窗口")
    elif not facts["order_delivered"]:
        tier = facts["user_tier"]
        window = (
            windows["not_delivered_vip_days"]
            if tier == "vip"
            else windows["not_delivered_normal_days"]
        )
        days = facts["days_since_created"]
        label = "VIP 用户" if tier == "vip" else "普通用户"
        if days <= window:
            reasons.append(f"{label}未签收退款窗口 {window} 天,订单创建于 {days} 天前,在窗口内")
        else:
            eligible = False
            reasons.append(f"{label}未签收退款窗口 {window} 天,订单创建于 {days} 天前,超出窗口")
    else:
        window = windows["delivered_no_questions_days"]
        days = facts["days_since_delivered"]
        if days <= window:
            reasons.append(f"签收后无理由退款窗口 {window} 天,订单签收于 {days} 天前,在窗口内")
        else:
            eligible = False
            reasons.append(f"签收后无理由退款窗口 {window} 天,订单签收于 {days} 天前,超出窗口")
    return {"eligible": eligible, "reasons": reasons}


async def compute_refund(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """退款金额 = 订单实付全额(本示例不做部分退款)。"""
    return {"amount_cents": input["order"]["total_cents"]}


async def estimate_priority(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """确定性打分:类目基础分 + VIP/高额加成 → low|medium|high|urgent。"""
    score = _CATEGORY_SCORE.get(input["category"], 20)
    facts = input["facts"]
    if facts.get("user_tier") == "vip":
        score += 10
    if facts.get("order_total_cents", 0) >= 100000:
        score += 10
    priority = (
        "urgent" if score >= 90 else "high" if score >= 70 else "medium" if score >= 40 else "low"
    )
    return {"priority": priority, "score": score}


async def process_refund(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """先经 compute_refund 算金额,再调 issue_refund 工具入账。"""
    amount = (await ctx.invoke("project.support_desk.compute_refund", {"order": input["order"]}))["amount_cents"]
    value = await _call(
        ctx,
        "project.support_desk.issue_refund",
        {
            "ticket_id": input["ticket_id"],
            "order_id": input["order"]["order_id"],
            "amount_cents": amount,
            "reason": "退款申请审核通过,自动退款",
        },
    )
    return {"refund_id": value["refund_id"], "amount_cents": amount}


async def notify_customer(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """把处理结果经 send_notification 送达客户(mock 留痕)。"""
    value = await _call(
        ctx,
        "project.support_desk.send_notification",
        {"user_id": input["user_id"], "channel": "email", "message": input["message"]},
    )
    return {"sent": True, "notification_id": value["notification_id"]}


async def escalate_ticket(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """调 escalate_to_human 工具把工单升级人工专项组。"""
    value = await _call(
        ctx,
        "project.support_desk.escalate_to_human",
        {
            "ticket_id": input["ticket_id"],
            "reason": input["reason"],
            "priority": input["priority"],
        },
    )
    return {"escalation_id": value["escalation_id"], "queue": value["queue"]}


async def write_case_summary(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """归档:PII 脱敏 → 审计编号 → 英文行,拼一份可入库的结案摘要。"""
    zh = (
        f"工单 {input['ticket_id']}({input['category']})处理完成:"
        f"决策 {input['decision']},退款 {input['refund_cents']} 分,"
        f"优先级 {input['priority']}。客户:{input['user']['name']} <{input['user']['email']}>"
    )
    redacted = (await ctx.invoke("project.support_desk.redact_pii", {"text": zh}))["text"]
    audit = (
        await ctx.invoke(
            "project.support_desk.build_audit_log",
            {
                "ticket_id": input["ticket_id"],
                "decision": input["decision"],
                "amount_cents": input["refund_cents"],
            },
        )
    )["audit"]
    en = (await ctx.invoke("project.support_desk.translate_en", {"text": redacted}))["translation"]
    return {"summary": f"{redacted}\nEN: {en}\n审计编号: {audit}"}


async def redact_pii(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """脱敏:邮箱掩码(归档摘要不落明文 PII)。"""
    masked = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "***@***", input["text"])
    return {"text": masked}


async def format_currency(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """分 → 人民币展示串(8900 → '¥89.00')。"""
    return {"text": f"¥{input['amount_cents'] / 100:,.2f}"}


async def build_audit_log(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """构造确定性审计编号(内容散列后缀,跨进程稳定)。"""
    raw = f"{input['ticket_id']}|{input['decision']}|{input['amount_cents']}"
    digest = int(hashlib.md5(raw.encode("utf-8")).hexdigest(), 16) % 10000
    return {"audit": f"AUDIT-{input['ticket_id']}-{input['decision'].upper()}-{digest:04d}"}
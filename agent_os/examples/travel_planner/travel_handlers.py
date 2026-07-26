"""travel_planner 的 code 技能 handlers:plan_route / plan_budget / format_itinerary。

确定性纯函数(无 LLM、无 IO),签名约定(DESIGN.md §6.3):
``async def <name>(input: dict, ctx) -> dict``;供 skills.yaml 经 dotted path
``travel_handlers:<name>`` 惰性加载(模块名独立,避免与其他示例的 handlers 撞名)。
"""

from __future__ import annotations

from typing import Any


def _area_key(attraction: dict[str, Any]) -> str:
    """资料注明的区域(如 Lintong);未注明 → "city"(城区)。"""
    return str(attraction.get("area") or "city")


async def plan_route(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """按区域聚类分天:远郊组(资料注明同一 area)同日,城区按资料顺序均分到剩余天。

    分组排序:远郊组按组内门票总价降序(同价按区域名字典序),城区组最后——
    确定性排序保证同输入同输出。
    """
    attractions = list(input["attractions"])
    n_days = max(1, int(input["days"]))
    groups: dict[str, list[dict[str, Any]]] = {}
    for a in attractions:
        groups.setdefault(_area_key(a), []).append(a)
    far = sorted(
        (k for k in groups if k != "city"),
        key=lambda k: (-sum(float(x.get("price") or 0) for x in groups[k]), k),
    )
    buckets: list[list[str]] = [[] for _ in range(n_days)]
    # 远郊组依次独占一天(组数多于天数时并入最后一天,同组不被拆散)
    di = 0
    for key in far:
        buckets[min(di, n_days - 1)].extend(a["name"] for a in groups[key])
        di += 1
    # 城区按资料顺序均分到剩余天(主题相邻的条目自然同日);无剩余天则并入最后一天
    city = [a["name"] for a in groups.get("city", [])]
    slots = list(range(min(di, n_days), n_days)) or [n_days - 1]
    base, extra = divmod(len(city), len(slots))
    pos = 0
    for rank, slot in enumerate(slots):
        take = base + (1 if rank < extra else 0)
        buckets[slot].extend(city[pos : pos + take])
        pos += take
    return {"days": [{"attractions": names} for names in buckets]}


async def plan_budget(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """预算纯算术:分项 = 单价 × 数量或价目数组求和;total = 五项之和。

    输入价目全部来自 research_destination 的抽取结果(大交通单价/住宿每晚价/
    门票列表/每日餐饮价/当地交通价目),本函数只做算术,可逐数追溯。
    """
    transport = float(input["transport_price"]) * int(input.get("legs") or 1)
    hotel = float(input["hotel_per_night"]) * int(input["nights"])
    tickets = sum(float(p) for p in input["tickets"])
    meals = sum(float(p) for p in input["meals_daily"])
    local = sum(float(p) for p in input["local"])

    def _n(x: float) -> int | float:
        return int(x) if x == int(x) else x

    transport, hotel, tickets, meals, local = (_n(x) for x in (transport, hotel, tickets, meals, local))
    total = _n(transport + hotel + tickets + meals + local)
    return {
        "transport": transport,
        "hotel": hotel,
        "tickets": tickets,
        "meals": meals,
        "local": local,
        "total": total,
    }


async def format_itinerary(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """结构化计划 → markdown 攻略(逐日:景点/餐饮;附住宿、预算、贴士、预订事项)。"""
    days = list(input["days"])
    hotel = input["hotel"]
    budget = input["budget"]
    lines = [f"# {input['destination']} {len(days)} 天旅行攻略", ""]
    if input.get("origin"):
        lines.append(f"出发地:{input['origin']}")
    lines.append(f"住宿:{hotel['name']}({hotel['area']},¥{hotel['price_per_night']}/晚)")
    lines.append("")
    for i, day in enumerate(days, 1):
        lines.append(f"## 第 {i} 天")
        lines.append("- 景点:" + "、".join(day["attractions"]))
        lines.append("- 餐饮:" + "、".join(day["meals"]))
        lines.append("")
    lines.append("## 预算")
    lines.append(
        f"- 大交通 ¥{budget['transport']} / 住宿 ¥{budget['hotel']} / 门票 ¥{budget['tickets']}"
        f" / 餐饮 ¥{budget['meals']} / 当地交通 ¥{budget['local']},合计 ¥{budget['total']}"
    )
    tips = [str(t) for t in input.get("local_tips") or []]
    if tips:
        lines += ["", "## 当地贴士"] + [f"- {t}" for t in tips]
    notes = [str(n) for n in input.get("booking_notes") or []]
    if notes:
        lines += ["", "## 预订事项"] + [f"- {n}" for n in notes]
    return {"itinerary": "\n".join(lines)}

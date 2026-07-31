"""travel_planner 示例的数据驱动 mock 大脑(现场演示的确定性复现模式)。

消息解析约定与 research_pipeline 的 ``ops_brain`` 相同(system 首行
``# skill: <name>`` 识别技能;首条可解析 JSON 的 USER 消息为帧输入;
TOOL 结果为 ``{"ok","value","error"}``)。核心在"真抽取、真算术":

- ``research_destination``:先 wiki_search 一次、再 wiki_fetch 一次,然后对取回的
  正文做真实抽取——See 段 ``* 名称. Entry ¥N.`` 形态抽景点(门票/建议时长/
  所在区域/免费/闭馆/预约),Eat 段抽餐饮人均,Sleep 段抽区域每晚价格区间,
  Get in 段抽大交通单程价,Get around 段抽当地交通与打车价——不臆造正文没有的事实;
- ``plan_trip``:parse → research → route(经子技能)→ meals/hotel/budget
  (budget 经 plan_budget code 技能全算术,可逐数追溯到抽取值)→ 超预算
  ask_supervisor → trim 则 adjust_for_budget 削减后重排 → review → format;
- 削减杠杆确定性有序:降住宿档 → 餐饮只留最便宜项 → 去出租车 → 门票从贵到贱砍
  (保留景点数不少于天数,免费优先保留);
- 同输入 → 同调用树 → 同结果(锚点 tests/examples/test_travel_planner.py 钉死)。
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)

# ---------------------------------------------------------------------------
# 请求解析(同 ops_brain 约定)
# ---------------------------------------------------------------------------


def _skill_name(req: ChatRequest) -> str:
    """system 消息首行 ``# skill: <name>`` → 技能名。"""
    first_line = req.messages[0].content.splitlines()[0]
    return first_line.split(":", 1)[1].strip()


def _frame_input(req: ChatRequest) -> dict[str, Any]:
    """首条可解析为 JSON 对象的 USER 消息 = 帧输入(状态元消息等自动跳过)。"""
    for m in req.messages:
        if m.role is Role.USER:
            try:
                data = json.loads(m.content)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return data
    raise AssertionError("帧上下文缺少输入消息")


def _named_results(req: ChatRequest) -> list[tuple[str, dict[str, Any]]]:
    """按序收集已完成调用 ``(工具名, {"ok","value","error"})``(容错,不断言 ok)。"""
    call_names: dict[str, str] = {}
    seq: list[tuple[str, dict[str, Any]]] = []
    for m in req.messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                call_names[tc.id] = tc.name
        elif m.role is Role.TOOL and m.tool_call_id in call_names:
            seq.append((call_names[m.tool_call_id], json.loads(m.content)))
    return seq


# ---------------------------------------------------------------------------
# 应答构造
# ---------------------------------------------------------------------------


def _final(payload: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _call(tc: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _issue(skill: str, seq: list[tuple[str, dict[str, Any]]], name: str, args: dict[str, Any]) -> ChatResponse:
    """发下一个调用;call id 由已完成结果数推导(帧内唯一且确定性)。"""
    return _call(ToolCall(id=f"call-{skill}-{len(seq)}", name=name, args=args))


def _values(seq: list[tuple[str, dict[str, Any]]], name: str) -> list[dict[str, Any]]:
    """某调用名的全部成功结果值(保序;重排轮次按序追加)。"""
    return [p["value"] for n, p in seq if n == name and p.get("ok")]


def _issue_skill(
    skill: str, seq: list[tuple[str, dict[str, Any]]], name: str, args: dict[str, Any]
) -> ChatResponse:
    """发子技能调用;同名调用已连续失败 ≥2 次时放弃(干净失败,不烧步数死循环)。"""
    failures = [p for n, p in seq if n == name and not p.get("ok")]
    if len(failures) >= 2:
        raise AssertionError(f"子技能 {name} 连续失败 {len(failures)} 次,demo 大脑放弃: {failures[-1].get('error')}")
    return _issue(skill, seq, name, args)


def _num(x: float) -> int | float:
    """整数值的 float 归一为 int(价目算术的输出形态稳定)。"""
    return int(x) if x == int(x) else x


# ---------------------------------------------------------------------------
# parse_request:自然语言 → 结构化参数(确定性正则抽取)
# ---------------------------------------------------------------------------


def _parse_request_text(request: str) -> dict[str, Any]:
    """从中文自然语言请求抽目的地/出发地/天数/预算/同行人/偏好(目的地无关)。"""
    m = re.search(r"去([一-鿿A-Za-z'·]+?)(?:玩|旅游|旅行|度假|,|，|。|$)", request)
    destination = m.group(1) if m else request.strip()
    m = re.search(r"从([一-鿿A-Za-z]+?)(?:出发)?去", request)
    origin = m.group(1) if m else ""
    m = re.search(r"(\d+)\s*天", request)
    days = int(m.group(1)) if m else 3
    m = re.search(r"预算\s*约?\s*(\d+(?:\.\d+)?)", request)
    travelers = [k for k in ("老人", "孩子", "小孩", "宝宝", "情侣", "朋友", "家人", "亲子") if k in request]
    preferences: list[str] = []
    if any(k in request for k in ("不赶", "太赶", "轻松", "休闲", "慢节奏")):
        preferences.append("节奏舒缓")
    out: dict[str, Any] = {
        "destination": destination,
        "days": days,
        "travelers": travelers,
        "preferences": preferences,
    }
    if origin:
        out["origin"] = origin
    if m:
        out["budget"] = _num(float(m.group(1)))
    return out


# ---------------------------------------------------------------------------
# research_destination:wiki 正文真实抽取(核心)
# ---------------------------------------------------------------------------


#: 节标题(== 到 ====== 级;真实 Wikivoyage 正文带层级与子节)
_HEADER_RE = re.compile(r"(?m)^(={2,6})\s*(.+?)\s*\1\s*$")

#: 编号列表项(真实 explaintext 列表形态:"1 Beilin Museum (...), addr, ...";
#: 限定编号后接大写/CJK,排除 "14 km around" 之类的续行误判)
_NUM_ITEM_RE = re.compile(r"^\d{1,2}[.)]?\s+(?=[A-Z一-鿿])")


def _sections(text: str) -> dict[str, str]:
    """``==Title==`` 分节(层级感知)→ {小写节名: 正文};子节内容并入最近的祖先节。

    Wikivoyage 标准目标节名:See/Eat/Sleep/Get in/Get around(canned 为 ``==See==``,
    真实正文为 ``== See ==`` 且条目散在 ``===``/``====`` 子节中)。
    """
    matches = list(_HEADER_RE.finditer(text))
    bodies: dict[str, str] = {}
    for i, m in enumerate(matches):
        level = len(m.group(1))
        end = len(text)
        for m2 in matches[i + 1 :]:
            if len(m2.group(1)) <= level:
                end = m2.start()
                break
        name = m.group(2).strip().lower()
        body = text[m.end() : end]
        bodies[name] = f"{bodies[name]}\n{body}" if name in bodies else body
    return bodies


def _bullets(section: str) -> list[str]:
    """列表项抽取:``* `` 项(canned)与 ``1 `` 编号项(真实 explaintext)都认;
    续行(非列表/非节标题的非空行)并回上一项;节标题行跳过。"""
    items: list[str] = []
    for line in section.splitlines():
        if line.startswith("*"):
            items.append(line.lstrip("*").strip())
        elif _NUM_ITEM_RE.match(line):
            items.append(_NUM_ITEM_RE.sub("", line, count=1).strip())
        elif _HEADER_RE.match(line):
            continue
        elif items and line.strip():
            items[-1] += " " + line.strip()
    return items


def _item_name(bullet: str) -> str:
    """列表项 → 名称:canned 形态取 ". " 前;真实编号项(名称后接逗号地址)取首个逗号前。"""
    dot = bullet.find(". ")
    comma = bullet.find(",")
    cut = comma if comma != -1 and (dot == -1 or comma < dot) else dot
    name = bullet[:cut].strip() if cut != -1 else bullet.strip()
    return name.rstrip(".").replace("'''", "")


def _parse_attraction(bullet: str) -> dict[str, Any]:
    """``* 名称. Entry ¥N. Located in X. Allow N hours.`` 形态 → 结构化景点。"""
    name = _item_name(bullet)
    m = re.search(r"Entry\s*¥\s*(\d+)", bullet) or re.search(r"¥\s*(\d+)", bullet)
    price = int(m.group(1)) if m else 0  # 无价目(Free 等)→ 0
    m = re.search(r"(\d+)\s*hours?", bullet)
    hours = int(m.group(1)) if m else 2  # 未注明建议时长 → 规划默认值 2 小时
    m = re.search(r"\bin ([A-Z][A-Za-z]+)", bullet)
    notes: list[str] = []
    if re.search(r"closed on mondays", bullet, re.IGNORECASE):
        notes.append("周一闭馆")
    if re.search(r"reservation required", bullet, re.IGNORECASE):
        notes.append("需预约")
    out: dict[str, Any] = {"name": name, "price": price, "hours": hours}
    if m:
        out["area"] = m.group(1)
    if notes:
        out["note"] = ",".join(notes)
    return out


def _parse_food(bullet: str) -> dict[str, Any]:
    """``* 名称. 描述, about ¥N per person.`` → {name, price}。"""
    m = re.search(r"¥\s*(\d+)", bullet)
    return {"name": _item_name(bullet), "price": int(m.group(1)) if m else 0}


def _parse_sleep(bullet: str) -> dict[str, Any]:
    """``* X area ...: ¥N-M per night.`` → {area, price_range}。"""
    m = re.search(r"^(.+?)\s+area\b", bullet)
    area = m.group(1).strip() if m else _item_name(bullet).split(":", 1)[0].strip()
    m = re.search(r"¥\s*\d+\s*-\s*\d+", bullet)
    return {"area": area, "price_range": m.group(0) if m else ""}


def _extract_facts(text: str) -> dict[str, Any]:
    """wiki 正文 → research_destination 输出(全部字段可追溯到正文原文)。"""
    sections = _sections(text)
    attractions: list[dict[str, Any]] = []
    tips: list[str] = []
    for bullet in _bullets(sections.get("see", "")):
        a = _parse_attraction(bullet)
        attractions.append(a)
        if a.get("note"):
            tips.append(f"{a['name']}:{a['note']}")
    foods = [_parse_food(b) for b in _bullets(sections.get("eat", ""))]
    sleep = [_parse_sleep(b) for b in _bullets(sections.get("sleep", ""))]
    get_in = _bullets(sections.get("get in", ""))
    m = re.search(r"¥\s*(\d+)", " ".join(get_in))
    around = _bullets(sections.get("get around", ""))
    tips.extend(f"当地交通:{b}" for b in around)
    m2 = re.search(r"[Tt]axi[^¥]*¥\s*(\d+)", " ".join(around))
    return {
        "attractions": attractions,
        "food": foods,
        "sleep": sleep,
        "transport_in": get_in[0] if get_in else "",
        "transport_price": int(m.group(1)) if m else 0,
        "taxi_price": int(m2.group(1)) if m2 else 0,
        "local_tips": tips,
    }


def _research_destination(
    inp: dict[str, Any], seq: list[tuple[str, dict[str, Any]]]
) -> ChatResponse:
    """wiki_search 一次 → wiki_fetch 一次 → 从正文真实抽取后 final。"""
    results: list[dict[str, Any]] = []
    for _name, payload in seq:
        assert payload["ok"], f"wiki 工具失败,demo 大脑不予恢复: {payload}"
        results.append(payload)
    destination = inp["destination"]
    if not results:
        return _issue("project.travel_planner.research_destination", seq, "project.travel_planner.wiki_search", {"query": destination})
    if len(results) == 1:
        titles = results[0]["value"].get("titles") or []
        # chars_limit 拉满:真实正文数万字符,See/Eat/Sleep 等目标节远在 6000 之后;
        # canned 正文仅千余字符,mock 形态不受此值影响(锚点钉死不变)
        return _issue(
            "project.travel_planner.research_destination", seq, "project.travel_planner.wiki_fetch",
            {"title": titles[0] if titles else destination, "chars_limit": 120000},
        )
    return _final(_extract_facts(results[1]["value"]["text"]))


# ---------------------------------------------------------------------------
# 叶技能:meals / hotel / review / adjust(全部读真实数据再决策)
# ---------------------------------------------------------------------------


def _leaf_plan_meals(inp: dict[str, Any]) -> dict[str, Any]:
    """foods × days → 逐日餐饮;默认全覆盖并按天轮换,cheapest_only 时每天只留最便宜项。"""
    foods = list(inp["food"])
    days = int(inp["days"])
    if not foods:
        return {"meals": [[] for _ in range(days)]}  # 资料无餐饮段:空安排,由 review 标出
    per_day = max(1, min(int(inp.get("per_day") or len(foods)), len(foods)))
    cheapest_only = bool(inp.get("cheapest_only"))
    cheapest = min(foods, key=lambda f: float(f["price"]))
    meals: list[list[str]] = []
    for d in range(days):
        if cheapest_only:
            picks = [cheapest["name"]]
        else:
            picks = [foods[(d + j) % len(foods)]["name"] for j in range(per_day)]
        meals.append(picks)
    return {"meals": meals}


def _sleep_low(item: dict[str, Any]) -> int:
    """price_range(如 "¥400-600")→ 区间下限;无数字 → 0。"""
    m = re.search(r"(\d+)\s*-\s*(\d+)", str(item.get("price_range") or ""))
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", str(item.get("price_range") or ""))
    return int(m.group(1)) if m else 0


def _leaf_select_hotel(inp: dict[str, Any]) -> dict[str, Any]:
    """按区间下限选最便宜区域(有 max_price 时先过滤);同价保留资料先列的。"""
    options = [{"area": s["area"], "price": _sleep_low(s)} for s in inp["sleep"]]
    positive = [o for o in options if o["price"] > 0]
    if positive:
        options = positive  # 无价目区间(真实正文常见)的条目只在全部为 0 时兜底
    cap = inp.get("max_price")
    if cap is not None:
        affordable = [o for o in options if o["price"] <= cap]
        if affordable:
            options = affordable
    if not options:
        # 资料没有住宿段(任何目的地都可能):保守给 0 档占位,由 review 标出
        return {"name": "资料未提供住宿区域", "price_per_night": 0, "area": ""}
    best = min(options, key=lambda o: o["price"])
    return {"name": f"{best['area']} 片区酒店", "price_per_night": best["price"], "area": best["area"]}


def _leaf_review_itinerary(inp: dict[str, Any]) -> dict[str, Any]:
    """结构审查:每天有景点与餐饮、住宿价为正、预算分项自洽、有 cap 不超支。"""
    issues: list[str] = []
    for i, day in enumerate(inp["days"], 1):
        if not day.get("attractions"):
            issues.append(f"第 {i} 天没有景点")
        if not day.get("meals"):
            issues.append(f"第 {i} 天没有餐饮安排")
    if float(inp["hotel"].get("price_per_night") or 0) <= 0:
        issues.append("住宿每晚价格必须为正(资料未提供住宿段)")
    b = inp["budget"]
    parts = b["transport"] + b["hotel"] + b["tickets"] + b["meals"] + b["local"]
    if b["total"] != parts:
        issues.append(f"预算分项之和 {parts} 不等于总计 {b['total']}")
    cap = inp.get("cap")
    if cap is not None and b["total"] > cap and not inp.get("over_budget_approved"):
        issues.append(f"预算 ¥{b['total']} 超出上限 ¥{cap}")
    return {"ok": not issues, "issues": issues}


def _leaf_adjust_for_budget(inp: dict[str, Any]) -> dict[str, Any]:
    """确定性削减:住宿降档 → 餐饮最便宜项 → 去出租车 → 门票从贵到贱砍(保留 ≥ 天数)。"""
    cap = float(inp["cap"])
    days = int(inp["days"])
    nights = int(inp["nights"])
    budget = inp["budget"]
    per_night = float(inp["hotel_per_night"])
    attractions = list(inp["attractions"])
    foods = list(inp["food"])
    sleep = list(inp["sleep"])
    transport = float(budget["transport"])
    meals_now = float(budget["meals"])
    local_now = float(budget["local"])
    notes: list[str] = []
    # 杠杆 1:住宿换资料中区间下限最低的区域
    lows = [(_sleep_low(s), s["area"]) for s in sleep]
    if lows:
        lo_price, lo_area = min(lows, key=lambda t: (t[0], t[1]))
        if 0 < lo_price < per_night:
            notes.append(f"住宿换到 {lo_area} 片区(¥{lo_price}/晚)")
            per_night = float(lo_price)
    hotel = per_night * nights
    keep = list(attractions)
    drop: list[str] = []
    cheapest_only = False
    use_taxi = local_now > 0

    def _total() -> float:
        return transport + hotel + sum(float(a["price"]) for a in keep) + meals_now + local_now

    # 杠杆 2:餐饮每天只留最便宜项
    if _total() > cap and foods:
        cheapest_only = True
        meals_now = min(float(f["price"]) for f in foods) * days
        notes.append("餐饮每天只留最便宜项")
    # 杠杆 3:当地交通去掉出租车
    if _total() > cap and local_now > 0:
        use_taxi = False
        local_now = 0.0
        notes.append("当地交通去掉出租车")
    # 杠杆 4:门票从贵到贱砍(免费景点优先保留;保留总数不少于天数)
    while _total() > cap:
        paid = [a for a in keep if float(a["price"]) > 0]
        if not paid or len(keep) <= days:
            break
        victim = max(paid, key=lambda a: float(a["price"]))
        keep.remove(victim)
        drop.append(str(victim["name"]))
        notes.append(f"砍掉门票 {victim['name']}(¥{victim['price']})")
    return {
        "keep_attractions": [str(a["name"]) for a in keep],
        "drop_attractions": drop,
        "meals_cheapest_only": cheapest_only,
        "use_taxi": use_taxi,
        "hotel_max": _num(per_night),
        "note": ";".join(notes) if notes else "无需削减",
    }


# ---------------------------------------------------------------------------
# plan_trip 编排:parse → research → route → meals/hotel/budget → (超支裁决)→ review → format
# ---------------------------------------------------------------------------


def _plan_trip(inp: dict[str, Any], seq: list[tuple[str, dict[str, Any]]]) -> ChatResponse:
    request = inp["request"]
    # 1. 解析请求
    parsed_v = _values(seq, "skill.project.travel_planner.parse_request")
    if not parsed_v:
        return _issue_skill("project.travel_planner.plan_trip", seq, "skill.project.travel_planner.parse_request", {"request": request})
    parsed = parsed_v[-1]
    destination = parsed["destination"]
    days = int(parsed["days"])
    nights = max(1, days - 1)
    cap = parsed.get("budget")
    # 2. 研究目的地(wiki_search + wiki_fetch + 正文抽取,在子帧内完成)
    research_v = _values(seq, "skill.project.travel_planner.research_destination")
    if not research_v:
        return _issue_skill("project.travel_planner.plan_trip", seq, "skill.project.travel_planner.research_destination", {"destination": destination})
    research = research_v[-1]
    attractions = research["attractions"]
    foods = research["food"]
    sleep = research["sleep"]
    # 削减方案(第二轮重排时存在):过滤景点/餐饮降档/去出租/住宿上限
    adj_v = _values(seq, "skill.project.travel_planner.adjust_for_budget")
    adj = adj_v[-1] if adj_v else None
    if adj is not None:
        keep = set(adj["keep_attractions"])
        active = [a for a in attractions if a["name"] in keep]
        cheapest_only = bool(adj["meals_cheapest_only"])
        use_taxi = bool(adj["use_taxi"])
        hotel_max: float | None = adj["hotel_max"]
    else:
        active = attractions
        cheapest_only = False
        use_taxi = True
        hotel_max = None
    rounds = 2 if adj is not None else 1
    # 3. 路线(按区域聚类分天)
    route_v = _values(seq, "skill.project.travel_planner.plan_route")
    if len(route_v) < rounds:
        return _issue_skill("project.travel_planner.plan_trip", seq, "skill.project.travel_planner.plan_route", {"attractions": active, "days": days})
    route = route_v[-1]
    # 4. 餐饮
    meals_v = _values(seq, "skill.project.travel_planner.plan_meals")
    if len(meals_v) < rounds:
        args: dict[str, Any] = {"food": foods, "days": days}
        if cheapest_only:
            args.update(per_day=1, cheapest_only=True)
        return _issue_skill("project.travel_planner.plan_trip", seq, "skill.project.travel_planner.plan_meals", args)
    meals = meals_v[-1]["meals"]
    # 5. 住宿
    hotel_v = _values(seq, "skill.project.travel_planner.select_hotel")
    if len(hotel_v) < rounds:
        args = {"sleep": sleep, "nights": nights}
        if hotel_max is not None:
            args["max_price"] = hotel_max
        return _issue_skill("project.travel_planner.plan_trip", seq, "skill.project.travel_planner.select_hotel", args)
    hotel = hotel_v[-1]
    # 6. 预算(价目全部来自抽取结果;大交通按资料单程价计入)
    budget_v = _values(seq, "skill.project.travel_planner.plan_budget")
    if len(budget_v) < rounds:
        price_of = {a["name"]: a["price"] for a in attractions}
        routed = [n for d in route["days"] for n in d["attractions"]]
        food_price = {f["name"]: f["price"] for f in foods}
        has_suburb = any(a.get("area") for a in attractions if a["name"] in set(routed))
        return _issue_skill(
            "project.travel_planner.plan_trip", seq, "skill.project.travel_planner.plan_budget",
            {
                "transport_price": research["transport_price"],
                "legs": 1,
                "hotel_per_night": hotel["price_per_night"],
                "nights": nights,
                "tickets": [price_of[n] for n in routed],
                "meals_daily": [sum(food_price[n] for n in day) for day in meals],
                "local": [2 * research["taxi_price"]] if use_taxi and has_suburb else [],
            },
        )
    budget = budget_v[-1]
    # 7. 超预算 → 上级裁决;trim → 削减后重走 3-6
    over_approved = False
    if cap is not None and budget["total"] > cap and adj is None:
        asks = [p for n, p in seq if n == "ask_supervisor"]
        if not asks:
            return _issue(
                "project.travel_planner.plan_trip", seq, "ask_supervisor",
                {
                    "question": f"预算 ¥{budget['total']} 超出 ¥{cap},批准超支还是削减?",
                    "context": {"destination": destination, "total": budget["total"], "cap": cap},
                    "options": ["approve", "trim"],
                },
            )
        last_ask = asks[-1]
        # 未装配/超时等失败形态:降级为削减(SUPERVISOR.md §3 retryable 语义)
        answer = last_ask["value"]["answer"] if last_ask.get("ok") else "trim"
        if answer == "approve":
            over_approved = True
        else:
            routed_names = {n for d in route["days"] for n in d["attractions"]}
            return _issue_skill(
                "project.travel_planner.plan_trip", seq, "skill.project.travel_planner.adjust_for_budget",
                {
                    "cap": cap,
                    "days": days,
                    "budget": budget,
                    "hotel_per_night": hotel["price_per_night"],
                    "nights": nights,
                    "attractions": [a for a in attractions if a["name"] in routed_names],
                    "food": foods,
                    "sleep": sleep,
                    "taxi_cost": research["taxi_price"],
                },
            )
    # 合并逐日计划
    days_final = [
        {
            "attractions": list(d["attractions"]),
            "meals": list(meals[i]) if i < len(meals) else [],
        }
        for i, d in enumerate(route["days"])
    ]
    # 8. 审查
    review_v = _values(seq, "skill.project.travel_planner.review_itinerary")
    if not review_v:
        args = {"destination": destination, "days": days_final, "hotel": hotel, "budget": budget}
        if cap is not None:
            args["cap"] = cap
        if over_approved:
            args["over_budget_approved"] = True
        return _issue_skill("project.travel_planner.plan_trip", seq, "skill.project.travel_planner.review_itinerary", args)
    # 预订事项(来自抽取的预约/闭馆事实与裁决记录)
    notes = [
        f"{a['name']}:需提前预约"
        for a in attractions
        if "需预约" in str(a.get("note") or "")
    ]
    if adj is not None:
        notes.append(f"已按上级裁决削减预算:{adj['note']}")
    if over_approved:
        notes.append(f"预算 ¥{budget['total']} 超出 ¥{cap},已获上级批准")
    # 9. 成稿
    fmt_v = _values(seq, "skill.project.travel_planner.format_itinerary")
    if not fmt_v:
        args = {
            "destination": destination,
            "days": days_final,
            "hotel": hotel,
            "budget": budget,
            "local_tips": research["local_tips"],
            "booking_notes": notes,
        }
        if parsed.get("origin"):
            args["origin"] = parsed["origin"]
        return _issue_skill("project.travel_planner.plan_trip", seq, "skill.project.travel_planner.format_itinerary", args)
    result: dict[str, Any] = {
        "destination": destination,
        "days": days_final,
        "hotel": hotel,
        "budget": budget,
        "itinerary": fmt_v[-1]["itinerary"],
        "booking_notes": notes,
    }
    if over_approved:
        result["over_budget_approved"] = True
    return _final(result)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


_LEAF_FINALS = {
    "project.travel_planner.parse_request": lambda inp: _parse_request_text(inp["request"]),
    "project.travel_planner.plan_meals": _leaf_plan_meals,
    "project.travel_planner.select_hotel": _leaf_select_hotel,
    "project.travel_planner.review_itinerary": _leaf_review_itinerary,
    "project.travel_planner.adjust_for_budget": _leaf_adjust_for_budget,
}

_ORCHESTRATORS = {
    "project.travel_planner.plan_trip": _plan_trip,
    "project.travel_planner.research_destination": _research_destination,
}


def travel_brain(req: ChatRequest) -> ChatResponse:
    """数据驱动 mock 大脑:按 system 首行的技能名分派到编排/叶行为。"""
    name = _skill_name(req)
    if name in _LEAF_FINALS:
        return _final(_LEAF_FINALS[name](_frame_input(req)))
    if name in _ORCHESTRATORS:
        return _ORCHESTRATORS[name](_frame_input(req), _named_results(req))
    raise AssertionError(f"travel_brain 未覆盖的技能: {name}")

"""升权审计面板(docs/ESCALATION.md §5;WS2):从产物重建一次 run 的升权决策全貌。

数据源(照 rca.py 纯函数先例,只读产物目录):

- ``trace.jsonl``:三条升权信号——``pre:skill.escalate``(确认请求发出;
  载荷 {skill, tier, params, requested},**不含 question_id**)、
  ``post:skill.escalate``({skill, tier, decision, decided_by, scope};
  decision ∈ approve-once|approve-run|grant-run|deny)、
  ``skill.escalation.denied``({skill, tier, decided_by});
- ``checkpoint.json``:``run.grants`` 台账(approve-run 登记;条目含
  question_id/frame_id 配对键,WS1)与帧段 ``tier``(事件行的档位迁移来源)。

配对(批准↔请求):

- pre ↔ post:同一 (frame_id, skill, tier) 按时间序闭合(确认闸门是串行挂起,
  帧内同调用同一时刻至多一个在途),配对成功并一行(kind="post",
  ``params``/``asked_ts`` 取自 pre,``paired=True``);grant-run 命中无 pre
  (Grant 直接放行,§5 实现注),单列;pre 无 post(挂起中/断电)单列 kind="pre"。
- question_id:信号载荷里没有,从 ``supervisor.ask`` 信号(kind="escalation",
  同帧同 skill/tier 紧随其后)取;Grant 命中(grant-run 无 ask)与 approve-run
  兜底从台账按 (skill, tier, decided_by) 匹配(approve-run 两处一致,
  tests/kernel/test_escalation.py 锚定)。approve-once 不登记台账(下次同调用
  重新过人眼),其 question_id 仍可由 ask 信号补上。

防御:trace/checkpoint 缺席或半写(落盘窗口)→ 对应段按空降级,不抛错——
在途 run 也能查询,只是面板为空或缺台账段。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_os.host.shared.artifacts import read_checkpoint, read_trace

#: 面板消费的三条升权信号(§5;kind=escalation 具名筛选同口径,app.py _KIND_HINTS)
PRE_ESCALATE = "pre:skill.escalate"
POST_ESCALATE = "post:skill.escalate"
DENIED = "skill.escalation.denied"

#: params 摘要截断长度(审计行内联显示;完整参数在信号 payload 面板可展开)
_PARAMS_DIGEST_MAX = 200


def _params_digest(params: Any) -> str | None:
    """params → JSON 摘要串(截断 200);None 直通。"""
    if params is None:
        return None
    try:
        text = json.dumps(params, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(params)
    if len(text) > _PARAMS_DIGEST_MAX:
        return text[: _PARAMS_DIGEST_MAX - 1] + "…"
    return text


def _match_grant(
    grants: list[dict[str, Any]],
    *,
    skill: Any,
    tier: Any,
    decided_by: Any,
    frame_id: Any,
    ts: Any,
) -> dict[str, Any] | None:
    """台账匹配(decision ∈ approve-run|grant-run 的 post 行):skill/tier/decided_by
    全等且 scope="run";多命中优先同 frame_id,再取 decided_at 距信号 ts 最近者。"""
    candidates = [
        g
        for g in grants
        if g.get("skill") == skill
        and g.get("tier") == tier
        and g.get("decided_by") == decided_by
        and g.get("scope") == "run"
    ]
    if not candidates:
        return None
    same_frame = [g for g in candidates if g.get("frame_id") == frame_id]
    pool = same_frame or candidates

    def _distance(g: dict[str, Any]) -> float:
        try:
            return abs(float(g.get("decided_at") or 0) - float(ts or 0))
        except (TypeError, ValueError):
            return float("inf")

    return min(pool, key=_distance)


def escalation_panel(run_dir: str | Path) -> dict[str, Any]:
    """产物目录 → ``{"summary", "events", "grants"}``(数据源与配对规则见模块 docstring)。

    事件行(时间序,trace 序即时间序)::

        {ts, kind(pre|post|denied), skill, tier, from_tier(发起帧档,checkpoint
        帧段), frame_id, decision?, decided_by?, scope?, question_id?,
        params(摘要,截断 200), paired(pre 并入 post), asked_ts(配对 pre 的 ts)}

    汇总 ``{total, approved, denied, grant_run}``:按裁决(post 行)计数,
    total = approved + denied + grant_run;grant_run 单列(台账命中放行,
    非本次人审,不计入 approved)。
    """
    try:
        rows = read_trace(run_dir)
    except (OSError, json.JSONDecodeError):
        rows = []
    try:
        checkpoint = read_checkpoint(run_dir)
    except (OSError, json.JSONDecodeError):
        checkpoint = {}

    frame_tier = {f.get("frame_id"): f.get("tier") for f in checkpoint.get("frames") or []}
    grants = [
        {
            "skill": g.get("skill"),
            "tier": g.get("tier"),
            "scope": g.get("scope"),
            "decided_by": g.get("decided_by"),
            "decided_at": g.get("decided_at"),
            "question_id": g.get("question_id") or None,
            "frame_id": g.get("frame_id") or None,
        }
        for g in ((checkpoint.get("run") or {}).get("grants") or [])
        if isinstance(g, dict)
    ]

    events: list[dict[str, Any]] = []
    open_pre: list[dict[str, Any]] = []  # 未闭合的 pre(等 ask/post 配对)
    last_deny_post: dict[str, Any] | None = None  # 最近一次 deny 裁决(denied 行配对用)

    def _find_open(frame_id: Any, skill: Any, tier: Any) -> dict[str, Any] | None:
        """同 (frame, skill, tier) 最近一个未闭合 pre(串行闸门:LIFO 即时间序最近)。"""
        for entry in reversed(open_pre):
            if (
                entry["frame_id"] == frame_id
                and entry["skill"] == skill
                and entry["tier"] == tier
            ):
                return entry
        return None

    for row in rows:
        if row.get("type") != "signal":
            continue
        name = row.get("name")
        payload = row.get("payload") or {}
        frame_id = row.get("frame_id")
        if name == PRE_ESCALATE:
            entry = {
                "ts": row.get("ts"),
                "kind": "pre",
                "skill": payload.get("skill"),
                "tier": payload.get("tier"),
                "from_tier": frame_tier.get(frame_id),
                "frame_id": frame_id,
                "decision": None,
                "decided_by": None,
                "scope": None,
                "question_id": None,
                "params": _params_digest(payload.get("params")),
                "paired": False,
                "asked_ts": None,
            }
            open_pre.append(entry)
            events.append(entry)
        elif name == "supervisor.ask" and payload.get("kind") == "escalation":
            # 升权确认的 question_id 唯一来源(信号载荷不带,§5):补到在等答案的 pre 上
            ctx = payload.get("context") or {}
            entry = _find_open(frame_id, ctx.get("skill"), ctx.get("tier"))
            if entry is None and frame_id is not None:
                entry = next(
                    (e for e in reversed(open_pre) if e["frame_id"] == frame_id), None
                )
            if entry is not None and entry.get("question_id") is None:
                entry["question_id"] = payload.get("question_id")
        elif name == POST_ESCALATE:
            decision = payload.get("decision")
            entry = _find_open(frame_id, payload.get("skill"), payload.get("tier"))
            if entry is not None:
                # pre 并入 post 一行(配对键即 supervisor.ask 补上的 question_id)
                open_pre.remove(entry)
                entry.update(
                    {
                        "ts": row.get("ts"),
                        "kind": "post",
                        "decision": decision,
                        "decided_by": payload.get("decided_by"),
                        "scope": payload.get("scope"),
                        "paired": True,
                        "asked_ts": entry["ts"],
                    }
                )
                event = entry
            else:
                # grant-run(无 pre,§5 实现注)/ 半写残留:post 单列
                event = {
                    "ts": row.get("ts"),
                    "kind": "post",
                    "skill": payload.get("skill"),
                    "tier": payload.get("tier"),
                    "from_tier": frame_tier.get(frame_id),
                    "frame_id": frame_id,
                    "decision": decision,
                    "decided_by": payload.get("decided_by"),
                    "scope": payload.get("scope"),
                    "question_id": None,
                    "params": None,
                    "paired": False,
                    "asked_ts": None,
                }
                events.append(event)
            if event.get("question_id") is None and payload.get("scope") == "run":
                grant = _match_grant(
                    grants,
                    skill=payload.get("skill"),
                    tier=payload.get("tier"),
                    decided_by=payload.get("decided_by"),
                    frame_id=frame_id,
                    ts=row.get("ts"),
                )
                if grant is not None:
                    event["question_id"] = grant.get("question_id")
            last_deny_post = event if decision == "deny" else last_deny_post
        elif name == DENIED:
            events.append(
                {
                    "ts": row.get("ts"),
                    "kind": "denied",
                    "skill": payload.get("skill"),
                    "tier": payload.get("tier"),
                    "from_tier": frame_tier.get(frame_id),
                    "frame_id": frame_id,
                    "decision": "deny",
                    "decided_by": payload.get("decided_by"),
                    "scope": None,
                    # denied 紧随 post(deny)发出(同帧同 skill/tier),沿用其配对
                    "question_id": (
                        last_deny_post.get("question_id")
                        if last_deny_post is not None
                        and last_deny_post.get("frame_id") == frame_id
                        and last_deny_post.get("skill") == payload.get("skill")
                        and last_deny_post.get("tier") == payload.get("tier")
                        else None
                    ),
                    "params": (last_deny_post or {}).get("params"),
                    "paired": False,
                    "asked_ts": None,
                }
            )

    decisions = [e for e in events if e["kind"] == "post"]
    approved = sum(1 for e in decisions if e["decision"] in ("approve-once", "approve-run"))
    denied = sum(1 for e in decisions if e["decision"] == "deny")
    grant_run = sum(1 for e in decisions if e["decision"] == "grant-run")
    return {
        "summary": {
            "total": approved + denied + grant_run,
            "approved": approved,
            "denied": denied,
            "grant_run": grant_run,
        },
        "events": events,
        "grants": grants,
    }

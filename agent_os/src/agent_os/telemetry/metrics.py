"""MetricsCollector(docs/DESIGN.md §10.2 metrics v1):信号流 → 每 run 计数报告。

``Exporter`` 契约实现:纯内存计数(``export`` **绝不 await IO**——sink 在
``record`` 里 inline-await 每个 exporter,jsonl_exporter.py:99-100),run 终态
信号(``run.finished``/``run.aborted``/``run.paused``)一次性 finalize 并原子落盘
``<traces_dir>/<run_id>.metrics.json``(tmp + ``os.replace``,口径照
host/shared/artifacts.py ``_write_json`` 先例;telemetry 不反向 import host 层)。

指标口径(v1 全部信号可 derivable;各 calibre 逐条锚定):

- ``llm.{calls, prompt_tokens, completion_tokens, cost}``:``post:llm.response``
  主循环源(**非** ``source="compress"``——压缩链补发同 replay.py:82-94 /
  otlp_exporter.py:320-325 口径过滤,摘要调用不计入主循环 llm 指标);token/cost
  取 ``usage`` 载荷基线三维(BudgetGuard 记账同源,runner._usage_payload);
- ``tools.{calls, ok, errors, legality_rate}``:``post:tool.call`` 口径——
  **Veto/白名单拒绝不进分母**(runner._dispatch_call 在白名单闸与 Veto 处提前
  返回,根本不发 post:tool.call;只有真正进了分发层的调用才有分母);
  ``legality_rate = ok / calls``,零调用时为 ``None``(无分母不可比率);
- ``data_denied``:``data.access.denied`` 信号计数(数据层 authZ 拒绝,
  docs/DATA-AUTHZ.md §3.3;与 tools.errors 会有重叠——数据拒绝的调用同时是
  ok=False 的工具调用,两维各计各的口径);
- ``steps``:``pre:step`` 计数(每帧每步恰好一次;post:step 只在有工具调用的
  步发,不能作口径;压缩链不发 pre:step,摘要调用不计步);
- ``frames``:``pre:frame.push`` 计数(含根帧);
- ``compress.{count, evicted, strategies}``:``post:compress`` 计数、
  ``evicted`` 求和、按 ``strategy`` 分桶计数(§7.2 压缩链遥测);
- ``escalations.{granted, denied}``:granted = ``post:skill.escalate`` 中
  ``decision != "deny"``(approve-once/approve-run/grant-run 三态);denied =
  ``skill.escalation.denied`` 计数(拒绝路径 post:skill.escalate decision=deny
  与该信号成对发,只计后者防双计);
- ``supervisor.{asks, timeouts}``:``supervisor.ask`` / ``supervisor.timeout``
  计数(docs/SUPERVISOR.md;含升权确认的 ask——kind="escalation" 也走该通道);
- ``budget.{warnings, exceeded}``:``budget.warning``(80% 预警,每帧每字段
  恰好一次)/``budget.exceeded`` 计数;
- ``run.{status}``:终态信号映射 ``done|aborted|paused``(同
  otlp_exporter._RUN_TERMINAL 的 status 维)。

窗口语义:``run.paused`` 也 finalize(可恢复挂起即一个观察窗口的结束);
同进程 resume 后同 run_id 开新窗口,终态**覆盖**同一路径文件(v1 口径:
每窗口一份报告,不做跨窗口合并)。未见本 run 任何信号的终态(resume 结算
DONE 帧路径只发 run.finished)→ 不落盘,防零值报告覆盖 pause 窗口的真实报告。

PII:sink 开 redact 时 payload 在 ``record`` 入口已换成脱敏副本
(jsonl_exporter.py:92-94),本 collector 只读数值/计数字段,不接触自由文本。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_os.api.v1 import (
    BUDGET_EXCEEDED,
    BUDGET_WARNING,
    DATA_ACCESS_DENIED,
    POST_COMPRESS,
    POST_LLM_RESPONSE,
    POST_SKILL_ESCALATE,
    POST_TOOL_CALL,
    PRE_FRAME_PUSH,
    PRE_STEP,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_PAUSED,
    SKILL_ESCALATION_DENIED,
    SUPERVISOR_ASK,
    SUPERVISOR_TIMEOUT,
    Signal,
)

__all__ = ["MetricsCollector"]

#: run 终态信号 → run.status 值(同 otlp_exporter._RUN_TERMINAL 的 status 维)
_RUN_STATUS: dict[str, str] = {
    RUN_FINISHED: "done",
    RUN_ABORTED: "aborted",
    RUN_PAUSED: "paused",  # 可恢复挂起(docs/DESIGN.md :940):一个观察窗口的结束,非错误
}


def _write_json(path: Path, doc: Any) -> None:
    """原子落盘:同目录 tmp + ``os.replace``(同文件系统 rename 原子)。

    口径照 host/shared/artifacts.py ``_write_json`` 先例逐字复刻——telemetry
    层不反向 import host 层(host 依赖内核/遥测,方向不可逆)。
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=repr), encoding="utf-8")
    os.replace(tmp, path)


def _new_counters() -> dict[str, Any]:
    """一个观察窗口的零值计数器(嵌套结构即报告字段;legality_rate/run.status 在 finalize 时补)。"""
    return {
        "llm": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0},
        "tools": {"calls": 0, "ok": 0, "errors": 0},
        "data_denied": 0,
        "steps": 0,
        "frames": 0,
        "compress": {"count": 0, "evicted": 0, "strategies": {}},
        "escalations": {"granted": 0, "denied": 0},
        "supervisor": {"asks": 0, "timeouts": 0},
        "budget": {"warnings": 0, "exceeded": 0},
    }


class MetricsCollector:
    """``agent_os.api.v1.Exporter`` 契约实现(§10.2):每 run 指标计数 + 终态落盘。

    ``export`` 纯内存计数(绝不 await IO);终态信号 finalize →
    ``<traces_dir>/<run_id>.metrics.json``;``report(run_id)`` 取已 finalize
    的报告(在跑/未知 run → ``None``)。
    """

    name: str = "metrics"

    def __init__(self, traces_dir: str) -> None:
        self.traces_dir = traces_dir
        Path(traces_dir).mkdir(parents=True, exist_ok=True)
        #: run_id → 在跑窗口计数器(终态 finalize 后摘除)
        self._runs: dict[str, dict[str, Any]] = {}
        #: run_id → 已 finalize 的报告(report() 数据源;覆盖语义见模块 docstring 窗口语义)
        self._reports: dict[str, dict[str, Any]] = {}
        self._closed = False

    async def export(self, sig: Signal) -> None:
        """计数一个信号(纯内存,**绝不 await IO**;sink 在 record 里 inline-await)。"""
        if self._closed:
            return
        status = _RUN_STATUS.get(sig.name)
        if status is not None:
            self._finalize(sig.run_id, status)
            return
        counters = self._runs.get(sig.run_id)
        if counters is None:
            counters = self._runs[sig.run_id] = _new_counters()
        payload = sig.payload or {}
        if sig.name == POST_LLM_RESPONSE:
            if payload.get("source") == "compress":
                return  # 压缩链补发不入 llm.*(同 replay/otlp 过滤口径)
            llm = counters["llm"]
            usage = payload.get("usage") or {}
            llm["calls"] += 1
            llm["prompt_tokens"] += usage.get("prompt", 0)
            llm["completion_tokens"] += usage.get("completion", 0)
            llm["cost"] += usage.get("cost", 0.0)
        elif sig.name == POST_TOOL_CALL:
            tools = counters["tools"]
            tools["calls"] += 1
            if payload.get("ok"):
                tools["ok"] += 1
            else:
                tools["errors"] += 1
        elif sig.name == DATA_ACCESS_DENIED:
            counters["data_denied"] += 1
        elif sig.name == PRE_STEP:
            counters["steps"] += 1
        elif sig.name == PRE_FRAME_PUSH:
            counters["frames"] += 1
        elif sig.name == POST_COMPRESS:
            compress = counters["compress"]
            compress["count"] += 1
            compress["evicted"] += payload.get("evicted", 0)
            strategy = str(payload.get("strategy", ""))
            compress["strategies"][strategy] = compress["strategies"].get(strategy, 0) + 1
        elif sig.name == POST_SKILL_ESCALATE:
            # 拒绝路径另有 skill.escalation.denied 成对信号,此处只计放行三态防双计
            if payload.get("decision") != "deny":
                counters["escalations"]["granted"] += 1
        elif sig.name == SKILL_ESCALATION_DENIED:
            counters["escalations"]["denied"] += 1
        elif sig.name == SUPERVISOR_ASK:
            counters["supervisor"]["asks"] += 1
        elif sig.name == SUPERVISOR_TIMEOUT:
            counters["supervisor"]["timeouts"] += 1
        elif sig.name == BUDGET_WARNING:
            counters["budget"]["warnings"] += 1
        elif sig.name == BUDGET_EXCEEDED:
            counters["budget"]["exceeded"] += 1

    def _finalize(self, run_id: str, status: str) -> None:
        """终态落盘:补 legality_rate/run.status,原子写文件,转存 report() 数据源。"""
        counters = self._runs.pop(run_id, None)
        if counters is None:
            # 未见本 run 任何信号的终态(resume 结算 DONE 帧只发 run.finished):
            # 不落盘——零值报告会覆盖 pause 窗口的真实报告(见模块 docstring)
            return
        tools = counters["tools"]
        report: dict[str, Any] = {
            "v": 1,
            "run_id": run_id,
            **counters,
            "tools": {
                **tools,
                "legality_rate": (tools["ok"] / tools["calls"]) if tools["calls"] else None,
            },
            "run": {"status": status},
        }
        _write_json(Path(self.traces_dir) / f"{run_id}.metrics.json", report)
        self._reports[run_id] = report

    def report(self, run_id: str) -> dict[str, Any] | None:
        """已 finalize 的报告 dict;在跑/未知 run → ``None``。"""
        return self._reports.get(run_id)

    async def close(self) -> None:
        """幂等(无句柄可关;计数全在内存,close 后信号丢弃)。"""
        self._closed = True

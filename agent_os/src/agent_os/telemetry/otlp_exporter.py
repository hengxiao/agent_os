"""OTLP exporter(docs/DESIGN.md §10.1 exporters 注册制 / §10.2 OTLP-OpenInference 映射)。

帧树 ≡ span 树(§10.2 逐字):映射是机械工作——

- ``run.started`` 开 run 根 span;``run.finished``/``run.aborted``/``run.paused``
  关闭它(``agent_os.run.status`` 属性;aborted 置 OTLP STATUS_CODE_ERROR);
- ``pre:frame.push`` 开 skill span(parent = 当前开放 span 栈顶,无栈则 run 根;
  真工具分发路径下子帧压栈时父即该工具 span;``skill.*`` 伪工具不开 tool
  span——内核直转 _invoke_skill,只发 pre/post:skill.invoke——此时父为父帧
  skill span,树形由统一开放栈自然得出);
  ``pre:frame.pop`` 关闭(``post:frame.pop`` 仅作事件);
- ``pre:tool.call`` 开 tool span;``post:tool.call`` 关闭(payload 无 call id,
  **帧内 LIFO 配对**);``ok`` 落属性;
- ``pre:llm.request`` 开 llm span;``post:llm.response`` 关闭,usage 落
  OpenInference 风格属性(docs/reports/ch06-evaluation.md:83 的 LLM 语义约定:
  ``llm.token_count.prompt`` 等);压缩链补发的 ``post:llm.response``
  (``source="compress"``)无 ``pre:llm.request`` 配对,按未配对计数跳过;
- 其余全部信号 → 当前顶 span 的事件(按 ``frame_id`` 匹配最内层开放 span,
  缺者落 run 根),payload 递归拍平为属性(标量直传,容器/杂项 JSON 化);
- 未配对/乱序(无开放 run/栈空 pop 等)→ ``_skipped`` 计数并跳过,**绝不抛**
  (遥测是观察通道,§5.3:订阅者故障不得拖垮 run;总线本就吞 handler 异常)。

可靠性形态(best-effort v1):

- ``export`` **绝不 await IO**(sink 在 ``record`` 里 inline-await 每个 exporter,
  jsonl_exporter.py:82-83):O(1) 入队有界 deque,发送由懒启动的后台 drainer
  task 承担(攒够 ``batch_max`` 或 ``flush_interval`` 到点即 flush);
- 队列满丢最旧(``_dropped`` 计数 + 限速 warning);POST 失败丢批、计数、记
  日志,**不重试**(v1 明确取舍;要可靠投递请用 collector 本机兜底);
- ``close()`` 是唯一排干点(不依赖 run.finished 的送达保证——DistillSidecar
  教训:终态信号的 ASYNC 派发有结构性竞态):幂等;取消 drainer(半途批次
  塞回队首),把余量按批做末次有界 POST,再关 httpx client;
- HTTP 内核照 tools/mcp_http.py 先例:阻塞 ``httpx.Client(trust_env=False)``
  + 显式 ``httpx.Timeout`` + ``asyncio.to_thread`` 包装。

线缆格式:POST ``{endpoint}/v1/traces``,OTLP/HTTP **JSON**
(``application/json``):``{"resourceSpans": [{"resource": ..., "scopeSpans":
[{"spans": [...]}]}]}``;时间戳一律 unix 纳秒字符串(OTLP JSON 对 fixed64/
int64 的 string 编码约定);trace_id = sha256(run_id) 前 32 hex,span_id =
run 内自增 16 hex。payload 在 sink 开 redact 时已脱敏,本模块**不再二扫**。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent_os import __version__
from agent_os.api.v1 import (
    POST_LLM_RESPONSE,
    POST_TOOL_CALL,
    PRE_FRAME_POP,
    PRE_FRAME_PUSH,
    PRE_LLM_REQUEST,
    PRE_TOOL_CALL,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_PAUSED,
    RUN_STARTED,
    Signal,
)

_log = logging.getLogger("agent_os.telemetry.otlp")

__all__ = ["OtlpExporter"]

#: OTLP status code(proto ``Status.StatusCode``):0=UNSET(缺省省略),1=OK,2=ERROR
_STATUS_OK = 1
_STATUS_ERROR = 2

#: run 终态信号 → (agent_os.run.status 属性值, OTLP status code 或 None)
_RUN_TERMINAL: dict[str, tuple[str, int | None]] = {
    RUN_FINISHED: ("done", _STATUS_OK),
    RUN_ABORTED: ("aborted", _STATUS_ERROR),
    RUN_PAUSED: ("paused", None),  # 可恢复挂起(docs/DESIGN.md :940),非错误
}


def _ns(ts: float) -> str:
    """unix 秒(float)→ OTLP JSON 的 unix 纳秒字符串(fixed64 的 string 编码)。"""
    return str(int(ts * 1_000_000_000))


def _attr(key: str, value: Any) -> dict[str, Any]:
    """标量 → OTLP JSON AnyValue(bool 先于 int——bool 是 int 子类)。"""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _flatten(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """payload 递归拍平为点分键标量表;容器/None 等杂项 JSON 化(None 跳过)。"""
    attrs: dict[str, Any] = {}
    for key, value in payload.items():
        flat = f"{prefix}{key}"
        if isinstance(value, dict):
            attrs.update(_flatten(value, f"{flat}."))
        elif value is None:
            continue
        elif isinstance(value, (bool, int, float, str)):
            attrs[flat] = value
        else:
            attrs[flat] = json.dumps(value, ensure_ascii=False, default=repr)
    return attrs


@dataclass
class _Span:
    """开放中的 span(span 关闭才入队——事件随生命周期累积,随体一并送出)。"""

    trace_id: str
    span_id: str
    parent_id: str
    name: str
    start_ns: str
    frame_id: str | None
    attributes: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    end_ns: str = ""  # _close 时落定
    status_code: int | None = None
    status_message: str = ""


@dataclass
class _RunSpans:
    """单个 run 的映射状态(终态信号后即回收)。"""

    trace_id: str
    next_id: int = 0  # span_id 自增种子(16-hex;trace 内唯一即可)
    open: list[_Span] = field(default_factory=list)  # 开放 span 统一栈(底 = run 根)
    tools: list[_Span] = field(default_factory=list)  # 开放 tool span(配对见下)
    llms: list[_Span] = field(default_factory=list)  # 开放 llm span(配对见下)


def _pop_lifo(spans: list[_Span], frame_id: str | None) -> _Span | None:
    """帧内 LIFO 配对(payload 无 call id):从栈尾找首个同帧 span;无 → None(未配对)。

    按 frame_id 过滤使 §3.4 spawn/parallel 的跨帧交错信号不错配;
    同帧内后开先关(LIFO)。
    """
    for i in range(len(spans) - 1, -1, -1):
        if spans[i].frame_id == frame_id:
            return spans.pop(i)
    return None


class OtlpExporter:
    """``agent_os.api.v1.Exporter`` 协议实现(§10.1):信号流 → OTLP/HTTP JSON span 流。"""

    name: str = "otlp"

    def __init__(
        self,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        batch_max: int = 64,
        flush_interval: float = 2.0,
        queue_max: int = 1000,
        timeout: float = 5.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._headers = dict(headers or {})
        self._batch_max = int(batch_max)
        self._flush_interval = float(flush_interval)
        self._queue_max = int(queue_max)
        self._timeout = float(timeout)
        # trust_env=False:不读 HTTP_PROXY/netrc(行为确定性,同 mcp_http.py 先例);
        # 构造零 IO,装配路径(build_kernel 同步上下文)可直造
        self._client = httpx.Client(trust_env=False)
        self._queue: deque[dict[str, Any]] = deque()  # 已关闭待发送的 span(有界手动管)
        self._runs: dict[str, _RunSpans] = {}
        self._drainer: asyncio.Task[None] | None = None  # 懒启动(首个 export 见到 running loop)
        self._wake: asyncio.Event | None = None
        self._closed = False
        #: 队列溢出丢弃的 span 数(丢最旧)
        self._dropped = 0
        #: POST 失败丢弃的 span 数(best-effort v1:丢批不重试)
        self._failed = 0
        #: 未配对/乱序而跳过的信号数(绝不抛)
        self._skipped = 0

    # ------------------------------------------------------------------
    # Exporter 协议
    # ------------------------------------------------------------------

    async def export(self, sig: Signal) -> None:
        """映射并入队(**绝不 await IO**;sink 在 record 里 inline-await 本方法)。"""
        if self._closed:
            return
        self._ensure_drainer()
        try:
            span = self._map(sig)
        except Exception:  # noqa: BLE001 — 映射缺陷不得拖垮 run(§5.3 观察通道纪律)
            _log.exception("OTLP 映射异常(跳过):signal=%r", sig.name)
            self._skipped += 1
            return
        if span is not None:
            self._enqueue(span)

    async def close(self) -> None:
        """排干点:取消 drainer → 余量按批末次 POST → 关 client。幂等。"""
        if self._closed:
            return
        self._closed = True
        task, self._drainer = self._drainer, None
        if task is not None and not task.done():
            same_loop = False
            with contextlib.suppress(RuntimeError):
                same_loop = task.get_loop() is asyncio.get_running_loop()
            if same_loop:
                # 半途批次由 drainer 的 CancelledError 分支塞回队首(见 _post)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            # 异 loop(宿主已销毁运行 loop,如 CLI 每命令一个 asyncio.run):
            # 任务随旧 loop 终了,只清引用;队列余量照排
        await self._flush_batches()
        await asyncio.to_thread(self._client.close)

    # ------------------------------------------------------------------
    # 信号 → span 树映射(机械,§10.2);返回关闭完成的 span(None = 无产出)
    # ------------------------------------------------------------------

    def _map(self, sig: Signal) -> _Span | None:
        if sig.name == RUN_STARTED:
            rs = _RunSpans(trace_id=hashlib.sha256(sig.run_id.encode()).hexdigest()[:32])
            self._runs[sig.run_id] = rs
            root = self._open(
                rs,
                "run",
                None,
                sig,
                {
                    "agent_os.run_id": sig.run_id,
                    "agent_os.skill": str(sig.payload.get("skill", "")),
                },
            )
            rs.open.append(root)
            return None
        rs = self._runs.get(sig.run_id)
        if rs is None or not rs.open:
            self._skipped += 1  # 未知 run / run 已收尾:未配对信号
            return None
        terminal = _RUN_TERMINAL.get(sig.name)
        if terminal is not None:
            return self._close_run(rs, sig, terminal)
        if sig.name == PRE_FRAME_PUSH:
            span = self._open(
                rs,
                f"skill:{sig.payload.get('skill', '')}",
                rs.open[-1].span_id,
                sig,
                {
                    "agent_os.run_id": sig.run_id,
                    "agent_os.frame_id": str(sig.frame_id or ""),
                    "agent_os.skill": str(sig.payload.get("skill", "")),
                    "agent_os.depth": sig.payload.get("depth", 0),
                },
            )
            rs.open.append(span)
            return None
        if sig.name == PRE_FRAME_POP:
            # 关闭该帧的栈顶 skill span(与 push 严格配对;post:frame.pop 仅作事件走下方兜底)
            for i in range(len(rs.open) - 1, 0, -1):
                if rs.open[i].frame_id == sig.frame_id:
                    return self._close(rs.open.pop(i), sig)
            self._skipped += 1
            return None
        if sig.name == PRE_TOOL_CALL:
            span = self._open(
                rs,
                f"tool:{sig.payload.get('tool', '')}",
                rs.open[-1].span_id,
                sig,
                {
                    "agent_os.run_id": sig.run_id,
                    "agent_os.frame_id": str(sig.frame_id or ""),
                    "agent_os.tool": str(sig.payload.get("tool", "")),
                },
            )
            rs.open.append(span)
            rs.tools.append(span)
            return None
        if sig.name == POST_TOOL_CALL:
            span = _pop_lifo(rs.tools, sig.frame_id)
            if span is None:
                self._skipped += 1  # 无 pre 配对(payload 无 call id,帧内 LIFO)
                return None
            rs.open.remove(span)
            span.attributes["agent_os.tool.ok"] = bool(sig.payload.get("ok"))
            return self._close(span, sig)
        if sig.name == PRE_LLM_REQUEST:
            span = self._open(
                rs,
                f"llm:{sig.payload.get('model', '')}",
                rs.open[-1].span_id,
                sig,
                {
                    "agent_os.run_id": sig.run_id,
                    "agent_os.frame_id": str(sig.frame_id or ""),
                    "llm.model_name": str(sig.payload.get("model", "")),
                },
            )
            rs.open.append(span)
            rs.llms.append(span)
            return None
        if sig.name == POST_LLM_RESPONSE:
            span = _pop_lifo(rs.llms, sig.frame_id)
            if span is None:
                # 压缩链补发的 post:llm.response(source="compress")无 pre 配对,同归未配对
                self._skipped += 1
                return None
            rs.open.remove(span)
            usage = sig.payload.get("usage") or {}
            for attr_key, usage_key in (
                ("llm.token_count.prompt", "prompt"),
                ("llm.token_count.completion", "completion"),
                ("llm.token_count.cache_read", "cache_read_tokens"),
                ("llm.token_count.cache_write", "cache_write_tokens"),
                ("llm.token_count.thinking", "thinking_tokens"),
                ("llm.cost", "cost"),
                ("llm.ttft_ms", "ttft_ms"),
                ("llm.total_ms", "total_ms"),
            ):
                if usage_key in usage:
                    span.attributes[attr_key] = usage[usage_key]
            return self._close(span, sig)
        # 其余信号 → 当前顶 span(按 frame_id 取最内层,缺者 run 根)的事件
        top = next(
            (span for span in reversed(rs.open) if span.frame_id == sig.frame_id),
            rs.open[0],
        )
        top.events.append(
            {
                "name": sig.name,
                "timeUnixNano": _ns(sig.ts),
                "attributes": [_attr(k, v) for k, v in _flatten(sig.payload).items()],
            }
        )
        return None

    def _open(
        self,
        rs: _RunSpans,
        name: str,
        parent_id: str | None,
        sig: Signal,
        attributes: dict[str, Any],
    ) -> _Span:
        rs.next_id += 1
        return _Span(
            trace_id=rs.trace_id,
            span_id=f"{rs.next_id:016x}",
            parent_id=parent_id or "",
            name=name,
            start_ns=_ns(sig.ts),
            frame_id=sig.frame_id,
            attributes=attributes,
        )

    def _close(self, span: _Span, sig: Signal) -> _Span:
        """补 end 时间返回(span 生命周期闭合,由调用方入队;事件已在开放期累积)。"""
        span.end_ns = _ns(sig.ts)
        return span

    def _close_run(self, rs: _RunSpans, sig: Signal, terminal: tuple[str, int | None]) -> _Span | None:
        """run 终态:强关仍开放的 tool/llm/帧 span(防半截树滞留),最后关根。"""
        status_value, status_code = terminal
        root = rs.open[0]
        # 逐个强关内层残留(乱序/中断路径的兜底;正常路径栈内只剩根)
        while len(rs.open) > 1:
            span = rs.open.pop()
            if span in rs.tools:
                rs.tools.remove(span)
            if span in rs.llms:
                rs.llms.remove(span)
            self._enqueue(self._close(span, sig))
        rs.tools.clear()
        rs.llms.clear()
        rs.open.pop()
        root.attributes["agent_os.run.status"] = status_value
        root.status_code = status_code
        if sig.name == RUN_ABORTED:
            root.status_message = str(sig.payload.get("error", ""))[:500]
        del self._runs[sig.run_id]
        return self._close(root, sig)

    # ------------------------------------------------------------------
    # 队列与 drainer(export 零 IO;发送全在后台 task)
    # ------------------------------------------------------------------

    def _enqueue(self, span: _Span) -> None:
        if len(self._queue) >= self._queue_max:
            self._queue.popleft()  # 有界队列丢最旧(背压取舍:新信号比旧 span 值钱)
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:  # 限速,防刷屏
                _log.warning(
                    "OTLP 队列溢出(上限 %d),丢最旧;累计丢弃 %d 条 span",
                    self._queue_max, self._dropped,
                )
        self._queue.append(span)
        if self._wake is not None and len(self._queue) >= self._batch_max:
            self._wake.set()

    def _ensure_drainer(self) -> None:
        """懒启动后台 flush task(首个 export 必在 running loop 内——sink inline-await)。"""
        task = self._drainer
        if task is not None and not task.done():
            with contextlib.suppress(RuntimeError):
                if task.get_loop() is asyncio.get_running_loop():
                    return
            # 运行 loop 已换(宿主每命令一个 asyncio.run):旧任务随旧 loop 终了,换绑
        self._wake = asyncio.Event()
        self._drainer = asyncio.get_running_loop().create_task(self._drain_loop())

    async def _drain_loop(self) -> None:
        """周期 flush:攒够 batch_max 立即发(_wake),否则 flush_interval 到点发。"""
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._flush_interval)
            self._wake.clear()
            await self._flush_batches()

    async def _flush_batches(self) -> None:
        while self._queue:
            batch = [self._queue.popleft() for _ in range(min(self._batch_max, len(self._queue)))]
            await self._post(batch)

    async def _post(self, batch: list[_Span]) -> None:
        """单批 POST;失败丢批计数记日志不重试(best-effort v1);取消时塞回队首。"""
        body = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            _attr("service.name", "agent-os"),
                            _attr("service.version", __version__),
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "agent_os.telemetry.otlp"},
                            "spans": [self._span_wire(span) for span in batch],
                        }
                    ],
                }
            ]
        }
        try:
            await asyncio.to_thread(self._post_blocking, body)
        except asyncio.CancelledError:
            for span in reversed(batch):  # 收尾取消:未送出的批回队首,交 close() 末次排干
                self._queue.appendleft(span)
            raise
        except Exception as e:  # noqa: BLE001 — 端点 down/超时/4xx/5xx 同归:丢批不重试
            self._failed += len(batch)
            _log.warning(
                "OTLP 导出失败,丢弃本批 %d 条 span(不重试;累计失败 %d 条): %r",
                len(batch), self._failed, e,
            )

    def _post_blocking(self, body: dict[str, Any]) -> None:
        """阻塞版单批 POST(to_thread 内;httpx 截止保证线程有界退出)。"""
        resp = self._client.post(
            f"{self._endpoint}/v1/traces",
            json=body,  # httpx 自带 Content-Type: application/json
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"OTLP endpoint 回 {resp.status_code}: {resp.text[:200]}"
            )

    @staticmethod
    def _span_wire(span: _Span) -> dict[str, Any]:
        """内部 span → OTLP JSON span 线形(end 时间在 _close 时落定)。"""
        wire: dict[str, Any] = {
            "traceId": span.trace_id,
            "spanId": span.span_id,
            "name": span.name,
            "kind": 1,  # SPAN_KIND_INTERNAL
            "startTimeUnixNano": span.start_ns,
            "endTimeUnixNano": span.end_ns or span.start_ns,
            "attributes": [_attr(k, v) for k, v in span.attributes.items()],
        }
        if span.parent_id:
            wire["parentSpanId"] = span.parent_id
        if span.events:
            wire["events"] = span.events
        if span.status_code is not None:
            status: dict[str, Any] = {"code": span.status_code}
            if span.status_message:
                status["message"] = span.status_message
            wire["status"] = status
        return wire

"""RL 轨迹 exporter(docs/DESIGN.md §10.2 "训练就绪导出"条;jsonl_exporter.py
模块 docstring 预留位的兑现):主循环 LLM 交换 → 行缓冲 JSONL。

行格式(schema ``agent_os.rl-trace/1``,首行版本头)::

    {"v": 1, "type": "header", "schema": "agent_os.rl-trace/1"}         # 每文件首行
    {"v": 1, "type": "llm_exchange", "run_id", "frame_id", "seq",
     "model", "request": {"messages": [...]}, "response": {"message": {...}},
     "usage": {...}, "ts"}

配对口径:

- 内核开 ``_rl_capture``(builder 见 sink 上注册了 ``rl_trajectory`` exporter
  即置位,duck-typed)后,``pre:llm.request`` payload 带 ``messages``(build 后
  实发线报,checkpoint ``_message_to_dict`` 同款序列化形状——WAL 行与
  checkpoint 消息可逐字互相对照),``post:llm.response`` payload 带 ``message``;
- pre 按 (run_id, frame_id) FIFO 缓冲(只缓冲带 ``messages`` 键的);post 带
  非 compress 源且带 ``message`` 键 → 弹 FIFO 出行(``seq`` 每 run 单调递增,
  ``ts`` 取 post 信号时戳 = 交换完成时刻);
- **压缩链补发的 ``post:llm.response``(``source="compress"``)一律过滤**——
  同 replay.py:82-94 / otlp_exporter.py:320-325 口径:摘要调用无 pre 配对,
  不是主循环交换,训练行只装主循环;
- 未配对/乱序(post 无缓冲 pre 可配)→ ``_skipped`` 计数,**绝不抛**(同
  OtlpExporter 纪律:遥测是观察通道,§5.3);run 终态清收本 run 残留 pre
  (中断路径的孤儿)并入 ``_skipped``;``_seq`` 跨 pause→resume 不清,同进程
  续跑序号保持单调。

保真边界(训练消费方须知):

- ``request.messages`` 是 **build 后实发线报**(压缩后主循环请求):模型真实
  所见即训练所见;压缩前的原始上下文不在行内——可由 WAL 的 pre/post:compress
  信号与 checkpoint 帧上下文重建(§10.2 轨迹即全部状态);
- loss masking 的角色维由消息自身携带:每条消息 dict 带 ``role`` 与
  ``source``(SYSTEM/PARENT_INPUT/TOOL_RESULT/INJECTED/EXTERNAL,§4.1)——
  文档化映射:assistant 可训练;system/user/tool/injected 全部 mask;
- PII:sink 开 redact 时 payload 在 ``record`` 入口已换成脱敏副本
  (jsonl_exporter.py:92-94),本 exporter 收的就是脱敏件(**train-on-redacted**
  语义,文档化不二次脱敏):合规优先于保真,与 WAL 同一份。

IO 纪律照 JsonlExporter 先例:append + 行缓冲(``export`` 内联写一行,
**绝不 await IO**——sink 在 ``record`` 里 inline-await),版本头(空文件首行),
``close()`` flush + fsync + 幂等。
"""

from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import IO, Any

from agent_os.api.v1 import (
    POST_LLM_RESPONSE,
    PRE_LLM_REQUEST,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_PAUSED,
    Signal,
)

__all__ = ["RlTrajectoryExporter"]

#: run 终态信号(清收本 run 残留 pre 缓冲的触发点)
_RUN_TERMINAL = (RUN_FINISHED, RUN_ABORTED, RUN_PAUSED)


class RlTrajectoryExporter:
    """``agent_os.api.v1.Exporter`` 契约实现(§10.2):主循环 LLM 交换 → 训练就绪 JSONL。"""

    name: str = "rl_trajectory"

    SCHEMA: str = "agent_os.rl-trace/1"

    def __init__(self, path: str) -> None:
        self.path = path
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] = open(  # noqa: SIM115 — 生命周期随 sink.close()(同 JsonlExporter 先例)
            target, "a", encoding="utf-8", buffering=1
        )
        if self._fh.tell() == 0:
            self._fh.write(
                json.dumps({"v": 1, "type": "header", "schema": self.SCHEMA}) + "\n"
            )
        #: (run_id, frame_id) → 待配对 pre payload 的 FIFO 队列(只缓冲带 messages 键的)
        self._pending: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        #: run_id → 已出行数(seq 单调种子;跨 pause→resume 不清)
        self._seq: dict[str, int] = {}
        #: 未配对/乱序/终态清收的信号数(绝不抛,同 OtlpExporter 纪律)
        self._skipped = 0
        self._closed = False

    async def export(self, sig: Signal) -> None:
        """配对并写一行(行缓冲同步写,**绝不 await IO**;sink 在 record 里 inline-await)。"""
        if self._closed:
            return
        payload = sig.payload or {}
        if sig.name == PRE_LLM_REQUEST:
            if "messages" not in payload:
                return  # 内核未开 _rl_capture(手工注册场景):无报文可配,忽略不计数
            key = (sig.run_id, sig.frame_id or "")
            self._pending.setdefault(key, deque()).append(payload)
            return
        if sig.name == POST_LLM_RESPONSE:
            if payload.get("source") == "compress":
                return  # 压缩链补发:非主循环交换(同 replay/otlp 过滤口径),不成行
            message = payload.get("message")
            if message is None:
                return  # 无响应报文(_rl_capture 未开的 legacy payload):忽略不计数
            key = (sig.run_id, sig.frame_id or "")
            queue = self._pending.get(key)
            if not queue:
                self._skipped += 1  # 未配对/乱序:计数跳过,绝不抛
                return
            pre = queue.popleft()
            if not queue:
                del self._pending[key]
            seq = self._seq.get(sig.run_id, 0) + 1
            self._seq[sig.run_id] = seq
            row = {
                "v": 1,
                "type": "llm_exchange",
                "run_id": sig.run_id,
                "frame_id": sig.frame_id,
                "seq": seq,
                "model": payload.get("model", pre.get("model", "")),
                "request": {"messages": pre["messages"]},
                "response": {"message": message},
                "usage": payload.get("usage") or {},
                "ts": sig.ts,  # 交换完成时刻(post 时戳)
            }
            self._fh.write(json.dumps(row, ensure_ascii=False, default=repr) + "\n")
            return
        if sig.name in _RUN_TERMINAL:
            # 中断路径残留 pre(发出后 run 即终止,永无 post 配对):清收防跨 run 泄漏
            for key in [k for k in self._pending if k[0] == sig.run_id]:
                self._skipped += len(self._pending.pop(key))

    async def close(self) -> None:
        """flush + fsync;幂等(sink.close 可能对同一 exporter 重复调用)。"""
        if self._closed:
            return
        self._closed = True
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()

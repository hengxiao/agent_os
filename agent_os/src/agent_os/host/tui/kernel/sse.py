"""SseClient(docs/TUI-DOC.md §3/§7;对译 godot/kernel/sse_client.gd)。

SSE 客户端:GET {base}/api/stream(decision.new/run.finished/keepalive 帧);
D2 起 path 可泛化(``start_stream(base, path=…)``,调试会话流
``/api/debug/sessions/{sid}/stream``,docs/TUI-DEBUG.md §4)。
httpx.stream 分块读流,按 "event:/data:" 帧解析;断线只上报,重连策略在宿主
(回落 2s 轮询是 web 前端既有语义,此处不替它做决定)。

T1 纪律(里程碑表):本模块只翻译,主循环不接——Doc Editor v1 未消费本通道,
这是内核给后续 app 备的 live 面。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx


class SseClient:
    """分块读流 + event:/data: 帧解析。事件经回调上报(原型 signal 对译)。"""

    def __init__(self) -> None:
        self._base: str = ""
        self._path: str = "/api/stream"
        self._buf: str = ""
        self._running: bool = False
        #: signal 对译:event_received(evt, data) / connection_changed(connected)
        self.event_received: Callable[[str, dict[str, Any]], None] | None = None
        self.connection_changed: Callable[[bool], None] | None = None

    def start_stream(self, base_url: str, path: str = "/api/stream") -> None:
        """配置流地址并开始(实际连接在 run_forever;start/stop 语义同原型)。
        ``path``:D2 调试流泛化(docs/TUI-DEBUG.md §4)——聚合流缺省
        ``/api/stream``,调试会话流 ``/api/debug/sessions/{sid}/stream``。"""
        self.stop_stream()
        self._base = base_url.rstrip("/")
        self._path = path
        self._running = True
        if self.connection_changed is not None:
            self.connection_changed(False)  # 先未连上;run_forever 连上后转 true

    def stop_stream(self) -> None:
        self._running = False

    @property
    def stream_url(self) -> str:
        return f"{self._base}{self._path}"

    def run_forever(self) -> None:
        """阻塞读流(宿主放线程里跑;断线/HTTP 错 → _fail() 只上报不重连)。"""
        if not self._running:
            return
        try:
            with httpx.Client(timeout=None) as client, client.stream("GET", self.stream_url) as resp:
                if resp.status_code >= 400:
                    self._fail()
                    return
                if self.connection_changed is not None:
                    self.connection_changed(True)
                for chunk in resp.iter_text():
                    if not self._running:
                        break
                    if chunk:
                        self._buf += chunk
                        self._drain_frames()
        except httpx.HTTPError:
            pass
        if self._running:
            self._fail()

    def _fail(self) -> None:
        self._running = False
        if self.connection_changed is not None:
            self.connection_changed(False)

    # ------------------------------------------------------------------
    # 帧解析(与原型逐行同语义;无头测试直接喂 _drain_frames)
    # ------------------------------------------------------------------

    def feed(self, text: str) -> None:
        """注入数据块(测试面;等价原型 _process 里的 chunk 累加)。"""
        self._buf += text
        self._drain_frames()

    def _drain_frames(self) -> None:
        while True:
            idx = self._buf.find("\n\n")
            if idx < 0:
                return
            frame = self._buf[:idx]
            self._buf = self._buf[idx + 2:]
            self._on_frame(frame)

    def _on_frame(self, raw: str) -> None:
        evt = "message"
        data = ""
        for line in raw.split("\n"):
            if line.startswith("event:"):
                evt = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].lstrip("\r").strip()
            # ":" 开头 = keepalive 注释帧,忽略
        if not data:
            return
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return  # 坏帧丢弃(流是只读聚合,容错优先)
        if isinstance(parsed, dict) and self.event_received is not None:
            self.event_received(evt, parsed)

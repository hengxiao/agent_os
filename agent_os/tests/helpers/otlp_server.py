"""极简 OTLP/HTTP JSON 罐头端点(tests/telemetry/test_otlp_exporter.py 的对端;FastAPI)。

照 tests/helpers/mcp_http_server.py 先例:状态外置 + 全量记账 + 故障注入。

- ``POST /v1/traces``:body(OTLP/JSON dict)原样记入 ``state.bodies``,
  默认 200 无体(OTLP/HTTP 成功响应允许空体);
- 故障注入:``state.status`` 非 2xx 时回该状态码(服务端拒绝测试);
  ``state.delay`` > 0 时先睡再回(慢端点/队列背压测试用)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response


@dataclass
class ServerState:
    """罐头端点可变状态(测试就地改;``bodies`` 全量记账供 span 树断言)。"""

    #: 每次 POST /v1/traces 的 JSON body 依次记账
    bodies: list[dict[str, Any]] = field(default_factory=list)
    #: 应答状态码(非 2xx = 故障注入:客户端丢批计数不重试)
    status: int = 200
    #: 应答前睡眠秒数(慢端点背压测试用)
    delay: float = 0.0


def create_app(state: ServerState) -> FastAPI:
    """罐头端点 app(状态外置:测试经 ``state`` 注入故障/读记账)。"""
    app = FastAPI()

    @app.post("/v1/traces")
    async def post_traces(request: Request) -> Response:
        if state.delay > 0:
            await asyncio.sleep(state.delay)
        body = await request.json()
        state.bodies.append(body)
        return Response(status_code=state.status)

    return app

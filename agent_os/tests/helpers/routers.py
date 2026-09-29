"""ModelRouter 测试桩(docs/DESIGN.md §4.2 扩展点;``[providers] router`` dotted path 目标)。

config 装配走 ``_load_dotted`` + 无参实例化,测试拿不到实例句柄,
故调用记录放模块级列表(CALLS),断言前由用例自行清空。
"""

from __future__ import annotations

from typing import Any

#: FixedRouter.route 的全部调用记录:[(route 前的 req.model, prefer), ...]
CALLS: list[tuple[str, list[str]]] = []


class FixedRouter:
    """固定把模型改写为 ``"mock/routed"`` 的测试 router(经 ``[providers] router`` 装配)。

    改写目标是可观察锚点:mock provider 的 recorded 里应看到 ``mock/routed``,
    以此证明三处内联解析点确实改走了自定义 router。
    """

    async def route(
        self, req: Any, prefer: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        CALLS.append((req.model, list(prefer or [])))
        return "mock/routed", {"temperature": req.temperature}


class ExplodingRouter:
    """无参实例化即炸的测试 router(``[providers] router`` 实例化失败 → ConfigError)。"""

    def __init__(self) -> None:
        raise RuntimeError("boom")

    async def route(
        self, req: Any, prefer: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

"""``[skills] register_smoke`` 配置扩展点的测试用冒烟回调(runtime/config.py)。

回调签名 ``(name: str, entry: dict) -> {"ok": bool, ...}``(同步/async 均可,
同 skills/local_file.py ``bind_register_smoke`` 契约)。
"""

from __future__ import annotations

from typing import Any


def smoke_ok(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """放行回调(配置钩子成功路径)。"""
    return {"ok": True, "detail": "测试冒烟通过"}


def smoke_fail(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """拒绝回调(配置钩子 fail 路径):detail 应透传进 GateError。"""
    return {"ok": False, "detail": "测试冒烟拒绝"}


async def smoke_ok_async(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """async 放行回调(同步/async 兼容路径)。"""
    return {"ok": True}

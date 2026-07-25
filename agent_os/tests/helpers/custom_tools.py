"""``[tools.custom]`` 配置扩展点的测试用注册钩子(runtime/config.py)。"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import Permission


def register(registry: Any) -> None:
    """注册一个 READ 级示例工具(配置钩子成功路径)。"""

    @registry.tool(permission=Permission.READ)
    def echo_text(text: str) -> dict:
        """原样回显输入文本。"""
        return {"echo": text}


def bad_register(registry: Any) -> None:
    """抛异常的注册钩子(配置装配失败路径 → ConfigError)。"""
    raise RuntimeError("模拟注册失败")


NOT_CALLABLE = "我不是函数"

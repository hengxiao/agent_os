r"""函数名边界映射(§NAMING.md「provider 线格式」):内部点分层级名 ↔ API 合法名。

OpenAI 兼容协议与 Anthropic 的函数名规则都不允许点分(``^[a-zA-Z][\w-]*$`` /
``^[a-zA-Z0-9_-]{1,64}$``),而内核 canonical 名是点分层级(``system.file.read``、
``skill.demo.fib``)。映射只在 provider 边界发生:

- 出方向(请求):tools schema 与 assistant 历史 tool_calls 的名字 ``.`` → ``__``;
- 入方向(响应):模型回传的 tool_calls 名字 ``__`` → ``.``。

双向安全:canonical 名与别名都不含 ``__``(别名只含单下划线,如 ``fs_read``),
模型幻觉出的未知名字映射与否都会在分发阶段以 NOT_FOUND 收尾,语义不变。
"""

from __future__ import annotations


def mangle_name(name: str) -> str:
    """点分层级名 → API 合法名(``system.file.read`` → ``system__file__read``)。"""
    return name.replace(".", "__")


def unmangle_name(name: str) -> str:
    """API 合法名 → 点分层级名(``system__file__read`` → ``system.file.read``)。

    无 ``__`` 的名字(旧扁平别名、幻觉名)原样通过——恒等映射保证向后兼容。
    """
    return name.replace("__", ".")

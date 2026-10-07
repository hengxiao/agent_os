"""迷你计算器(coding_agent 测试 fixture 的目标源码;add 故意带 bug)。"""


def add(a: int, b: int) -> int:
    """返回两数之和。"""
    return a - b  # BUG:加法写成了减法


def multiply(a: int, b: int) -> int:
    """返回两数之积。"""
    return a * b

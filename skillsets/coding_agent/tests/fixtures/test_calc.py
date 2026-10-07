"""calc 的 pytest 用例(fixture):test_add 在修复前必失败。"""

from calc import add, multiply


def test_add():
    assert add(2, 3) == 5


def test_multiply():
    assert multiply(2, 3) == 6

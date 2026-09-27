"""协议内核(docs/TUI-DOC.md §2/§3):godot/kernel 14 个 .gd 的 Python 对译。

渲染无关:三铁律(state 可序列化 / 事件上行永不出海 / render 纯渲染)、
注册即校验、/root 寻址、事件闸门、badge 记账、cascade 单向向上、统一信封、
超时两档全部照译;唯一引擎耦合(``root: Control``)换成"cell buffer 上的
一段区域"(``render_into`` 约定)。
"""

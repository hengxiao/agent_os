"""TUI agent 调试器(docs/TUI-DEBUG.md;GDB 操作模型,以 skill 调用栈为基础)。

TUI 宿主的第二个 app(与 doc_editor 平级):命令窗是第一界面(``(adb)`` 提示符),
三窗布局(调用栈 / 断点表 / 信号轨迹 + 底部命令窗)。D1 里程碑:命令方言解析器
+ 三窗只读骨架 + Demo/Offline 源(无 live)。
"""

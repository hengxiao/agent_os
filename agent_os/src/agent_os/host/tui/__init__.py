"""终端 TUI 宿主(docs/TUI-DOC.md §0):第四个薄宿主,与 cli/web/web_platform 平级。

协议内核与后端零改动:``kernel/`` 是 godot/kernel 的 Python 对译(渲染无关),
``tui/`` 是终端呈现层(cell buffer / 键解码 / keymap / 主题 / 动效),
``apps/doc_editor/`` 是信件模型 Doc Editor 的 compound 组装。
"""

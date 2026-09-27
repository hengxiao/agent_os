"""呈现层(docs/TUI-DOC.md §2):终端特有的渲染/输入面,内核零依赖它。

- ``cells``  cell buffer:2D 格子,CJK 双宽收口,ANSI 输出(§3);
- ``keys``   键解码:Ctrl/Meta/转义序列;Esc 超时裁决;关 IXON(§5.1);
- ``keymap`` KeymapPack:intent 层 + 契约校验(vim/emacs 内置两包,§5);
- ``theme``  ThemePack:ANSI 调色板 + copy + 动效档位(classic/terminal,§6);
- ``motion`` MotionPlayer:具名+档位,定时器重绘实现(§6);
- ``sty``    Sty:语义 token 取色/取文案(组件唯一取色通道)。
"""

"""Agent OS — 微内核 agentic engine(docs/DESIGN.md §1)。

内核只做流控制 + 权限控制 + IPC;九个子系统全部外置,经契约层
(:mod:`agent_os.api.v1`)通信。当前为架构骨架,不含实现逻辑。
"""

__version__ = "0.0.1"

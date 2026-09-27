"""Agent OS — 微内核 agentic engine(docs/DESIGN.md §1)。

内核只做流控制 + 权限控制 + IPC;九个子系统全部外置,经契约层
(:mod:`agent_os.api.v1`)通信。M0–M5 里程碑已实现(agent loop、checkpoint/
resume、调试器、升权、providers、工具、沙箱、技能系统、宿主层);M6 演化
(memory 子系统、技能运行期注册)尚未实现。
"""

__version__ = "0.0.1"

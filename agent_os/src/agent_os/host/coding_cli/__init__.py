"""
交互式 coding 宿主(docs/RUNNERS.md §2 宿主契约):暂停/恢复会话模型。

一个交互会话 = 长存 run + checkpoint resume(WS2):
``SessionStore`` 管理 ``<root>/sessions/<session_id>.json`` 会话文档,
记录每一轮 turn 的 run_id 与 checkpoint_path(恢复数据侧锚点);
``SessionRunner``(P2-M1)是会话生命周期核心——worker 线程跑 run、
信号行化进事件队列、问答/暂停/插话通道,与 I/O 完全解耦(渲染在 M2 repl.py)。
"""

from agent_os.host.coding_cli.session import InboxUserChannel, SessionRunner
from agent_os.host.coding_cli.session_store import SessionStore

__all__ = ["InboxUserChannel", "SessionRunner", "SessionStore"]

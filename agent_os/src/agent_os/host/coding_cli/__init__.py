"""
交互式 coding 宿主(docs/RUNNERS.md \u00a72 \u5bbf\u4e3b\u5951\u7ea6):\u6682\u505c/\u6062\u590d\u4f1a\u8bdd\u6a21\u578b\u7684\u4f1a\u8bdd\u6301\u4e45\u5316\u5c42\u3002

\u4e00\u4e2a\u4ea4\u4e92\u4f1a\u8bdd = \u957f\u5b58 run + checkpoint resume(WS2):
``SessionStore`` \u7ba1\u7406 ``<root>/sessions/<session_id>.json`` \u4f1a\u8bdd\u6587\u6863,
\u8bb0\u5f55\u6bcf\u4e00\u8f6e turn \u7684 run_id \u4e0e checkpoint_path(\u6062\u590d\u6570\u636e\u4fa7\u951a\u70b9)\u3002
"""

from agent_os.host.coding_cli.session_store import SessionStore

__all__ = ["SessionStore"]

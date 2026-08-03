"""Agent OS Web Platform(方案 A:对话中枢;docs/WEB-PLATFORM.md)。

chat-first 的新宿主:页面不再是目的地,而是对话中生成的产物卡。
本包是后端骨架(会话/产物卡协议/意图编排/卡片动作转发);
前端在 ``static/`` 预留(下一步)。

边界(docs/WEB-PLATFORM.md §7):复用内核/provider/DraftStore/gate/closure/
plan/promote/升权收件箱;**不新开权限通道**——卡片动作一律经
``artifacts.ACTION_WHITELIST`` 裁决后转发既有端点。
"""

from agent_os.host.web_platform.app import create_platform_app

__all__ = ["create_platform_app"]

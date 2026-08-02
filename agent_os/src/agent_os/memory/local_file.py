"""LocalFileMemoryService(docs/DESIGN.md §11.2 baseline;M6)。

Markdown 文件 + frontmatter(tags/created/freshness)+ 检索工具(grep/BM25 即可),
即 ``MEMORY.md`` 路线:可人读人改、保序、Git 可版本化。
检索层权限过滤:数据离开存储层之前按 principal 过滤(§11.2);
检索结果经 Context 组装时以独立"参考资料"角色注入,显式声明无指令效力。
"""

from __future__ import annotations

from agent_os.api.v1 import EntryRef, MemoryEntry, Principal, Provenance


class LocalFileMemoryService:
    """``agent_os.api.v1.MemoryService`` 协议实现(M6)。"""

    def __init__(self, root: str = "./memory") -> None:
        self.root = root

    async def search(self, query: str, k: int, principal: Principal) -> list[MemoryEntry]:
        raise NotImplementedError("M6")

    async def write(self, entry: MemoryEntry, provenance: Provenance) -> EntryRef:
        raise NotImplementedError("M6")

    async def evict(self, ref: EntryRef, reason: str) -> None:
        raise NotImplementedError("M6")

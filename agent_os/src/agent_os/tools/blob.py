"""blob store 基础版(docs/DESIGN.md §8.4/§7.2 spill;M3)。

运行目录下的文件存储;ref 采用 ``blob://<run_id>/<sha>`` URI 形态
(为跨 run 记忆层留命名空间)。spill 替换串一经生成永久冻结(§7.2)。
"""

from __future__ import annotations

import hashlib


class InMemoryBlobStore:
    """``agent_os.api.v1.BlobStore`` 协议的内存实现(M0 切片:ToolContext.blob 注入用)。

    进程内 dict 存储,run 结束即弃;文件版见下方 :class:`FileBlobStore`(M3)。
    """

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, data: bytes, run_id: str) -> str:
        """写入并返回 ``blob://<run_id>/<sha>`` ref(内容寻址,天然去重)。"""
        ref = f"blob://{run_id}/{hashlib.sha256(data).hexdigest()}"
        self._blobs[ref] = data
        return ref

    async def get(self, ref: str, offset: int = 0, limit: int | None = None) -> bytes:
        """分页读取(§8.3 ``blob_get`` offset/limit)。"""
        data = self._blobs[ref]
        return data[offset:] if limit is None else data[offset : offset + limit]


class FileBlobStore:
    """``agent_os.api.v1.BlobStore`` 协议实现(M3)。"""

    def __init__(self, root: str = "./blobs") -> None:
        self.root = root

    async def put(self, data: bytes, run_id: str) -> str:
        raise NotImplementedError("M3")

    async def get(self, ref: str, offset: int = 0, limit: int | None = None) -> bytes:
        raise NotImplementedError("M3")

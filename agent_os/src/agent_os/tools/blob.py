"""blob store 基础版(docs/DESIGN.md §8.4/§7.2 spill;M3)。

运行目录下的文件存储;ref 采用 ``blob://<run_id>/<sha>`` URI 形态
(为跨 run 记忆层留命名空间)。spill 替换串一经生成永久冻结(§7.2)。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


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
    """``agent_os.api.v1.BlobStore`` 协议实现(M3):root 目录下按内容寻址落盘。

    ref 形态与内存版一致(``blob://<run_id>/<sha>``),落盘路径 ``<root>/<run_id>/<sha>``;
    内容寻址天然去重(同内容重 put 幂等)。run_id 走白名单校验(``[A-Za-z0-9_-]``,
    不含 ``.``/``/``)防目录逃逸;非法 ref 按不存在处理(get 抛 KeyError,同内存版)。
    """

    #: run_id 白名单(run_id 进文件路径,放开 ``.`` 即开目录逃逸口子)
    _SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
    #: sha256 十六进制(内容寻址段;ref 由本类 put 生成,外来 ref 严格校验)
    _SHA256 = re.compile(r"[0-9a-f]{64}")

    def __init__(self, root: str = "./blobs") -> None:
        self.root = Path(root)

    def _path_for(self, ref: str) -> Path:
        """``blob://<run_id>/<sha>`` → ``<root>/<run_id>/<sha>``;非法 ref 抛 KeyError。"""
        run_id, sep, sha = ref.removeprefix("blob://").partition("/")
        if not sep or not self._SAFE_ID.fullmatch(run_id) or not self._SHA256.fullmatch(sha):
            raise KeyError(ref)
        return self.root / run_id / sha

    async def put(self, data: bytes, run_id: str) -> str:
        """写入并返回 ``blob://<run_id>/<sha>`` ref(与内存版同形;同内容重写幂等跳过)。"""
        if not self._SAFE_ID.fullmatch(run_id):
            raise ValueError(f"非法 run_id(白名单 [A-Za-z0-9_-]{{1,128}}): {run_id!r}")
        sha = hashlib.sha256(data).hexdigest()
        target = self.root / run_id / sha
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():  # 内容寻址:同 sha 重写是同一份字节,跳过省 IO
            target.write_bytes(data)
        return f"blob://{run_id}/{sha}"

    async def get(self, ref: str, offset: int = 0, limit: int | None = None) -> bytes:
        """分页读取(§8.3 ``blob_get`` offset/limit);未知/非法 ref 抛 KeyError(同内存版)。"""
        try:
            data = self._path_for(ref).read_bytes()
        except FileNotFoundError:
            raise KeyError(ref) from None
        return data[offset:] if limit is None else data[offset : offset + limit]

"""InMemoryBlobStore 锚点测试(DESIGN.md §8.4/§7.2;内容寻址 + 分页读取)。"""

from __future__ import annotations

import asyncio

import pytest

from agent_os.tools.blob import InMemoryBlobStore


def test_put_returns_content_addressed_ref():
    store = InMemoryBlobStore()

    async def main():
        ref = await store.put(b"hello", "run-1")
        return ref, await store.get(ref)

    ref, data = asyncio.run(main())
    assert ref.startswith("blob://run-1/")
    assert len(ref.rsplit("/", 1)[1]) == 64  # sha256 hex
    assert data == b"hello"


def test_same_content_same_ref_dedup():
    store = InMemoryBlobStore()

    async def main():
        r1 = await store.put(b"same", "run-1")
        r2 = await store.put(b"same", "run-1")
        r3 = await store.put(b"other", "run-1")
        return r1, r2, r3

    r1, r2, r3 = asyncio.run(main())
    assert r1 == r2  # 内容寻址,天然去重
    assert r1 != r3


def test_get_offset_limit_paging():
    store = InMemoryBlobStore()

    async def main():
        ref = await store.put(b"0123456789", "run-1")
        return (
            await store.get(ref, offset=3),
            await store.get(ref, offset=3, limit=4),
            await store.get(ref, offset=8, limit=100),
        )

    tail, window, over = asyncio.run(main())
    assert tail == b"3456789"
    assert window == b"3456"
    assert over == b"89"  # 越界 limit 截到末尾


def test_get_unknown_ref_raises():
    store = InMemoryBlobStore()
    with pytest.raises(KeyError):
        asyncio.run(store.get("blob://run-x/deadbeef"))

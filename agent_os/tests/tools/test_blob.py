"""InMemoryBlobStore 锚点测试(docs/DESIGN.md §8.4/§7.2;内容寻址 + 分页读取)。"""

from __future__ import annotations

import asyncio

import pytest

from agent_os.api.v1 import ToolErrorKind
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


# ---------------------------------------------------------------------------
# blob_get 工具:spill 的读取端(§8.3)
# ---------------------------------------------------------------------------


def _blob_ctx(allowed=("blob_get",)):
    from agent_os.api.v1 import Permission, SkillFrame, ToolPolicy
    from agent_os.tools.local_registry import ToolDispatchContext

    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=list(allowed),
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


def test_blob_get_registered_and_pages_through_spill():
    """**spill 闭环**:大结果 spill 出去后,模型能凭 ref 分页取回。

    回归的是一个真实缺陷:多个工具在返回值里指引"用 blob_get 读取完整结果",
    而 ``blob_get`` 从未注册(裸 NotImplementedError),spill 全是死胡同。
    """
    from agent_os.api.v1 import ToolCall
    from agent_os.tools.local_registry import LocalPythonToolRegistry

    reg = LocalPythonToolRegistry.with_builtins()
    assert reg.has("blob_get"), "blob_get 必须在默认工具面里,否则 spill 无读取端"
    ctx = _blob_ctx()

    async def main():
        # 写入 registry 自己的 blob store(工具经 ctx.blob 拿到的是同一个实例)
        store = getattr(reg, "blob", None) or reg._blob
        ref = await store.put(b"0123456789" * 3, "r1")
        first = await reg.dispatch(
            ToolCall(id="1", name="blob_get", args={"ref": ref, "limit": 10}), ctx
        )
        rest = await reg.dispatch(
            ToolCall(id="2", name="blob_get", args={"ref": ref, "offset": 10, "limit": 100}), ctx
        )
        missing = await reg.dispatch(
            ToolCall(id="3", name="blob_get", args={"ref": "blob://r1/deadbeef"}), ctx
        )
        return first, rest, missing

    first, rest, missing = asyncio.run(main())
    assert first.ok, first.error
    assert first.value["content"] == "0123456789"
    assert first.value["total_bytes"] == 30
    assert first.value["truncated"] is True, "未取完必须显式标记(§3.2 契约 2)"
    assert rest.value["content"] == "0123456789" * 2
    assert rest.value["truncated"] is False
    assert not missing.ok and missing.error.kind is ToolErrorKind.NOT_FOUND
    assert missing.error.hint, "错误须带可执行的下一步建议(§W0-3)"


# ---------------------------------------------------------------------------
# FileBlobStore(M3):root 目录下按内容寻址落盘,ref 形态与内存版一致
# ---------------------------------------------------------------------------


def test_file_blob_put_get_roundtrip(tmp_path):
    """put 落盘并返回 ``blob://<run_id>/<sha>`` ref;get 取回同一份字节。"""
    from agent_os.tools.blob import FileBlobStore

    store = FileBlobStore(str(tmp_path / "blobs"))

    async def main():
        ref = await store.put(b"hello file blob", "run-1")
        return ref, await store.get(ref)

    ref, data = asyncio.run(main())
    assert ref.startswith("blob://run-1/")
    sha = ref.rsplit("/", 1)[1]
    assert len(sha) == 64, "ref 形态须与内存版一致(sha256 hex)"
    assert data == b"hello file blob"
    assert (tmp_path / "blobs" / "run-1" / sha).is_file(), "内容须按 <root>/<run_id>/<sha> 落盘"


def test_file_blob_content_addressed_dedup_and_paging(tmp_path):
    """内容寻址天然去重;offset/limit 分页语义同内存版。"""
    from agent_os.tools.blob import FileBlobStore

    store = FileBlobStore(str(tmp_path / "blobs"))

    async def main():
        r1 = await store.put(b"0123456789", "run-1")
        r2 = await store.put(b"0123456789", "run-1")
        return r1, r2, await store.get(r1, offset=3, limit=4)

    r1, r2, window = asyncio.run(main())
    assert r1 == r2, "同内容同 ref(去重)"
    assert window == b"3456"


def test_file_blob_unknown_ref_raises_key_error(tmp_path):
    """未知 ref → KeyError(同内存版;blob_get 工具据此报 NOT_FOUND)。"""
    from agent_os.tools.blob import FileBlobStore

    store = FileBlobStore(str(tmp_path / "blobs"))
    with pytest.raises(KeyError):
        asyncio.run(store.get("blob://run-x/" + "0" * 64))


def test_file_blob_rejects_traversal_and_malformed_ref(tmp_path):
    """目录逃逸拒绝(白名单校验):含 ``..``/``/`` 的段、非 sha 段、畸形 ref 一律 KeyError。"""
    from agent_os.tools.blob import FileBlobStore

    store = FileBlobStore(str(tmp_path / "blobs"))
    evil = [
        "blob://../escape/" + "0" * 64,  # run_id 段含 ..
        "blob://run-1/.." ,  # sha 段逃逸
        "blob://run-1/" + "0" * 64 + "/extra",  # 多段
        "blob://run-1/nothex",  # 非 sha256 hex
        "not-a-blob-uri",
        "blob:///" + "0" * 64,  # 空 run_id
    ]
    for ref in evil:
        with pytest.raises(KeyError):
            asyncio.run(store.get(ref))


def test_file_blob_put_rejects_unsafe_run_id(tmp_path):
    """put 的 run_id 同样过白名单(run_id 进文件路径,放开 ``.`` 即开逃逸口子)。"""
    from agent_os.tools.blob import FileBlobStore

    store = FileBlobStore(str(tmp_path / "blobs"))
    with pytest.raises(ValueError):
        asyncio.run(store.put(b"x", ".."))
    with pytest.raises(ValueError):
        asyncio.run(store.put(b"x", "a/b"))

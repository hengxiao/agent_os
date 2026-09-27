"""memory 工具面锚点测试(docs/DESIGN.md §11.2;M6)。

固定约定:

- ``system.memory.search``(READ)/``system.memory.write``(WRITE)随 ``with_builtins`` 常驻注册,
  别名 ``memory_search``/``memory_write``;
- MemoryService 数据源由 KernelBuilder 经 ``bind_memory`` 注入(同 bind_skills 先例);
  未 bind 时调用报"未装配"结构化错误(行为与引入前一致);
- READ 档不占帧白名单;WRITE 档受帧白名单约束(dispatch 三层权限,§8.1);
- ToolContext.principal(数据层 subject 形态)映射为 memory principal(user=subject)透传。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_os.api.v1 import (
    EntryRef,
    MemoryPrincipal,
    Permission,
    Principal,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.memory.local_file import LocalFileMemoryService
from agent_os.tools.local_registry import LocalPythonToolRegistry


def _ctx(tmp_path: Path, allowed: list[str], principal=None) -> ToolDispatchContext:
    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1", principal=principal),
        allowed_tools=allowed,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=tmp_path,
    )


def _dispatch(reg, name, args, ctx):
    return asyncio.run(reg.dispatch(ToolCall(id="t", name=name, args=args), ctx))


# ---------------------------------------------------------------------------
# 注册与档位
# ---------------------------------------------------------------------------


def test_memory_tools_registered_in_builtins():
    reg = LocalPythonToolRegistry.with_builtins()
    assert reg.has("system.memory.search") and reg.has("system.memory.write")
    assert reg.has("memory_search") and reg.has("memory_write"), "flat 别名"
    specs = {s.name: s for s in reg.specs()}
    assert specs["system.memory.search"].permission is Permission.READ
    assert specs["system.memory.write"].permission is Permission.WRITE


def test_memory_tools_unbound_report_not_assembled(tmp_path):
    """未 bind_memory:工具在场但报结构化"未装配"错误(零破坏回归锚;not_found 同 supervisor 先例)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    result = _dispatch(reg, "system.memory.search", {"query": "retry"}, _ctx(tmp_path, []))
    assert not result.ok and result.error is not None
    assert result.error.kind is ToolErrorKind.NOT_FOUND and "未装配" in result.error.message
    result = _dispatch(
        reg, "system.memory.write", {"content": "x"}, _ctx(tmp_path, ["system.memory.write"])
    )
    assert not result.ok and result.error is not None and "未装配" in result.error.message


def test_memory_write_requires_frame_whitelist(tmp_path):
    """WRITE 档受帧白名单约束(§8.1);READ 档不占白名单。"""
    reg = LocalPythonToolRegistry.with_builtins()
    reg.bind_memory(LocalFileMemoryService(str(tmp_path / "memory")))
    denied = _dispatch(reg, "system.memory.write", {"content": "x"}, _ctx(tmp_path, []))
    assert not denied.ok and denied.error is not None
    assert denied.error.kind is ToolErrorKind.PERMISSION_DENIED
    ok = _dispatch(reg, "system.memory.search", {"query": "x"}, _ctx(tmp_path, []))
    assert ok.ok, "READ 档不受帧白名单限制"


# ---------------------------------------------------------------------------
# bind 后闭环与 principal 透传
# ---------------------------------------------------------------------------


def test_memory_write_then_search_roundtrip(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    reg.bind_memory(LocalFileMemoryService(str(tmp_path / "memory")))
    ctx = _ctx(tmp_path, ["system.memory.write"])
    written = _dispatch(
        reg,
        "system.memory.write",
        {"content": "retry 教训: 先看日志再放量", "tags": ["retry"], "note": "复盘"},
        ctx,
    )
    assert written.ok, written.error
    assert written.value["id"], "write 返回 EntryRef id"
    found = _dispatch(reg, "memory_search", {"query": "retry"}, ctx)
    assert found.ok, found.error
    assert any("先看日志" in r["content"] for r in found.value["results"])
    entry = found.value["results"][0]
    assert entry["trust"] == "experience", "经验条目不具指令效力"
    assert entry["source"]["kind"] == "experience", "强制 source tagging"


class _FakeMemory:
    """捕获 search/write 入参的 MemoryService 结构实现(principal 透传断言用)。"""

    def __init__(self) -> None:
        self.seen: tuple | None = None
        self.written: tuple | None = None

    async def search(self, query, k, principal):
        self.seen = (query, k, principal)
        return []

    async def write(self, entry, provenance):
        self.written = (entry, provenance)
        return EntryRef(id="fake")

    async def evict(self, ref, reason): ...


def test_memory_tools_map_data_principal_to_memory_principal(tmp_path):
    """ToolContext.principal(subject/issuer/attrs)→ memory principal(user=subject)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    fake = _FakeMemory()
    reg.bind_memory(fake)
    caller = Principal(subject="user:alice", issuer="cli")
    result = _dispatch(
        reg, "system.memory.search", {"query": "q", "k": 3}, _ctx(tmp_path, [], principal=caller)
    )
    assert result.ok, result.error
    query, k, principal = fake.seen
    assert (query, k) == ("q", 3)
    assert isinstance(principal, MemoryPrincipal) and principal.user == "user:alice"

    _dispatch(
        reg,
        "system.memory.write",
        {"content": "alice 的偏好"},
        _ctx(tmp_path, ["system.memory.write"], principal=caller),
    )
    entry, provenance = fake.written
    assert entry.source["user"] == "user:alice", "写入条目按调用者打 source.user"
    assert provenance.run_id == "r1" and provenance.task == "f1", "provenance 由内核注入"


def test_memory_tools_none_principal_passes_through(tmp_path):
    """单用户语义:principal 为 None 原样透传(检索全通);写入 source 不带 user 键。"""
    reg = LocalPythonToolRegistry.with_builtins()
    fake = _FakeMemory()
    reg.bind_memory(fake)
    _dispatch(reg, "system.memory.search", {"query": "q"}, _ctx(tmp_path, []))
    assert fake.seen[2] is None
    _dispatch(reg, "system.memory.write", {"content": "全局经验"}, _ctx(tmp_path, ["system.memory.write"]))
    entry, _prov = fake.written
    assert "user" not in entry.source

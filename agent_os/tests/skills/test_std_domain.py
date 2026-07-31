"""W4-2/3/4 锚点测试:std/memory · std/web · std/learn(STDLIB-CATALOG §W4-2~W4-4)。

固定约定:

- memory 文件版四件:common.memory.extract(会话→候选)/common.memory.reconcile(候选×既有→ADD/UPDATE/DELETE/NOOP)/
  common.memory.consolidate(code,评分聚类剪枝)/common.memory.check(code,约束交叉校验);
- common.web.fetch_page:outputs 强制 `source` 字段,正文以 `<external_content source=...>` 包裹;
- common.research.iterative:检索 → 判充分 → 精化查询 → 再检索(充分即停);
- learn 三件:common.learn.distill_experience/reflect_on_failure/common.memory.verify(入库闸门)。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolPolicy,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

STD_DIR = Path(__file__).resolve().parents[2] / "std"


def domain_brain(req: ChatRequest) -> ChatResponse:
    system = req.messages[0].content if req.messages else ""
    calls = sum(1 for m in req.messages if m.role is Role.ASSISTANT and m.tool_calls)

    def respond(payload):
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    def call(name, args):
        return ChatResponse(
            message=Message(role=Role.ASSISTANT,
                            tool_calls=[{"id": "c1", "name": name, "args": args}]),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )

    if "抽取" in system and "记忆" in system:
        return respond({"candidates": [{"fact": "用户偏好 ISO 日期", "kind": "preference"}]})
    if "调和" in system or "reconcile" in system.lower():
        return respond({"decisions": [
            {"fact": "用户偏好 ISO 日期", "action": "ADD", "reason": "无冲突"},
            {"fact": "用户偏好美式日期", "action": "DELETE", "reason": "与前者矛盾"},
        ]})
    if "蒸馏" in system:
        return respond({"experience": [{"pattern": "先验证再写入", "transferability": "high"}]})
    if "反思" in system:
        return respond({"negative_rules": ["不要在没有证据时声称完成"]})
    if "研究" in system:
        if calls == 0:
            return call("common.web.fetch_page", {"url": "https://example.com/a"})
        if calls == 1:
            return respond({"sufficient": False, "refined_query": "更精确的问题"})
        if calls == 2:
            return call("common.web.fetch_page", {"url": "https://example.com/b"})
        return respond({"answer": "综合答案", "sources": ["https://example.com/a", "https://example.com/b"]})
    if "调研" in system:
        if calls == 0:
            return call("common.web.fetch_page", {"url": "https://example.com/a"})
        return respond({"answer": "单轮答案", "sources": ["https://example.com/a"]})
    return respond({"ok": True})


def _register_mock_http(tools):
    @tools.tool(name="system.net.http_fetch", permission=Permission.NET, timeout=5)
    async def http_fetch(url: str, max_bytes: int = 100_000) -> dict:
        """抓取 URL(测试 mock)。"""
        return {"status": 200, "content": f"<html><body>{url} 的正文内容,包含研究所需事实。</body></html>",
                "truncated": False}


def _kernel(brain=domain_brain):
    sys.path.insert(0, str(STD_DIR))
    tools = LocalPythonToolRegistry.with_builtins()
    _register_mock_http(tools)
    return (
        KernelBuilder(RunConfig(model="mock/x", tool_policy=ToolPolicy(max_permission=Permission.EXEC), compression="off"))
        .providers(MockProvider(brain))
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(STD_DIR)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


def run(skill: str, input: dict):
    return asyncio.run(_kernel().run(skill, input))


# ---------------------------------------------------------------------------
# std/memory
# ---------------------------------------------------------------------------


def test_memory_extract():
    r = run("common.memory.extract", {"session": "用户说:以后日期都用 ISO。"})
    assert r["candidates"] and r["candidates"][0]["fact"]


def test_memory_reconcile_four_actions():
    r = run("common.memory.reconcile", {
        "candidates": [{"fact": "用户偏好 ISO 日期"}],
        "existing": [{"fact": "用户偏好美式日期"}],
    })
    actions = {d["action"] for d in r["decisions"]}
    assert actions <= {"ADD", "UPDATE", "DELETE", "NOOP"}
    assert any(d["action"] == "DELETE" for d in r["decisions"]), "矛盾项必须 DELETE 而非并存"


def test_memory_consolidate_and_check():
    entries = [
        {"fact": "A 重要事实", "access_count": 9, "created_at": "2026-07-01"},
        {"fact": "B 陈旧琐事", "access_count": 0, "created_at": "2025-01-01"},
    ]
    # today 显式传入:不读系统钟,断言才不随运行日期漂移(§1 复现性纪律)
    r = run("common.memory.consolidate", {"entries": entries, "today": "2026-07-26"})
    facts = [e["fact"] for e in r["entries"]]
    assert "A 重要事实" in facts
    assert r.get("pruned"), "低价值条目应被标记剪枝"

    bad = run("common.memory.check", {"entries": [
        {"fact": "护照有效期 2030 年"},
        {"fact": "护照有效期 2020 年,已过期"},
    ]})
    assert bad["ok"] is False and bad["conflicts"]


# ---------------------------------------------------------------------------
# std/web
# ---------------------------------------------------------------------------


def test_fetch_page_has_source_and_wrapping():
    r = run("common.web.fetch_page", {"url": "https://example.com/a"})
    assert r["source"] == "https://example.com/a"
    assert "<external_content" in r["content"] and "source=" in r["content"]
    assert "研究所需事实" in r["content"]


def test_research_one():
    r = run("common.research.one", {"query": "什么是 X"})
    assert r["answer"]
    assert r["sources"] == ["https://example.com/a"]


def test_research_iterative_refines_until_sufficient():
    r = run("common.research.iterative", {"query": "X 的最新进展"})
    assert r["answer"] == "综合答案"
    assert r["rounds"] >= 2, "不充分时必须精化再检索"
    assert r["sources"] == ["https://example.com/a", "https://example.com/b"]


# ---------------------------------------------------------------------------
# std/learn
# ---------------------------------------------------------------------------


def test_distill_experience():
    r = run("common.learn.distill_experience", {"trajectory": "一次成功的调试过程……"})
    assert r["experience"] and r["experience"][0]["pattern"]


def test_reflect_on_failure():
    r = run("common.learn.reflect_on_failure", {"failure": "声称完成但测试没跑"})
    assert r["negative_rules"]


def test_verify_before_store_gate():
    ok = run("common.memory.verify", {
        "artifact": {"kind": "code_skill", "name": "x"},
        "replay_result": {"total": 45},
        "expected": {"total": 45},
    })
    assert ok["verdict"] == "pass"
    bad = run("common.memory.verify", {
        "artifact": {"kind": "code_skill", "name": "x"},
        "replay_result": {"total": 44},
        "expected": {"total": 45},
    })
    assert bad["verdict"] == "fail"

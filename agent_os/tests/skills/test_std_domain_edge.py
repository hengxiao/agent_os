"""W4-2/3/4 边界测试(非锚点):std/web · std/memory · std/learn 的拒绝/退化路径。

锚点 tests/skills/test_std_domain.py 钉死主路径;本文件覆盖:

- fetch_page 工具层:正文清洗(script/style/实体)、超长截断 + spill、
  缺 http_fetch 的结构化错误、http_fetch 失败透传、max_chars 截断;
- research_iterative 驱动:永不充分时的轮次收敛上限(不死循环)、一轮即充分;
- memory_consolidate:近重复去重、阈值覆盖、today 注入、畸形/缺省字段;
- memory_check:同值不冲突、"是/为"锚冲突、无锚忽略、多键混合;
- verify_before_store:int/float 归一、bool ≠ 1、嵌套键序、fail 差异说明。
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
    SkillFrame,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.errors import ToolDispatchError
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry, ToolDispatchContext

STD_DIR = Path(__file__).resolve().parents[2] / "std"
sys.path.insert(0, str(STD_DIR))


def _register_mock_http(tools, body: str | None = None):
    @tools.tool(permission=Permission.NET, timeout=5)
    async def http_fetch(url: str, max_bytes: int = 100_000) -> dict:
        """抓取 URL(测试 mock)。"""
        content = body if body is not None else f"<html><body>{url} 的正文。</body></html>"
        return {"status": 200, "content": content, "truncated": False}


def _kernel(brain=None, http_body: str | None = None):
    tools = LocalPythonToolRegistry.with_builtins()
    _register_mock_http(tools, http_body)
    return (
        KernelBuilder(
            RunConfig(model="mock/x", tool_policy=ToolPolicy(max_permission=Permission.EXEC), compression="off")
        )
        .providers(MockProvider(brain or (lambda req: None)))
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(STD_DIR / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


def run(skill: str, input: dict, **kw):
    return asyncio.run(_kernel(**kw).run(skill, input))


def _tool_ctx(tmp_path: Path) -> ToolDispatchContext:
    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=["fetch_page"],
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=tmp_path,
    )


def _dispatch(reg, args: dict, ctx):
    return asyncio.run(reg.dispatch(ToolCall(id="fp", name="fetch_page", args=args), ctx))


# ---------------------------------------------------------------------------
# fetch_page 工具层
# ---------------------------------------------------------------------------


def test_fetch_page_strips_scripts_styles_and_entities(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    _register_mock_http(
        reg,
        "<html><head><style>body{color:red}</style><script>alert(1)</script></head>"
        "<body><p>甲 &amp; 乙</p><b>加粗</b></body></html>",
    )
    r = _dispatch(reg, {"url": "https://example.com/x"}, _tool_ctx(tmp_path))
    assert r.ok, r.error
    text = r.value["content"]
    assert "alert" not in text and "color:red" not in text
    assert "甲 & 乙" in text and "加粗" in text
    assert text.startswith('<external_content source="https://example.com/x">')
    assert r.value["truncated"] is False and r.value["status"] == 200


def test_fetch_page_truncates_and_spills(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()
    _register_mock_http(reg, "<html><body>" + "长" * 5000 + "</body></html>")
    r = _dispatch(reg, {"url": "https://example.com/big", "max_chars": 100}, _tool_ctx(tmp_path))
    assert r.ok, r.error
    assert r.value["truncated"] is True
    assert "[truncated]" in r.value["content"]
    assert r.value.get("spill_ref"), "截断时全文应 spill 至 blob"
    spilled = asyncio.run(reg._blob.get(r.value["spill_ref"]))
    assert len(spilled.decode("utf-8")) == 5000  # 全文未丢


def test_fetch_page_missing_http_fetch_is_structured_error(tmp_path):
    reg = LocalPythonToolRegistry()  # 构造器自带 fetch_page,但无 http_fetch
    assert reg.has("fetch_page")
    r = _dispatch(reg, {"url": "https://example.com/x"}, _tool_ctx(tmp_path))
    assert not r.ok
    assert r.error is not None and r.error.kind.value == "not_found"
    assert "http_fetch" in r.error.message


def test_fetch_page_http_failure_is_not_ok(tmp_path):
    reg = LocalPythonToolRegistry.with_builtins()

    @reg.tool(permission=Permission.NET, timeout=5)
    async def http_fetch(url: str, max_bytes: int = 100_000) -> dict:
        """抓取 URL(坏掉 mock)。"""
        raise ConnectionError("network down")

    r = _dispatch(reg, {"url": "https://example.com/x"}, _tool_ctx(tmp_path))
    assert not r.ok, "http_fetch 失败不得当成功"


def test_fetch_page_skill_failure_raises(tmp_path):
    tools = LocalPythonToolRegistry.with_builtins()

    @tools.tool(permission=Permission.NET, timeout=5)
    async def http_fetch(url: str, max_bytes: int = 100_000) -> dict:
        """抓取 URL(坏掉 mock)。"""
        raise ConnectionError("network down")

    kernel = (
        KernelBuilder(RunConfig(model="mock/x", tool_policy=ToolPolicy(max_permission=Permission.EXEC)))
        .providers(MockProvider())
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(STD_DIR / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )
    with pytest.raises(ToolDispatchError):
        asyncio.run(kernel.run("fetch_page", {"url": "https://example.com/x"}))


# ---------------------------------------------------------------------------
# research_iterative 驱动边界
# ---------------------------------------------------------------------------


def _iter_brain(always_insufficient: bool):
    def brain(req: ChatRequest) -> ChatResponse:
        calls = sum(1 for m in req.messages if m.role is Role.ASSISTANT and m.tool_calls)

        def respond(payload):
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
                finish_reason="stop",
                usage=ChatUsage(prompt=1, completion=1),
            )

        if calls == 0:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[{"id": "c1", "name": "fetch_page", "args": {"url": "https://example.com/a"}}],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        if always_insufficient:
            return respond({"sufficient": False, "refined_query": "更精确的问题"})
        return respond({"answer": "一轮就够", "sources": ["https://example.com/a"]})

    return brain


def test_research_iterative_sufficient_after_one_round():
    r = run("research_iterative", {"query": "X"}, brain=_iter_brain(always_insufficient=False))
    assert r["answer"] == "一轮就够"
    assert r["rounds"] == 1
    assert r["sources"] == ["https://example.com/a"]


def test_research_iterative_never_sufficient_hits_round_cap():
    r = run("research_iterative", {"query": "X", "max_rounds": 2}, brain=_iter_brain(always_insufficient=True))
    assert r["rounds"] == 2, "轮次用尽必须收束,不得死循环"
    assert "未能" in r["answer"]
    assert r["sources"], "兜底 sources 来自实际抓取"


# ---------------------------------------------------------------------------
# memory_consolidate 边界
# ---------------------------------------------------------------------------


def test_memory_consolidate_dedup_near_duplicate():
    entries = [
        {"fact": "用户偏好 ISO 日期", "access_count": 5, "created_at": "2026-07-01"},
        {"fact": "用户偏好ISO日期", "access_count": 1, "created_at": "2026-07-01"},
    ]
    r = run("memory_consolidate", {"entries": entries, "today": "2026-07-26"})
    kept = [e["fact"] for e in r["entries"]]
    assert kept == ["用户偏好 ISO 日期"], "近重复簇只留高分项"
    assert any("近重复" in p.get("reason", "") for p in r["pruned"])


def test_memory_consolidate_threshold_override_and_bad_fields():
    entries = [{"fact": "A 重要事实", "access_count": 9, "created_at": "2026-07-01"}]
    r = run("memory_consolidate", {"entries": entries, "today": "2026-07-26", "keep_threshold": 99})
    assert r["entries"] == [] and len(r["pruned"]) == 1, "阈值拉高后全部进 pruned"
    # 缺 created_at / 畸形 access_count 不崩溃,按最老处理
    r2 = run("memory_consolidate", {"entries": [{"fact": "无日期条目"}], "today": "2026-07-26"})
    assert r2["pruned"] and not r2["entries"]
    r3 = run("memory_consolidate", {"entries": []})
    assert r3 == {"entries": [], "pruned": []}


# ---------------------------------------------------------------------------
# memory_check 边界
# ---------------------------------------------------------------------------


def test_memory_check_same_value_is_ok():
    r = run("memory_check", {"entries": [{"fact": "护照有效期 2030 年"}, {"fact": "护照有效期 2030 年"}]})
    assert r["ok"] is True and r["conflicts"] == []


def test_memory_check_be_anchor_conflict_and_mixed():
    r = run("memory_check", {"entries": [
        {"fact": "城市是北京"},
        {"fact": "城市是上海"},
        {"fact": "今天天气不错"},  # 无锚,不参与判定
        {"fact": "年龄 30 岁"},
    ]})
    assert r["ok"] is False
    assert r["conflicts"] == [{"key": "城市", "values": ["上海", "北京"]}], "只报冲突键,无锚条忽略"


# ---------------------------------------------------------------------------
# verify_before_store 边界
# ---------------------------------------------------------------------------


def test_verify_before_store_normalization():
    ok = run("verify_before_store", {
        "artifact": {},
        "replay_result": {"total": 45, "items": [1, {"b": 2}]},
        "expected": {"items": [1.0, {"b": 2.0}], "total": 45.0},  # 键序无关 + int/float 归一
    })
    assert ok["verdict"] == "pass"


def test_verify_before_store_bool_is_not_one():
    bad = run("verify_before_store", {
        "artifact": {},
        "replay_result": {"ok": True},
        "expected": {"ok": 1},  # True 不应等于 1
    })
    assert bad["verdict"] == "fail"
    assert "replay=" in bad["reason"] and "expected=" in bad["reason"]

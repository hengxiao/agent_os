"""library-design-plan Phase 4 锚点测试:common.* 缺口技能(retrieval 归置/dev 快检/web 解析/research 排序与报告)。

固定约定(与 W2/W4 锚点相同):

- 包位置:`agent_os/std/`(域分包 *.yaml 合并加载);code 技能零 LLM、零外部依赖、确定性;
- 质量门槛(STDLIB §8):每技能 ≥ 覆盖 happy / 边界 / 拒绝 三类;
- `common.research.source_rank` 复用 transform.bm25_score 口径;`common.web.parse_html`
  复用工具层 _extract_text 口径(同一逻辑一份)。
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


def report_brain(req: ChatRequest) -> ChatResponse:
    """common.research.report 的 mock 大脑:把 findings 拼成报告(照输入 JSON 反应)。"""
    data = json.loads(next(
        m.content for m in req.messages if m.role is Role.USER and m.content.startswith("{")
    ))
    lines = [f"# 报告({data['outline']})"] + [f["fact"] for f in data["findings"]]
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT, content=json.dumps({"report": "\n".join(lines)}, ensure_ascii=False)
        ),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _kernel(workdir: Path | None = None, brain=None):
    sys.path.insert(0, str(STD_DIR))
    return (
        KernelBuilder(RunConfig(
            model="mock/x",
            tool_policy=ToolPolicy(max_permission=Permission.EXEC),
            compression="off",
            workdir=str(workdir) if workdir else None,
        ))
        .providers(MockProvider(brain) if brain else MockProvider())
        .tools(LocalPythonToolRegistry.with_builtins())
        .skills(LocalFileSkillRegistry(str(STD_DIR)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


def run(skill: str, input: dict, workdir: Path | None = None, brain=None):
    return asyncio.run(_kernel(workdir, brain).run(skill, input))


# ---------------------------------------------------------------------------
# common.retrieval.deduplicate
# ---------------------------------------------------------------------------


def test_deduplicate_by_key_keeps_first():
    r = run("common.retrieval.deduplicate", {
        "items": [{"id": "a", "v": 1}, {"id": "a", "v": 2}, {"id": "b"}],
        "key": "id",
    })
    assert [u["id"] for u in r["unique"]] == ["a", "b"]
    assert [d["v"] for d in r["duplicates"]] == [2]


def test_deduplicate_without_key_and_all_unique():
    r = run("common.retrieval.deduplicate", {"items": ["x", "x", "y"]})
    assert r["unique"] == ["x", "y"] and r["duplicates"] == ["x"]
    r2 = run("common.retrieval.deduplicate", {"items": [1, 2, 3]})
    assert r2["unique"] == [1, 2, 3] and r2["duplicates"] == []


def test_deduplicate_rejects_bad_input():
    with pytest.raises(Exception):
        run("common.retrieval.deduplicate", {"items": "not-a-list"})
    with pytest.raises(Exception):
        run("common.retrieval.deduplicate", {"items": ["a"], "key": "id"})  # 指定 key 须为 dict 条目


# ---------------------------------------------------------------------------
# common.retrieval.sort_by / group_by
# ---------------------------------------------------------------------------


def test_sort_by_stable_and_missing_last():
    items = [{"id": "a", "s": 2}, {"id": "b", "s": 1}, {"id": "c", "s": 2}, {"id": "d"}]
    r = run("common.retrieval.sort_by", {"items": items, "key": "s"})
    assert [i["id"] for i in r["items"]] == ["b", "a", "c", "d"]  # 稳定(同键保原序)+ 缺键排尾
    r2 = run("common.retrieval.sort_by", {"items": items, "key": "s", "reverse": True})
    assert [i["id"] for i in r2["items"]] == ["a", "c", "b", "d"]


def test_sort_by_rejects_missing_key():
    with pytest.raises(Exception):
        run("common.retrieval.sort_by", {"items": [{"s": 1}]})


def test_group_by_first_seen_order():
    r = run("common.retrieval.group_by", {
        "items": [{"k": "x", "n": 1}, {"k": "y"}, {"k": "x", "n": 2}],
        "key": "k",
    })
    assert list(r["groups"]) == ["x", "y"]  # 组序 = 首见序
    assert [i.get("n") for i in r["groups"]["x"]] == [1, 2]


def test_group_by_rejects_bad_input():
    with pytest.raises(Exception):
        run("common.retrieval.group_by", {"items": [], "key": ""})
    with pytest.raises(Exception):
        run("common.retrieval.group_by", {"items": ["a"], "key": "k"})  # 非 dict 条目


# ---------------------------------------------------------------------------
# common.dev.lint_diff
# ---------------------------------------------------------------------------


def test_lint_diff_flags_common_problems():
    diff = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,1 +1,6 @@\n"
        " ctx\n"
        "+clean\n"
        "+trail \n"
        "+\ttab_indented\n"
        "+  space_indented\n"
        "+<<<<<<< HEAD\n"
        "\\ No newline at end of file\n"
    )
    r = run("common.dev.lint_diff", {"diff": diff})
    by_kind = {}
    for issue in r["issues"]:
        by_kind.setdefault(issue["kind"], []).append(issue)
    assert [i["line"] for i in by_kind["trailing_whitespace"]] == [3]
    assert [i["line"] for i in by_kind["conflict_marker"]] == [6]
    assert by_kind["mixed_indent"][0]["file"] == "x.py"
    assert [i["line"] for i in by_kind["missing_eof_newline"]] == [6]
    assert all(i["message"] for i in r["issues"])


def test_lint_diff_clean_diff_has_no_issues():
    diff = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " ctx\n"
        "+clean\n"
    )
    assert run("common.dev.lint_diff", {"diff": diff})["issues"] == []


def test_lint_diff_rejects_empty():
    with pytest.raises(Exception):
        run("common.dev.lint_diff", {"diff": "  \n"})


# ---------------------------------------------------------------------------
# common.dev.find_definition
# ---------------------------------------------------------------------------


def test_find_definition_file(tmp_path):
    (tmp_path / "mod.py").write_text(
        "def target():\n    pass\n\ntarget = 1\n\nclass target:\n    pass\n", encoding="utf-8"
    )
    r = run("common.dev.find_definition", {"path": "mod.py", "symbol": "target"}, tmp_path)
    assert [(loc["kind"], loc["line"]) for loc in r["locations"]] == [
        ("def", 1), ("assign", 4), ("class", 6),
    ]


def test_find_definition_directory_scans_py(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("class foo:\n    pass\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("def foo(): nope\n", encoding="utf-8")  # 非 .py 不扫
    r = run("common.dev.find_definition", {"path": ".", "symbol": "foo"}, tmp_path)
    assert [(loc["file"], loc["kind"]) for loc in r["locations"]] == [("a.py", "def"), ("b.py", "class")]


def test_find_definition_rejects(tmp_path):
    with pytest.raises(Exception):
        run("common.dev.find_definition", {"path": ".", "symbol": "not-an-ident"}, tmp_path)
    with pytest.raises(Exception):
        run("common.dev.find_definition", {"path": "missing.py", "symbol": "foo"}, tmp_path)


# ---------------------------------------------------------------------------
# common.research.source_rank / report
# ---------------------------------------------------------------------------


def test_source_rank_relevance_order():
    r = run("common.research.source_rank", {
        "query": "python 版本发布",
        "sources": [
            {"url": "https://ex.com/b", "text": "完全无关的烹饪食谱内容"},
            {"url": "https://ex.com/a", "text": "python 3.13 版本发布说明"},
        ],
    })
    assert r["ranked"][0]["id"] == "https://ex.com/a"
    assert r["ranked"][0]["score"] > r["ranked"][1]["score"]


def test_source_rank_title_fallback_and_k():
    r = run("common.research.source_rank", {
        "query": "python 发布",
        "k": 1,
        "sources": [
            {"id": "s1", "title": "python 新版本发布"},
            {"id": "s2", "title": "无关主题"},
        ],
    })
    assert [x["id"] for x in r["ranked"]] == ["s1"]


def test_source_rank_rejects_bad_input():
    with pytest.raises(Exception):
        run("common.research.source_rank", {"query": "  ", "sources": []})
    with pytest.raises(Exception):
        run("common.research.source_rank", {"query": "q", "sources": ["not-a-dict"]})


def test_research_report_assembles():
    r = run("common.research.report", {
        "outline": "1. 背景",
        "findings": [{"fact": "X 于 2026 年发布", "source": "https://ex.com/a"}],
    }, brain=report_brain)
    assert "X 于 2026 年发布" in r["report"]
    assert "1. 背景" in r["report"]


# ---------------------------------------------------------------------------
# common.web.parse_html / extract_links
# ---------------------------------------------------------------------------


def test_parse_html_text_links_title():
    html = (
        "<html><head><title> 测试页 </title></head><body>"
        "<script>var x = 1;</script><p>你好 <b>世界</b></p>"
        '<a href="/a">A</a><a href="https://ex.com/b">B</a><a href="/a">A2</a>'
        "</body></html>"
    )
    r = run("common.web.parse_html", {"html": html})
    assert r["title"] == "测试页"
    assert "你好" in r["text"] and "var x" not in r["text"]
    assert r["links"] == ["/a", "https://ex.com/b"]  # 去重保序


def test_parse_html_rejects_empty():
    with pytest.raises(Exception):
        run("common.web.parse_html", {"html": "  "})


def test_extract_links_absolutizes():
    r = run("common.web.extract_links", {
        "html": '<a href="/docs">d</a><a href="https://x.com/y">y</a><a href="p2">p</a>',
        "base_url": "https://ex.com/a/page",
    })
    assert r["links"] == ["https://ex.com/docs", "https://x.com/y", "https://ex.com/a/p2"]


def test_extract_links_rejects_missing_base():
    with pytest.raises(Exception):
        run("common.web.extract_links", {"html": '<a href="/a">x</a>', "base_url": ""})

"""W4-1 锚点测试:std/files(STDLIB-CATALOG §W4-1)。

固定约定:

- `common.eval.progress_track`(code):JSON 进度文件,`{action: init|update|summary}`;
  init 建文件(tasks 全 passes:false),update 置 passes,summary 给 resume 摘要;
- `common.dev.run_tests`(code,EXEC):跑 pytest 并**结构化解析** `{passed, failed, errors, failures[], ok}`;
- `common.dev.read_file_smart`(code):`{path, level: L0|L1|L2, offset?, limit?}` 三级阅读
  (L0 概要/L1 结构/L2 全文带行号);
- `common.dev.apply_patch`(code):unified diff 应用到 workdir,`{applied, files}`;冲突拒绝;
- `common.dev.summarize_tree`(code):目录级结构摘要(目录树 + 计数 + 关键文件)。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from agent_os.api.v1 import Permission, RunConfig, ToolPolicy
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

STD_DIR = Path(__file__).resolve().parents[2] / "std"


def _kernel(workdir: Path):
    sys.path.insert(0, str(STD_DIR))
    config = RunConfig(
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=str(workdir),
    )
    return (
        KernelBuilder(config)
        .providers(MockProvider())
        .tools(LocalPythonToolRegistry.with_builtins())
        .skills(LocalFileSkillRegistry(str(STD_DIR)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


def run(skill: str, input: dict, workdir: Path):
    return asyncio.run(_kernel(workdir).run(skill, input))


# ---------------------------------------------------------------------------
# common.eval.progress_track
# ---------------------------------------------------------------------------


def test_progress_track_lifecycle(tmp_path):
    path = str(tmp_path / "progress.json")
    r = run("common.eval.progress_track", {
        "action": "init", "path": path,
        "tasks": [{"id": "t1", "text": "调研"}, {"id": "t2", "text": "写报告"}],
    }, tmp_path)
    assert r["ok"] is True
    data = json.loads(Path(path).read_text())
    assert all(t["passes"] is False for t in data["tasks"])

    r2 = run("common.eval.progress_track", {"action": "update", "path": path, "task_id": "t1", "passes": True}, tmp_path)
    assert r2["ok"] is True
    data = json.loads(Path(path).read_text())
    assert next(t for t in data["tasks"] if t["id"] == "t1")["passes"] is True

    r3 = run("common.eval.progress_track", {"action": "summary", "path": path}, tmp_path)
    assert "写报告" in r3["summary"] and "调研" in r3["summary"]
    assert r3["remaining"] == ["t2"]


def test_progress_track_complete_when_all_pass(tmp_path):
    path = str(tmp_path / "progress.json")
    run("common.eval.progress_track", {"action": "init", "path": path, "tasks": [{"id": "t1", "text": "x"}]}, tmp_path)
    run("common.eval.progress_track", {"action": "update", "path": path, "task_id": "t1", "passes": True}, tmp_path)
    r = run("common.eval.progress_track", {"action": "summary", "path": path}, tmp_path)
    assert r["remaining"] == []
    assert r["complete"] is True


# ---------------------------------------------------------------------------
# common.dev.run_tests
# ---------------------------------------------------------------------------


def _seed_pyproject(root: Path, failing: bool) -> None:
    (root / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n\n"
        + ("def test_bad():\n    assert 1 == 2\n" if failing else "def test_ok2():\n    assert 2 == 2\n"),
        encoding="utf-8",
    )


def test_run_tests_passing(tmp_path):
    _seed_pyproject(tmp_path, failing=False)
    r = run("common.dev.run_tests", {"path": "."}, tmp_path)
    assert r["ok"] is True
    assert r["passed"] >= 2 and r["failed"] == 0


def test_run_tests_failing_structured(tmp_path):
    _seed_pyproject(tmp_path, failing=True)
    r = run("common.dev.run_tests", {"path": "."}, tmp_path)
    assert r["ok"] is False
    assert r["failed"] >= 1
    assert any("test_bad" in f for f in r["failures"])


# ---------------------------------------------------------------------------
# common.dev.read_file_smart
# ---------------------------------------------------------------------------


def test_read_file_smart_levels(tmp_path):
    (tmp_path / "big.py").write_text(
        "# 模块注释\n" + "\n".join(f"def f{i}():\n    return {i}" for i in range(30)), encoding="utf-8"
    )
    l0 = run("common.dev.read_file_smart", {"path": "big.py", "level": "L0"}, tmp_path)
    assert "30" in l0["summary"] or "f0" in l0["summary"]
    l1 = run("common.dev.read_file_smart", {"path": "big.py", "level": "L1"}, tmp_path)
    assert "def f" in l1["summary"] or any("def f" in s for s in l1.get("structure", []))
    l2 = run("common.dev.read_file_smart", {"path": "big.py", "level": "L2", "offset": 3, "limit": 4}, tmp_path)
    assert "1\t" in l2["text"] or "\t" in l2["text"]


# ---------------------------------------------------------------------------
# common.dev.apply_patch
# ---------------------------------------------------------------------------


def test_apply_patch_applies_and_conflicts(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    patch = (
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " hello\n"
        "-world\n"
        "+agent_os\n"
    )
    r = run("common.dev.apply_patch", {"patch": patch}, tmp_path)
    assert r["applied"] is True
    assert (tmp_path / "a.txt").read_text() == "hello\nagent_os\n"

    conflict = run("common.dev.apply_patch", {"patch": patch}, tmp_path)
    assert conflict["applied"] is False


# ---------------------------------------------------------------------------
# common.dev.summarize_tree
# ---------------------------------------------------------------------------


def test_summarize_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("y", encoding="utf-8")
    (tmp_path / "README.md").write_text("# p", encoding="utf-8")
    r = run("common.dev.summarize_tree", {"path": "."}, tmp_path)
    assert "src" in r["summary"]
    assert r["file_count"] == 3
    assert "README.md" in r["summary"]

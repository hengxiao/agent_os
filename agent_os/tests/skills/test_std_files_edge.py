"""std/files 边界补充测试(非锚点;锚点见 test_std_files.py)。

锚点已覆盖五技能的 happy path 与 apply_patch 冲突拒绝,本文件补薄弱处:

- progress_track:init 覆盖拒绝/重复 id/空 tasks、update 未知 id/非布尔 passes、
  summary 文件不存在(NOT_FOUND)/坏 JSON、路径越界拒绝;
- run_tests:退出码 5(no tests collected)绝不当绿灯、收集错误计入 errors、
  路径不存在 NOT_FOUND;
- read_file_smart:非法 level、NOT_FOUND、L2 分页边界;
- apply_patch:新建/删除/多文件原子性/行号偏移唯一补偿/多义拒绝/畸形与
  计数不符拒绝/越界拒绝;
- summarize_tree:深度上限、.gitignore 与 .git 跳过、关键文件、非目录拒绝;
- 确定性:同输入同输出(read_file_smart / summarize_tree)。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_os.api.v1 import Permission, RunConfig, ToolPolicy
from agent_os.kernel.errors import SkillLoadError, ToolDispatchError
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

#: handler 内 ValueError/FileNotFoundError 经 Logic Kernel 折为 ToolDispatchError;
#: inputs schema 拒绝在 Kernel.run 入口抛 SkillLoadError(§3.2/§9.4)
HANDLER_ERRORS = (ToolDispatchError, SkillLoadError)

STD_DIR = Path(__file__).resolve().parents[2] / "std"


def _kernel(workdir: Path):
    config = RunConfig(
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=str(workdir),
    )
    return (
        KernelBuilder(config)
        .providers(MockProvider())
        .tools(LocalPythonToolRegistry.with_builtins())
        .skills(LocalFileSkillRegistry(str(STD_DIR / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


def run(skill: str, input: dict, workdir: Path):
    return asyncio.run(_kernel(workdir).run(skill, input))


# ---------------------------------------------------------------------------
# progress_track
# ---------------------------------------------------------------------------


def test_init_refuses_overwrite_and_bad_tasks(tmp_path):
    path = str(tmp_path / "p.json")
    run("progress_track", {"action": "init", "path": path, "tasks": [{"id": "t1", "text": "x"}]}, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="已存在"):
        run("progress_track", {"action": "init", "path": path, "tasks": [{"id": "t2", "text": "y"}]}, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="重复"):
        run("progress_track", {
            "action": "init", "path": str(tmp_path / "q.json"),
            "tasks": [{"id": "t", "text": "a"}, {"id": "t", "text": "b"}],
        }, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="非空"):
        run("progress_track", {"action": "init", "path": str(tmp_path / "r.json"), "tasks": []}, tmp_path)


def test_update_unknown_task_and_non_bool_passes(tmp_path):
    path = str(tmp_path / "p.json")
    run("progress_track", {"action": "init", "path": path, "tasks": [{"id": "t1", "text": "x"}]}, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="任务不存在"):
        run("progress_track", {"action": "update", "path": path, "task_id": "nope", "passes": True}, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="boolean"):  # inputs schema 层拦截
        run("progress_track", {"action": "update", "path": path, "task_id": "t1", "passes": "yes"}, tmp_path)


def test_summary_missing_file_bad_json_and_order(tmp_path):
    with pytest.raises(HANDLER_ERRORS, match="NOT_FOUND"):
        run("progress_track", {"action": "summary", "path": "missing.json"}, tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(HANDLER_ERRORS, match="合法 JSON"):
        run("progress_track", {"action": "summary", "path": "bad.json"}, tmp_path)
    path = str(tmp_path / "p.json")
    run("progress_track", {
        "action": "init", "path": path,
        "tasks": [{"id": "t1", "text": "a"}, {"id": "t2", "text": "b"}, {"id": "t3", "text": "c"}],
    }, tmp_path)
    run("progress_track", {"action": "update", "path": path, "task_id": "t2", "passes": True}, tmp_path)
    r = run("progress_track", {"action": "summary", "path": path}, tmp_path)
    assert r["remaining"] == ["t1", "t3"]  # 保序
    assert r["complete"] is False


def test_progress_path_escape_rejected(tmp_path):
    with pytest.raises(HANDLER_ERRORS, match="越界"):
        run("progress_track", {
            "action": "init", "path": "../escape.json", "tasks": [{"id": "t", "text": "x"}],
        }, tmp_path)


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------


def test_run_tests_no_tests_collected_is_not_green(tmp_path):
    r = run("run_tests", {"path": "."}, tmp_path)
    assert r["exit_code"] == 5
    assert r["ok"] is False
    assert r["errors"] == 0  # 退出码 5 不算错误计数,但绝不当静默绿灯
    assert "no tests" in r["note"]


def test_run_tests_collection_error(tmp_path):
    (tmp_path / "test_broken.py").write_text("def test_x(:\n", encoding="utf-8")
    r = run("run_tests", {"path": "."}, tmp_path)
    assert r["ok"] is False
    assert r["errors"] >= 1
    assert any("test_broken" in f for f in r["failures"])


def test_run_tests_missing_path(tmp_path):
    with pytest.raises(HANDLER_ERRORS, match="NOT_FOUND"):
        run("run_tests", {"path": "no/such/dir"}, tmp_path)


# ---------------------------------------------------------------------------
# read_file_smart
# ---------------------------------------------------------------------------


def test_read_file_smart_bad_level_and_missing(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    with pytest.raises(HANDLER_ERRORS, match="L0"):  # enum 在 inputs schema 层拦截
        run("read_file_smart", {"path": "a.py", "level": "L3"}, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="NOT_FOUND"):
        run("read_file_smart", {"path": "ghost.py", "level": "L0"}, tmp_path)


def test_read_file_smart_l2_bounds(tmp_path):
    (tmp_path / "a.txt").write_text("x\ny\n", encoding="utf-8")
    beyond = run("read_file_smart", {"path": "a.txt", "level": "L2", "offset": 10}, tmp_path)
    assert beyond["text"] == "" and beyond["total_lines"] == 2
    with pytest.raises(HANDLER_ERRORS):
        run("read_file_smart", {"path": "a.txt", "level": "L2", "offset": 0}, tmp_path)


def test_read_file_smart_markdown_headings_and_empty_structure(tmp_path):
    (tmp_path / "doc.md").write_text("# 标题\n\n正文\n\n## 小节\n", encoding="utf-8")
    l1 = run("read_file_smart", {"path": "doc.md", "level": "L1"}, tmp_path)
    heads = [s for s in l1["structure"] if s["kind"] == "heading"]
    assert [h["name"] for h in heads] == ["标题", "小节"]
    (tmp_path / "plain.txt").write_text("只有文本\n没有定义\n", encoding="utf-8")
    l0 = run("read_file_smart", {"path": "plain.txt", "level": "L0"}, tmp_path)
    assert "0 个" in l0["summary"]


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


def test_apply_patch_create_and_delete(tmp_path):
    create = run("apply_patch", {"patch": (
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1,2 @@\n"
        "+a\n"
        "+b\n"
    )}, tmp_path)
    assert create["applied"] is True
    assert (tmp_path / "new.txt").read_text() == "a\nb\n"
    # 新建目标已存在 → 冲突拒绝
    again = run("apply_patch", {"patch": (
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+z\n"
    )}, tmp_path)
    assert again["applied"] is False and "已存在" in again["reason"]
    # 删除(hunks 覆盖整个文件)
    delete = run("apply_patch", {"patch": (
        "--- a/new.txt\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-a\n"
        "-b\n"
    )}, tmp_path)
    assert delete["applied"] is True
    assert not (tmp_path / "new.txt").exists()


def test_apply_patch_multi_file_is_atomic(tmp_path):
    (tmp_path / "f1.txt").write_text("a\nb\n", encoding="utf-8")
    (tmp_path / "f2.txt").write_text("x\ny\n", encoding="utf-8")
    r = run("apply_patch", {"patch": (
        "--- a/f1.txt\n"
        "+++ b/f1.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        "--- a/f2.txt\n"
        "+++ b/f2.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " x\n"
        "-NOT-THERE\n"
        "+z\n"
    )}, tmp_path)
    assert r["applied"] is False
    assert r["file"] == "f2.txt"
    assert (tmp_path / "f1.txt").read_text() == "a\nb\n"  # 整组回退,不写半个补丁


def test_apply_patch_line_number_fuzz_and_ambiguity(tmp_path):
    (tmp_path / "a.txt").write_text("l1\nl2\nl3\n", encoding="utf-8")
    # 声明行号偏离,但上下文唯一 → 唯一匹配补偿
    r = run("apply_patch", {"patch": (
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -10,1 +10,1 @@\n"
        "-l2\n"
        "+L2\n"
    )}, tmp_path)
    assert r["applied"] is True
    assert (tmp_path / "a.txt").read_text() == "l1\nL2\nl3\n"
    # 上下文多处匹配且声明行号落空 → 拒绝(不猜)
    (tmp_path / "d.txt").write_text("dup\ndup\n", encoding="utf-8")
    conflict = run("apply_patch", {"patch": (
        "--- a/d.txt\n"
        "+++ b/d.txt\n"
        "@@ -5,1 +5,1 @@\n"
        "-dup\n"
        "+uni\n"
    )}, tmp_path)
    assert conflict["applied"] is False and "处匹配" in conflict["reason"]


def test_apply_patch_multi_hunk_and_malformed(tmp_path):
    (tmp_path / "n.txt").write_text("1\n2\n3\n4\n5\n", encoding="utf-8")
    r = run("apply_patch", {"patch": (
        "--- a/n.txt\n"
        "+++ b/n.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "-1\n"
        "+一\n"
        "@@ -5,1 +5,1 @@\n"
        "-5\n"
        "+五\n"
    )}, tmp_path)
    assert r["applied"] is True
    assert (tmp_path / "n.txt").read_text() == "一\n2\n3\n4\n五\n"
    with pytest.raises(HANDLER_ERRORS, match="unified diff"):
        run("apply_patch", {"patch": "这不是补丁"}, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="超过 @@ 声明"):
        run("apply_patch", {"patch": (
            "--- a/n.txt\n"
            "+++ b/n.txt\n"
            "@@ -1,1 +1,1 @@\n"
            "-1\n"
            "+一\n"
            "+多余行\n"
        )}, tmp_path)


def test_apply_patch_path_escape_rejected(tmp_path):
    with pytest.raises(HANDLER_ERRORS, match="越界"):
        run("apply_patch", {"patch": (
            "--- a/../evil.txt\n"
            "+++ b/../evil.txt\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )}, tmp_path)


# ---------------------------------------------------------------------------
# summarize_tree
# ---------------------------------------------------------------------------


def test_summarize_tree_depth_limit(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "deep.py").write_text("x", encoding="utf-8")
    (tmp_path / "top.py").write_text("x", encoding="utf-8")
    r = run("summarize_tree", {"path": ".", "depth": 1}, tmp_path)
    assert r["file_count"] == 1 and r["dir_count"] == 1
    assert "deep.py" not in r["summary"]
    full = run("summarize_tree", {"path": "."}, tmp_path)
    assert full["file_count"] == 2


def test_summarize_tree_respects_gitignore_and_skips_dotgit(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("secret.log\n", encoding="utf-8")
    (tmp_path / "secret.log").write_text("x", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    r = run("summarize_tree", {"path": "."}, tmp_path)
    assert "secret.log" not in r["summary"]
    assert "config" not in r["summary"]  # .git/config 若被遍历会出现在树里
    assert r["file_count"] == 2  # keep.py + .gitignore 本身


def test_summarize_tree_rejects_file_and_missing(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    with pytest.raises(HANDLER_ERRORS, match="不是目录"):
        run("summarize_tree", {"path": "f.txt"}, tmp_path)
    with pytest.raises(HANDLER_ERRORS, match="NOT_FOUND"):
        run("summarize_tree", {"path": "ghost"}, tmp_path)
    with pytest.raises(HANDLER_ERRORS):
        run("summarize_tree", {"path": ".", "depth": 0}, tmp_path)


def test_summarize_tree_lists_key_files(tmp_path):
    (tmp_path / "README.md").write_text("# p", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    r = run("summarize_tree", {"path": "."}, tmp_path)
    assert "关键文件" in r["summary"] and "README.md" in r["summary"]


# ---------------------------------------------------------------------------
# 确定性:同输入同输出
# ---------------------------------------------------------------------------


def test_determinism_same_input_same_output(tmp_path):
    (tmp_path / "m.py").write_text("class A:\n    def m(self):\n        pass\n", encoding="utf-8")
    a = run("read_file_smart", {"path": "m.py", "level": "L1"}, tmp_path)
    b = run("read_file_smart", {"path": "m.py", "level": "L1"}, tmp_path)
    assert a == b
    s1 = run("summarize_tree", {"path": "."}, tmp_path)
    s2 = run("summarize_tree", {"path": "."}, tmp_path)
    assert s1 == s2
    # progress 文件落盘内容也确定(不读钟)
    p = str(tmp_path / "p.json")
    run("progress_track", {"action": "init", "path": p, "tasks": [{"id": "t", "text": "x"}]}, tmp_path)
    first = json.loads(Path(p).read_text(encoding="utf-8"))
    run("progress_track", {"action": "update", "path": p, "task_id": "t", "passes": False}, tmp_path)
    assert json.loads(Path(p).read_text(encoding="utf-8")) == first

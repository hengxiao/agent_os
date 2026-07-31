"""std/files code handler(STDLIB-CATALOG §W4-1;STDLIB §3.6/§4.5)。

五个面向文件系统与子进程的 code 技能:

- ``progress_track``:JSON 进度文件(init/update/summary),``passes`` 语义 +
  resume 摘要;卡死检测由调用方配 ``fs_list`` mtime + ``now`` 白拿(catalog §W4-1),
  故本技能**不读钟**(确定性,文件内不存时间戳)。
- ``run_tests``:workdir 内起子进程跑 ``sys.executable -m pytest -q --tb=no
  -p no:cacheprovider``(120s 上限),**结构化解析**计数与 FAILED/ERROR 行,
  不回传原始输出。
- ``read_file_smart``:L0 概要 / L1 结构 / L2 带行号分页(行号格式与 fs_read
  工具契约一致:``"{n}\\t{line}"``,offset/limit 从 1 起)。
- ``apply_patch``:最小但正确的 unified diff 应用器(单/多文件、多 hunk、
  上下文精确匹配,失败可整组回退——全部在内存应用成功后才统一落盘)。
- ``summarize_tree``:目录树摘要,遍历复用 §W1 工具层同一棵 walker
  (默认尊重 .gitignore、跳过 .git/node_modules/.venv)。

**TRUSTED 档与空 permissions 的决策**:本包技能要读真实 workdir、起子进程,
只能跑 TRUSTED(ctx 为 ``KernelLogicContext``)。manifest 不声明
``permissions.tools`` 并非疏漏:``KernelBuilder.build`` 的 §6.1 权限闸门会拒绝
"声明了未注册工具"的 manifest,而既有装配(含 std 锚点)用**空**工具表加载同一份
``skills.yaml``——声明 tools 会让那些装配全部拒绝加载。故本包不经
``ctx.call_tool``,直接以 TRUSTED 身份做文件 IO;workdir/read_paths 经
``ctx._kernel.config`` 现取(getattr 防御链,非内核装配回落 cwd),路径安全复用
内核统一解析器 ``resolve_work_path``(§W0-1 三段判定:只读区/workdir 读写/越界
拒绝)——与 fs 工具同一安全边界,不另行发明。

错误形态(catalog §W4-1 + §W0-3):文件不存在抛 ``FileNotFoundError``、参数非法抛
``ValueError``,经 Logic Kernel 归一化为帧失败(RUNTIME_ERROR → ToolDispatchError,
消息含 ``NOT_FOUND:`` 等结构化前缀)——与 transform/eval handler 的错误约定一致;
``apply_patch`` 的**格式良好但上下文冲突**不算错误,返回 ``{applied: false,
reason, file}`` 供调用方自纠。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_os.api.v1 import LogicError, SkillError
from agent_os.tools.local_registry import resolve_work_path
from agent_os.tools.std import _walk_tree

#: run_tests 子进程墙钟上限(秒;catalog §W4-1 钉 120;manifest limits.timeout
#: 须大于它,否则帧 wall_time(默认 60s)先杀 handler)
_PYTEST_TIMEOUT = 120.0

#: read_file_smart L0/L1 摘要里列出的结构项上限(超出记 "… 等 N 项")
_SUMMARY_MAX_ITEMS = 20

#: read_file_smart L2 单页默认行数(与 fs_read 工具契约一致)
_L2_DEFAULT_LIMIT = 2000

#: summarize_tree 树文本行数上限(超出截断并如实标注,计数不受影响)
_TREE_MAX_LINES = 200

#: summarize_tree 的"关键文件"基名(小写匹配;项目指纹的惯常锚点)
_KEY_FILE_NAMES = frozenset({
    "readme", "readme.md", "pyproject.toml", "package.json", "setup.py",
    "setup.cfg", "cargo.toml", "go.mod", "makefile", "skills.yaml",
    "agents.md", "claude.md",
})

#: run_tests 摘要行计数正则(passed/failed/error(s);取全文**最后一次**出现,
#: pytest 的计数摘要行在输出末尾,FAILED/ERROR 行内不含 "数字+计数词")
_COUNT_RES = {
    "passed": re.compile(r"(\d+)\s+passed\b"),
    "failed": re.compile(r"(\d+)\s+failed\b"),
    "errors": re.compile(r"(\d+)\s+errors?\b"),
}

#: run_tests failures 行(pytest short test summary info 的条目行)
_FAILURE_LINE_RE = re.compile(r"^(FAILED|ERROR)\s")

#: read_file_smart 结构识别:Python def/class(任意深度)与 Markdown 标题
_DEF_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)")
_CLASS_RE = re.compile(r"^(\s*)class\s+([A-Za-z_]\w*)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")

#: apply_patch hunk 头(@@ -s[,c] +s[,c] @@,count 缺省为 1;允许尾部节标题)
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ---------------------------------------------------------------------------
# 公共:workdir 现取 + §W0-1 三段路径判定(与 fs 工具同一解析器)
# ---------------------------------------------------------------------------


def _run_config(ctx: Any) -> Any:
    """TRUSTED 档 ctx(KernelLogicContext)→ RunConfig;非内核装配(直调/沙箱)为 None。"""
    kernel = getattr(ctx, "_kernel", None)
    return getattr(kernel, "config", None)


def _workdir(ctx: Any) -> Path:
    config = _run_config(ctx)
    workdir = getattr(config, "workdir", None)
    if workdir:
        return Path(workdir).expanduser().resolve()
    return Path.cwd()


def _read_paths(ctx: Any) -> list[Path]:
    config = _run_config(ctx)
    return [Path(p) for p in (getattr(config, "read_paths", None) or [])]


def _resolve(ctx: Any, path: str, *, write: bool = False) -> Path:
    """按 §W0-1 三段判定解析 ``path``;越界/只读区写 → :class:`SkillError`。

    ``resolve_work_path`` **已经**构造好带运行期事实(当前 workdir / 只读区)
    的 ``hint`` 与 ``kind``;此前这里只取 message 抛 ValueError,把它们整个丢掉,
    模型只收到"路径越界"却不知道该往哪写(§W0-3 / STDLIB §8 第 13 条)。
    """
    target = resolve_work_path(_workdir(ctx), _read_paths(ctx), path, write=write)
    if not isinstance(target, Path):
        err = target.error
        raise SkillError(
            err.message,
            kind=LogicError.REJECTED,
            hint=err.hint,
            retryable=err.retryable,
        )
    return target


def _display(ctx: Any, target: Path) -> str:
    """回显路径:相对 workdir 的 posix 串(越不出 workdir 时才给绝对路径)。"""
    try:
        return target.relative_to(_workdir(ctx)).as_posix()
    except ValueError:
        return str(target)


def _read_text(target: Path, path: str) -> str:
    if not target.is_file():
        raise FileNotFoundError(f"NOT_FOUND: 文件不存在: {path}(先确认路径或创建)")
    return target.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# progress_track —— JSON 进度文件(init/update/summary;确定性,不读钟)
# ---------------------------------------------------------------------------

_PROGRESS_SCHEMA = "agent_os/progress@1"


def _load_progress(target: Path, path: str) -> dict[str, Any]:
    raw = _read_text(target, path)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SkillError(
            f"进度文件不是合法 JSON: {path}({e})",
            kind=LogicError.REJECTED,
            hint="核对该文件内容,或删除后重新 init",
        ) from None
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise SkillError(
            f"进度文件结构非法: {path}",
            kind=LogicError.REJECTED,
            hint='应为含 tasks 数组的对象,如 {"tasks": [{"id": "t1", "text": "..."}]}',
        )
    return data


def _dump_progress(target: Path, data: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def progress_track(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{action: init|update|summary}``:init 建文件(tasks 全 passes:false)、

    update 置单条 passes、summary 给 resume 摘要(人读文本 + remaining + complete)。
    """
    action = input.get("action")
    path = input.get("path")
    if action not in ("init", "update", "summary"):
        raise ValueError(f"action 非法: {action!r}(取 init/update/summary)")
    if not isinstance(path, str) or not path:
        raise ValueError("path 必填(进度文件路径,相对 workdir 或 workdir 内绝对路径)")
    target = _resolve(ctx, path, write=True)

    if action == "init":
        tasks = input.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("init 需要非空 tasks 数组,元素 {id, text}")
        if target.exists():
            raise ValueError(
                f"进度文件已存在: {path}(init 只创建;推进用 update,查看用 summary)"
            )
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for index, item in enumerate(tasks):
            if not isinstance(item, dict):
                raise SkillError(
                    f"tasks[{index}] 不是对象",
                    kind=LogicError.REJECTED,
                    hint='每条形如 {"id": "t1", "text": "做什么"}',
                )
            task_id, text = item.get("id"), item.get("text")
            if not isinstance(task_id, str) or not task_id:
                raise ValueError(f"tasks[{index}] 缺少非空字符串 id")
            if task_id in seen:
                raise ValueError(f"tasks[{index}] id 重复: {task_id!r}(id 必须唯一)")
            if not isinstance(text, str) or not text:
                raise ValueError(f"tasks[{index}](id={task_id})缺少非空字符串 text")
            seen.add(task_id)
            items.append({"id": task_id, "text": text, "passes": False})
        _dump_progress(target, {"schema": _PROGRESS_SCHEMA, "tasks": items})
        return {"ok": True, "path": _display(ctx, target), "count": len(items)}

    data = _load_progress(target, path)
    tasks: list[dict[str, Any]] = data["tasks"]

    if action == "update":
        task_id = input.get("task_id")
        passes = input.get("passes")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("update 需要非空字符串 task_id")
        if not isinstance(passes, bool):
            raise ValueError(f"update 需要布尔 passes(收到 {passes!r})")
        task = next((t for t in tasks if isinstance(t, dict) and t.get("id") == task_id), None)
        if task is None:
            known = [t.get("id") for t in tasks if isinstance(t, dict)]
            raise ValueError(f"任务不存在: {task_id!r}(现有 id: {known})")
        task["passes"] = passes
        _dump_progress(target, data)
        return {"ok": True, "task": dict(task)}

    # summary:人读文本(全部任务与状态)+ remaining + complete(resume 摘要)
    done = [t for t in tasks if isinstance(t, dict) and t.get("passes") is True]
    remaining = [t.get("id") for t in tasks if isinstance(t, dict) and t.get("passes") is not True]
    lines = [f"进度 {len(done)}/{len(tasks)}{'全部完成' if not remaining else '，剩余 ' + str(len(remaining)) + ' 项'}:"]
    for t in tasks:
        if not isinstance(t, dict):
            continue
        mark = "x" if t.get("passes") is True else " "
        lines.append(f"- [{mark}] {t.get('id')}: {t.get('text')}")
    return {
        "ok": True,
        "summary": "\n".join(lines),
        "remaining": remaining,
        "complete": not remaining,
    }


# ---------------------------------------------------------------------------
# run_tests —— pytest 子进程 + 结构化解析(不回传原文)
# ---------------------------------------------------------------------------


def _last_count(stdout: str, kind: str) -> int:
    matches = _COUNT_RES[kind].findall(stdout)
    return int(matches[-1]) if matches else 0


async def run_tests(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{path}`` → 在 workdir 内跑 pytest → ``{passed, failed, errors, failures, ok}``。

    ok 口径:``failed==0 and errors==0 and 退出码==0``;退出码 5(no tests
    collected)视为 errors=0 但 ok=False 并注明;120s 超时杀进程记 errors=1。
    """
    path = input.get("path", ".")
    if not isinstance(path, str) or not path:
        raise ValueError("path 须为非空字符串(测试文件/目录,相对 workdir)")
    target = _resolve(ctx, path)
    if not target.exists():
        raise FileNotFoundError(f"NOT_FOUND: 路径不存在: {path}(用 fs_list 确认结构)")
    cwd = target if target.is_dir() else target.parent
    arg = "." if target.is_dir() else target.name
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", arg,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=_PYTEST_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "passed": 0, "failed": 0, "errors": 1, "failures": [], "ok": False,
            "exit_code": None, "note": f"pytest 超过 {_PYTEST_TIMEOUT:.0f}s,已终止",
        }
    stdout = out_b.decode("utf-8", errors="replace")
    stderr = err_b.decode("utf-8", errors="replace")
    exit_code = proc.returncode if proc.returncode is not None else 1
    passed = _last_count(stdout, "passed")
    failed = _last_count(stdout, "failed")
    errors = _last_count(stdout, "errors")
    failures = [line.strip() for line in stdout.splitlines() if _FAILURE_LINE_RE.match(line)][:50]
    result: dict[str, Any] = {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "failures": failures,
        "ok": failed == 0 and errors == 0 and exit_code == 0,
        "exit_code": exit_code,
    }
    if exit_code == 5:
        # pytest 退出码 5 = 未收集到用例:不算错误计数,但绝不能当"通过"(静默绿灯)
        result["ok"] = False
        result["note"] = "no tests collected(pytest 退出码 5)"
    elif exit_code not in (0, 1) and failed == 0 and errors == 0:
        # 用法错误/内部错误等:无计数可解析,如实记 1 个 error + stderr 尾部
        result["errors"] = 1
        result["ok"] = False
        result["note"] = f"pytest 异常退出(码 {exit_code}): {stderr.strip()[-200:]}"
    return result


# ---------------------------------------------------------------------------
# read_file_smart —— L0 概要 / L1 结构 / L2 带行号分页
# ---------------------------------------------------------------------------


def _structure(lines: list[str], *, top_only: bool) -> list[dict[str, Any]]:
    """扫描 def/class(任意深度)/Markdown 标题;``top_only`` 只收零缩进定义。"""
    out: list[dict[str, Any]] = []
    for n, line in enumerate(lines, start=1):
        m = _DEF_RE.match(line)
        if m and (not top_only or not m.group(1)):
            out.append({"kind": "def", "name": m.group(2), "line": n})
            continue
        m = _CLASS_RE.match(line)
        if m and (not top_only or not m.group(1)):
            out.append({"kind": "class", "name": m.group(2), "line": n})
            continue
        if not top_only:
            m = _HEADING_RE.match(line)
            if m:
                out.append({"kind": "heading", "name": m.group(1), "line": n})
    return out


def _items_brief(items: list[dict[str, Any]], cap: int = _SUMMARY_MAX_ITEMS) -> str:
    shown = ", ".join(f"{s['kind']} {s['name']}@{s['line']}" for s in items[:cap])
    if len(items) > cap:
        shown += f" … 等 {len(items)} 项"
    return shown


async def read_file_smart(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{path, level: L0|L1|L2, offset?, limit?}`` 三级阅读。

    L0 → ``{summary}``(行数/字节/顶级 def-class 前 N 个);L1 → ``{summary,
    structure}``(def/class/标题行);L2 → ``{text}``(``"{n}\\t{line}"``,offset/limit)。
    """
    path = input.get("path")
    level = input.get("level")
    if not isinstance(path, str) or not path:
        raise ValueError("path 必填(相对 workdir 的文件路径)")
    if level not in ("L0", "L1", "L2"):
        raise ValueError(f"level 非法: {level!r}(取 L0/L1/L2)")
    target = _resolve(ctx, path)
    text = _read_text(target, path)
    lines = text.splitlines()

    if level == "L2":
        offset = input.get("offset", 1)
        limit = input.get("limit", _L2_DEFAULT_LIMIT)
        if not isinstance(offset, int) or not isinstance(limit, int) or offset < 1 or limit < 1:
            raise ValueError(
                f"offset/limit 必须是 >= 1 的整数(收到 offset={offset!r}, limit={limit!r})"
            )
        window = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{n}\t{line}" for n, line in enumerate(window, start=offset))
        return {"text": numbered, "total_lines": len(lines)}

    if level == "L0":
        tops = _structure(lines, top_only=True)
        size = len(text.encode("utf-8"))
        summary = f"{path}: {len(lines)} 行, {size} 字节, 顶级 def/class {len(tops)} 个"
        summary += f": {_items_brief(tops)}" if tops else "(无顶级 def/class)"
        return {"summary": summary}

    structure = _structure(lines, top_only=False)
    summary = f"{path}: {len(lines)} 行, 结构项 {len(structure)} 个"
    summary += f": {_items_brief(structure, cap=10)}" if structure else "(无 def/class/标题)"
    return {"summary": summary, "structure": structure}


# ---------------------------------------------------------------------------
# apply_patch —— 最小但正确的 unified diff 应用器
#
# 解析边界(docstring 即契约):git 风格 ``--- a/x`` / ``+++ b/x``(无前缀路径与
# 传统 ``file\t时间戳`` 形态也接受);``/dev/null`` 表新建/删除;hunk 行数与
# @@ 声明不符、文件头缺 +++ 行、hunk 截断 → ValueError(参数非法);
# body 内完全空的行宽松按 context 空行对待(LLM 产出常丢尾空格);
# 不支持引号文件名与 CRLF(行内 \r 视为内容,context 不匹配即冲突)。
# 应用口径:全部文件在内存应用成功后才统一落盘(多文件整组回退);
# 单 hunk 先按声明行号(含前序 hunk 位移)精确匹配,失败则全文找**唯一**
# 匹配(0 处或多处 → 冲突拒绝);``\ No newline at end of file`` 严格校验。
# ---------------------------------------------------------------------------


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)  # (op, text),op ∈ " -+"
    old_eof_no_nl: bool = False  # 旧侧末行带 "\ No newline at end of file"
    new_eof_no_nl: bool = False  # 新侧末行同上


@dataclass
class _FilePatch:
    old_path: str  # "/dev/null" 表新建
    new_path: str  # "/dev/null" 表删除
    hunks: list[_Hunk] = field(default_factory=list)


def _strip_prefix(spec: str) -> str:
    """``a/x``/``b/x`` 去前缀;传统形态去 ``\\t时间戳`` 尾;``/dev/null`` 原样。"""
    name = spec.split("\t", 1)[0].strip()
    if name != "/dev/null" and len(name) > 2 and name[1] == "/" and name[0] in ("a", "b"):
        name = name[2:]
    return name


def _parse_patch(patch: str) -> list[_FilePatch]:
    lines = patch.split("\n")
    files: list[_FilePatch] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.startswith("--- "):
            i += 1
            continue  # diff --git / index / 前导垃圾:跳过
        old_path = _strip_prefix(line[4:])
        i += 1
        if i >= n or not lines[i].startswith("+++ "):
            raise ValueError(f"patch 畸形: '--- {old_path}' 后缺 '+++ ' 行")
        new_path = _strip_prefix(lines[i][4:])
        i += 1
        fp = _FilePatch(old_path=old_path, new_path=new_path)
        while i < n and lines[i].startswith("@@ "):
            m = _HUNK_RE.match(lines[i])
            if not m:
                raise ValueError(f"patch 畸形: 无法解析 hunk 头 {lines[i]!r}")
            hunk = _Hunk(
                old_start=int(m.group(1)),
                old_count=int(m.group(2)) if m.group(2) is not None else 1,
                new_start=int(m.group(3)),
                new_count=int(m.group(4)) if m.group(4) is not None else 1,
            )
            i += 1
            old_seen = new_seen = 0
            while old_seen < hunk.old_count or new_seen < hunk.new_count:
                if i >= n:
                    raise ValueError("patch 畸形: hunk 截断(行数不足 @@ 声明)")
                body = lines[i]
                if body.startswith("\\"):
                    # "\ No newline at end of file":标记前一行的对应侧
                    if hunk.lines and hunk.lines[-1][0] == "-":
                        hunk.old_eof_no_nl = True
                    elif hunk.lines and hunk.lines[-1][0] == "+":
                        hunk.new_eof_no_nl = True
                    else:
                        hunk.old_eof_no_nl = hunk.new_eof_no_nl = True
                    i += 1
                    continue
                op, text = (body[0], body[1:]) if body else (" ", "")
                # 宽松:完全空的行按 context 空行对待(尾空格丢失是常见产出缺陷)
                if op not in (" ", "-", "+"):
                    raise ValueError(f"patch 畸形: hunk 内非法行 {body[:40]!r}")
                hunk.lines.append((op, text))
                if op in (" ", "-"):
                    old_seen += 1
                if op in (" ", "+"):
                    new_seen += 1
                i += 1
            # 声明计数已满足,紧跟的 body 形态行是计数不符的溢出——静默忽略会把
            # 错补丁写进文件,必须拒绝。"@@ "/--- "(下一 hunk/文件)与空行是合法后继
            if i < n and lines[i] and (
                lines[i][0] in (" ", "+")
                or (lines[i][0] == "-" and not lines[i].startswith("--- "))
            ):
                raise ValueError(f"patch 畸形: hunk 行数超过 @@ 声明(多余行 {lines[i][:40]!r})")
            fp.hunks.append(hunk)
        if not fp.hunks:
            raise ValueError(f"patch 畸形: 文件 {new_path!r} 没有任何 hunk")
        files.append(fp)
    if not files:
        raise ValueError("patch 里找不到 unified diff(需要 '--- '/'+++ '/'@@ ' 结构)")
    return files


def _split_content(text: str) -> tuple[list[str], bool]:
    """文本 → (行列表, 末尾有换行);空文本按 ([], True)(写出即空文件)。"""
    if text == "":
        return [], True
    if text.endswith("\n"):
        return text[:-1].split("\n"), True
    return text.split("\n"), False


def _find_unique(haystack: list[str], needle: list[str]) -> list[int]:
    width = len(needle)
    return [i for i in range(len(haystack) - width + 1) if haystack[i : i + width] == needle]


def _apply_hunks(content: str, fp: _FilePatch) -> str | dict[str, Any]:
    """单文件内存应用;成功 → 新文本,冲突 → ``{applied: false, reason, file}``。"""
    working, final_nl = _split_content(content)
    new_final_nl = final_nl
    delta = 0  # 前序 hunk 造成的行号位移
    for hunk in fp.hunks:
        old_seq = [t for op, t in hunk.lines if op in (" ", "-")]
        new_seq = [t for op, t in hunk.lines if op in (" ", "+")]
        pos = (hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1) + delta
        if pos < 0 or pos > len(working) or working[pos : pos + len(old_seq)] != old_seq:
            spots = _find_unique(working, old_seq)
            if len(spots) == 1:
                pos = spots[0]
            elif not spots:
                return {
                    "applied": False,
                    "file": fp.new_path if fp.new_path != "/dev/null" else fp.old_path,
                    "reason": f"上下文不匹配(hunk @@ -{hunk.old_start},{hunk.old_count} @@ 的内容在文件中找不到)",
                }
            else:
                return {
                    "applied": False,
                    "file": fp.new_path if fp.new_path != "/dev/null" else fp.old_path,
                    "reason": f"上下文在文件中有 {len(spots)} 处匹配,无法唯一定位(请带更多上下文)",
                }
        # 旧侧末行须确为文件末行且无尾换行
        if hunk.old_eof_no_nl and (pos + len(old_seq) != len(working) or final_nl):
            return {
                "applied": False,
                "file": fp.old_path,
                "reason": "'\\ No newline' 标记与文件实际末尾状态不符",
            }
        working[pos : pos + len(old_seq)] = new_seq
        if hunk.new_eof_no_nl and pos + len(new_seq) != len(working):
            return {
                "applied": False,
                "file": fp.new_path,
                "reason": "'\\ No newline' 标记不在结果文件末尾",
                }
            new_final_nl = False
        delta += len(new_seq) - len(old_seq)
    if not working:
        return ""
    return "\n".join(working) + ("\n" if new_final_nl else "")


async def apply_patch(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{patch}`` → 应用 unified diff 到 workdir → ``{applied, files}``;冲突拒绝。

    全部文件内存应用成功后才统一落盘(任一冲突则整组不写);越界路径抛
    ValueError(§W0-1 解析器拒绝)。
    """
    patch = input.get("patch")
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError("patch 必填(unified diff 文本)")
    file_patches = _parse_patch(patch)

    planned: list[tuple[Path, str | None, str]] = []  # (目标, 新内容|None=删除, 回显路径)
    for fp in file_patches:
        is_new = fp.old_path == "/dev/null"
        is_delete = fp.new_path == "/dev/null"
        rel = fp.old_path if is_delete else fp.new_path
        target = _resolve(ctx, rel, write=True)
        display = _display(ctx, target)
        if is_new:
            if target.exists():
                return {"applied": False, "file": display, "reason": "新建失败: 目标文件已存在"}
            content = ""
            newline = "\n"
        else:
            if not target.is_file():
                return {"applied": False, "file": display, "reason": f"文件不存在: {display}"}
            raw = target.read_bytes()
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                # 静默用 U+FFFD 覆盖写回等于毁文件(§8 第 7 条:不得静默改写)
                return {
                    "applied": False,
                    "file": display,
                    "reason": f"文件不是合法 UTF-8(偏移 {e.start}),拒绝改写以免破坏内容",
                }
            newline = _detect_newline(raw)
            # 归一化为 LF 再匹配(hunk 按 \n 切行);写回时按 newline 还原,
            # 使 CRLF 文件既能打补丁、又不被静默转成 LF
            content = content.replace("\r\n", "\n").replace("\r", "\n")
        result = _apply_hunks(content, fp)
        if isinstance(result, dict):
            result["file"] = display
            return result
        if is_delete:
            # 删除只在补丁确实覆盖整个文件时成立(与 GNU patch 同一口径:
            # 内容对不上的删除是冲突,不是"照删")
            if result != "":
                return {
                    "applied": False,
                    "file": display,
                    "reason": "删除补丁未覆盖整个文件(应用后仍有剩余内容)",
                }
            planned.append((target, None, display, newline))
            continue
        planned.append((target, result, display, newline))

    for target, new_content, _display_path, newline in planned:
        if new_content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="" 关掉换行翻译,原文件是 CRLF 就仍写回 CRLF(§8 第 7 条)
            with target.open("w", encoding="utf-8", newline="") as fh:
                fh.write(new_content if newline == "\n" else new_content.replace("\n", newline))
    return {"applied": True, "files": [d for _t, _c, d, _n in planned]}


# ---------------------------------------------------------------------------
# summarize_tree —— 目录级结构摘要(复用 §W1 walker:gitignore + 跳过依赖目录)
# ---------------------------------------------------------------------------


def _detect_newline(raw: bytes) -> str:
    """按首个换行判定原文件行尾(CRLF / CR / LF),用于写回时保真。"""
    i = raw.find(b"\n")
    if i > 0 and raw[i - 1:i] == b"\r":
        return "\r\n"
    if i == -1 and b"\r" in raw:
        return "\r"
    return "\n"


async def summarize_tree(input: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """``{path, depth?}`` → ``{summary, file_count, dir_count}``(深度默认 3)。"""
    path = input.get("path", ".")
    depth = input.get("depth", 3)
    if not isinstance(path, str) or not path:
        raise ValueError("path 须为非空字符串(目录,相对 workdir)")
    if not isinstance(depth, int) or depth < 1:
        raise ValueError(f"depth 必须是 >= 1 的整数(收到 {depth!r})")
    target = _resolve(ctx, path)
    if not target.is_dir():
        if not target.exists():
            raise FileNotFoundError(f"NOT_FOUND: 目录不存在: {path}")
        raise ValueError(f"不是目录: {path}(读文件用 read_file_smart)")

    entries = [
        (rel, is_dir)
        for _abs, rel, is_dir in _walk_tree(target, include_ignored=False)
        if rel.count("/") + 1 <= depth
    ]
    file_count = sum(1 for _rel, is_dir in entries if not is_dir)
    dir_count = sum(1 for _rel, is_dir in entries if is_dir)

    tree_lines: list[str] = []
    for rel, is_dir in entries:
        segs = rel.count("/") + 1
        name = rel.rsplit("/", 1)[-1]
        tree_lines.append(f"{'  ' * (segs - 1)}{name}{'/' if is_dir else ''}")
    truncated = len(tree_lines) > _TREE_MAX_LINES
    if truncated:
        tree_lines = tree_lines[:_TREE_MAX_LINES]

    key_files = sorted(
        rel for rel, is_dir in entries
        if not is_dir and rel.rsplit("/", 1)[-1].lower() in _KEY_FILE_NAMES
    )
    header = (
        f"{path}: {file_count} 个文件, {dir_count} 个目录"
        f"(深度 ≤{depth},尊重 .gitignore,跳过 .git/node_modules/.venv)"
    )
    body = [header, *tree_lines]
    if truncated:
        body.append(f"… 树过大,仅显示前 {_TREE_MAX_LINES} 行(计数为全量)")
    if key_files:
        body.append(f"关键文件: {', '.join(key_files)}")
    return {
        "summary": "\n".join(body),
        "file_count": file_count,
        "dir_count": dir_count,
    }

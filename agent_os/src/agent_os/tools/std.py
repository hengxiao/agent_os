"""STDLIB 第 1 波核心工具(STDLIB-CATALOG §W1;STDLIB §3.3 补齐清单)。

- §W1-1 ``system.file.list`` / §W1-2 ``system.file.search``:目录列举与内容检索,纯 Python 实现
  (不 shell out 到 ripgrep,std 边界是"无重型二进制依赖"),共用同一棵目录遍历;
  默认尊重 ``.gitignore`` 并跳过 ``.git/`` ``node_modules/`` ``.venv/``
  (``include_ignored`` / 显式单文件路径除外);分页 cursor = base64 编码的稳定
  偏移,截断时 ``total`` 如实 + ``next_cursor``(静默截断是危险的,§W1-1 决策②)。
- §W1-3 ``system.time.now``:服务端时钟,声明 ``replayable``;回放机制在
  ``local_registry.dispatch``(replay 模式按调用序弹出 ``replay_records``
  记录值返回,不执行——否则任何含 ``system.time.now`` 的 run 都无法确定性复现)。
- §W1-4 ``system.task.todo_write``/``system.task.todo_update``:run 级任务清单,存 registry
  ``run_states``(同 run_id 跨帧共享);状态栏注入见 ``context.manager``,
  checkpoint 持久见 ``kernel.checkpoint``。
- §W1-5 ``system.skill.search``:tools/skills registry 子串检索,结果带权限信息
  (防选中无权工具,§W1-5 坑);skills 数据源由 KernelBuilder 经 ``bind_skills``
  注入(bind 模式,同 system.python.exec 的 ``bind`` 先例)。
- Phase 3 补齐(library-design-plan §4.2):``system.file.stat``(读/写决策前探查,
  不存在返回 ``exists=False`` 而非报错)、``system.file.delete``(高危,``confirm=True``,
  仅文件与空目录)、``system.file.mkdir``(parents/exist_ok 语义,幂等)。

``.gitignore`` 支持常见模式子集:``*.ext``、``dir/``、含 ``/`` 的锚定相对路径、
``!`` 取反(后命中优先);不实现完整 gitwildmatch(``**`` 仅按 fnmatch 近似、
被排除目录下的文件不能再包含)——覆盖真实仓库的依赖/构建目录已够用。
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_os.api.v1 import (
    Permission,
    Tool,
    ToolContext,
    ToolError,
    ToolErrorKind,
    ToolResult,
)
from agent_os.tools.local_registry import _FunctionTool, derive_spec, resolve_work_path
from agent_os.tools.builtins import _check_if_match

if TYPE_CHECKING:
    from agent_os.tools.local_registry import LocalPythonToolRegistry

#: 默认跳过的目录(§W1-1 决策①:不做这件事,真实仓库第一次列举就淹没在依赖目录里)
_ALWAYS_SKIP_DIRS = frozenset({".git", "node_modules", ".venv"})

#: system.file.list 单次返回条目数默认上限(glob 递归在大仓库上很慢,§W1-1 坑)
_LIST_DEFAULT_MAX = 200

#: system.file.search 单文件正则扫描超时(秒;灾难性回溯防护,超时跳过该文件并记 warning)
_SEARCH_REGEX_TIMEOUT = 2.0

#: system.file.search 单次返回命中数默认上限
_SEARCH_DEFAULT_MAX = 200

#: system.file.search 结果 spill 阈值与 spill 后上下文保留条数(§W1-2 决策③)
_SPILL_MAX_HITS = 1000
_SPILL_MAX_BYTES = 100_000
_SPILL_KEEP = 50

#: todo 状态取值(§W1-4)
_TODO_STATUSES = frozenset({"pending", "doing", "done", "cancelled"})


def _invalid(message: str, hint: str) -> ToolResult:
    return ToolResult(
        ok=False,
        error=ToolError(kind=ToolErrorKind.INVALID_ARGS, message=message, retryable=False, hint=hint),
    )


# ---------------------------------------------------------------------------
# 分页 cursor(base64 编码的稳定偏移;§W1-1 决策② total 与 next_cursor 是契约)
# ---------------------------------------------------------------------------


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode()


def _decode_cursor(cursor: str) -> int | ToolResult:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, _, num = raw.partition(":")
        if prefix != "offset":
            raise ValueError(raw)
        return int(num)
    except ValueError:
        return _invalid(
            f"无法识别的 cursor: {cursor!r}",
            "cursor 由上一次调用的 next_cursor 原样回传,不要自行构造或修改",
        )


# ---------------------------------------------------------------------------
# .gitignore 常见模式子集(见模块 docstring 的支持范围说明)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IgnoreRule:
    pattern: str  # 去掉前导 ``!`` 与尾 ``/`` 后的模式体
    negated: bool
    dir_only: bool
    anchored: bool  # 模式含 ``/`` → 锚定到该 .gitignore 所在目录,否则按基名任意深度匹配


def _parse_gitignore(text: str) -> list[_IgnoreRule]:
    rules: list[_IgnoreRule] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        dir_only = line.endswith("/")
        if dir_only:
            line = line.rstrip("/")
        line = line.lstrip("/")  # 前导 / 仅表示锚定(anchored 标志已覆盖)
        if not line:
            continue
        rules.append(
            _IgnoreRule(pattern=line, negated=negated, dir_only=dir_only, anchored="/" in line)
        )
    return rules


def _is_ignored(
    stack: list[tuple[str, list[_IgnoreRule]]], rel: str, name: str, is_dir: bool
) -> bool:
    """gitignore 语义判定:规则栈自外向内,**最后一条命中**生效(``!`` 取反翻转)。"""
    ignored = False
    for base, rules in stack:
        target = rel if not base else (rel[len(base) + 1 :] if rel.startswith(base + "/") else None)
        if target is None:
            continue  # 该 .gitignore 不覆盖此路径
        for rule in rules:
            if rule.dir_only and not is_dir:
                continue
            matched = (
                fnmatch.fnmatch(target, rule.pattern)
                if rule.anchored
                else fnmatch.fnmatch(name, rule.pattern)
            )
            if matched:
                ignored = not rule.negated
    return ignored


def _walk_tree(root: Path, include_ignored: bool) -> list[tuple[Path, str, bool]]:
    """递归遍历 ``root``,返回 ``(绝对路径, 相对 root 的 posix 路径, is_dir)``。

    确定性序(逐层按名字排序);不跟随符号链接(防环);默认剪除忽略目录
    (.gitignore + ``_ALWAYS_SKIP_DIRS``)——被剪目录本身也不出现在结果里。
    """
    out: list[tuple[Path, str, bool]] = []

    def recurse(dir_path: Path, rel_prefix: str, stack: list[tuple[str, list[_IgnoreRule]]]) -> None:
        if not include_ignored:
            gitignore = dir_path / ".gitignore"
            if gitignore.is_file():
                try:
                    rules = _parse_gitignore(gitignore.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    rules = []
                if rules:
                    stack = [*stack, (rel_prefix, rules)]
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except OSError:
            return  # 权限等:该子树跳过,不因单点失败拖垮整次列举
        for entry in entries:
            is_dir = entry.is_dir(follow_symlinks=False)
            rel = f"{rel_prefix}/{entry.name}" if rel_prefix else entry.name
            if not include_ignored:
                if is_dir and entry.name in _ALWAYS_SKIP_DIRS:
                    continue
                if _is_ignored(stack, rel, entry.name, is_dir):
                    continue
            path = Path(entry.path)
            out.append((path, rel, is_dir))
            if is_dir:
                recurse(path, rel, stack)

    recurse(root, "", [])
    return out


def _display_path(path: Path, workdir: Path) -> str:
    """条目路径:相对 workdir 的 posix 串(可直接喂 system.file.read/system.file.search);workdir 之外给绝对路径。"""
    try:
        return path.relative_to(workdir).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# §W1-1 system.file.list
# ---------------------------------------------------------------------------


async def fs_list(
    path: str = ".",
    pattern: str = "",
    max_entries: int = _LIST_DEFAULT_MAX,
    cursor: str = "",
    include_ignored: bool = False,
    ctx: ToolContext | None = None,
) -> dict[str, Any] | ToolResult:
    """递归列举目录,返回 {entries: [{path, size, mtime, is_dir}], total, next_cursor?}。

    Use when 探索目录结构(文件任务的第一步);Do not use when 已知路径要读内容
    (用 system.file.read)或按内容找文件(用 system.file.search)。默认尊重 .gitignore 并跳过
    .git/node_modules/.venv(include_ignored=true 关闭);pattern 为 glob
    (如 "*.py",fnmatch 语义);截断时 total 如实、next_cursor 原样回传 cursor
    翻页;mtime 与 system.time.now 组合可做卡死检测。
    """
    if max_entries < 1:
        return _invalid(
            f"max_entries 必须 >= 1(收到 {max_entries})", "调大 max_entries,或用 cursor 翻页"
        )
    offset = _decode_cursor(cursor) if cursor else 0
    if isinstance(offset, ToolResult):
        return offset
    workdir = Path(ctx.workdir).resolve() if ctx is not None else Path.cwd()
    target = resolve_work_path(workdir, ctx.read_paths if ctx is not None else (), path)
    if isinstance(target, ToolResult):
        return target
    if not target.is_dir():
        if not target.exists():
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.NOT_FOUND,
                    message=f"目录不存在: {path}",
                    retryable=False,
                    hint="用 system.file.list 列举父目录确认结构(路径从 workdir 起算)",
                ),
            )
        return _invalid(f"不是目录: {path}", "读文件用 system.file.read;按内容查找用 system.file.search")
    entries: list[dict[str, Any]] = []
    for file_path, _rel, is_dir in _walk_tree(target, include_ignored):
        display = _display_path(file_path, workdir)
        if pattern and not fnmatch.fnmatch(display, pattern):
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue  # 断链等:跳过该条目,不拖垮整次列举
        entries.append(
            {"path": display, "size": stat.st_size, "mtime": stat.st_mtime, "is_dir": is_dir}
        )
    entries.sort(key=lambda e: e["path"])
    total = len(entries)
    result: dict[str, Any] = {"entries": entries[offset : offset + max_entries], "total": total}
    if offset + max_entries < total:
        result["next_cursor"] = _encode_cursor(offset + max_entries)
    return result


# ---------------------------------------------------------------------------
# §W1-2 system.file.search
# ---------------------------------------------------------------------------


def _scan_lines(
    lines: list[str], regex: re.Pattern[str], context_lines: int
) -> list[tuple[int, list[str], str, list[str]]]:
    """逐行正则扫描(在 worker 线程运行,供外层 wait_for 限时);一行命中记一次。"""
    found: list[tuple[int, list[str], str, list[str]]] = []
    for index, line in enumerate(lines):
        if regex.search(line) is not None:
            before = lines[max(0, index - context_lines) : index]
            after = lines[index + 1 : index + 1 + context_lines]
            found.append((index + 1, before, line, after))
    return found


async def fs_search(
    pattern: str,
    path: str = ".",
    glob: str = "",
    context_lines: int = 0,
    max_hits: int = _SEARCH_DEFAULT_MAX,
    cursor: str = "",
    ctx: ToolContext | None = None,
) -> dict[str, Any] | ToolResult:
    """按正则检索文件内容,返回 {hits: [{path, line, text, before, after}], total, next_cursor?, spill_ref?}。

    Use when 按内容定位文件(代码/文档检索标配原语);Do not use when 只列目录
    (用 system.file.list)或已知文件读片段(用 system.file.read)。行号从 1 起计;context_lines
    带前后文;二进制文件(NUL 字节探测)与 .gitignore 忽略项默认跳过;单文件
    正则扫描超 2s 跳过该文件并记入 warnings(灾难性回溯防护);结果过大
    (>1000 命中或 >100KB)自动 spill 到 blob 并返回 spill_ref,此处只留前 50 条。
    """
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return _invalid(
            f"正则不合法: {e}", "pattern 为 Python re 语法;匹配字面串先 re.escape 再传入"
        )
    if max_hits < 1:
        return _invalid(f"max_hits 必须 >= 1(收到 {max_hits})", "调大 max_hits,或用 cursor 翻页")
    if context_lines < 0:
        return _invalid(f"context_lines 必须 >= 0(收到 {context_lines})", "不带上下文用 context_lines=0")
    offset = _decode_cursor(cursor) if cursor else 0
    if isinstance(offset, ToolResult):
        return offset
    workdir = Path(ctx.workdir).resolve() if ctx is not None else Path.cwd()
    target = resolve_work_path(workdir, ctx.read_paths if ctx is not None else (), path)
    if isinstance(target, ToolResult):
        return target
    if target.is_file():
        candidates = [target]  # 显式单文件:直接检索,不适用忽略规则
    elif target.is_dir():
        candidates = [p for p, _rel, is_dir in _walk_tree(target, include_ignored=False) if not is_dir]
    else:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"路径不存在: {path}",
                retryable=False,
                hint="用 system.file.list 确认目录结构(路径从 workdir 起算)",
            ),
        )
    files: list[tuple[Path, str]] = []
    for candidate in candidates:
        display = _display_path(candidate, workdir)
        if glob and not fnmatch.fnmatch(display, glob):
            continue
        files.append((candidate, display))
    files.sort(key=lambda item: item[1])
    hits: list[dict[str, Any]] = []
    warnings: list[str] = []
    for file_path, display in files:
        try:
            raw = file_path.read_bytes()
        except OSError as e:
            warnings.append(f"{display}: 读取失败({e}),已跳过")
            continue
        if b"\x00" in raw[:8192]:
            continue  # 二进制文件(NUL 字节探测,§W1-2 坑)
        lines = raw.decode("utf-8", errors="replace").splitlines()
        try:
            scanned = await asyncio.wait_for(
                asyncio.to_thread(_scan_lines, lines, regex, context_lines),
                timeout=_SEARCH_REGEX_TIMEOUT,
            )
        except TimeoutError:
            # 灾难性回溯防护:跳过该文件并显式记 warning。注意 Python 无法强杀线程,
            # 超时后该扫描线程仍在后台跑(纯 CPU 正则占 GIL)——这是解释器层面的上限,
            # 契约只承诺"超时跳过 + warning",不承诺资源回收
            warnings.append(
                f"{display}: 正则扫描超过 {_SEARCH_REGEX_TIMEOUT:.0f}s,疑似灾难性回溯,已跳过"
            )
            continue
        for line_no, before, text, after in scanned:
            hits.append({"path": display, "line": line_no, "text": text, "before": before, "after": after})
    total = len(hits)
    page = hits[offset : offset + max_hits]
    result: dict[str, Any] = {"hits": page, "total": total}
    if offset + max_hits < total:
        result["next_cursor"] = _encode_cursor(offset + max_hits)
    # §W1-2 决策③:大结果自动 spill 到 blob,上下文只留前 _SPILL_KEEP 条
    oversized = total > _SPILL_MAX_HITS or len(json.dumps(hits, ensure_ascii=False)) > _SPILL_MAX_BYTES
    if oversized and ctx is not None and ctx.blob is not None:
        ref = await ctx.blob.put(json.dumps(hits, ensure_ascii=False).encode("utf-8"), ctx.run_id)
        result["hits"] = page[:_SPILL_KEEP]
        result["spill_ref"] = ref
        result["note"] = (
            f"结果过大(共 {total} 条命中),已完整 spill 至 {ref};"
            f"此处仅返回前 {len(result['hits'])} 条,用 blob_get 分页读取完整结果"
        )
    if warnings:
        result["warnings"] = warnings
    return result


# ---------------------------------------------------------------------------
# Phase 3 补齐(library-design-plan §4.2):system.file.stat / delete / mkdir
# ---------------------------------------------------------------------------


async def fs_stat(path: str, ctx: ToolContext | None = None) -> dict[str, Any] | ToolResult:
    """探查路径元信息,返回 {size, mtime, is_dir, exists};不存在返回 exists=False 而非报错。

    Use when 读/写/删除决策前探查——确认存在性、类型、大小;mtime 与 system.time.now
    组合可做卡死检测;Do not use when 要读内容(用 system.file.read)或列举目录
    (用 system.file.list)。"不存在"是合法探查结果(ok=True, exists=False),
    越出工作目录/read_paths 才是错误(INVALID_ARGS,同其他 fs 工具)。
    """
    workdir = Path(ctx.workdir).resolve() if ctx is not None else Path.cwd()
    target = resolve_work_path(workdir, ctx.read_paths if ctx is not None else (), path)
    if isinstance(target, ToolResult):
        return target
    if not target.exists():
        return {"size": 0, "mtime": 0.0, "is_dir": False, "exists": False}
    stat = target.stat()
    return {"size": stat.st_size, "mtime": stat.st_mtime, "is_dir": target.is_dir(), "exists": True}


async def fs_delete(path: str, if_match: str = "", ctx: ToolContext | None = None) -> str | ToolResult:
    """删除文件或空目录(**高危**,spec 声明 confirm;``if_match`` 乐观锁语义同 system.file.write/edit)。

    Use when 确定要移除工作目录内的文件(如清理临时产物);Do not use when 只想改内容
    (用 system.file.edit/system.file.write)或要删非空目录(不支持,先用 system.file.list
    列出内容逐个删除,再删空目录)。``if_match`` 为期望的当前内容(或其 sha256 十六进制):
    不匹配拒删,不传不检查,仅对文件有效;路径不存在 → NOT_FOUND。
    """
    workdir = Path(ctx.workdir).resolve() if ctx is not None else Path.cwd()
    target = resolve_work_path(workdir, ctx.read_paths if ctx is not None else (), path, write=True)
    if isinstance(target, ToolResult):
        return target
    if not target.exists():
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"路径不存在: {path}",
                retryable=False,
                hint="用 system.file.list 或 system.file.stat 确认路径(从 workdir 起算)",
            ),
        )
    if target.is_dir():
        if if_match:
            return _invalid("if_match 仅对文件有效(目录无内容可比对)", "删除空目录不传 if_match")
        try:
            target.rmdir()
        except OSError:
            return _invalid(
                f"目录非空,拒绝删除: {path}",
                "先用 system.file.list 列出内容,逐个删除后再删空目录;递归删除留待后续里程碑",
            )
        return f"已删除空目录 {path}"
    conflict = _check_if_match(target, path, if_match)
    if conflict is not None:
        return conflict
    target.unlink()
    return f"已删除 {path}"


async def fs_mkdir(path: str, ctx: ToolContext | None = None) -> dict[str, Any] | ToolResult:
    """创建目录(parents=True、exist_ok=True 语义),返回 {created};已存在是幂等成功而非错误。

    Use when 需要显式建目录本身(如搭产物目录结构);Do not use when 只是要写文件
    (system.file.write 会自动建父目录,直接写即可)。路径上已有同名文件 → INVALID_ARGS。
    """
    workdir = Path(ctx.workdir).resolve() if ctx is not None else Path.cwd()
    target = resolve_work_path(workdir, ctx.read_paths if ctx is not None else (), path, write=True)
    if isinstance(target, ToolResult):
        return target
    if target.exists():
        if not target.is_dir():
            return _invalid(
                f"路径已存在且不是目录: {path}", "换个目录名,或先用 system.file.delete 移除同名文件"
            )
        return {"created": False}
    target.mkdir(parents=True, exist_ok=True)
    return {"created": True}


# ---------------------------------------------------------------------------
# §W1-3 system.time.now(replayable;回放机制在 local_registry.dispatch)
# ---------------------------------------------------------------------------


async def now(tz: str = "local") -> dict[str, Any] | ToolResult:
    """服务端时钟,返回 {iso, epoch, tz};声明 replayable:replay 时返回 trace 记录值,不重取时钟。

    Use when 需要当前时间——卡死检测、窗口期守门校验必须以此为准,模型自报的时间
    不可信(§W1-3:它是安全边界,不只是便利);Do not use when 只要相对耗时。
    tz 为 IANA 时区名(如 "Asia/Shanghai"),"local"(默认)/"utc" 为别名;
    未知时区 → INVALID_ARGS。
    """
    lowered = tz.strip().lower()
    if lowered == "local":
        moment = datetime.now().astimezone()
        return {"iso": moment.isoformat(), "epoch": moment.timestamp(), "tz": "local"}
    if lowered in ("utc", "z"):
        zone: Any = UTC
        label = "UTC"
    else:
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            return _invalid(
                f"未知时区: {tz!r}", "tz 用 IANA 时区名(如 Asia/Shanghai),或 local/utc"
            )
        label = tz
    moment = datetime.now(zone)
    return {"iso": moment.isoformat(), "epoch": moment.timestamp(), "tz": label}


# ---------------------------------------------------------------------------
# §W1-4 system.task.todo_write / system.task.todo_update(run 级状态;状态栏/checkpoint 接线见模块 docstring)
# ---------------------------------------------------------------------------


def todo_write_tool(*, name: str = "system.task.todo_write", run_states: dict[str, dict[str, Any]]) -> Tool:
    """构造 ``system.task.todo_write``(§W1-4;WRITE·run 级状态):全新规划,覆盖式写本 run 清单。"""

    async def todo_write(
        items: list[dict[str, Any]], ctx: ToolContext | None = None
    ) -> dict[str, Any] | ToolResult:
        """(重新)规划任务清单:整体覆盖本 run 的 TODO,返回 {count};跨帧共享(子帧可见)。

        Use when 任务有多步、需要显式计划防漏做;Do not use when 只推进单条状态
        (用 system.task.todo_update,更便宜)。items 元素 {id, text, status?},status ∈
        pending/doing/done/cancelled(缺省 pending),id 须唯一。
        """
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return _invalid(
                    f"items[{index}] 不是对象", "每条任务形如 {id, text, status?}"
                )
            task_id = item.get("id")
            text = item.get("text")
            status = item.get("status", "pending")
            if not isinstance(task_id, str) or not task_id:
                return _invalid(f"items[{index}] 缺少非空字符串 id", "每条任务需要唯一 id(字符串)")
            if task_id in seen:
                return _invalid(f"items[{index}] id 重复: {task_id!r}", "id 必须唯一")
            if not isinstance(text, str) or not text:
                return _invalid(f"items[{index}](id={task_id})缺少非空字符串 text", "text 是任务描述")
            if status not in _TODO_STATUSES:
                return _invalid(
                    f"items[{index}](id={task_id})status 非法: {status!r}",
                    f"status 取值为 {'/'.join(sorted(_TODO_STATUSES))}",
                )
            seen.add(task_id)
            normalized.append({"id": task_id, "text": text, "status": status})
        run_id = ctx.run_id if ctx is not None else ""
        run_states.setdefault(run_id, {})["todos"] = normalized
        return {"count": len(normalized)}

    return _FunctionTool(
        todo_write,
        derive_spec(
            todo_write, name=name, permission=Permission.WRITE, timeout=10.0, idempotent=True, cost_hint="~1ms"
        ),
    )


def todo_update_tool(*, name: str = "system.task.todo_update", run_states: dict[str, dict[str, Any]]) -> Tool:
    """构造 ``system.task.todo_update``(§W1-4;WRITE·run 级状态):推进单条任务,高频便宜。"""

    async def todo_update(
        id: str, status: str, note: str = "", ctx: ToolContext | None = None
    ) -> dict[str, Any] | ToolResult:
        """推进单条任务状态,返回更新后的 {item}(run 级清单,跨帧可见)。

        Use when 开始/完成/取消某条已规划任务;Do not use when 重新规划整张清单
        (用 system.task.todo_write)。status ∈ pending/doing/done/cancelled;note 可选,
        记录在 item 上(如"找到 3 个来源")。
        """
        if status not in _TODO_STATUSES:
            return _invalid(
                f"status 非法: {status!r}", f"status 取值为 {'/'.join(sorted(_TODO_STATUSES))}"
            )
        run_id = ctx.run_id if ctx is not None else ""
        todos = run_states.get(run_id, {}).get("todos") or []
        item = next((t for t in todos if t.get("id") == id), None)
        if item is None:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.NOT_FOUND,
                    message=f"任务不存在: {id!r}(本 run 清单共 {len(todos)} 条)",
                    retryable=False,
                    hint="先用 system.task.todo_write 规划清单,或核对 id",
                ),
            )
        item["status"] = status
        if note:
            item["note"] = note
        return {"item": dict(item)}

    return _FunctionTool(
        todo_update,
        derive_spec(
            todo_update, name=name, permission=Permission.WRITE, timeout=10.0, idempotent=True, cost_hint="~1ms"
        ),
    )


def todo_read_tool(*, name: str = "system.task.todo_read", run_states: dict[str, dict[str, Any]]) -> Tool:
    """构造 ``system.task.todo_read``(READ·run 级状态):读取完整任务清单,可选按状态过滤。"""

    async def todo_read(
        status: str = "", ctx: ToolContext | None = None
    ) -> dict[str, Any] | ToolResult:
        """读取本 run 的任务清单,返回 {todos};支持按 status 过滤。

        Use when 需要完整清单做规划或确认队列位置(上下文注入只给摘要);
        Do not use when 只想看进度(上下文状态行已注入,更便宜)。
        status 可选值为 pending/doing/done/cancelled,缺省返回全部。
        """
        if status and status not in _TODO_STATUSES:
            return _invalid(
                f"status 非法: {status!r}", f"status 取值为 {'/'.join(sorted(_TODO_STATUSES))} 或留空"
            )
        run_id = ctx.run_id if ctx is not None else ""
        todos = run_states.get(run_id, {}).get("todos") or []
        if status:
            todos = [t for t in todos if t.get("status") == status]
        return {"todos": [dict(t) for t in todos], "count": len(todos)}

    return _FunctionTool(
        todo_read,
        derive_spec(
            todo_read,
            name=name,
            permission=Permission.READ,
            timeout=10.0,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~1ms",
        ),
    )


# ---------------------------------------------------------------------------
# §W1-5 system.skill.search
# ---------------------------------------------------------------------------


def _match_score(query: str, keywords: list[str], name: str, description: str) -> int:
    """子串/关键词匹配打分(大小写不敏感;§W1-5 v1,W2-9 bm25_score 落地后换 BM25)。"""
    haystack = f"{name}\n{description}".lower()
    if query in name.lower():
        return 3
    if query in description.lower():
        return 2
    if keywords and all(k in haystack for k in keywords):
        return 1
    return 0


def skill_search_tool(*, name: str = "system.skill.search", registry: LocalPythonToolRegistry) -> Tool:
    """构造 ``system.skill.search``(§W1-5;READ):检索 tools/skills registry,结果带权限信息。

    skills 数据源:装配时 KernelBuilder 调 ``tools.bind_skills(skills)`` 注入
    (bind 模式,同 system.python.exec 的 ``bind`` 先例);未装配时只检索工具面。
    """

    async def skill_search(
        query: str, kind: str = "all", limit: int = 20
    ) -> dict[str, Any] | ToolResult:
        """按子串/关键词检索已注册的工具与技能(大小写不敏感),结果带权限信息。

        Use when 不确定该用哪个工具/技能、或想找没列进当前白名单的能力;
        Do not use when 已知名字(直接调用,不必先检索)。kind: tool|skill|all;
        每条结果 {name, kind, description, permission|permissions}——
        调用前先核对自己是否有权(防选中无权工具,白浪费一轮)。
        """
        if kind not in ("all", "tool", "skill"):
            return _invalid(f"kind 非法: {kind!r}", "kind 取值为 all/tool/skill")
        if limit < 1:
            return _invalid(f"limit 必须 >= 1(收到 {limit})", "调大 limit")
        text = query.strip().lower()
        if not text:
            return _invalid("query 为空", "给子串或关键词,如 \"edit\"、\"检索 文件\"")
        keywords = text.split()
        scored: list[tuple[int, dict[str, Any]]] = []
        if kind in ("all", "tool"):
            for spec in registry.specs():
                score = _match_score(text, keywords, spec.name, spec.description)
                if score:
                    scored.append((score, {
                        "name": spec.name,
                        "kind": "tool",
                        "description": spec.description,
                        "permission": spec.permission.name,
                    }))
        if kind in ("all", "skill"):
            skills = getattr(registry, "_skills", None)  # bind_skills 注入,同 manager 取 _blob 先例
            manifests_fn = getattr(skills, "manifests", None)
            if callable(manifests_fn):
                for m in manifests_fn():
                    score = _match_score(text, keywords, m.name, m.description)
                    if score:
                        scored.append((score, {
                            "name": m.name,
                            "kind": "skill",
                            "description": m.description,
                            "permissions": {
                                "tools": list(m.permissions.tools),
                                "skills": list(m.permissions.skills),
                                "blackboard": list(m.permissions.blackboard),
                            },
                        }))
        scored.sort(key=lambda item: (-item[0], item[1]["name"]))
        results = [entry for _score, entry in scored]
        return {"results": results[:limit], "total": len(results)}

    return _FunctionTool(
        skill_search,
        derive_spec(
            skill_search,
            name=name,
            permission=Permission.READ,
            timeout=10.0,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~5ms,取决于注册条目数",
        ),
    )

"""platform.* 动作技能的副作用面(docs/APP-MODEL.md v0.5 §17.7 第 2 步;L2 拆 tool)。

L1 的 code 技能只是"戴着 tier 徽章的宿主函数"(§17.4)——handler 直接 import
业务写函数,三层权限交集与数据 authZ 不在路径上。本模块把**有真实世界副作用**
的操作拆成声明了 ``permission``/``side_effect``/``data_domains`` 的 tool(注册进
platform 内核的 Tool Registry);skill 退为编排,经 ``ctx.call_tool`` 调用——
帧白名单 ∩ RunConfig 上限 ∩ 工具自报档三层交集 + pre/post:tool.call 信号 +
数据层 authZ 由此真正进路径。

错误通道:tool 内的可归类错误折成结构化 ``ToolResult``(kind 映射:
``invalid_args``→400 / ``not_found``→404 / ``vetoed``→409——409 冲突用
vetoed,语义同为"与当前状态冲突被拒"),handler 侧 ``_tool()`` 原位翻译回
``PlatformActionError``——HTTP 状态码/文案与 L1 前逐字一致(行为零变化)。

``data_domains`` 说明(docs/DATA-AUTHZ.md):D1 数据闸只强制 ``fs.*`` 域
(local_registry._check_data_access 对非 fs 域声明跳过),``drafts.*``/``docs.*``/
``runs.*``/``skills.*`` 是 D2 预告面——声明即账面,判定留 D2。

irreversible 纪律:``platform.draft.delete``(rmtree 连版本史)是全包唯一
irreversible tool;platform 动作是宿主直接启动的根帧(人点按钮),没有也不得有
approve-run 式的 run 级放行面——删除确认在 UI(人按),技能/工具层不另开通道。
"""

from __future__ import annotations

import functools
import inspect
import re
from pathlib import Path
from typing import Any

from agent_os.api.v1 import Permission, ToolError, ToolErrorKind, ToolResult
from agent_os.host.web.run_manager import RunValidationError
from agent_os.kernel.errors import AgentOSError, SkillLoadError
from agent_os.skills.gate import GateError
from agent_os.skills.iterate import edit_members
from agent_os.skills.package import promote_package

#: 组内全部工具名(§17.8 静态扫描断言用)
PLATFORM_TOOLS = (
    "platform.skill.promote",
    "platform.draft.accept",
    "platform.draft.delete",
    "platform.doc.write",
    "platform.run.control",
    "platform.debug.intervene",
    "platform.skills.reload",
)

#: 锚点格式(docs/DOC-EDITOR.md §2.1;v3 用户裁决 2026-08-11):
#: doc.md#L<start>[:C<col>]-L<end>[:C<col>](1-based 行号区间,列可选;
#: 旧行级 anchor 向后兼容)
_ANCHOR_RE = re.compile(r"^doc\.md#L(\d+)(?::C(\d+))?-L(\d+)(?::C(\d+))?$")


class PlatformActionError(Exception):
    """action 执行期的可归类错误(status + detail;工具折 ToolResult、技能折信封)。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _terr(status: int, detail: str) -> ToolResult:
    """HTTP 归类 → 结构化 ToolResult(400/404/409 三档;其余不走此面)。"""
    kind = {
        400: ToolErrorKind.INVALID_ARGS,
        404: ToolErrorKind.NOT_FOUND,
        409: ToolErrorKind.VETOED,  # 与当前状态冲突被拒(gate 红关/非在途/非 paused)
    }[status]
    return ToolResult(ok=False, value=None, error=ToolError(kind=kind, message=detail, retryable=False))


def _domain_guard(fn: Any) -> Any:
    """tool 入口包装:可归类域异常 → 结构化 ToolResult(与 handlers._guarded
    的 HTTP 归类表一一对应);未列名异常由 dispatch 边界归 INTERNAL(→ 500,
    与升格前的未捕获路径同归类)。"""

    @functools.wraps(fn)
    async def run(*args: Any, **kwargs: Any) -> Any:
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        except PlatformActionError as e:
            return _terr(e.status, e.detail)
        except FileExistsError as e:
            return _terr(409, f"同名草稿已存在:{e}")
        except GateError as e:
            return _terr(409, str(e))
        except KeyError as e:
            return _terr(404, e.args[0] if e.args else str(e))
        except FileNotFoundError as e:
            return _terr(404, str(e))
        except (SkillLoadError, RunValidationError, ValueError) as e:
            return _terr(400, str(e))
        except AgentOSError as e:
            return _terr(409, str(e))

    return run


def register_platform_tools(registry: Any, *, deps: dict[str, Any]) -> None:
    """把副作用工具组注册进 ``registry``(platform 内核装配时调用)。

    ``deps`` 惰性取用(``dict.get``):宿主只给得起的面才可用——web/app.py 的
    收编端点只有 manager/lab_store,调不到的工具在调用期才报装配错误。
    """
    manager = deps.get("manager")
    lab_store = deps.get("lab_store")
    doc_store = deps.get("doc_store")
    artifacts_root = deps.get("artifacts_root")
    read_json = deps.get("read_json")

    @registry.tool(
        name="platform.skill.promote", permission=Permission.WRITE,
        side_effect="reversible", data_domains=["skills.*"], timeout=300.0,
    )
    @_domain_guard
    async def skill_promote(plan_id: str, warnings_ack: bool = False, ctx: Any = None) -> ToolResult:
        """发布草稿包到生产(promote 写盘 + 必要时 reload skillsets)。

        Use when plan.confirm 批准发布;Do not use when 只是检查(那是 platform.draft.check)。
        可逆面:上一版在 store 版本史,可再发布旧版回滚(故 reversible)。
        """
        result = promote_package(
            store=lab_store,
            plan_id=plan_id,
            warnings_ack=warnings_ack,
            production=manager.shared_skills_registry(),
            tools=manager.shared_tools_registry(),
            principal=manager.principal().subject,
            skillsets_root=manager.skillsets_root(),
        )
        if result.get("set"):
            manager.refresh_skillsets()
        return ToolResult(value=result)

    @registry.tool(
        name="platform.draft.accept", permission=Permission.WRITE,
        side_effect="reversible", data_domains=["drafts.*"], timeout=120.0,
    )
    @_domain_guard
    async def draft_accept(name: str, ctx: Any = None) -> ToolResult:
        """接受迭代候选:快照(prefer_candidate)→ 覆盖 working → 清候选区。

        Use when candidate.accept 人按接受;Do not use when 只是查看 diff。
        可逆面:覆盖前自动快照,恢复 = restore_version(故 reversible)。
        """
        members = edit_members(
            lab_store, manager.shared_skills_registry(), manager.shared_tools_registry(), name
        )
        cand = lab_store.candidate_members(name)
        if not cand:
            raise FileNotFoundError(f"无候选: {name}")
        latest = lab_store.list_versions(name)
        _, comments = lab_store.latest_comments(name)
        vid = lab_store.snapshot(
            name, members, source="iterate",
            parent=latest[0]["version"] if latest else None,
            comments_digest=f"{len(comments)} 条边注",
            prefer_candidate=True,  # N2:快照 = 被接受的候选内容(B4)
        )
        for member in cand:
            data = lab_store.read_candidate_member(name, member)
            current = lab_store.read(member)
            lab_store.save(
                member,
                manifest=data["manifest"] or {},
                prompt=data["prompt"],
                handler=current["handler"],
                tests=data["tests"] or None,
            )
        lab_store.clear_candidate(name)
        return ToolResult(value={"version": vid})

    @registry.tool(
        name="platform.draft.delete", permission=Permission.WRITE,
        side_effect="irreversible", data_domains=["drafts.*"], timeout=60.0,
    )
    @_domain_guard
    async def draft_delete(name: str, ctx: Any = None) -> ToolResult:
        """删草稿(rmtree,连版本史一起删——**不可恢复**,全包唯一 irreversible)。

        Use when 人已在 UI 确认删除草稿;Do not use when 只想放弃候选
        (那是 candidate.discard,working 不动)。删除确认在 UI(人按),
        本面不另开放行通道(无 approve-run 面)。
        """
        lab_store.delete(name)
        return ToolResult(value={"name": name})

    @registry.tool(
        name="platform.doc.write", permission=Permission.WRITE,
        side_effect="reversible", data_domains=["docs.*", "drafts.*"], timeout=60.0,
    )
    @_domain_guard
    async def doc_write(
        name: str,
        text: Any = None,
        anchor: str = "",
        replace_text: Any = None,
        expected: Any = None,
        ctx: Any = None,
    ) -> ToolResult:
        """写文档:整文(``text``)或按锚点段替换(``anchor`` + ``replace_text``)。

        Use when doc.save(整文)/ doc.apply(按段,人按才落);Do not use when
        只读(那是读面端点)。可逆面:写之前上一版自动 .bak(故 reversible);
        ``notes.<draft>`` 名空间整文保存时同步写回草稿 manifest.notes(故 drafts.* 域)。
        """
        import logging

        if anchor:
            # 按段替换(doc.apply 语义;人按才落,越界/已变 → 400)
            m = _ANCHOR_RE.match(anchor)
            if not m:
                raise PlatformActionError(400, f"锚点格式非法: {anchor!r}(须 doc.md#L<start>-L<end>,列可选)")
            # v3:RE 组 (1,3)=行号,(2,4)=可选列;apply 按整行段替换(列级精确定位未开)
            start, end = int(m.group(1)), int(m.group(3))
            doc = doc_store.read(name)
            lines = doc["text"].split("\n")
            if start < 1 or end < start or end > len(lines):
                raise PlatformActionError(400, "文档已变化,请重新评审")
            if expected is not None and "\n".join(lines[start - 1 : end]) != str(expected):
                raise PlatformActionError(400, "文档已变化,请重新评审")
            new_lines = lines[: start - 1] + str(replace_text or "").split("\n") + lines[end:]
            doc_store.save(name, "\n".join(new_lines))
            doc_store.save_bubble(name, anchor, {"role": "system", "text": "已应用"})
            return ToolResult(value={"text": doc_store.read(name)["text"]})
        # 整文保存(doc.save 语义;notes.* 写回草稿)
        doc = doc_store.save(name, str(text or ""))
        if name.startswith("notes.") and lab_store is not None:
            draft = name[len("notes."):]
            try:
                dd = lab_store.read(draft)
                lab_store.save(
                    draft,
                    manifest={**(dd["manifest"] or {}), "notes": str(text or "")},
                    prompt=dd["prompt"],
                    handler=dd["handler"],
                )
            except (FileNotFoundError, ValueError) as e:
                logging.getLogger("agent_os.platform").info(
                    "NOTES 写回跳过(草稿 %s 不可读): %s", draft, e
                )
        return ToolResult(value={"savedAt": doc["meta"].get("savedAt", 0)})

    @registry.tool(
        name="platform.run.control", permission=Permission.WRITE,
        side_effect="reversible", data_domains=["runs.*"], timeout=1800.0,
    )
    @_domain_guard
    async def run_control(run_id: str, command: str, ctx: Any = None) -> ToolResult:
        """run 控制:stop(在途置中止标志)/ resume(checkpoint 恢复)/ rerun(按产物重跑)。

        Use when run.stop/resume/rerun 动作;Do not use when 起全新 run(那是
        platform.run.launch 技能面)。归档:WRITE/reversible——三者都改变 run 进程态
        而非删除数据(stop/resume 状态可再来回,rerun 是新建不动旧产物;进程态
        不构成 irreversible 的数据销毁)。resume 阻塞到 run 结束,timeout 对齐
        RunConfig.max_wall_time 缺省。
        """
        if command == "stop":
            ok = await manager.stop_run(run_id)
            if not ok:
                raise PlatformActionError(409, f"run 不在在途状态,无法停止: {run_id}")
            return ToolResult(value={})
        if command == "resume":
            try:
                record = await manager.resume_run(run_id)
            except Exception as e:  # ResumeConflictError 等状态冲突归 409
                if "Conflict" in type(e).__name__:
                    raise PlatformActionError(409, str(e)) from e
                raise
            return ToolResult(value={"status": record.get("status", "")})
        if command == "rerun":
            meta = read_json(Path(artifacts_root) / "runs" / run_id / "meta.json")
            if meta is None:
                raise PlatformActionError(404, f"找不到 run 产物: {run_id}")
            new_id = await manager.start_run(meta.get("skill") or "", meta.get("input") or {})
            return ToolResult(value={"new_id": new_id, "skill": meta.get("skill") or ""})
        raise PlatformActionError(400, f"未知 run 控制命令: {command!r}")

    @registry.tool(
        name="platform.debug.intervene", permission=Permission.WRITE,
        side_effect="reversible", data_domains=["runs.*"], timeout=60.0,
    )
    @_domain_guard
    async def debug_intervene(
        session_id: str,
        command: str,
        patch: Any = None,
        frame_id: str = "",
        text: str = "",
        ctx: Any = None,
    ) -> ToolResult:
        """调试干预:modify(改写在跑帧的工具调用参数)/ inject(注入消息后放行)。

        Use when 调试会话暂停在 pre:tool.call 需要人工干预;Do not use when
        只是放行/停止(那是 platform.debug.command)。**全系统特权最高的 UI 动作**
        (§17.3 #9)——必须经帧白名单与权限交集,不允许裸 host 调用。
        可逆面:干预作用于在跑帧的当次调用,不改持久数据(故 reversible)。
        """
        if command == "modify":
            await manager.debug_modify(session_id, dict(patch or {}))
            return ToolResult(value={})
        if command == "inject":
            fid = await manager.debug_inject(session_id, frame_id or None, text)
            return ToolResult(value={"frame_id": fid})
        raise PlatformActionError(400, f"未知调试干预命令: {command!r}")

    @registry.tool(
        name="platform.skills.reload", permission=Permission.WRITE,
        side_effect="reversible", data_domains=["skills.*"], timeout=120.0,
    )
    @_domain_guard
    async def skills_reload(skill_set: str = "", ctx: Any = None) -> ToolResult:
        """热重载生产 registry(mtime 检查;只影响其后新建的 run)。

        Use when skills 文件变更后要生效;Do not use when 改单个草稿(那是
        lab.draft.* 面)。可逆面:再改文件再 reload 即回(故 reversible)。
        """
        reloaded = manager.reload_skills(skill_set or None)
        return ToolResult(value={"reloaded": reloaded})

"""platform.* 内置动作技能包 —— handler 面(docs/APP-MODEL.md v0.5 §17.7;L1+L2)。

L1 = **机械搬运**:本体来自 web_platform/app.py 原 ``_act_*`` / ``_mut_*`` 薄函数,
行为逐字保留,只做了三处适配:

1. 依赖不再闭包取值,改从 ``ctx._kernel.platform_deps`` 取(宿主装配 platform
   内核时注入;每 app 一颗内核,deps 不跨实例串);
2. handler 里的 ``HTTPException`` 直抛改为 ``PlatformActionError(status, detail)``——
   异常过不了 Logic Kernel 执行边界(会塌成 RUNTIME_ERROR 丢状态码),
   统一由 ``_guarded`` 折成 ``_action_error`` 信封,管道侧原位翻译成
   HTTPException(状态码/文案与升格前一致,行为零变化);
3. ``asyncio.run(manager.*)`` 改为直接 ``await``(handler 已在帧的 loop 里,
   不能再开私有 loop;manager 的 run 跑在独立 worker 线程,await 安全)。

L2(§17.4,升格序第 2 步):**有真实世界副作用的 skill 退为编排**——副作用面
拆成 tools.py 里声明了 permission/side_effect/data_domains 的 tool,handler
经 ``_tool()``(= ctx.call_tool)调用:帧白名单 ∩ RunConfig 上限 ∩ 工具自报档
+ pre/post:tool.call 信号 + 数据层 authZ 由此进路径。**L2 handler 内禁止直接
import/调用业务写函数**(§17.8 静态扫描断言;读面与纯卡片构造不在禁令内)。
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

import jsonschema

from agent_os.api.v1 import ProviderError, derive_skill_tier
from agent_os.host.web_platform.artifacts import (
    build_diff_card,
    build_gate_report_card,
    build_publish_card,
    build_skill_pack_card,
    build_table_card,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.draft_store import (
    OverlaySkillRegistry,
    skeleton_from_schema,
    smoke_case_from_schema,
)
from agent_os.skills.gate import GateError, validate_draft
from agent_os.skills.iterate import arun_iterate, edit_members
from agent_os.skills.lab_assistant import ITERATOR_NAME, iterator_skill
from agent_os.skills.platform.tools import PlatformActionError

_log = logging.getLogger("agent_os.platform")


def _err_env(status: int, detail: str) -> dict[str, Any]:
    return {"ok": False, "_action_error": {"status": status, "detail": detail}}


def _guarded(fn: Any) -> Any:
    """入口包装:可归类异常 → ``_action_error`` 信封(归类表与升格前 _run_handler
    逐字一致);未列名异常原样上抛(→ RUNTIME_ERROR → 500,与旧面同归类)。"""

    @functools.wraps(fn)
    async def run(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        try:
            return await fn(args, ctx)
        except PlatformActionError as e:
            return _err_env(e.status, e.detail)
        except FileExistsError as e:
            # 草稿重名(重复批准/历史残留):友好 409,不 500
            return _err_env(409, f"同名草稿已存在:{e}")
        except ProviderError:
            # N7(O7):凭证/模型服务故障 → 503 人话
            return _err_env(503, "模型服务暂不可用(凭证可能已过期),请刷新凭证后重试")
        except GateError as e:
            return _err_env(409, str(e))
        except FileNotFoundError as e:
            return _err_env(404, str(e))
        except SkillLoadError as e:
            return _err_env(400, str(e))
        except ValueError as e:
            return _err_env(400, str(e))

    return run


def _deps(ctx: Any) -> dict[str, Any]:
    """取宿主注入的依赖面(platform 内核装配时挂上;缺失 = 装配错误)。"""
    deps = getattr(getattr(ctx, "_kernel", None), "platform_deps", None)
    if deps is None:
        raise RuntimeError("platform 内核未注入 platform_deps(装配错误,§17.7)")
    return deps


async def _tool(ctx: Any, name: str, args: dict[str, Any]) -> Any:
    """经 ctx 调 tool(L2:副作用唯一通道——帧白名单 ∩ RunConfig 上限 ∩ 工具自报档
    + 数据层 authZ);结构化错误原位翻译回 PlatformActionError(HTTP 归类逐字不变)。"""
    r = await ctx.call_tool(name, args)
    if r.get("ok"):
        return r.get("value")
    err = r.get("error") or {}
    status = {"invalid_args": 400, "not_found": 404, "vetoed": 409}.get(str(err.get("kind") or ""))
    if status is None:
        # 未归类(permission_denied/internal/timeout)= 装配或内部错误 → 500 面
        # (与升格前"未捕获异常 → 500"同归类)
        raise RuntimeError(f"tool {name} 失败({err.get('kind')}): {err.get('message')}")
    raise PlatformActionError(status, str(err.get("message") or ""))


# ---------------------------------------------------------------------------
# 卡片动作系(旧 cards/action 与 action 管道共用)
# ---------------------------------------------------------------------------


@_guarded
async def scaffold_approve(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """scaffold.approve(意图①批准):创建首稿 → 五关 → skill_pack + gate_report 卡。

    N3(O3,B2):首稿即带 1 个合 schema 的冒烟用例(tests/smoke.json,
    按 inputs schema 骨架生成)——首稿 G4 不再必然 warn。
    (写面 = DraftStore.create/save:草稿区写入,不在 §17.4 的 L2 名单——
    名单只收"有真实世界副作用"的特权操作;草稿写是 lab.draft.* 工具面的地盘)
    """
    d = _deps(ctx)
    lab_store, manager = d["lab_store"], d["manager"]
    name = str(args.get("name") or "")
    template = args.get("template") or None
    lab_store.create(name, template=template)
    draft = lab_store.read(name)
    if not draft["tests"]:
        case = smoke_case_from_schema((draft["manifest"] or {}).get("inputs"))
        if case is not None:
            lab_store.save(
                name,
                manifest=draft["manifest"],
                prompt=draft["prompt"],
                handler=draft["handler"],
                tests={"smoke.json": case},
            )
            draft = lab_store.read(name)
    report = validate_draft(
        draft,
        production=manager.shared_skills_registry(),
        tools=manager.shared_tools_registry(),
        store=lab_store,
    )
    lab_store.save_gate_report(name, report)
    tier = derive_skill_tier(
        lab_store.load_skill(name).manifest, manager.shared_tools_registry(),
        OverlaySkillRegistry(manager.shared_skills_registry(), lab_store),
    )
    members = edit_members(lab_store, manager.shared_skills_registry(), manager.shared_tools_registry(), name)
    return {
        "ok": True,
        "text": f"首稿完成: {name}(推导档 {tier})。五关 {report['status']};"
        f"{'可以继续迭代,或生成提交计划。' if report['status'] != 'fail' else '有红关,先修再提交。'}",
        "cards": [
            build_skill_pack_card(name=name, tier=tier, members=members),
            build_gate_report_card(
                draft=name, status=report["status"], gates=report["gates"],
                plan_payload={"root": name},
            ),
        ],
    }


@_guarded
async def plan_confirm(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """plan.confirm(§17.4 L2 名单):发布 = platform.skill.promote tool
    (promote 写盘 + reload;WRITE/reversible)——本 skill 只取参/调 tool/造卡。"""
    result = await _tool(
        ctx,
        "platform.skill.promote",
        {
            "plan_id": str(args.get("plan_id") or ""),
            "warnings_ack": bool(args.get("warnings_ack")),
        },
    )
    return {
        "ok": True,
        "text": f"已发布 {result['root']}({result['form']},hash {result['package_hash'][:8]})。",
        "cards": [
            build_skill_pack_card(
                name=result["root"],
                tier="",
                members=[m["name"] for m in result["members"]],
            )
        ],
    }


@_guarded
async def candidate_accept(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """candidate.accept(§17.4 L2 名单):覆盖 working = platform.draft.accept tool
    (快照 → 覆盖 → 清候选;WRITE/reversible)——本 skill 只取参/调 tool。"""
    r = await _tool(ctx, "platform.draft.accept", {"name": str(args.get("name") or "")})
    return {"ok": True, "text": f"已接受为 {r['version']}(候选已覆盖 working)。"}


@_guarded
async def candidate_discard(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    d = _deps(ctx)
    d["lab_store"].clear_candidate(str(args.get("name") or ""))
    return {"ok": True, "text": "已放弃该候选(working 未动)。"}


@_guarded
async def version_rewind(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    d = _deps(ctx)
    name = str(args.get("name") or "")
    version = str(args.get("version") or "")
    d["lab_store"].restore_version(name, version)
    return {"ok": True, "text": f"已恢复到 {version}(版本历史未动)。"}


@_guarded
async def plan_recheck(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """plan.recheck:生成提交计划(复用 packages/plan;返回 publish 卡)。"""
    from agent_os.skills.package import build_plan

    d = _deps(ctx)
    lab_store, manager = d["lab_store"], d["manager"]
    root = str(args.get("root") or "")
    plan = build_plan(
        root,
        store=lab_store,
        production=manager.shared_skills_registry(),
        tools=manager.shared_tools_registry(),
        smoke_runner=None,
    )
    return {
        "ok": True,
        "text": f"提交计划已生成(hash {plan['package_hash'][:8]});"
        f"阻塞 {len(plan['blockers'])} 项。",
        "cards": [
            build_publish_card(
                root=root,
                members=plan["members"],
                plan_id=plan["plan_id"],
                package_hash=plan["package_hash"],
                blockers=plan["blockers"],
                warnings=plan["warnings"],
            )
        ],
    }


@_guarded
async def iterate_generate(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """iterate.generate:Flow C 同一条生成路径(产出 diff 卡)。

    帧内 await arun_iterate(L1 适配:原 asyncio.run 私有 loop 不能再开)。
    """
    d = _deps(ctx)
    lab_store, manager = d["lab_store"], d["manager"]
    name = str(args.get("name") or "")
    result = await arun_iterate(
        manager.assemble_lab_kernel(
            OverlaySkillRegistry(
                manager.shared_skills_registry(),
                lab_store,
                extra={ITERATOR_NAME: iterator_skill()},
            )
        ),
        store=lab_store,
        production=manager.shared_skills_registry(),
        tools_registry=manager.shared_tools_registry(),
        name=name,
        comments=list(args.get("comments") or []),
        note=str(args.get("note") or ""),
    )
    return {
        "ok": True,
        "text": f"已生成候选: {result['reply']}",
        # M4a run 通道:run_id/status 透传(管道据此 spawn run app 持 run_id)
        "run_id": result.get("run_id", ""),
        "run_status": result.get("run_status", ""),
        "skill": name,
        "cards": [build_diff_card(name=name, diff=result["diff"])],
    }


@_guarded
async def decision_answer(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.decision.answer:升权作答(answer 由管道按 action_id 注入)。"""
    d = _deps(ctx)
    qid = str(args.get("question_id") or "")
    answer = str(args.get("answer") or "")
    try:
        d["manager"].supervisor_answer(qid, answer)
    except KeyError as e:
        raise PlatformActionError(404, str(e)) from e
    except ValueError as e:
        raise PlatformActionError(400, str(e)) from e
    return {"ok": True, "text": "已记录你的决定。", "state": {"resolved": answer}}


# ── M3(docs/APP-MODEL.md §8):run/debug/lab-draft 三 kind 的技能——
# L2 后 run.* 的副作用收进 platform.run.control tool,本层只编排 ─────────────────


@_guarded
async def run_stop(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.run.stop(§17.4 L2 名单):中止在途 run = platform.run.control
    tool(WRITE/reversible;不在途 → 409,与旧 web stop 同归类)。"""
    await _tool(
        ctx, "platform.run.control", {"run_id": str(args.get("run_id") or ""), "command": "stop"}
    )
    return {"ok": True, "text": "已发送停止请求(run 在下一个安全点中止)。", "state": {"status": "stopping"}}


@_guarded
async def run_resume(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.run.resume(§17.4 L2 名单):checkpoint 恢复 = platform.run.control
    tool(404/400/409 同旧 web resume)。"""
    r = await _tool(
        ctx, "platform.run.control", {"run_id": str(args.get("run_id") or ""), "command": "resume"}
    )
    return {
        "ok": True,
        "text": f"恢复运行完成: {r['status']}",
        "state": {"status": r["status"]},
    }


@_guarded
async def run_rerun(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.run.rerun(§17.4 L2 名单):按产物 meta 重跑 = platform.run.control
    tool(新 run;结果卡挂新锚)。"""
    r = await _tool(
        ctx, "platform.run.control", {"run_id": str(args.get("run_id") or ""), "command": "rerun"}
    )
    new_id = r["new_id"]
    return {
        "ok": True,
        "text": f"已按原参数重跑,新 run: {new_id[:8]}。",
        "cards": [
            build_table_card(
                title="重跑",
                columns=["run", "skill", "摘要"],
                rows=[[new_id[:8], r["skill"], "已启动"]],
                ref={"kind": "run", "id": new_id},
            )
        ],
    }


@_guarded
async def debug_command(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.debug.command:放行/停止(command 由管道按 action_id 注入:
    debug.continue→continue,debug.stop→stop;非 paused → 409)。"""
    d = _deps(ctx)
    sid = str(args.get("session_id") or "")
    command = str(args.get("command") or "continue")
    try:
        await d["manager"].debug_command(sid, command)
    except KeyError as e:
        raise PlatformActionError(404, str(e)) from e
    except ValueError as e:
        raise PlatformActionError(400, str(e)) from e
    except Exception as e:  # AgentOSError(非 paused)归 409
        if "Error" in type(e).__name__:
            raise PlatformActionError(409, str(e)) from e
        raise
    human = "已放行。" if command == "continue" else "已发送停止。"
    return {"ok": True, "text": human, "state": {"last_command": command}}


@_guarded
async def debug_intervene(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.debug.intervene(§17.3 #9;L2 新收编):modify/inject——全系统
    特权最高的 UI 动作,副作用只经 platform.debug.intervene tool(帧白名单 ∩
    权限交集;旧 web /api/debug/sessions/{sid}/modify|inject 端点已收编到本技能)。"""
    r = await _tool(
        ctx,
        "platform.debug.intervene",
        {
            "session_id": str(args.get("session_id") or ""),
            "command": str(args.get("command") or ""),
            "patch": args.get("patch"),
            "frame_id": str(args.get("frame_id") or ""),
            "text": str(args.get("text") or ""),
        },
    )
    return {"ok": True, **({"frame_id": r["frame_id"]} if r.get("frame_id") else {})}


@_guarded
async def draft_check(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.draft.check:草稿五关(与 Lab validate 端点同逻辑;返回 gate_report 卡)。"""
    d = _deps(ctx)
    lab_store, manager = d["lab_store"], d["manager"]
    name = str(args.get("name") or "")
    draft = lab_store.read(name)
    report = validate_draft(
        draft,
        production=manager.shared_skills_registry(),
        tools=manager.shared_tools_registry(),
        store=lab_store,
    )
    lab_store.save_gate_report(name, report)
    return {
        "ok": True,
        "text": f"检查完成: {report['status']}",
        "cards": [
            build_gate_report_card(
                draft=name, status=report["status"], gates=report["gates"],
                plan_payload={"root": name},
            )
        ],
    }


@_guarded
async def draft_delete(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.draft.delete(§17.3 #11;L2 新收编):rmtree 连版本史——
    全包唯一 irreversible 面,副作用只经 platform.draft.delete tool
    (旧 web DELETE /api/lab/drafts/{name} 端点已收编到本技能)。"""
    name = str(args.get("name") or "")
    await _tool(ctx, "platform.draft.delete", {"name": name})
    return {"ok": True, "name": name}


@_guarded
async def skills_reload(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.skills.reload(§17.3 #11;L2 新收编):热重载生产 registry,
    副作用只经 platform.skills.reload tool(旧 web POST /api/skills/reload
    端点已收编到本技能)。"""
    r = await _tool(
        ctx, "platform.skills.reload", {"skill_set": str(args.get("skill_set") or "")}
    )
    return {"reloaded": r["reloaded"]}


@_guarded
async def run_launch(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.run.launch(M4a 发起面归一):app 内"再跑一次"。

    input 缺省 = 按 skill inputs schema 的 skeleton 骨架(用户可改,改后
    服务端按 schema 校验,不合 → 400);skill 缺省时从产物 meta 回推
    (run tab spawn 的 state.skill 可能为空)。lab-draft 的"试跑"同通道
    (overlay 解析序:草稿优先)。
    (不在 §17.4 的 L2 名单——名单收的是 run.stop/resume/rerun;
    launch 是发起面,与 POST /api/runs 同通道)
    """
    d = _deps(ctx)
    lab_store, manager = d["lab_store"], d["manager"]
    skill = str(args.get("skill") or "")
    run_id = str(args.get("run_id") or "")
    if not skill and run_id:
        meta = d["read_json"](Path(d["artifacts_root"]) / "runs" / run_id / "meta.json")
        skill = str((meta or {}).get("skill") or "")
    if not skill:
        raise PlatformActionError(400, "launch 需要 skill(state.skill 或产物 meta)")
    # inputs schema 解析(overlay:草稿 ∪ 生产;拿不到 schema 就只校验是 object)
    inputs_schema: dict[str, Any] = {}
    try:
        overlay = OverlaySkillRegistry(manager.shared_skills_registry(), lab_store)
        inputs_schema = overlay.get_by_name(skill).manifest.inputs or {}
    except Exception as e:  # noqa: BLE001 — 未知技能由 start_run 归 400(RunValidationError)
        _log.info("launch schema 解析失败,按无 schema 继续: %s", e)
    input_value = args.get("input")
    if input_value is None:
        input_value = skeleton_from_schema(inputs_schema)  # 缺省 = 骨架(§M4a 发起面)
    if inputs_schema:
        try:
            jsonschema.validate(input_value, inputs_schema)
        except jsonschema.ValidationError as e:
            raise PlatformActionError(400, f"input 不合 {skill} 的 inputs schema: {e.message}") from e
    try:
        new_id = await manager.start_run(skill, input_value)
    except Exception as e:  # RunValidationError 等归 400(与 POST /api/runs 同)
        raise PlatformActionError(400, str(e)) from e
    return {
        "ok": True,
        "text": f"已发起 {skill},新 run: {new_id[:8]}。",
        "run_id": new_id,
        "skill": skill,
        "status": "running",
        "cards": [
            build_table_card(
                title="新 run",
                columns=["run", "skill", "摘要"],
                rows=[[new_id[:8], skill, "已启动"]],
                ref={"kind": "run", "id": new_id},
            )
        ],
    }


# ── M5(docs/APP-MODEL.md §13.1):shell 的 endpoint 动作 ──


@_guarded
async def shell_theme_set(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.shell.theme.set(M5 §13.1):切主题(持久化偏好 → shell.state.theme)。"""
    return {"ok": True, "text": "", "state": {"theme": str(args.get("theme") or "")}}


@_guarded
async def shell_session_create(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.shell.session.create(M5 §13.1):新会话(app 孵化,与 POST /api/sessions 同源)。"""
    d = _deps(ctx)
    session = d["sessions"].create()
    d["instances"].register(
        kind="conversation", ref=session["id"], title=session["id"],
        state={"messages": [], "outbox": []}, created_by="",
    )
    return {"ok": True, "text": "", "session": session}


# ── D1(docs/DOC-EDITOR.md §2/§3):doc 的 endpoint 技能(L2:写面收进
# platform.doc.write tool,本层只编排) ──


@_guarded
async def doc_save(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.doc.save(§17.4 L2 名单):保存全文 = platform.doc.write tool
    (整文;.bak + notes.* 写回草稿 manifest.notes;WRITE/reversible)。"""
    r = await _tool(
        ctx,
        "platform.doc.write",
        {"name": str(args.get("name") or ""), "text": str(args.get("text") or "")},
    )
    return {
        "ok": True,
        "text": "已保存。",
        "state": {"dirty": False, "savedAt": r["savedAt"]},
    }


@_guarded
async def doc_snapshot(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.doc.snapshot:封存 versions/vNNN(内容 = 保存时全文,iterate 同语义)。"""
    d = _deps(ctx)
    doc_store = d["doc_store"]
    name = str(args.get("name") or "")
    # parent 不传 → store 按工作稿 baseVersion 解析(v1.8 树状版本:rewind 后分支)
    vid = doc_store.snapshot(name, source="manual")
    return {
        "ok": True,
        "text": f"已封存 {vid}。",
        "state": {"versions": [v["version"] for v in doc_store.list_versions(name)]},
    }


@_guarded
async def doc_rewind(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.doc.rewind:恢复某版本到全文(历史不动);``rolled_back_from``
    (P3 裁决 C2)给被回滚的版本标 rolledBackTo(不删)。"""
    d = _deps(ctx)
    name = str(args.get("name") or "")
    version = str(args.get("version") or "")
    doc = d["doc_store"].restore(
        name, version, rolled_back_from=str(args.get("rolled_back_from") or "") or None
    )
    return {
        "ok": True,
        "text": f"已恢复到 {version}(版本历史未动)。",
        "state": {"dirty": False, "text": doc["text"]},
    }


@_guarded
async def doc_export(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.doc.export:导出全文(.md 文本面;不进生产面,§6 接点纪律)。"""
    d = _deps(ctx)
    name = str(args.get("name") or "")
    doc = d["doc_store"].read(name)
    return {"ok": True, "text": doc["text"], "filename": f"{name}.md"}


@_guarded
async def doc_apply(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.doc.apply(§17.4 L2 名单;D2 人按才落):按锚点替换 =
    platform.doc.write tool(按段;越界/已变 → 400;WRITE/reversible)。"""
    r = await _tool(
        ctx,
        "platform.doc.write",
        {
            "name": str(args.get("name") or ""),
            "anchor": str(args.get("anchor") or ""),
            "replace_text": str(args.get("replace_text") or ""),
            "expected": args.get("expected"),
        },
    )
    return {
        "ok": True,
        "text": "已应用。",
        "state": {"dirty": False, "text": r["text"]},
    }


# ── M5:shell 的 local 技能(docs/APP-MODEL.md §13.1)──────────────
# local = 仅改 app.state(不出海);L1 起也经 kernel.run(§17.7:
# exec.mode 降级为元信息,调用面归一)——``_state``/``_instance_id``
# 由框架(管道)注入(§17.10:框架准备参数),写穿透经 instances.update_state。


@_guarded
async def shell_tab_open(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    d = _deps(ctx)
    state = args["_state"]
    tabs = state.setdefault("tabs", [])
    # conversation 是唯一且恒首:已存在则聚焦,不追加(conv 可关后由此补回)
    if args.get("kind") == "conversation":
        conv = next((t for t in tabs if t.get("kind") == "conversation"), None)
        if conv is None:
            tabs.insert(0, {
                "id": args.get("id", "conv"), "instance_id": args.get("instance_id", ""),
                "kind": "conversation", "ref": args.get("ref", "conv"),
                "title": args.get("title", ""),
            })
            state["active_tab"] = args.get("id", "conv")
        else:
            state["active_tab"] = conv["id"]
        d["instances"].update_state(args["_instance_id"], state)
        return {"ok": True}
    existing = next(
        (t for t in tabs
         if t.get("kind") != "conversation" and t.get("kind") == args.get("kind") and t.get("ref") == args.get("ref")),
        None,
    )
    if existing is None:
        tabs.append({
            "id": args.get("id", ""), "instance_id": args.get("instance_id", ""),
            "kind": args.get("kind", ""), "ref": args.get("ref", ""), "title": args.get("title", ""),
        })
        state["active_tab"] = args.get("id", "")
    else:
        state["active_tab"] = existing.get("id", "")  # kind+ref 去重聚焦(§6)
    d["instances"].update_state(args["_instance_id"], state)
    return {"ok": True}


@_guarded
async def shell_tab_focus(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    d = _deps(ctx)
    args["_state"]["active_tab"] = args.get("tab", "conv")
    d["instances"].update_state(args["_instance_id"], args["_state"])
    return {"ok": True}


@_guarded
async def shell_tab_close(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    d = _deps(ctx)
    state = args["_state"]
    tab_id = args.get("tab", "")
    tabs = [t for t in state.get("tabs", []) if t.get("id") != tab_id]
    state["tabs"] = tabs
    if state.get("active_tab") == tab_id:
        # 关闭回落:普通 tab 回 conv;conv 自己可关(有桌面)——回下一个 tab,空则桌面("")
        state["active_tab"] = next(
            (t["id"] for t in tabs if t.get("id") == "conv"),
            tabs[0]["id"] if tabs else "",
        )
    d["instances"].update_state(args["_instance_id"], state)
    return {"ok": True}


@_guarded
async def shell_layout_set(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    d = _deps(ctx)
    args["_state"].setdefault("layout", {})["icon_mode"] = bool(args.get("icon_mode", False))
    d["instances"].update_state(args["_instance_id"], args["_state"])
    return {"ok": True}


@_guarded
async def shell_layout_move_tab(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    d = _deps(ctx)
    state = args["_state"]
    tabs = [t for t in state.get("tabs", []) if t.get("id") != args.get("tab")]
    moving = next((t for t in state.get("tabs", []) if t.get("id") == args.get("tab")), None)
    if moving is not None:
        before = args.get("before") or ""
        if before == "__start__":
            tabs.insert(1 if tabs and tabs[0].get("id") == "conv" else 0, moving)  # conv 恒首
        elif before:
            idx = next((i for i, t in enumerate(tabs) if t.get("id") == before), len(tabs))
            tabs.insert(idx, moving)
        else:
            tabs.append(moving)  # before 缺省 = 移到末尾
        state["tabs"] = tabs
        d["instances"].update_state(args["_instance_id"], state)
    return {"ok": True}


@_guarded
async def shell_tab_minimize(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """最小化(M5 增补,桌面化):active_tab 置 "" —— 无激活 tab = 桌面主屏;
    tab 全保留(关闭≠销毁的另一面:最小化≠关闭)。"""
    d = _deps(ctx)
    args["_state"]["active_tab"] = ""
    d["instances"].update_state(args["_instance_id"], args["_state"])
    return {"ok": True}


@_guarded
async def shell_desktop_set(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """桌面开关(M5 增补):wallpaper 持久化进 shell.state.desktop(写穿透)。"""
    d = _deps(ctx)
    desktop = args["_state"].setdefault("desktop", {"pinned": [], "wallpaper": True})
    if "wallpaper" in args:
        desktop["wallpaper"] = bool(args["wallpaper"])
    d["instances"].update_state(args["_instance_id"], args["_state"])
    return {"ok": True}


@_guarded
async def doc_meta_set(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """doc.meta.set 的 local 技能(D1):白名单键合并进 instance.state
    (view/dirty;文本与版本不在此面——save/rewind 是 endpoint)。"""
    d = _deps(ctx)
    patch = {k: v for k, v in args.items() if k in ("view", "dirty")}
    args["_state"].update(patch)
    d["instances"].update_state(args["_instance_id"], args["_state"])
    return {"ok": True}


# ── conversation 的三个前端本地动作(§17.5 裁决 b:registry 里是真技能,
# 执行面在前端 JS;管道侧仍按"local 不出海"拒——M3.5 语义不变,见
# app.py local 分支的 400)——账面注册,handler 不会被服务端调用 ──


@_guarded
async def act_spawn(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.act.spawn:前端本地动作(打开卡面对应 app);服务端不执行。"""
    return {"ok": True}


@_guarded
async def act_pin(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.act.pin:前端本地动作(钉住);服务端不执行。"""
    return {"ok": True}


@_guarded
async def act_close(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """platform.act.close:前端本地动作(关闭卡);服务端不执行。"""
    return {"ok": True}

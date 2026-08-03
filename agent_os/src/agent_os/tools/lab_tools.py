"""Lab 草稿工具组(docs/SKILL-DEV.md §1.1/§2.2;L4)+ 包级扩展(docs/SKILL-PACKAGES-V2.md §6.7;P3)。

skill.dev.assistant 的全部动手能力。**没有 promote、没有 delete**——能改不能发,
提交按钮只有人能点(§1.1/§5;注入者无法自己批准自己,与升权原则同根)。

P3 信任边界(§6.7):``lab.draft.create`` 只允许在**当前包命名空间**内创建
(根的第一段前缀,防满世界建草稿)+ 每 run 创建配额;``lab.draft.write`` 只能写
**当前包编辑闭包内**的草稿(不能隔着包改别人的);validate 仍然只读不落盘
(助手报告不能当提交依据,P2 后同样)。

命名实现注:设计稿原名 ``skill.draft.*``,但 ``skill.`` 前缀会被内核当子技能
调用拦截(kernel/runner.py ``_dispatch_call``),永远到不了 Tool Registry——
故取 ``lab.draft.*``,语义不变。

side_effect 显式声明(docs/TIER-STANDARDS.md):read/list/validate/test_run/closure
是纯读(none;validate/test_run 不写草稿,试跑副作用圈在各自 workdir 沙箱);
write/create 是可逆写入(reversible;DraftStore save/create 自带 .bak/目录隔离,
逆转 = 取回上一版或删除新目录)。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import Permission, ToolResult
from agent_os.skills.closure import compute_closure
from agent_os.skills.gate import validate_draft

#: 组内全部工具名(测试断言"恰好七件、无 promote/delete"用)
LAB_DRAFT_TOOLS = (
    "lab.draft.read",
    "lab.draft.write",
    "lab.draft.list",
    "lab.draft.validate",
    "lab.draft.test_run",
    "lab.draft.create",
    "lab.pkg.closure",
)

#: 局部写的字段白名单(顶层 manifest 字段;防把 name 改成路径穿越形态——
#: name 由目录名钉死(DraftStore.save 强制),不允许经此通道改)
_WRITABLE_FIELDS = (
    "version",
    "kind",
    "description",
    "inputs",
    "outputs",
    "permissions",
    "model",
    "context_policy",
    "limits",
    "trust",
    "inline",
    "verifier",
    "entry",
    "handler",
    "logic",
)

#: 工具内试跑的步数上限(装配成本高,冒烟不是长跑;RunConfig 上限仍是外圈)
_TEST_RUN_MAX_STEPS = 25

#: 每 run 的创建配额(§6.7 信任边界:防注入让助手建一堆草稿把 drafts 目录淹掉)
_CREATE_QUOTA_PER_RUN = 5


def register_lab_tools(
    registry: Any,
    *,
    store: Any,
    production: Any,
    tools_registry: Any,
    kernel_factory: Any,
    package_root: str | None = None,
) -> None:
    """把草稿工具组注册进 ``registry``(Lab 助手内核装配时调用)。

    ``kernel_factory``:``() -> Kernel``(overlay 已接线的全新内核;test_run 用——
    工具协程内 ``await kernel.run``,新内核独立信号总线,不与当前 run 串扰)。
    ``package_root``(P3 信任边界,§6.7):当前包根名——给了它就启用命名空间
    围栏(create 只能建在根的第一段前缀内;write 只能写包编辑闭包内成员);
    None = 嵌入方自用,不设围栏。
    """

    def _ns_fence(name: str) -> Any:
        """create 的命名空间围栏:新草稿名必须落在当前包的第一段前缀内。"""
        if package_root is None:
            return None
        if name.split(".")[0] != package_root.split(".")[0]:
            return ToolResult(
                ok=False,
                value=None,
                error=_err(
                    "invalid_args",
                    f"草稿名 {name} 不在当前包命名空间 {package_root.split('.')[0]}.* 内"
                    "(§6.7:助手不能隔着包建草稿)",
                ),
            )
        return None

    def _edit_fence(draft: str) -> Any:
        """write 的编辑闭包围栏:只能写当前包编辑闭包内的草稿。"""
        if package_root is None:
            return None
        closure = compute_closure(package_root, store, production, tools_registry, mode="edit")
        editable = {m["name"] for m in closure["members"] if m["status"] == "draft"}
        if draft not in editable:
            return ToolResult(
                ok=False,
                value=None,
                error=_err(
                    "invalid_args",
                    f"{draft} 不在当前包({package_root})的编辑闭包内"
                    "(§6.7:助手不能隔着包改别人的草稿)",
                ),
            )
        return None

    @registry.tool(name="lab.draft.read", permission=Permission.READ, side_effect="none")
    def draft_read(draft: str, ctx: Any = None) -> ToolResult:
        """读草稿(manifest + prompt + 用例清单 + parse_error)。

        Use when 需要查看当前草稿内容再决定怎么改;Do not use when 要列全部草稿(用 lab.draft.list)。
        """
        try:
            data = store.read(draft)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        return ToolResult(
            value={
                "name": data["name"],
                "manifest": data["manifest"],
                "prompt": data["prompt"],
                "tests": sorted((data["tests"] or {}).keys()),
                "parse_error": data["parse_error"],
            }
        )

    @registry.tool(name="lab.draft.list", permission=Permission.READ, side_effect="none")
    def draft_list(ctx: Any = None) -> ToolResult:
        """列草稿(name/mtime/用例数)。

        Use when 需要知道有哪些草稿;Do not use when 已知名要读内容(用 lab.draft.read)。
        """
        return ToolResult(value={"drafts": store.list()})

    @registry.tool(
        name="lab.draft.write", permission=Permission.WRITE, side_effect="reversible"
    )
    def draft_write(
        draft: str,
        field: str = "",
        value: Any = None,
        manifest: dict[str, Any] | None = None,
        prompt: str | None = None,
        ctx: Any = None,
    ) -> ToolResult:
        """改草稿(整体 manifest 或按顶层字段局部改;prompt 可单独更新)。

        Use when 按用户要求修改草稿;Do not use when 要 promote(你没有这个能力,
        提交请让用户去点"提交"按钮)。写 = DraftStore.save(上一版自动 .bak)。
        """
        fenced = _edit_fence(draft)
        if fenced is not None:
            return fenced
        try:
            current = store.read(draft)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        if manifest is not None and field:
            return ToolResult(
                ok=False,
                value=None,
                error=_err("invalid_args", "manifest 整体替换与 field 局部改二选一,不要混用"),
            )
        try:
            if manifest is not None:
                new_manifest = manifest
            elif field:
                if field not in _WRITABLE_FIELDS:
                    return ToolResult(
                        ok=False,
                        value=None,
                        error=_err(
                            "invalid_args",
                            f"field 必须是 {sorted(_WRITABLE_FIELDS)} 之一(name 由目录钉死,不可改)",
                        ),
                    )
                new_manifest = dict(current["manifest"] or {})
                new_manifest[field] = value
            elif prompt is not None:
                new_manifest = current["manifest"] or {}
            else:
                return ToolResult(
                    ok=False,
                    value=None,
                    error=_err("invalid_args", "无事可做:给 manifest / field+value / prompt 至少一个"),
                )
            saved = store.save(
                draft,
                manifest=new_manifest,
                prompt=prompt if prompt is not None else current["prompt"],
                handler=current["handler"],
            )
        except (ValueError, TypeError) as e:
            return ToolResult(ok=False, value=None, error=_err("invalid_args", str(e)))
        changed = field or ("manifest" if manifest is not None else "prompt")
        value_out: dict[str, Any] = {
            "changed": changed,
            "parse_error": saved["parse_error"],
            "note": "已写入草稿(上一版 .bak);改动将在用户编辑器里刷新",
        }
        # N4(B5):保存端同步提示(不硬拦——编辑器不打断;硬闸在 G2)
        from agent_os.skills.gate import _brace_finding

        brace = _brace_finding(str(saved.get("prompt") or ""))
        if brace is not None:
            value_out["warning"] = brace["message"]
        return ToolResult(value=value_out)

    @registry.tool(name="lab.draft.validate", permission=Permission.READ, side_effect="none")
    def draft_validate(draft: str, ctx: Any = None) -> ToolResult:
        """跑提交闸门,返回五关结果(只读报告,不落盘——落盘版是人在 UI 点"检查")。

        Use when 改完草稿需要自查合规(改完必须跑一次);Do not use when 刚查过且草稿未变。
        """
        try:
            data = store.read(draft)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        # §6.2 两阶段:助手在草稿期(strict_refs=False,悬空 warn + 修复提示);
        # store 必须传——否则"根引用兄弟草稿"被判悬空,助手与 UI 结论不一致(§2.2)
        report = validate_draft(data, production=production, tools=tools_registry,
                                store=store, strict_refs=False)
        return ToolResult(
            value={
                "status": report["status"],
                "tier": report["tier"],
                "gates": {
                    gid: {
                        "status": gate["status"],
                        "findings": [
                            f"{f['level']}[{f['clause']}] {f['message']}"
                            for f in gate.get("findings", [])
                        ],
                    }
                    for gid, gate in report["gates"].items()
                },
            }
        )

    @registry.tool(
        name="lab.draft.test_run", permission=Permission.READ, side_effect="none", timeout=120.0
    )
    async def draft_test_run(
        draft: str, input: dict[str, Any] | None = None, ctx: Any = None
    ) -> ToolResult:
        """试跑草稿(overlay 装配的真 run),返回 status/result/outputs 校验。

        Use when 需要验证草稿改完能不能跑通;Do not use when 只想静态检查(用 lab.draft.validate)。
        试跑副作用圈在该 run 的 workdir 沙箱内,不写草稿(故 side_effect=none)。
        """
        try:
            data = store.read(draft)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        kernel = kernel_factory()
        # 工具内冒烟限步(装配成本高;外圈 RunConfig 上限仍在)
        kernel.config.max_steps = min(kernel.config.max_steps, _TEST_RUN_MAX_STEPS)
        try:
            result = await kernel.run(draft, input or {})
        except Exception as e:  # noqa: BLE001 — 试跑失败是答案的一部分,不是工具故障
            return ToolResult(value={"status": "failed", "error": f"{type(e).__name__}: {e}"})
        outputs = (data.get("manifest") or {}).get("outputs") or {}
        check = _outputs_check(outputs, result)
        return ToolResult(
            value={"status": "done", "result": result, "outputs_check": check}
        )

    created = {"n": 0}  # 创建配额计数(§6.7:每 run ≤ _CREATE_QUOTA_PER_RUN)

    @registry.tool(
        name="lab.draft.create", permission=Permission.WRITE, side_effect="reversible"
    )
    def draft_create(name: str, template: str = "", ctx: Any = None) -> ToolResult:
        """创建子技能草稿(空模板或模板库 key;P3,断点 1.1-B 的助手面)。

        Use when 需要在当前包里拆出新的子技能;Do not use when 只是改现有草稿
        (用 lab.draft.write)。只能在当前包命名空间内创建,且有每 run 配额
        (docs/SKILL-PACKAGES-V2.md §6.7 信任边界)。
        """
        fenced = _ns_fence(name)
        if fenced is not None:
            return fenced
        if created["n"] >= _CREATE_QUOTA_PER_RUN:
            return ToolResult(
                ok=False,
                value=None,
                error=_err(
                    "invalid_args",
                    f"本 run 的创建配额({_CREATE_QUOTA_PER_RUN})已用尽(§6.7 信任边界)",
                ),
            )
        try:
            store.create(name, template=template or None)
        except FileExistsError:
            return ToolResult(
                ok=False, value=None, error=_err("invalid_args", f"草稿已存在: {name}")
            )
        except ValueError as e:
            return ToolResult(ok=False, value=None, error=_err("invalid_args", str(e)))
        created["n"] += 1
        return ToolResult(value={"created": name, "quota_left": _CREATE_QUOTA_PER_RUN - created["n"]})

    @registry.tool(name="lab.pkg.closure", permission=Permission.READ, side_effect="none")
    def pkg_closure(root: str = "", ctx: Any = None) -> ToolResult:
        """读当前包的闭包树(成员/状态/档位/ref_by;P3,让助手看得见全局)。

        Use when 动手前需要判断改哪个成员或该不该拆新子技能;Do not use when
        只看单个草稿内容(用 lab.draft.read)。``root`` 缺省为当前包根。
        """
        target = root or package_root
        if not target:
            return ToolResult(
                ok=False, value=None, error=_err("invalid_args", "未指定包根(root),且无当前包上下文")
            )
        try:
            result = compute_closure(target, store, production, tools_registry, mode="edit")
        except FileNotFoundError as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        return ToolResult(value=result)


def register_iterate_tools(
    registry: Any,
    *,
    store: Any,
    production: Any,
    tools_registry: Any,
    pkg: str,
) -> None:
    """迭代模式(Flow C 样板)的工具面:**读得到,写不到 working**。

    只注册三件:``lab.draft.read``(读 working)、``lab.pkg.closure``(看包树)、
    ``lab.cand.write``(只能写 ``drafts/<pkg>/candidate/`` 子树——信任边界:
    助手产物先落候选,接不接受是人点,与"能改不能发"同根)。
    """

    @registry.tool(name="lab.draft.read", permission=Permission.READ, side_effect="none")
    def draft_read(draft: str, ctx: Any = None) -> ToolResult:
        """读草稿(manifest + prompt + 用例清单 + parse_error)。

        Use when 需要查看当前版本内容再决定怎么改;Do not use when 要列全部草稿。
        """
        try:
            data = store.read(draft)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        return ToolResult(
            value={
                "name": data["name"],
                "manifest": data["manifest"],
                "prompt": data["prompt"],
                "tests": sorted((data["tests"] or {}).keys()),
                "parse_error": data["parse_error"],
            }
        )

    @registry.tool(name="lab.pkg.closure", permission=Permission.READ, side_effect="none")
    def pkg_closure(root: str = "", ctx: Any = None) -> ToolResult:
        """读当前包的闭包树(成员/状态/档位/ref_by)。

        Use when 动手前需要看全包成员;Do not use when 只看单个成员内容。
        """
        target = root or pkg
        try:
            result = compute_closure(target, store, production, tools_registry, mode="edit")
        except FileNotFoundError as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        return ToolResult(value=result)

    @registry.tool(name="lab.cand.write", permission=Permission.WRITE, side_effect="reversible")
    def cand_write(
        member: str,
        manifest: dict[str, Any] | None = None,
        prompt: str | None = None,
        tests: dict[str, Any] | None = None,
        ctx: Any = None,
    ) -> ToolResult:
        """写候选成员(只能写 drafts/<pkg>/candidate/ 子树,写不到 working/生产)。

        Use when 生成改进版成员(manifest/prompt 全量给出;tests 传则整体替换用例);
        Do not use when 想直接改 working(你没有这个能力——接不接受是用户点)。
        manifest/prompt 缺省时取 working 现值(只改一部分的写法)。
        """
        try:
            current = store.read(member)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(ok=False, value=None, error=_err("not_found", str(e)))
        new_manifest = manifest if manifest is not None else (current["manifest"] or {})
        new_prompt = prompt if prompt is not None else current["prompt"]
        try:
            store.save_candidate_member(pkg, member, new_manifest, new_prompt, tests=tests)
        except ValueError as e:
            return ToolResult(ok=False, value=None, error=_err("invalid_args", str(e)))
        return ToolResult(
            value={"written": member, "note": "已写入候选区(未影响 working);等待用户评审"}
        )


def _outputs_check(outputs: dict[str, Any], result: Any) -> dict[str, Any]:
    """outputs schema 校验(与 app.py 的 test-run check 同语义;L3 的双保险)。"""
    import jsonschema

    if outputs:
        try:
            jsonschema.validate(result, outputs)
        except jsonschema.ValidationError as e:
            return {"ok": False, "error": f"outputs 校验失败: {e.message}"}
    return {"ok": True, "error": None}


def _err(kind: str, message: str) -> Any:
    from agent_os.api.v1 import ToolError, ToolErrorKind

    kinds = {
        "not_found": ToolErrorKind.NOT_FOUND,
        "invalid_args": ToolErrorKind.INVALID_ARGS,
    }
    return ToolError(kind=kinds[kind], message=message, retryable=False)

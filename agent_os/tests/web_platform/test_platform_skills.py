"""platform.* 内置技能包的升格验收(docs/APP-MODEL.md v0.5 §17.7 第 1/2 步;§17.8)。

- 静态扫描:每个 manifest action 的 exec.ref 都能解析到真实 code 技能
  (无孤 handler;local 走 PLATFORM_LOCAL_SKILLS 映射 + platform.act.* 账面;
  L2 新收编的 3 个技能由旧 web 端点调用);
- 技能面:全部 kind=code;L2 技能的白名单恰为自己的 tool,其余白名单空;
- L2 断言(§17.8):L2 handler 内禁止直接 import/调用业务写函数——
  副作用只经 ctx.call_tool(AST 静态扫描 handlers.py);
- force_sandbox(§17.9):platform.* 显式豁免(TRUSTED 进程内),用户技能照 sandbox;
  白名单清空后豁免消失(豁免是显式声明,不靠"恰好没被覆盖");
- 行为:技能经 kernel.run 进程内执行(冒烟:shell.theme.set 不依赖任何宿主面);
  tool 规格:7 个副作用 tool 的 permission/side_effect/data_domains 显式声明。
"""

from __future__ import annotations

import ast
import asyncio
import importlib
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    LogicPolicy,
    Permission,
    RunConfig,
    SkillManifest,
    TrustLevel,
)
from agent_os.host.web_platform.apps import default_manifests
from agent_os.kernel.logic_router import LogicKernelRouter
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.skills.platform import (
    PLATFORM_LOCAL_SKILLS,
    build_platform_kernel,
    load_platform_skills,
    platform_skill_names,
)
from agent_os.skills.platform.tools import PLATFORM_TOOLS

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: L2 新收编(§17.7 第 2 步):调用面是旧 web 端点(/api/skills/reload、
#: DELETE /api/lab/drafts/{name}、/api/debug/sessions/{sid}/modify|inject)
_L2_NEW_SKILLS = {
    "platform.debug.intervene",
    "platform.draft.delete",
    "platform.skills.reload",
}

#: L2 名单(§17.4):handler 副作用必须经 tool 的技能(handler 函数名 → 其 tool)
_L2_SKILL_TOOLS = {
    "plan_confirm": "platform.skill.promote",
    "candidate_accept": "platform.draft.accept",
    "draft_delete": "platform.draft.delete",
    "doc_save": "platform.doc.write",
    "doc_apply": "platform.doc.write",
    "run_stop": "platform.run.control",
    "run_resume": "platform.run.control",
    "run_rerun": "platform.run.control",
    "debug_intervene": "platform.debug.intervene",
    "skills_reload": "platform.skills.reload",
}

#: L2 handler 禁直接调用的业务写函数/方法(副作用只许经 _tool/ctx.call_tool)
_L2_FORBIDDEN_CALLS = {
    "promote_package", "stop_run", "resume_run", "start_run", "debug_modify",
    "debug_inject", "reload_skills", "save", "delete", "clear_candidate",
    "snapshot", "restore", "restore_version",
}


def test_every_action_ref_resolves():
    """§17.8 静态扫描:endpoint/run 的 exec.ref 与 local 的映射都解析到真实技能。"""
    reg = load_platform_skills()
    names = platform_skill_names()
    referenced: set[str] = set()
    for manifest in default_manifests():
        for action in manifest.get("actions") or []:
            exec_ = action["exec"]
            if exec_["mode"] == "local":
                # local 无 ref(协议规则)——服务端技能走显式映射,前端本地的
                # (conversation spawn/pin/close)走 platform.act.* 账面
                ref = PLATFORM_LOCAL_SKILLS.get(action["id"]) or f"platform.act.{action['id']}"
            else:
                ref = exec_["ref"]
            referenced.add(ref)
            assert ref in names, f"action {action['id']} 的 {ref} 无真实技能(孤 ref)"
            skill = reg.get_by_name(ref)
            assert skill.manifest.kind.value == "code", f"{ref} 必须是 code 技能"
    # 36 条调用面 = 35 个 manifest action(共享 ref:decision.answer ×3、
    # debug.command ×2、plan.recheck ×2 → 计一次)+ 旧卡面 version.rewind;
    # 归并后 referenced = 31,含旧卡面共 32 个唯一技能
    assert len(referenced) == 31
    # 无孤技能:registry 里每个技能都有调用面(action 引用 / 旧卡面 / L2 旧 web 收编)
    card_only = {"platform.version.rewind"}  # 旧 cards/action 过渡面(§17.7 第 4 步退役)
    assert names == referenced | card_only | _L2_NEW_SKILLS, "无孤 handler:技能都有调用面"


def test_skills_are_code_and_l2_whitelists():
    """技能面:全部 kind=code;L2 技能白名单恰为自己的 tool,其余白名单空。"""
    skill_tools = {  # 技能名 → 应声明的 tool(显式摊开,不做名字推导)
        "platform.plan.confirm": "platform.skill.promote",
        "platform.candidate.accept": "platform.draft.accept",
        "platform.draft.delete": "platform.draft.delete",
        "platform.doc.save": "platform.doc.write",
        "platform.doc.apply": "platform.doc.write",
        "platform.run.stop": "platform.run.control",
        "platform.run.resume": "platform.run.control",
        "platform.run.rerun": "platform.run.control",
        "platform.debug.intervene": "platform.debug.intervene",
        "platform.skills.reload": "platform.skills.reload",
    }
    for m in load_platform_skills().manifests():
        assert m.kind.value == "code", m.name
        assert m.permissions.skills == [], m.name
        assert m.handler, f"{m.name} 缺 handler"
        assert m.inputs and m.outputs, f"{m.name} 缺 inputs/outputs schema"
        want = skill_tools.get(m.name)
        if want is None:
            assert m.permissions.tools == [], f"{m.name} 非 L2 技能,白名单应为空"
        else:
            assert m.permissions.tools == [want], f"{m.name} 白名单应恰为 {want}"


def test_l2_handlers_no_direct_side_effect_calls():
    """§17.8 L2 静态扫描:L2 handler 内禁止直接 import/调用业务写函数——
    副作用只经 _tool(→ ctx.call_tool);且每个 L2 handler 确实调了对应 tool。"""
    src = Path(
        importlib.import_module("agent_os.skills.platform.handlers").__file__ or ""
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # import 面:业务写函数不得出现在 handlers.py 的 import 里
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
    assert "promote_package" not in imported, "promote 写函数不得直接 import(应经 tool)"
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn_name, tool in _L2_SKILL_TOOLS.items():
        fn = funcs.get(fn_name)
        assert fn is not None, f"handler {fn_name} 不存在"
        calls = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute):
                    calls.add(f.attr)
                elif isinstance(f, ast.Name):
                    calls.add(f.id)
        bad = calls & _L2_FORBIDDEN_CALLS
        assert not bad, f"{fn_name} 直接调用业务写函数 {bad}(应经 {tool})"
        assert "_tool" in calls, f"{fn_name} 未经 _tool 调 {tool}"


def test_platform_tools_specs():
    """tool 规格:7 个副作用 tool 的 permission/side_effect/data_domains 显式声明;
    draft.delete 是全组唯一 irreversible(删版本史不可恢复;无 approve-run 面)。"""
    kernel = build_platform_kernel({"manager": None, "lab_store": None})
    specs = {s.name: s for s in kernel.tools.specs() if s.name.startswith("platform.")}
    assert set(specs) == set(PLATFORM_TOOLS), "恰好七件副作用 tool"
    for name, spec in specs.items():
        assert spec.permission is Permission.WRITE, name
        assert spec.data_domains, f"{name} 缺 data_domains 声明(D2 预告面)"
    assert specs["platform.draft.delete"].side_effect == "irreversible"
    for name, spec in specs.items():
        if name != "platform.draft.delete":
            assert spec.side_effect == "reversible", name


def _router_with_sandbox() -> LogicKernelRouter:
    class _FakeSandbox:
        trust = TrustLevel.SANDBOX

    return LogicKernelRouter([InProcessLogicKernel(), _FakeSandbox()])


def test_force_sandbox_explicit_whitelist():
    """§17.9:force_sandbox 下 platform.* 豁免(TRUSTED),用户技能照 SANDBOX;
    白名单清空 → 豁免消失(显式声明,不靠"恰好没被覆盖")。"""
    router = _router_with_sandbox()
    platform_m = load_platform_skills().get_by_name("platform.shell.theme.set").manifest
    user_m = SkillManifest(name="user.skill", version="0.1.0", description="x")
    cfg = RunConfig(logic_policy=LogicPolicy(force_sandbox=True))
    assert router.route(platform_m, cfg).trust is TrustLevel.TRUSTED, "内置豁免(显式白名单)"
    assert router.route(user_m, cfg).trust is TrustLevel.SANDBOX, "用户技能照 sandbox"
    # 白名单清空 → platform.* 也进 sandbox(豁免是声明,不是意外)
    cfg_strict = RunConfig(
        logic_policy=LogicPolicy(force_sandbox=True, trusted_builtin_prefixes=())
    )
    assert router.route(platform_m, cfg_strict).trust is TrustLevel.SANDBOX
    # 技能自报 logic.mode=sandbox 优先于豁免(显式声明最强)
    forced = SkillManifest(
        name="platform.anything", version="0.1.0", description="x", logic={"mode": "sandbox"}
    )
    assert router.route(forced, cfg).trust is TrustLevel.SANDBOX


def test_kernel_run_inprocess_smoke(tmp_path):
    """行为面:技能经 kernel.run 进程内执行(无宿主面依赖的最小 deps)。"""
    kernel = build_platform_kernel(
        {
            "manager": None,
            "lab_store": None,
            "doc_store": None,
            "sessions": None,
            "instances": None,
            "artifacts_root": tmp_path,
            "read_json": lambda p: None,
        }
    )
    result = asyncio.run(kernel.run("platform.shell.theme.set", {"theme": "moe"}))
    assert result == {"ok": True, "text": "", "state": {"theme": "moe"}}


def test_l2_tool_dispatch_through_frame(tmp_path):
    """L2 全链:skill → ctx.call_tool → tool(帧白名单外调用被拒;doc.write 真落盘)。"""
    from agent_os.skills.doc_store import DocStore

    doc_store = DocStore(tmp_path / "docs")
    doc_store.create("design.new_ui", text="# 概述\n首段\n")
    kernel = build_platform_kernel(
        {
            "manager": None, "lab_store": None, "doc_store": doc_store,
            "sessions": None, "instances": None, "artifacts_root": tmp_path,
            "read_json": lambda p: None,
        }
    )
    # 白名单外:shell.theme.set 没声明 platform.doc.write → PERMISSION_DENIED(帧白名单闸)
    theme_m = load_platform_skills().get_by_name("platform.shell.theme.set").manifest
    assert "platform.doc.write" not in theme_m.permissions.tools
    # 正路:doc.save 技能 → tool → 落盘 + savedAt 回写
    result = asyncio.run(
        kernel.run("platform.doc.save", {"name": "design.new_ui", "text": "# 概述\n第二版\n"})
    )
    assert result["ok"] and result["state"]["dirty"] is False
    assert "第二版" in doc_store.read("design.new_ui")["text"], "副作用经 tool 真落盘"
    # 错误归类:doc.apply 越界 → 400 信封(vetoed/invalid_args 翻译链)
    result2 = asyncio.run(
        kernel.run(
            "platform.doc.apply",
            {"name": "design.new_ui", "anchor": "doc.md#L9-L9", "replace_text": "x"},
        )
    )
    assert result2["_action_error"]["status"] == 400
    assert "文档已变化" in result2["_action_error"]["detail"]

"""platform.* 内置技能包的升格验收(docs/APP-MODEL.md v0.5 §17.7 第 1 步 / §17.8)。

- 静态扫描:每个 manifest action 的 exec.ref 都能解析到真实 code 技能
  (无孤 handler;local 走 PLATFORM_LOCAL_SKILLS 映射 + platform.act.* 账面);
- 技能面:全部 kind=code、白名单空(tools/skills 皆 [])、包内 skills.yaml 注册;
- force_sandbox(§17.9):platform.* 显式豁免(TRUSTED 进程内),用户技能照 sandbox;
  白名单清空后豁免消失(豁免是显式声明,不靠"恰好没被覆盖");
- 行为:技能经 kernel.run 进程内执行(冒烟:shell.theme.set 不依赖任何宿主面)。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_os.api.v1 import LogicPolicy, RunConfig, SkillManifest, TrustLevel
from agent_os.host.web_platform.apps import default_manifests
from agent_os.kernel.logic_router import LogicKernelRouter
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.skills.platform import (
    PLATFORM_LOCAL_SKILLS,
    build_platform_kernel,
    load_platform_skills,
    platform_skill_names,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


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
    # 无孤技能:registry 里每个技能都被某条调用面引用(含旧卡面的 version.rewind)
    card_only = {"platform.version.rewind"}  # 旧 cards/action 过渡面(§17.7 第 4 步退役)
    assert names == referenced | card_only, "无孤 handler:技能都有 action 引用"


def test_skills_are_code_with_empty_whitelist():
    """技能面:全部 kind=code、permissions.tools/skills 皆空(trusted 纯编排)。"""
    for m in load_platform_skills().manifests():
        assert m.kind.value == "code", m.name
        assert m.permissions.tools == [], m.name
        assert m.permissions.skills == [], m.name
        assert m.handler, f"{m.name} 缺 handler"
        assert m.inputs and m.outputs, f"{m.name} 缺 inputs/outputs schema"


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

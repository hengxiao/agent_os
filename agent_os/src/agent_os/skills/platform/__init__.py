"""platform.* 内置动作技能包(docs/APP-MODEL.md v0.5 §17.7 升格序第 1 步;L1)。

action 管道的全部 36 条调用面(35 个 manifest action + 旧卡面 version.rewind)
归并为 32 个唯一 code 技能,注册面 = 包内 ``skills.yaml``(LocalFileSkillRegistry
目录源);全部 trusted 档(进程内 Logic Kernel;force_sandbox 下经
``logic_policy.trusted_builtin_prefixes`` 显式豁免,§17.9)。

L1 只做机械搬运:handler 本体 = 原 ``_act_*``/``_mut_*`` 薄函数(handlers.py),
副作用拆 tool 是 L2(§17.4,本期不做;候选已在 handler 注释标注)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_os.api.v1 import RunConfig
from agent_os.kernel.logic_router import LogicKernelRouter
from agent_os.kernel.runner import Kernel
from agent_os.kernel.signals import InProcessSignalBus
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.skills.local_file import LocalFileSkillRegistry

#: 包内 skills.yaml 所在目录(LocalFileSkillRegistry 的目录源)
PLATFORM_PACKAGE_DIR = Path(__file__).resolve().parent

#: local 动作 → 技能名(服务端有执行面的 8 个;管道 local 分支据此分发)。
#: conversation 的 spawn/pin/close 不在此表——它们是前端本地动作,调到管道
#: 仍拒(M3.5 语义;registry 里有 platform.act.* 同名技能是 §17.5(b) 的账面符合)
PLATFORM_LOCAL_SKILLS = {
    "shell.tab.open": "platform.shell.tab.open",
    "shell.tab.focus": "platform.shell.tab.focus",
    "shell.tab.close": "platform.shell.tab.close",
    "shell.layout.set": "platform.shell.layout.set",
    "shell.layout.move_tab": "platform.shell.layout.move_tab",
    "shell.tab.minimize": "platform.shell.tab.minimize",
    "shell.desktop.set": "platform.shell.desktop.set",
    "meta.set": "platform.doc.meta.set",
}


def load_platform_skills() -> LocalFileSkillRegistry:
    """加载包内 skills.yaml(32 个 platform.* code 技能;skills.yaml 注册面)。"""
    return LocalFileSkillRegistry(str(PLATFORM_PACKAGE_DIR))


def platform_skill_names() -> set[str]:
    """全部 platform.* 技能名(AppRegistry 的 known_skills 与 §17.8 静态扫描用)。"""
    return {m.name for m in load_platform_skills().manifests()}


def build_platform_kernel(deps: dict[str, Any]) -> Kernel:
    """装配 platform 动作内核(每 app 一颗;进程内、不落 run 产物——run_iterate 先例)。

    最小面:code 技能不过 LLM/ContextManager,只需要 skills + logic 路由 +
    信号总线;``deps``(manager/lab_store/doc_store/sessions/instances/
    artifacts_root/read_json)挂在内核对象上,handler 经 ``ctx._kernel.
    platform_deps`` 取用(宿主注入面,§17.10:框架准备参数)。
    """
    kernel = Kernel(
        config=RunConfig(),
        skills=load_platform_skills(),
        logic=LogicKernelRouter([InProcessLogicKernel()]),
        signals=InProcessSignalBus(),
    )
    kernel.platform_deps = deps  # 宿主注入面(handlers._deps 的唯一取用处)
    return kernel

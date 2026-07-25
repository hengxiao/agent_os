"""M2 锚点测试:code 技能经 Logic Kernel 执行与编排(DESIGN.md §3.1/§6/§9)。

固定约定:

- runner 的 ``kind == CODE`` 分支构造 ExecRequest 交 Logic Kernel;
  默认 TRUSTED(InProcessLogicKernel),``manifest.logic.mode == "sandbox"`` 或
  ``RunConfig.logic_policy.force_sandbox`` 时走 SANDBOX;
- TRUSTED 模式注入 ``LogicContext``:``invoke(skill, input)`` 返回子帧结果(失败抛
  AgentOSError 子类);``call_tool(tool, args)`` 返回 ``{"ok","value","error"}`` 字典;
  两者都回到内核分发路径(白名单、信号、记账);
- code 技能返回值经 outputs schema 校验,失败抛 ``OutputValidationError``;
- 技能文件循环依赖在**加载期**报 ``SkillLoadError``;
- ``LocalFileSkillRegistry.reload()`` 按 mtime 检查并重载,新帧用新版;
- code 技能帧执行前后发 ``pre:logic.exec``/``post:logic.exec``,payload 含 ``trust``。
"""

from __future__ import annotations

import asyncio
import os
import textwrap

import pytest

from agent_os.api.v1 import (
    POST_LOGIC_EXEC,
    Permission,
    RunConfig,
    ToolPolicy,
)
from agent_os.kernel.errors import AgentOSError, OutputValidationError, SkillLoadError
from agent_os.skills.local_file import LocalFileSkillRegistry
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import assemble

# fib 技能(与 skills/skills.yaml 中一致;code 技能测试的依赖项)
FIB_SKILL = """
  - name: fib
    version: 1.0.0
    kind: prompt
    description: 生成前 n 个菲波拉契数。
    inputs:
      type: object
      properties: { n: { type: integer, minimum: 1 } }
      required: [n]
    outputs:
      type: object
      properties:
        seq: { type: array, items: { type: integer } }
      required: [seq]
    permissions:
      tools: [python_exec]
      skills: [fib]
    model: { prefer: ["mock/fib"] }
    limits: { max_steps: 8, timeout: 60 }
    prompt: |
      你是菲波拉契数列生成器。
"""

CODE_SKILLS = """
  - name: fib_pair
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:fib_pair
    inputs:
      type: object
      properties: { a: { type: integer }, b: { type: integer } }
      required: [a, b]
    outputs:
      type: object
      properties: { combined: { type: array, items: { type: integer } } }
      required: [combined]
    permissions: { tools: [], skills: [fib] }
  - name: double_it
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:double_it
    inputs:
      type: object
      properties: { x: { type: integer } }
      required: [x]
    outputs:
      type: object
      properties: { doubled: { type: integer } }
      required: [doubled]
    permissions: { tools: [python_exec], skills: [] }
  - name: pure_add
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:pure_add
    inputs:
      type: object
      properties: { a: { type: integer }, b: { type: integer } }
      required: [a, b]
    outputs:
      type: object
      properties: { sum: { type: integer } }
      required: [sum]
    permissions: { tools: [], skills: [] }
  - name: naughty
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:naughty
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { never: { type: boolean } }
    permissions: { tools: [], skills: [] }
  - name: bad_output
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:bad_output
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { combined: { type: array } }
      required: [combined]
    permissions: { tools: [], skills: [] }
"""


def _write_yaml(tmp_path, body: str) -> str:
    path = tmp_path / "skills.yaml"
    path.write_text("skills:\n" + textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _build(skills_yaml: str, *, force_sandbox: bool = False):
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    config.logic_policy.force_sandbox = force_sandbox
    return assemble(config, fib_brain, skills_yaml)


def test_code_skill_orchestrates_prompt_skills(tmp_path):
    """code 技能经 ctx.invoke 编排两个 LLM 技能帧(§9.3 编排者)。"""
    kernel = _build(_write_yaml(tmp_path, FIB_SKILL + CODE_SKILLS))
    result = asyncio.run(kernel.run("fib_pair", {"a": 3, "b": 2}))
    assert result == {"combined": [0, 1, 1, 0, 1]}


def test_code_skill_calls_tool(tmp_path):
    """code 技能经 ctx.call_tool 调 python_exec(沙箱)计算。"""
    kernel = _build(_write_yaml(tmp_path, FIB_SKILL + CODE_SKILLS))
    result = asyncio.run(kernel.run("double_it", {"x": 21}))
    assert result == {"doubled": 42}


def test_logic_exec_signals_carry_trust(tmp_path):
    """code 技能帧执行发 pre/post:logic.exec,payload 标 trust。"""
    kernel = _build(_write_yaml(tmp_path, FIB_SKILL + CODE_SKILLS))
    seen = []

    async def rec(sig):
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    asyncio.run(kernel.run("pure_add", {"a": 1, "b": 2}))
    trusts = [s.payload.get("trust") for s in seen if s.name == POST_LOGIC_EXEC]
    assert "trusted" in trusts


def test_invoke_permission_denied(tmp_path):
    """code 技能调用白名单外子技能 → 失败上抛(权限拒绝)。"""
    kernel = _build(_write_yaml(tmp_path, FIB_SKILL + CODE_SKILLS))
    with pytest.raises(AgentOSError, match="白名单"):
        asyncio.run(kernel.run("naughty", {}))


def test_code_skill_output_validation(tmp_path):
    """code 技能返回值不合 outputs schema → OutputValidationError。"""
    kernel = _build(_write_yaml(tmp_path, FIB_SKILL + CODE_SKILLS))
    with pytest.raises(OutputValidationError):
        asyncio.run(kernel.run("bad_output", {}))


def test_force_sandbox_routes_pure_code_skill(tmp_path):
    """force_sandbox=True 时纯计算 code 技能在沙箱子进程中执行(§9.2)。"""
    kernel = _build(_write_yaml(tmp_path, FIB_SKILL + CODE_SKILLS), force_sandbox=True)
    seen = []

    async def rec(sig):
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    result = asyncio.run(kernel.run("pure_add", {"a": 40, "b": 2}))
    assert result == {"sum": 42}
    trusts = [s.payload.get("trust") for s in seen if s.name == POST_LOGIC_EXEC]
    assert "sandbox" in trusts


def test_circular_dependency_rejected_at_load(tmp_path):
    """加载期:依赖图成环 → SkillLoadError(§6.1)。"""
    circular = """
      - name: a
        version: 1.0.0
        kind: prompt
        inputs: { type: object, properties: {} }
        permissions: { tools: [], skills: [b] }
        prompt: a
      - name: b
        version: 1.0.0
        kind: prompt
        inputs: { type: object, properties: {} }
        permissions: { tools: [], skills: [a] }
        prompt: b
    """
    with pytest.raises(SkillLoadError):
        LocalFileSkillRegistry(_write_yaml(tmp_path, circular))


def test_reload_picks_up_new_version(tmp_path):
    """热重载:手动 reload()(mtime 检查)后新帧用新版(§6.1/§6.3)。"""
    v1 = """
      - name: greeter
        version: 1.0.0
        kind: prompt
        inputs: { type: object, properties: {} }
        permissions: { tools: [], skills: [] }
        prompt: 你好 v1
    """
    path = _write_yaml(tmp_path, v1)
    reg = LocalFileSkillRegistry(path)
    assert reg.get_by_name("greeter").manifest.version == "1.0.0"

    v2 = v1.replace('version: 1.0.0', 'version: 1.1.0').replace("你好 v1", "你好 v2")
    with open(path, "w", encoding="utf-8") as f:
        f.write("skills:\n" + textwrap.dedent(v2))
    os.utime(path, (os.path.getmtime(path) + 2, os.path.getmtime(path) + 2))

    assert reg.reload() is True
    assert reg.get_by_name("greeter").manifest.version == "1.1.0"
    assert reg.get_by_name("greeter").prompt is not None
    assert "你好 v2" in reg.get_by_name("greeter").prompt

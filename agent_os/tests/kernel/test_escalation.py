"""E1 升权系统锚点测试(docs/ESCALATION.md §2.1/§2.2/§3;§7 分期 E1)。

固定约定:

- 三档按副作用可逆性:none < reversible < irreversible;tool 可声明
  ``ToolSpec.side_effect``(缺省按 Permission 推导),skill 只推导不声明
  (白名单 tools/skills 取 max);
- 升权事件 = 低档帧调用高档 skill;同档移动与降权(高→低)不确认;
  根技能由宿主直接启动,不过闸;
- 确认走 supervisor 闭环:``Question.kind == "escalation"``,
  options 恒为 ``["approve-once", "deny"]``(E1 无 approve-run);
- approve-once 放行本次;deny → 父帧 PERMISSION_DENIED 错误观察;
  参数预校验失败 → INVALID_ARGS,**不产生确认请求**;
- pending 升权以 ``frame.context.working["_pending_escalation"]`` 入 checkpoint,
  resume 重走闸门;
- 干净 context 不变量:升权子帧 messages 恰为 [USER(input)],SYSTEM 只含
  被调 skill 自己的 prompt(父帧 prompt 物理上不进子帧)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    EscalationRequest,
    Message,
    Permission,
    Role,
    RunConfig,
    Source,
    ToolCall,
    ToolPolicy,
    ToolSpec,
    derive_side_effect,
    derive_skill_tier,
    tier_exceeds,
)
from agent_os.kernel.errors import RunAborted, SkillLoadError
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.skills.manifest import parse_manifest, validate_manifest
from agent_os.tools.local_registry import LocalPythonToolRegistry

SKILLS_YAML = """
skills:
  - name: root_caller
    version: 1.0.0
    kind: prompt
    description: 根调用方。Use when 测试升权闸门;Do not use when 其他。
    inputs:
      type: object
      properties: { task: { type: string } }
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: []
      skills: [child_exec, child_read, child_write]
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      ROOT_PROMPT_SECRET 你是根调用方,按需调用子技能并汇报结果。
  - name: root_exec
    version: 1.0.0
    kind: prompt
    description: 高档根调用方。Use when 测试同档/降权不确认;Do not use when 其他。
    inputs:
      type: object
      properties: { task: { type: string } }
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: [exec_tool]
      skills: [child_exec, child_read]
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是高档根调用方,按需调用子技能并汇报结果。
  - name: child_write
    version: 1.0.0
    kind: prompt
    description: 中档子技能。Use when 需要写入;Do not use when 只读或不可逆。
    inputs:
      type: object
      properties: { cmd: { type: string } }
      required: [cmd]
    outputs:
      type: object
      properties: { ran: { type: boolean } }
      required: [ran]
    permissions:
      tools: [write_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 3 }
    prompt: |
      CHILD_WRITE_MARK 你是写入员,按 cmd 写入并汇报。
  - name: root_spawn
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:spawn_one
    inputs:
      type: object
      properties: { skill: { type: string }, args: { type: object } }
      required: [skill]
    outputs:
      type: object
      properties: { frame_id: { type: string }, value: { type: object } }
      required: [frame_id, value]
    permissions:
      tools: []
      skills: [child_exec, child_write, child_read]
  - name: child_exec
    version: 1.0.0
    kind: prompt
    description: 高档子技能。Use when 需要执行命令;Do not use when 只读。
    inputs:
      type: object
      properties: { cmd: { type: string } }
      required: [cmd]
    outputs:
      type: object
      properties: { ran: { type: boolean } }
      required: [ran]
    permissions:
      tools: [exec_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 3 }
    prompt: |
      CHILD_PROMPT_MARK 你是命令执行员,按 cmd 执行并汇报。
  - name: child_read
    version: 1.0.0
    kind: prompt
    description: 低档子技能。Use when 只需要读取;Do not use when 要改世界。
    inputs:
      type: object
      properties: { cmd: { type: string } }
    outputs:
      type: object
      properties: { ran: { type: boolean } }
      required: [ran]
    permissions:
      tools: [read_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 3 }
    prompt: |
      CHILD_READ_MARK 你是只读员,读取并汇报。
"""


def _yaml(tmp_path: Path, text: str = SKILLS_YAML) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(p)


def _tools() -> LocalPythonToolRegistry:
    tools = LocalPythonToolRegistry()

    @tools.tool(name="read_tool", permission=Permission.READ)
    def read_tool() -> str:
        """只读工具。Use when 测试 L1。"""
        return "r"

    @tools.tool(name="write_tool", permission=Permission.WRITE)
    def write_tool() -> str:
        """写入工具。Use when 测试 L2。"""
        return "w"

    @tools.tool(name="exec_tool", permission=Permission.EXEC)
    def exec_tool() -> str:
        """执行工具。Use when 测试 L3。"""
        return "x"

    return tools


def _brain(root_call: dict) -> callable:
    """按 SYSTEM 内容分发:根帧先发 skill 调用再汇报;子帧直接交付。"""

    def brain(req: ChatRequest) -> ChatResponse:
        system = req.messages[0].content if req.messages else ""
        if "CHILD_" in system:
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content=json.dumps({"ran": True})),
                finish_reason="stop",
                usage=ChatUsage(prompt=1, completion=1),
            )
        called = any(
            tc.name.startswith("skill.")
            for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
        )
        if not called:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[ToolCall(id="c1", name=root_call["name"], args=root_call["args"])],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        result = json.loads(
            next(m.content for m in reversed(req.messages) if m.role is Role.TOOL)
        )
        decision = "ok" if result["ok"] else result["error"]["kind"]
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=json.dumps({"decision": decision, "value": result.get("value")}),
            ),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    return brain


def _build(tmp_path, handler, root_call, *, yaml_text: str = SKILLS_YAML):
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(_brain(root_call)))
        .tools(_tools())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path, yaml_text)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if handler is not None:
        builder = builder.supervisor(handler, timeout_s=5.0)
    return builder.build()


# ---------------------------------------------------------------------------
# tier 推导矩阵(§2.1)
# ---------------------------------------------------------------------------


def test_tool_side_effect_derivation_matrix():
    """tool 缺省推导:READ→none,WRITE/NET→reversible,EXEC→irreversible。"""
    assert derive_side_effect(ToolSpec(permission=Permission.READ)) == "none"
    assert derive_side_effect(ToolSpec(permission=Permission.WRITE)) == "reversible"
    assert derive_side_effect(ToolSpec(permission=Permission.NET)) == "reversible"
    assert derive_side_effect(ToolSpec(permission=Permission.EXEC)) == "irreversible"


def test_tool_side_effect_explicit_override():
    """显式声明优先于推导:只读诊断 exec 可下调,读取工具也可上调(就高不就低)。"""
    assert (
        derive_side_effect(ToolSpec(permission=Permission.EXEC, side_effect="none")) == "none"
    )
    assert (
        derive_side_effect(ToolSpec(permission=Permission.READ, side_effect="irreversible"))
        == "irreversible"
    )


def test_derive_skill_tier_takes_max(tmp_path):
    """skill 推导档 = 白名单 tools/skills 取 max;skills 引用递归推导。"""
    skills = LocalFileSkillRegistry(_yaml(tmp_path))
    tools = _tools()
    by_name = {m.name: m for m in skills.manifests()}
    assert derive_skill_tier(by_name["child_read"], tools, skills) == "none"
    assert derive_skill_tier(by_name["root_caller"], tools, skills) == "irreversible"  # 经 child_exec 递归
    assert derive_skill_tier(by_name["root_exec"], tools, skills) == "irreversible"  # 自有 exec_tool

    nested = parse_manifest(
        {
            "name": "mid",
            "kind": "prompt",
            "description": "Use when x;Do not use when y(凑长度过 lint)",
            "permissions": {"tools": ["read_tool", "write_tool"], "skills": []},
            "prompt": "p",
        }
    )
    assert derive_skill_tier(nested, tools, skills) == "reversible"


def test_escalation_matrix_3x3():
    """升权判定 3×3:只有低→高触发;同档移动与降权(高→低)永不确认。"""
    tiers = ["none", "reversible", "irreversible"]
    expected = {
        (c, t)
        for c in tiers
        for t in tiers
        if tiers.index(t) > tiers.index(c)
    }
    for caller in tiers:
        for target in tiers:
            assert tier_exceeds(target, caller) == ((caller, target) in expected)


# ---------------------------------------------------------------------------
# 挂起 → approve-once → 恢复全流程(§3)
# ---------------------------------------------------------------------------


def test_suspend_approve_once_full_flow(tmp_path):
    """低档帧调高档 skill → 挂起等裁决 → approve-once 放行 → 子帧结果回父帧。"""
    received = []

    async def handler(question):
        received.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}})
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))

    assert result["decision"] == "ok"
    assert result["value"] == {"ran": True}
    assert len(received) == 1, "升权确认必须恰好一次"
    q = received[0]
    assert q.kind == "escalation"
    assert q.options == ["approve-once", "deny"]
    assert q.urgency == "high"  # L3 收件箱优先呈现
    assert q.context["skill"] == "child_exec"
    assert q.context["tier"] == "irreversible"
    assert q.context["params"] == {"cmd": "ls"}
    assert q.context["requested"] == {"tools": ["exec_tool"], "skills": []}
    assert q.context["reason_hint"] == "none → irreversible"


def test_deny_returns_permission_denied(tmp_path):
    """deny → 父帧 PERMISSION_DENIED 错误观察;子帧从未执行。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "deny", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "rm"}})
    mock = kernel.providers.providers["mock"]
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))

    assert result["decision"] == "permission_denied"
    assert len(asked) == 1
    child_reqs = [r for r in mock.recorded if "CHILD_PROMPT_MARK" in r.messages[0].content]
    assert not child_reqs, "被拒绝的升权调用不得进入子帧"


def test_retry_after_deny_suspends_again(tmp_path):
    """拒绝后重试同调用会再次挂起(E1 无 Grant 缓存——防"磨到批准")。"""

    async def handler(question):
        return {"answer": "deny", "decided_by": "user:test"}

    asked = []

    async def counting_handler(question):
        asked.append(question)
        return await handler(question)

    def brain(req: ChatRequest) -> ChatResponse:
        system = req.messages[0].content if req.messages else ""
        if "CHILD_PROMPT_MARK" in system:
            raise AssertionError("deny 后子帧不得执行")
        # 根帧连调两次同样的升权调用,然后汇报
        calls = [
            tc
            for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
        ]
        if len(calls) < 2:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(id=f"c{len(calls)}", name="skill.child_exec", args={"cmd": "rm"})
                    ],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": "done"})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(_tools())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(counting_handler, timeout_s=5.0)
        .build()
    )
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))
    assert result["decision"] == "done"
    assert len(asked) == 2, "同一调用重试必须再次挂起确认"


def test_invalid_args_no_confirmation(tmp_path):
    """参数预校验失败 → INVALID_ARGS 错误观察,**不产生确认请求**(原则 1)。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"bad": 1}})
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))

    assert result["decision"] == "invalid_args"
    assert not asked, "参数不合 schema 不得发出升权确认"


def test_same_tier_and_downgrade_no_confirmation(tmp_path):
    """同档移动与降权不确认:高档根帧调高档/低档子技能都直通;根技能启动也不过闸。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once"}

    # root_exec 自身推导档 irreversible(exec_tool)——直接启动不过闸;
    # 调 child_exec 是同档移动,调 child_read 是降权,都不确认
    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}})
    result = asyncio.run(kernel.run("root_exec", {"task": "t"}))
    assert result["decision"] == "ok"
    assert not asked, "同档移动不得触发升权确认"

    kernel2 = _build(tmp_path, handler, {"name": "skill.child_read", "args": {"cmd": "ls"}})
    result2 = asyncio.run(kernel2.run("root_exec", {"task": "t"}))
    assert result2["decision"] == "ok"
    assert not asked, "降权(高→低)不得触发升权确认"


def test_escalation_without_supervisor_denied(tmp_path):
    """无 supervisor 通道时升权 fail-closed:PERMISSION_DENIED(无人可审 = 无人把关)。"""
    kernel = _build(tmp_path, None, {"name": "skill.child_exec", "args": {"cmd": "ls"}})
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))
    assert result["decision"] == "permission_denied"


# ---------------------------------------------------------------------------
# checkpoint/resume:挂起中序列化恢复后继续(§3 原则 3)
# ---------------------------------------------------------------------------


def test_checkpoint_resume_pending_escalation(tmp_path):
    """挂起确认期间断电 → checkpoint 含 _pending_escalation → resume 重问 → 批准后完成。"""
    calls = {"n": 0}

    async def crashing_handler(question):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RunAborted("模拟断电")
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel1 = _build(
        tmp_path, crashing_handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}}
    )
    seen = []

    async def rec(sig):
        seen.append(sig)

    kernel1.signals.subscribe("run.started", rec)

    async def first_run():
        with pytest.raises(RunAborted):
            await kernel1.run("root_caller", {"task": "t"})

    asyncio.run(first_run())
    run_id = seen[0].run_id

    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    pending = [
        f["context"].get("working", {}).get("_pending_escalation") for f in data["frames"]
    ]
    pending = [p for p in pending if p]
    assert pending, "checkpoint 必须含 _pending_escalation"
    assert pending[0]["skill"] == "child_exec"
    assert pending[0]["tier"] == "irreversible"
    assert pending[0]["params"] == {"cmd": "ls"}

    kernel2 = _build(
        tmp_path, crashing_handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}}
    )
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result["decision"] == "ok"
    assert result["value"] == {"ran": True}
    assert calls["n"] == 2, "resume 必须重新向调用方发起升权确认"

    # 帧 tier 随 checkpoint 往返:恢复后的父帧仍是 none(升权判定基准不漂移)
    frames = {f["frame_id"]: f for f in json.loads(ckpt.read_text())["frames"]}
    tiers = {f["skill"].split(":")[-1].split("@")[0]: f.get("tier") for f in frames.values()}
    assert tiers["root_caller"] == "none"


# ---------------------------------------------------------------------------
# 干净 context 不变量(§3 原则 3/4)
# ---------------------------------------------------------------------------


def test_clean_context_invariant(tmp_path):
    """升权子帧:messages 恰为 [USER(input)];SYSTEM 只含被调 skill prompt,父帧 prompt 不进。"""

    async def handler(question):
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}})
    mock = kernel.providers.providers["mock"]
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))
    assert result["decision"] == "ok"

    child_reqs = [r for r in mock.recorded if "CHILD_PROMPT_MARK" in r.messages[0].content]
    assert len(child_reqs) == 1, "子帧只 build 一次(max_steps 内一步交付)"
    req = child_reqs[0]
    system, user = req.messages[0], req.messages[1]
    assert system.role is Role.SYSTEM
    assert "ROOT_PROMPT_SECRET" not in system.content, "父帧 prompt 不得进升权子帧 SYSTEM"
    assert user.role is Role.USER
    assert user.source is Source.PARENT_INPUT
    assert json.loads(user.content) == {"cmd": "ls"}, "首条 USER 即校验过的参数 JSON"

    # make_frame 层面:子帧 context.messages 恰为 [USER(input)],不多不少
    skills = LocalFileSkillRegistry(_yaml(tmp_path))
    parent = skills.make_frame  # 仅需 registry 直测
    from agent_os.api.v1 import SkillCall, SkillFrame, SkillRef

    root = SkillFrame(frame_id="f0", run_id="r0", skill=SkillRef(name="root_caller"), depth=1)
    child = parent(SkillCall(name="child_exec", args={"cmd": "ls"}), root)
    assert len(child.context.messages) == 1
    assert child.context.messages[0].role is Role.USER
    assert child.context.messages[0].source is Source.PARENT_INPUT


# ---------------------------------------------------------------------------
# 分档硬 lint(§2.1/§3.4)
# ---------------------------------------------------------------------------


def test_l2_inline_hard_gate(tmp_path):
    """推导档 ≥L2 禁 inline: true(merge 会破坏干净 context 不变量)。"""
    manifest = parse_manifest(
        {
            "name": "bad_inline",
            "kind": "prompt",
            "description": "Use when x;Do not use when y(凑长度过 lint)",
            "inline": True,
            "permissions": {"tools": ["exec_tool"], "skills": []},
            "prompt": "p",
        }
    )
    with pytest.raises(SkillLoadError, match="inline"):
        validate_manifest(manifest, tier="reversible")
    # 不给 tier 时仍是 inline 纯度闸门先拦(loader 路径行为不变)
    with pytest.raises(SkillLoadError, match="纯度"):
        validate_manifest(manifest)


def test_l3_confirm_first_rejected(tmp_path):
    """推导档 L3 禁 confirm: first(不可逆操作不允许批量授权)——装配期硬闸。"""
    yaml_text = SKILLS_YAML.replace(
        "  - name: child_exec\n",
        "  - name: child_exec\n    trust: { confirm: first }\n",
    )

    async def handler(question):
        return {"answer": "approve-once"}

    with pytest.raises(SkillLoadError, match="confirm: first"):
        _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}}, yaml_text=yaml_text)


def test_trust_block_parsed(tmp_path):
    """trust 块解析进 SkillManifest.trust;缺省为 None(向后兼容)。"""
    skills = LocalFileSkillRegistry(_yaml(tmp_path))
    by_name = {m.name: m for m in skills.manifests()}
    assert by_name["child_exec"].trust is None

    with_trust = parse_manifest(
        {
            "name": "t",
            "kind": "prompt",
            "description": "Use when x;Do not use when y(凑长度过 lint)",
            "trust": {"confirm": "always", "reversal": "r", "blast_radius": "b"},
            "prompt": "p",
        }
    )
    assert with_trust.trust.confirm == "always"
    assert with_trust.trust.reversal == "r"
    assert with_trust.trust.blast_radius == "b"

    with pytest.raises(SkillLoadError, match="trust.confirm"):
        parse_manifest(
            {
                "name": "bad",
                "kind": "prompt",
                "description": "Use when x;Do not use when y(凑长度过 lint)",
                "trust": {"confirm": "sometimes"},
                "prompt": "p",
            }
        )


def test_escalation_request_mapping():
    """EscalationRequest → supervisor 参数/落盘形态:kind、options、结构化载荷齐备。"""
    req = EscalationRequest(
        question_id="esc-1",
        run_id="r",
        frame_id="f",
        skill="child_exec",
        tier="irreversible",
        params={"cmd": "ls"},
        requested={"tools": ["exec_tool"], "skills": []},
        reason_hint="none → irreversible",
    )
    args = req.to_supervisor_args("root_caller")
    assert args["kind"] == "escalation"
    assert args["question_id"] == "esc-1"
    assert args["options"] == ["approve-once", "deny"]
    assert args["urgency"] == "high"
    assert args["context"]["params"] == {"cmd": "ls"}
    pending = req.to_pending("c1")
    assert pending["call_id"] == "c1"
    assert pending["question_id"] == "esc-1"
    assert pending["skill"] == "child_exec"


# ---------------------------------------------------------------------------
# E2:approve-run Grant(§4)、信号三枚(§5)、spawn 升权闸、CLI 透传
# ---------------------------------------------------------------------------


def _build_with_brain(tmp_path, handler, brain, *, yaml_text: str = SKILLS_YAML):
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(_tools())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path, yaml_text)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if handler is not None:
        builder = builder.supervisor(handler, timeout_s=5.0)
    return builder.build()


def _twice_brain(call: dict, state: dict | None = None):
    """根帧连发两次同一 skill 调用再汇总;child_calls 计数可注入断电(崩溃恢复测试用)。"""
    st = state if state is not None else {"child_calls": 0, "crash_at": None}

    def brain(req: ChatRequest) -> ChatResponse:
        system = req.messages[0].content if req.messages else ""
        if "CHILD_" in system:
            st["child_calls"] += 1
            if st.get("crash_at") == st["child_calls"]:
                raise RunAborted("模拟断电")
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content=json.dumps({"ran": True})),
                finish_reason="stop",
                usage=ChatUsage(prompt=1, completion=1),
            )
        calls = [
            tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
        ]
        if len(calls) < 2:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(id=f"c{len(calls)}", name=call["name"], args=call["args"])
                    ],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        results = [json.loads(m.content) for m in req.messages if m.role is Role.TOOL]
        decision = "ok" if results and all(r["ok"] for r in results) else "fail"
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": decision})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    return brain


def test_l2_options_include_approve_run(tmp_path):
    """L2(reversible)升权确认的 options 三枚:approve-once / approve-run / deny。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-run", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_write", "args": {"cmd": "w"}})
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))
    assert result["decision"] == "ok"
    assert asked[0].options == ["approve-once", "approve-run", "deny"]


def test_l3_approve_run_answer_rejected(tmp_path):
    """L3 永不提供 approve-run:手工构造该答案被 options 校验打回重问(§3 原则 2)。"""
    attempts = []

    async def handler(question):
        attempts.append(question.previous_error)
        return {"answer": "approve-run" if len(attempts) == 1 else "approve-once"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}})
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))
    assert result["decision"] == "ok"
    assert len(attempts) == 2 and attempts[0] is None and attempts[1], (
        "L3 的 approve-run 答案必须被打回(previous_error 重问)"
    )


def test_approve_run_registers_grant_and_skips_reconfirmation(tmp_path):
    """approve-run → 登记 run 档 Grant(tier 快照/decided_by);本 run 内同 skill
    后续调用直接放行,并发 post:skill.escalate decision="grant-run"。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-run", "decided_by": "user:test"}

    call = {"name": "skill.child_write", "args": {"cmd": "w"}}
    kernel = _build_with_brain(tmp_path, handler, _twice_brain(call))
    escalate_signals = []

    async def rec(sig):
        escalate_signals.append(sig)

    run_ids = []

    async def rec_run(sig):
        run_ids.append(sig.run_id)

    kernel.signals.subscribe("post:skill.escalate", rec)
    kernel.signals.subscribe("run.started", rec_run)
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))

    assert result["decision"] == "ok"
    assert len(asked) == 1, "Grant 命中后第二次调用不得再问"
    grants = kernel._runs[run_ids[0]].grants
    assert len(grants) == 1
    grant = grants[0]
    assert grant.skill == "child_write"
    assert grant.scope == "run"
    assert grant.tier == "reversible"  # 批准时的推导档快照
    assert grant.decided_by == "user:test"
    assert grant.decided_at > 0
    decisions = [s.payload["decision"] for s in escalate_signals]
    assert decisions == ["approve-run", "grant-run"]
    hit = escalate_signals[1]
    assert hit.payload["skill"] == "child_write"
    assert hit.payload["tier"] == "reversible"
    assert hit.payload["scope"] == "run"
    assert hit.payload["decided_by"] == "user:test"


def test_grant_consumption_only_reversible():
    """Grant 消费点双保险(§4):run 档只对 reversible 目标生效;once 档消费即焚。"""
    from agent_os.api.v1 import Grant, SkillFrame
    from agent_os.kernel.run import Run

    # 只需内核对象的 _runs 表:最小装配
    config = RunConfig(model="mock/x", compression="off")
    kernel = (
        KernelBuilder(config)
        .providers(MockProvider(lambda req: None))
        .tools(_tools())
        .build()
    )
    run = Run(run_id="r1")
    kernel._runs["r1"] = run
    frame = SkillFrame(frame_id="f1", run_id="r1", tier="none")

    run.grants.append(
        Grant(skill="s3", tier="irreversible", scope="run", decided_by="u", decided_at=1.0)
    )
    assert kernel._consume_grant(frame, "s3", "irreversible") is None, (
        "L3 目标永不消费 run 档 Grant(即使手工构造)"
    )
    run.grants.append(
        Grant(skill="s2", tier="reversible", scope="run", decided_by="u", decided_at=1.0)
    )
    assert kernel._consume_grant(frame, "s2", "reversible") is not None
    assert kernel._consume_grant(frame, "other", "reversible") is None, "异 skill 不命中"

    run.grants.append(
        Grant(skill="s1", tier="irreversible", scope="once", decided_by="u", decided_at=1.0)
    )
    hit = kernel._consume_grant(frame, "s1", "irreversible")
    assert hit is not None and hit.scope == "once"
    assert all(g.skill != "s1" for g in run.grants), "once 档消费即焚"
    assert kernel._consume_grant(frame, "s1", "irreversible") is None


def test_escalate_signals_payloads(tmp_path):
    """信号三枚(§5):pre 载荷 {skill, tier, params, requested};post 载荷
    {decision, decided_by, scope};deny 另有 skill.escalation.denied。"""
    seen = []

    async def rec(sig):
        seen.append(sig)

    async def handler(question):
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}})
    for name in ("pre:skill.escalate", "post:skill.escalate", "skill.escalation.denied"):
        kernel.signals.subscribe(name, rec)
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))
    assert result["decision"] == "ok"

    pre = next(s for s in seen if s.name == "pre:skill.escalate")
    assert pre.payload["skill"] == "child_exec"
    assert pre.payload["tier"] == "irreversible"
    assert pre.payload["params"] == {"cmd": "ls"}
    assert pre.payload["requested"] == {"tools": ["exec_tool"], "skills": []}
    assert pre.payload["frame_id"]
    post = next(s for s in seen if s.name == "post:skill.escalate")
    assert post.payload["decision"] == "approve-once"
    assert post.payload["decided_by"] == "user:test"
    assert post.payload["scope"] == "once"
    assert not any(s.name == "skill.escalation.denied" for s in seen)


def test_denied_signal_payload(tmp_path):
    """deny → post:skill.escalate(decision="deny")+ skill.escalation.denied 各一枚。"""
    seen = []

    async def rec(sig):
        seen.append(sig)

    async def handler(question):
        return {"answer": "deny", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "rm"}})
    for name in ("post:skill.escalate", "skill.escalation.denied"):
        kernel.signals.subscribe(name, rec)
    result = asyncio.run(kernel.run("root_caller", {"task": "t"}))
    assert result["decision"] == "permission_denied"

    post = next(s for s in seen if s.name == "post:skill.escalate")
    assert post.payload["decision"] == "deny"
    assert post.payload["decided_by"] == "user:test"
    denied = next(s for s in seen if s.name == "skill.escalation.denied")
    assert denied.payload["skill"] == "child_exec"
    assert denied.payload["tier"] == "irreversible"
    assert denied.payload["decided_by"] == "user:test"


def test_checkpoint_restores_grants(tmp_path):
    """grants 随 checkpoint 序列化:approve-run 后断电 → resume 恢复 Grant →
    后续同 skill 调用命中放行(不再确认)。"""
    state = {"child_calls": 0, "crash_at": 1}
    call = {"name": "skill.child_write", "args": {"cmd": "w"}}
    asks = {"k1": 0, "k2": 0}

    async def handler1(question):
        asks["k1"] += 1
        return {"answer": "approve-run", "decided_by": "user:test"}

    async def handler2(question):
        asks["k2"] += 1
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel1 = _build_with_brain(tmp_path, handler1, _twice_brain(call, state))
    seen = []

    async def rec(sig):
        seen.append(sig)

    kernel1.signals.subscribe("run.started", rec)

    async def first_run():
        with pytest.raises(RunAborted):
            await kernel1.run("root_caller", {"task": "t"})

    asyncio.run(first_run())
    run_id = seen[0].run_id
    ckpt = tmp_path / "ckpt-grants.json"
    kernel1.checkpoint(run_id, str(ckpt))
    doc = json.loads(ckpt.read_text(encoding="utf-8"))
    assert doc["run"]["grants"], "checkpoint 必须含 grants"
    assert doc["run"]["grants"][0]["skill"] == "child_write"
    assert doc["run"]["grants"][0]["scope"] == "run"

    kernel2 = _build_with_brain(tmp_path, handler2, _twice_brain(call, state))
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result["decision"] == "ok"
    assert asks["k1"] == 1 and asks["k2"] == 0, "resume 后 Grant 命中,不得重新确认"


# ---------------------------------------------------------------------------
# E2:spawn 后台帧升权闸(§3.4 + docs/ESCALATION.md §3)
# ---------------------------------------------------------------------------


def test_spawn_escalation_approve(tmp_path):
    """code 技能 spawn 高档 skill → 闸触发(确认等待在 spawn 调用点)→ 批准 → 后台帧跑完。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "ls"}})
    result = asyncio.run(kernel.run("root_spawn", {"skill": "child_exec", "args": {"cmd": "ls"}}))
    assert result["value"] == {"ran": True}
    assert len(asked) == 1
    assert asked[0].kind == "escalation"
    assert asked[0].context["skill"] == "child_exec"


def test_spawn_escalation_deny_raises(tmp_path):
    """spawn 升权被拒 → SkillLoadError 上抛(与白名单拒绝同形),后台帧未创建。"""

    async def handler(question):
        return {"answer": "deny", "decided_by": "user:test"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"cmd": "rm"}})
    mock = kernel.providers.providers["mock"]
    with pytest.raises(Exception, match="拒绝"):
        asyncio.run(kernel.run("root_spawn", {"skill": "child_exec", "args": {"cmd": "rm"}}))
    child_reqs = [r for r in mock.recorded if "CHILD_PROMPT_MARK" in r.messages[0].content]
    assert not child_reqs, "被拒绝的 spawn 不得进入子帧"


def test_spawn_invalid_args_no_confirmation(tmp_path):
    """spawn 参数预校验失败 → SkillLoadError,不产生确认请求(与 invoke 同语义)。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_exec", "args": {"bad": 1}})
    with pytest.raises(Exception, match="inputs schema"):
        asyncio.run(kernel.run("root_spawn", {"skill": "child_exec", "args": {"bad": 1}}))
    assert not asked


def test_spawn_same_tier_no_confirmation(tmp_path):
    """spawn 低档 skill(降权/同档)不触发确认,直接后台运行。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once"}

    kernel = _build(tmp_path, handler, {"name": "skill.child_read", "args": {"cmd": "r"}})
    result = asyncio.run(kernel.run("root_spawn", {"skill": "child_read", "args": {"cmd": "r"}}))
    assert result["value"] == {"ran": True}
    assert not asked


# ---------------------------------------------------------------------------
# E2:CLI 答案透传(docs/SUPERVISOR.md §2.3 CLI 宿主通道 + docs/ESCALATION.md §3)
# ---------------------------------------------------------------------------


def test_cli_supervisor_passthrough_escalation(monkeypatch, capsys):
    """CLI 通道:escalation 问题带 kind 与结构化载荷,options 内答案原样闭环。"""
    import io
    import sys

    from agent_os.api.v1 import Question
    from agent_os.host.cli.main import _cli_supervisor

    question = Question(
        question_id="esc-1",
        run_id="r",
        frame_id="f",
        question="升权确认:root(none → reversible)请求调用 child_write,批准本次执行?",
        context={
            "skill": "child_write",
            "tier": "reversible",
            "params": {"cmd": "w"},
            "requested": {"tools": ["write_tool"], "skills": []},
            "reason_hint": "none → reversible",
        },
        options=["approve-once", "approve-run", "deny"],
        kind="escalation",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("approve-run\n"))
    out = asyncio.run(_cli_supervisor(question))
    assert out == {"answer": "approve-run", "decided_by": "host:cli"}
    row = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert row["kind"] == "escalation"
    assert row["options"] == ["approve-once", "approve-run", "deny"]
    assert row["context"]["tier"] == "reversible"
    assert row["context"]["params"] == {"cmd": "w"}

    # 普通问答行形状不变(不带 kind)
    monkeypatch.setattr(sys, "stdin", io.StringIO("ok\n"))
    asyncio.run(_cli_supervisor(Question(question_id="q-1", question="继续?")))
    row2 = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "kind" not in row2

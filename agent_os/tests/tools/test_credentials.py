"""WS1 凭证注入锚点测试(ToolSpec.credentials / [credentials] 段 / bind_credentials)。

固定约定:

- ``ToolSpec.credentials``:工具声明需要的凭证 key 列表,缺省 [] = 不要凭证,
  dispatch 注入空 dict;
- 作用域:``[credentials]`` 段解析为 凭证名 → env 变量名(:class:`CredentialScope`),
  dispatch 时现读 ``os.environ``(动态解析,防 token 过期;不装配期快照);
- 注入:只注入声明键;未声明 / 作用域缺该键 / env 缺席 → 键不出现;
  未 bind resolver → 空 dict(回归锚);
- 泄露纪律:凭证值只进 ToolContext,不进帧上下文/checkpoint/trace;
  工具错误消息只带键名,不回显值。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolPolicy,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.runtime.config import CredentialScope
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

#: 测试专用 env 名/值(避免与真实环境变量撞名;值带可识别标记供泄露断言)
GH_ENV = "AGENT_OS_TEST_GH_TOKEN"
GL_ENV = "AGENT_OS_TEST_GL_TOKEN"
SECRET = "TESTSECRET-GH-TOKEN"


def _frame(run_id: str = "r1") -> SkillFrame:
    return SkillFrame(frame_id="f1", run_id=run_id, principal=None)


def _dispatch_ctx(frame: SkillFrame) -> ToolDispatchContext:
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=["capture_cred"],
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


def _tools_with_capture(seen: list, declared: list[str]) -> LocalPythonToolRegistry:
    """注册 capture_cred:捕获 ctx.credentials 快照,返回无害串(不回显凭证值)。"""
    tools = LocalPythonToolRegistry()

    @tools.tool(name="capture_cred", permission=Permission.READ, credentials=declared)
    def capture_cred(ctx) -> str:
        """捕获 credentials。Use when 测试凭证注入;Do not use when 其他。"""
        seen.append(dict(ctx.credentials))
        return "ok"

    return tools


# ---------------------------------------------------------------------------
# dispatch 注入语义(声明过滤 / 缺省空)
# ---------------------------------------------------------------------------


def test_declared_key_injected(monkeypatch):
    """声明键注入:工具声明 credentials=["github"] + 作用域配置 github →
    handler 收到 ctx.credentials["github"] 为 env 现值。"""
    monkeypatch.setenv(GH_ENV, SECRET)
    seen: list = []
    tools = _tools_with_capture(seen, ["github"])
    tools.bind_credentials(CredentialScope({"github": GH_ENV}))
    result = asyncio.run(tools.dispatch(ToolCall(id="c1", name="capture_cred", args={}), _dispatch_ctx(_frame())))
    assert result.ok
    assert seen == [{"github": SECRET}]


def test_injection_filtered_by_declaration(monkeypatch):
    """按声明过滤:作用域有 github/gitlab,工具只声明 github → gitlab 不出现。"""
    monkeypatch.setenv(GH_ENV, SECRET)
    monkeypatch.setenv(GL_ENV, "TESTSECRET-GL-TOKEN")
    seen: list = []
    tools = _tools_with_capture(seen, ["github"])
    tools.bind_credentials(CredentialScope({"github": GH_ENV, "gitlab": GL_ENV}))
    result = asyncio.run(tools.dispatch(ToolCall(id="c1", name="capture_cred", args={}), _dispatch_ctx(_frame())))
    assert result.ok
    assert seen == [{"github": SECRET}]
    assert "gitlab" not in seen[0]


def test_undeclared_means_empty(monkeypatch):
    """工具未声明 credentials → 作用域配了键也注入空 dict。"""
    monkeypatch.setenv(GH_ENV, SECRET)
    seen: list = []
    tools = _tools_with_capture(seen, [])
    tools.bind_credentials(CredentialScope({"github": GH_ENV}))
    result = asyncio.run(tools.dispatch(ToolCall(id="c1", name="capture_cred", args={}), _dispatch_ctx(_frame())))
    assert result.ok
    assert seen == [{}]


def test_unbound_resolver_means_empty():
    """回归锚:registry 未 bind resolver → credentials 恒空(行为与引入 WS1 前一致)。"""
    seen: list = []
    tools = _tools_with_capture(seen, ["github"])
    result = asyncio.run(tools.dispatch(ToolCall(id="c1", name="capture_cred", args={}), _dispatch_ctx(_frame())))
    assert result.ok
    assert seen == [{}]


def test_env_missing_key_absent(monkeypatch):
    """env 缺席语义:声明键的 env 变量不存在 / 作用域未配置该键 → 键不出现
    (工具按 ``ctx.credentials.get(key)`` 判缺凭证)。"""
    monkeypatch.delenv(GH_ENV, raising=False)
    seen: list = []
    tools = _tools_with_capture(seen, ["github", "unknown_key"])
    tools.bind_credentials(CredentialScope({"github": GH_ENV}))  # unknown_key 作用域未配置
    result = asyncio.run(tools.dispatch(ToolCall(id="c1", name="capture_cred", args={}), _dispatch_ctx(_frame())))
    assert result.ok
    assert seen == [{}]
    assert "github" not in seen[0] and "unknown_key" not in seen[0]


def test_dynamic_resolution(monkeypatch):
    """动态解析:第一次 dispatch 后改 env 值,第二次 dispatch 拿到新值(不快照)。"""
    monkeypatch.setenv(GH_ENV, "OLD-TOKEN")
    seen: list = []
    tools = _tools_with_capture(seen, ["github"])
    tools.bind_credentials(CredentialScope({"github": GH_ENV}))
    asyncio.run(tools.dispatch(ToolCall(id="c1", name="capture_cred", args={}), _dispatch_ctx(_frame())))
    monkeypatch.setenv(GH_ENV, "NEW-TOKEN")  # token 续期
    asyncio.run(tools.dispatch(ToolCall(id="c2", name="capture_cred", args={}), _dispatch_ctx(_frame())))
    assert seen == [{"github": "OLD-TOKEN"}, {"github": "NEW-TOKEN"}]


# ---------------------------------------------------------------------------
# 泄露反向测试:凭证值不进 checkpoint 序列化
# ---------------------------------------------------------------------------

SKILLS_YAML = """
skills:
  - name: cred_demo
    version: 1.0.0
    kind: prompt
    description: 凭证演示帧。Use when 测试凭证泄露;Do not use when 其他。
    inputs:
      type: object
      properties: { task: { type: string } }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions:
      tools: [capture_cred]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4 }
    prompt: |
      CRED_MARK 你是凭证演示帧,调 capture_cred 后交付。
"""


def _yaml(tmp_path: Path) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    return str(p)


def _cred_brain(req: ChatRequest) -> ChatResponse:
    calls = [tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls]
    if not any(tc.name == "capture_cred" for tc in calls):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="capture_cred", args={})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def test_credential_not_in_checkpoint(tmp_path, monkeypatch):
    """泄露纪律:凭证值到达 ToolContext(正向控制),但不出现在 checkpoint 序列化里。"""
    monkeypatch.setenv(GH_ENV, SECRET)
    seen: list = []
    tools = _tools_with_capture(seen, ["github"])
    tools.bind_credentials(CredentialScope({"github": GH_ENV}))
    kernel = (
        KernelBuilder(RunConfig(model="mock/x", compression="off"))
        .providers(MockProvider(_cred_brain))
        .tools(tools)
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel())
        .build()
    )
    run_ids = []

    async def rec(sig):
        run_ids.append(sig.run_id)

    kernel.signals.subscribe("run.started", rec)
    result = asyncio.run(kernel.run("cred_demo", {"task": "t"}))
    assert result == {"done": True}
    assert seen == [{"github": SECRET}], "正向控制:凭证确实注入到了工具"

    ckpt = tmp_path / "ckpt.json"
    kernel.checkpoint(run_ids[0], str(ckpt))
    blob = ckpt.read_text(encoding="utf-8")
    assert SECRET not in blob  # 凭证值不落盘
    assert GH_ENV not in blob  # env 变量名也不进 trajectory

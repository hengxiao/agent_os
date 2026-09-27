"""D1 数据层 authN+Z 锚点测试(docs/DATA-AUTHZ.md §2/§3/§5;§8 分期 D1)。

固定约定:

- ``allow()``:clearance >= sensitivity 才放行,默认拒绝;``principal is None``
  → 放行(v1 单用户语义:宿主未注入身份 = 数据层未启用);
- dispatch 顺序:schema 校验 → **数据 authZ** → 三层权限交集 → 执行(§5.2),
  数据拒绝先于权限拒绝;
- D1 兼容策略(§8 D1 实现注):未配置域 = 不拦截(退化为 resolve_work_path
  现状沙箱);默认拒绝只作用于已配置域(内置 fs.workdir=public +
  ``register_fs_domain`` 注册的域);
- 身份不变量(§2.3):子帧/升权帧原样继承 principal;checkpoint 序列化往返。

D2 续写(文件尾部,§3.1/§3.2/§6):``[data]`` 策略接线(build_kernel)、net 域
URL 前缀判定、per-subject 白名单第二判据、审计信号 payload、``_authz`` 判据回写;
以上 13 例为 D1 回归锚,语义逐字不动(``allow(whitelist=None)`` 即 D1 判定,
由 3x3 矩阵锚定)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import httpx

from agent_os.api.v1 import (
    CONFIDENTIAL,
    DATA_ACCESS_DENIED,
    DATA_ACCESS_GRANTED,
    INTERNAL,
    PUBLIC,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    DataDomain,
    DataPolicy,
    Message,
    Permission,
    Principal,
    Role,
    RunConfig,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
    allow,
    clearance_of,
    cli_principal,
    web_single_user_principal,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.runtime.config import build_kernel
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

# ---------------------------------------------------------------------------
# allow() 判定矩阵(§3.2)
# ---------------------------------------------------------------------------


def _p(clearance: str) -> Principal:
    return Principal(subject="user:t", issuer="test", attrs={"clearance": clearance})


def test_allow_matrix_3x3():
    """3 clearance × 3 sensitivity:只有 clearance >= sensitivity 放行。"""
    levels = [PUBLIC, INTERNAL, CONFIDENTIAL]
    for c in levels:
        for s in levels:
            assert allow(_p(c), DataDomain(name="d", sensitivity=s)) == (
                levels.index(c) >= levels.index(s)
            ), f"clearance={c} sensitivity={s}"


def test_allow_default_deny_and_fail_closed():
    """默认拒绝:未知 clearance 按 public(只读得动 public,再高即拒)、
    未知 sensitivity 按 confidential(谁都读不动)——两个方向都 fail closed。"""
    assert allow(_p("bogus"), DataDomain(name="d", sensitivity=PUBLIC))
    assert not allow(_p("bogus"), DataDomain(name="d", sensitivity=INTERNAL))
    # 未知 sensitivity 按 confidential 处理(§3.3:只有最高 clearance 可读)
    assert not allow(_p(INTERNAL), DataDomain(name="d", sensitivity="bogus"))
    assert allow(_p(CONFIDENTIAL), DataDomain(name="d", sensitivity="bogus"))
    # attrs 缺 clearance → public
    assert clearance_of(Principal(subject="u", issuer="t")) == PUBLIC


def test_allow_none_principal_single_user():
    """principal 为 None → 放行(v1 单用户语义:未注入身份 = 未启用拦截)。"""
    assert allow(None, DataDomain(name="d", sensitivity=CONFIDENTIAL))


# ---------------------------------------------------------------------------
# 来源构造(§2.2;D1:CLI + Web 单用户)
# ---------------------------------------------------------------------------


def test_cli_principal_source(monkeypatch):
    """CLI = user:$USER,issuer=cli;单用户 = 机器的主人,clearance=confidential。"""
    monkeypatch.setenv("USER", "hengxiao")
    p = cli_principal()
    assert p.subject == "user:hengxiao"
    assert p.issuer == "cli"
    assert clearance_of(p) == CONFIDENTIAL


def test_web_single_user_principal_source(monkeypatch):
    """Web 单用户 = 部署者:登录名取宿主配置,缺省退化为 user:$USER;issuer=web-session。"""
    p = web_single_user_principal("deployer")
    assert p.subject == "user:deployer"
    assert p.issuer == "web-session"
    assert clearance_of(p) == CONFIDENTIAL
    monkeypatch.setenv("USER", "fallback")
    assert web_single_user_principal(None).subject == "user:fallback"
    assert web_single_user_principal("").subject == "user:fallback"


# ---------------------------------------------------------------------------
# dispatch 强制点(§3.3/§5.2)
# ---------------------------------------------------------------------------


def _frame(principal: Principal | None, run_id: str = "r1") -> SkillFrame:
    return SkillFrame(frame_id="f1", run_id=run_id, principal=principal)


def _dispatch_ctx(frame: SkillFrame, workdir: Path | None = None, read_paths=(), allowed=()):
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=list(allowed),
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=workdir,
        read_paths=list(read_paths),
    )


def test_dispatch_order_data_before_permission(tmp_path):
    """顺序(§5.2):数据拒绝先于权限拒绝——WRITE 工具不在白名单且数据域也不够时,
    报 DATA_ACCESS_DENIED 而非 PERMISSION_DENIED。"""
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register_fs_domain(
        DataDomain(name="fs.secret", sensitivity=CONFIDENTIAL), tmp_path
    )
    ctx = _dispatch_ctx(_frame(_p(PUBLIC)), workdir=tmp_path, allowed=[])  # 空白名单
    result = asyncio.run(
        tools.dispatch(
            ToolCall(id="c1", name="system.file.write", args={"path": "a.txt", "content": "x"}),
            ctx,
        )
    )
    assert result.ok is False
    assert result.error.kind is ToolErrorKind.DATA_ACCESS_DENIED


def test_configured_confidential_domain_denied_without_leak(tmp_path):
    """已配置 confidential 域 + 低 clearance → DATA_ACCESS_DENIED;错误面不泄漏
    路径与域内内容。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT", encoding="utf-8")
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register_fs_domain(
        DataDomain(name="fs.secret", sensitivity=CONFIDENTIAL), tmp_path
    )
    ctx = _dispatch_ctx(_frame(_p(INTERNAL)), workdir=tmp_path)
    result = asyncio.run(
        tools.dispatch(ToolCall(id="c1", name="system.file.read", args={"path": "secret.txt"}), ctx)
    )
    assert result.ok is False
    assert result.error.kind is ToolErrorKind.DATA_ACCESS_DENIED
    blob = f"{result.error.message} {result.error.hint}"
    assert "fs.secret" in blob and "confidential" in blob  # 带域名与所需档
    assert "secret.txt" not in blob  # 不回显目标路径
    assert "TOP-SECRET-CONTENT" not in blob  # 不泄漏域内内容


def test_sufficient_clearance_reads_configured_domain(tmp_path):
    """clearance 足够 → 已配置域正常读;内置 fs.workdir=public 对任何身份放行。"""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register_fs_domain(
        DataDomain(name="fs.secret", sensitivity=CONFIDENTIAL), tmp_path
    )
    ctx = _dispatch_ctx(_frame(_p(CONFIDENTIAL)), workdir=tmp_path)
    result = asyncio.run(
        tools.dispatch(ToolCall(id="c1", name="system.file.read", args={"path": "a.txt"}), ctx)
    )
    assert result.ok
    assert "hello" in result.value

    # 内置默认域:workdir 内 = fs.workdir(public),最低 clearance 也放行
    tools2 = LocalPythonToolRegistry.with_builtins()
    ctx2 = _dispatch_ctx(_frame(_p(PUBLIC)), workdir=tmp_path)
    result2 = asyncio.run(
        tools2.dispatch(ToolCall(id="c2", name="system.file.read", args={"path": "a.txt"}), ctx2)
    )
    assert result2.ok


def test_unconfigured_path_degrades_to_sandbox(tmp_path):
    """未配置域(read_paths 内、任何已配置域之外)→ D1 不拦截,维持现状沙箱语义。"""
    outside = tmp_path / "shared"
    outside.mkdir()
    (outside / "b.txt").write_text("shared-data", encoding="utf-8")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    tools = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx(_frame(_p(PUBLIC)), workdir=workdir, read_paths=[outside])
    result = asyncio.run(
        tools.dispatch(ToolCall(id="c1", name="system.file.read", args={"path": "b.txt"}), ctx)
    )
    # resolve_work_path:path 相对 workdir 解析,"b.txt" 不在 workdir;用绝对路径打 read_paths 区
    if not result.ok:
        result = asyncio.run(
            tools.dispatch(
                ToolCall(id="c2", name="system.file.read", args={"path": str(outside / "b.txt")}),
                ctx,
            )
        )
    assert result.ok
    assert "shared-data" in result.value


def test_none_principal_not_intercepted(tmp_path):
    """principal 为 None(v1 单用户语义):已配置 confidential 域也不拦截。"""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register_fs_domain(
        DataDomain(name="fs.secret", sensitivity=CONFIDENTIAL), tmp_path
    )
    ctx = _dispatch_ctx(_frame(None), workdir=tmp_path)
    result = asyncio.run(
        tools.dispatch(ToolCall(id="c1", name="system.file.read", args={"path": "a.txt"}), ctx)
    )
    assert result.ok


def test_tool_context_principal_filled(tmp_path):
    """ToolContext.principal 从预留变实填:工具内可见本帧 principal。"""
    seen = []
    tools = LocalPythonToolRegistry()

    @tools.tool(name="capture", permission=Permission.READ)
    def capture(ctx) -> str:
        """捕获 principal。Use when 测试。"""
        seen.append(ctx.principal)
        return "ok"

    p = _p(INTERNAL)
    ctx = _dispatch_ctx(_frame(p), workdir=tmp_path)
    result = asyncio.run(tools.dispatch(ToolCall(id="c1", name="capture", args={}), ctx))
    assert result.ok
    assert seen == [p]


# ---------------------------------------------------------------------------
# 身份不变量(§2.3/§5.1):子帧/升权帧原样继承;checkpoint 往返
# ---------------------------------------------------------------------------

SKILLS_YAML = """
skills:
  - name: root_data
    version: 1.0.0
    kind: prompt
    description: 根帧。Use when 测试身份不变量;Do not use when 其他。
    inputs:
      type: object
      properties: { task: { type: string } }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions:
      tools: [capture_principal]
      skills: [child_high]
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 8 }
    prompt: |
      ROOT_MARK 你是根帧,先自报身份再调子技能。
  - name: child_high
    version: 1.0.0
    kind: prompt
    description: 高档子帧。Use when 测试升权帧身份;Do not use when 其他。
    inputs:
      type: object
      properties: { cmd: { type: string } }
    outputs:
      type: object
      properties: { ran: { type: boolean } }
      required: [ran]
    permissions:
      tools: [capture_principal, exec_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4 }
    prompt: |
      CHILD_MARK 你是高档子帧,自报身份并交付。
"""


def _yaml(tmp_path: Path) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    return str(p)


def _capturing_tools(seen: list) -> LocalPythonToolRegistry:
    tools = LocalPythonToolRegistry()

    @tools.tool(name="capture_principal", permission=Permission.READ)
    def capture_principal(ctx) -> str:
        """记录本帧 principal。Use when 测试身份不变量。"""
        seen.append(ctx.principal)
        return "captured"

    @tools.tool(name="exec_tool", permission=Permission.EXEC)
    def exec_tool() -> str:
        """执行工具。Use when 测试 L3。"""
        return "x"

    return tools


def _identity_brain(req: ChatRequest) -> ChatResponse:
    """根帧:先 capture 再调 child_high;子帧:capture 后交付。"""
    system = req.messages[0].content if req.messages else ""
    if "CHILD_MARK" in system:
        captured = any(
            tc.name == "capture_principal"
            for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
        )
        if not captured:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[ToolCall(id="k1", name="capture_principal", args={})],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"ran": True})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )
    calls = [tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls]
    if not any(tc.name == "capture_principal" for tc in calls):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="r1", name="capture_principal", args={})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    if not any(tc.name.startswith("skill.") for tc in calls):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="r2", name="skill.child_high", args={"cmd": "go"})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def test_principal_identity_invariant_across_frames(tmp_path):
    """身份不变量(§2.3/§5.1):根帧 → 升权子帧(L3,approve-once 放行)principal 逐帧相等。"""
    seen: list = []

    async def handler(question):
        return {"answer": "approve-once", "decided_by": "user:test"}

    principal = Principal(subject="user:alice", issuer="test", attrs={"clearance": "internal"})
    kernel = (
        KernelBuilder(
            RunConfig(
                model="mock/x",
                tool_policy=ToolPolicy(max_permission=Permission.EXEC),
                compression="off",
            )
        )
        .providers(MockProvider(_identity_brain))
        .tools(_capturing_tools(seen))
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(handler, timeout_s=5.0)
        .build()
    )
    result = asyncio.run(kernel.run("root_data", {"task": "t"}, principal=principal))
    assert result == {"done": True}
    assert len(seen) == 2, "根帧与升权子帧各 capture 一次"
    assert seen[0] is principal and seen[1] is principal, "子帧/升权帧 principal 原样继承"


def test_checkpoint_principal_round_trip(tmp_path):
    """checkpoint:principal 随帧序列化;resume 重建的帧身份不变。"""
    seen: list = []

    async def handler(question):
        return {"answer": "approve-once", "decided_by": "user:test"}

    principal = Principal(subject="user:alice", issuer="test", attrs={"clearance": "internal"})
    kernel = (
        KernelBuilder(
            RunConfig(
                model="mock/x",
                tool_policy=ToolPolicy(max_permission=Permission.EXEC),
                compression="off",
            )
        )
        .providers(MockProvider(_identity_brain))
        .tools(_capturing_tools(seen))
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(handler, timeout_s=5.0)
        .build()
    )
    run_ids = []

    async def rec(sig):
        run_ids.append(sig.run_id)

    kernel.signals.subscribe("run.started", rec)
    asyncio.run(kernel.run("root_data", {"task": "t"}, principal=principal))

    ckpt = tmp_path / "ckpt.json"
    kernel.checkpoint(run_ids[0], str(ckpt))
    doc = json.loads(ckpt.read_text(encoding="utf-8"))
    assert all(f["principal"]["subject"] == "user:alice" for f in doc["frames"])
    assert all(f["principal"]["issuer"] == "test" for f in doc["frames"])

    # resume(DONE 帧跳过,直接结算):重建帧的 principal 与原帧相等
    kernel2 = (
        KernelBuilder(
            RunConfig(
                model="mock/x",
                tool_policy=ToolPolicy(max_permission=Permission.EXEC),
                compression="off",
            )
        )
        .providers(MockProvider(_identity_brain))
        .tools(_capturing_tools(seen))
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(handler, timeout_s=5.0)
        .build()
    )
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result == {"done": True}
    restored = [f.principal for f in kernel2.stack.tree() if f.run_id == run_ids[0]]
    assert restored and all(p == principal for p in restored)


# ---------------------------------------------------------------------------
# D2:[data] 策略(§3.1/§3.2)——build_kernel 接线 + 白名单第二判据
# ---------------------------------------------------------------------------


def _kernel_cfg(data: dict) -> dict:
    """带 [data] 段的 build_kernel 输入(本文件只取 ``kernel.tools`` 直接 dispatch,
    不接 skills 子系统——fib 域声明 system.python.exec,挂上反而要多配 python_exec)。"""
    return {
        "run": {"model": "mock/fib", "compression": "off"},
        "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
        "tools": {"builtins": True},
        "data": data,
    }


def test_build_kernel_data_section_confidential_denies_low_clearance(tmp_path):
    """[data] 段接线:confidential 已配置域对低 clearance principal 拒绝;
    sensitivity 缺省 confidential(忘了配 = 最严,§3.1)。"""
    (tmp_path / "s.txt").write_text("TOP-SECRET", encoding="utf-8")
    kernel = build_kernel(
        _kernel_cfg(
            {
                # sensitivity 缺省 → confidential;白名单配了也救不了 clearance 不够
                "domains": [{"name": "fs.secret", "path_prefix": str(tmp_path)}],
                "principals": {"user:t": {"domains": ["fs.*"]}},
            }
        )
    )
    tools = kernel.tools
    assert tools._data_policy is not None, "[data] 段存在即应 bind"
    ctx = _dispatch_ctx(_frame(_p(INTERNAL)), workdir=tmp_path)
    result = asyncio.run(
        tools.dispatch(ToolCall(id="c1", name="system.file.read", args={"path": "s.txt"}), ctx)
    )
    assert result.ok is False
    assert result.error.kind is ToolErrorKind.DATA_ACCESS_DENIED
    blob = f"{result.error.message} {result.error.hint}"
    assert "fs.secret" in blob and "confidential" in blob
    assert "s.txt" not in blob and "TOP-SECRET" not in blob  # 不泄路径与域内内容


def test_build_kernel_whitelist_second_criterion(tmp_path):
    """白名单第二判据(§3.2):clearance 够但域不在白名单 → 同样拒绝;
    未配置 subject → 空表白名单 = 全拒(fail closed);配置且命中 → 放行。"""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    kernel = build_kernel(
        _kernel_cfg(
            {
                "domains": [
                    {"name": "fs.shared", "sensitivity": "internal", "path_prefix": str(tmp_path)}
                ],
                "principals": {
                    "user:full": {"domains": ["fs.*"]},
                    "user:netside": {"domains": ["net.*"]},
                },
            }
        )
    )
    tools = kernel.tools

    def read_as(subject: str):
        p = Principal(subject=subject, issuer="test", attrs={"clearance": CONFIDENTIAL})
        ctx = _dispatch_ctx(_frame(p), workdir=tmp_path)
        return asyncio.run(
            tools.dispatch(ToolCall(id="c1", name="system.file.read", args={"path": "a.txt"}), ctx)
        )

    assert read_as("user:full").ok, "clearance 够 + 白名单命中 → 放行"
    denied = read_as("user:netside")
    assert denied.ok is False, "clearance 够但域不在白名单 → 拒绝"
    assert denied.error.kind is ToolErrorKind.DATA_ACCESS_DENIED
    denied2 = read_as("user:stranger")
    assert denied2.ok is False, "未配置 subject → 空表白名单,全拒(fail closed)"
    assert denied2.error.kind is ToolErrorKind.DATA_ACCESS_DENIED


def test_allow_whitelist_glob_second_criterion():
    """allow(whitelist):glob 匹配域名,任一命中即过;空表 = 全拒(fail closed);
    白名单不放大 clearance。whitelist=None(缺省)与 D1 逐字一致,由上面 3x3 矩阵锚定。"""
    d = DataDomain(name="fs.secret", sensitivity=INTERNAL)
    assert allow(_p(CONFIDENTIAL), d, whitelist=["fs.*"])
    assert allow(_p(CONFIDENTIAL), d, whitelist=["net.*", "fs.secret"])  # 任一命中即过
    assert not allow(_p(CONFIDENTIAL), d, whitelist=["net.*"])  # clearance 够,白名单不命中
    assert not allow(_p(CONFIDENTIAL), d, whitelist=[])  # 空表 fail closed
    assert not allow(_p(PUBLIC), d, whitelist=["fs.*"])  # 白名单不放大 clearance


def test_data_policy_whitelist_for_fail_closed():
    """whitelist_for:配置原样返回;未配置 subject → 空表(fail closed)。"""
    policy = DataPolicy(whitelists={"user:a": ("fs.*", "net.pub")})
    assert policy.whitelist_for("user:a") == ("fs.*", "net.pub")
    assert policy.whitelist_for("user:b") == ()


# ---------------------------------------------------------------------------
# D2:net 域(§3.1/§3.3)——URL 前缀判定 + 未配置按 confidential + D1 回归锚
# ---------------------------------------------------------------------------


def _net_tools(text: str = "ok-content") -> LocalPythonToolRegistry:
    """带 MockTransport 的内置工具表(net 测试不碰真实网络,同 with_builtins 先例)。"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=text))
    return LocalPythonToolRegistry.with_builtins(http_transport=transport)


_NET_POLICY = DataPolicy(
    domains={"net.pub": DataDomain(name="net.pub", sensitivity=PUBLIC)},
    whitelists={"user:t": ("net.*",)},
    boundaries={"net.pub": ("net", "https://api.example.com/")},
)


def _bind_net_policy(tools: LocalPythonToolRegistry) -> None:
    """按 build_kernel 的装配形态接线:bind policy + 注册 URL 前缀边界。"""
    tools.bind_data_policy(_NET_POLICY)
    tools.register_net_domain(DataDomain(name="net.pub", sensitivity=PUBLIC), "https://api.example.com/")


def test_net_domain_url_prefix_hit_granted():
    """URL 前缀命中已配置 net 域 + clearance/白名单够 → 放行(fetch 真实执行)。"""
    tools = _net_tools()
    _bind_net_policy(tools)
    # NET 档工具过数据闸后还有三层权限闸(§5.2 串联):帧白名单放行,隔离测数据闸语义
    ctx = _dispatch_ctx(_frame(_p(PUBLIC)), allowed=["system.net.http_fetch"])
    result = asyncio.run(
        tools.dispatch(
            ToolCall(id="c1", name="system.net.http_fetch", args={"url": "https://api.example.com/x"}),
            ctx,
        )
    )
    assert result.ok
    assert "ok-content" in str(result.value)


def test_net_url_unmatched_confidential_denied_without_leak():
    """policy 在场(§3.1):URL 未命中任何已配置 net 域 → 按 confidential,
    clearance 不够即拒;拒绝面不泄 URL(域名/敏感度/subject/clearance 之外零信息)。"""
    tools = _net_tools()
    _bind_net_policy(tools)
    ctx = _dispatch_ctx(_frame(_p(INTERNAL)))
    result = asyncio.run(
        tools.dispatch(
            ToolCall(
                id="c1",
                name="system.net.http_fetch",
                args={"url": "https://other.example.org/s3cr3t-path"},
            ),
            ctx,
        )
    )
    assert result.ok is False
    assert result.error.kind is ToolErrorKind.DATA_ACCESS_DENIED
    blob = f"{result.error.message} {result.error.hint}"
    assert "net.unconfigured" in blob and "confidential" in blob
    assert "other.example.org" not in blob and "s3cr3t-path" not in blob  # 不泄 URL


def test_net_tool_without_policy_not_intercepted():
    """回归锚(§8 D1 实现注 1):未 bind policy → net 工具不拦截,
    注册了域也一样,行为与 D1 逐字一致(未配置 = 不启用数据层拦截)。"""
    tools = _net_tools("d1-content")
    tools.register_net_domain(
        DataDomain(name="net.sec", sensitivity=CONFIDENTIAL), "https://sec.example.com/"
    )
    ctx = _dispatch_ctx(_frame(_p(PUBLIC)), allowed=["system.net.http_fetch"])
    result = asyncio.run(
        tools.dispatch(
            ToolCall(id="c1", name="system.net.http_fetch", args={"url": "https://sec.example.com/a"}),
            ctx,
        )
    )
    assert result.ok
    assert "d1-content" in str(result.value)


# ---------------------------------------------------------------------------
# D2:审计信号(§6)与 _authz 判据回写(§3.2)
# ---------------------------------------------------------------------------


class _FakeBus:
    """最小信号总线:收集 emit 的 Signal(审计信号断言用;registry 只依赖 ``emit``)。"""

    def __init__(self) -> None:
        self.emitted: list = []

    async def emit(self, sig) -> None:
        self.emitted.append(sig)


def test_data_access_denied_signal_exact_payload():
    """拒绝 → data.access.denied,payload 恰为 {subject, domain, sensitivity, tool},
    不含 URL/路径/域内内容(不泄漏)。"""
    bus = _FakeBus()
    tools = _net_tools()
    tools.bind_signals(bus)
    _bind_net_policy(tools)
    ctx = _dispatch_ctx(_frame(_p(INTERNAL)))
    result = asyncio.run(
        tools.dispatch(
            ToolCall(
                id="c1",
                name="system.net.http_fetch",
                args={"url": "https://other.example.org/s3cr3t"},
            ),
            ctx,
        )
    )
    assert result.ok is False
    denied = [s for s in bus.emitted if s.name == DATA_ACCESS_DENIED]
    assert len(denied) == 1
    payload = denied[0].payload
    assert payload == {
        "subject": "user:t",
        "domain": "net.unconfigured",
        "sensitivity": CONFIDENTIAL,
        "tool": "system.net.http_fetch",
    }
    assert "other.example.org" not in json.dumps(payload, ensure_ascii=False)


def test_data_access_granted_signal_payload(tmp_path):
    """放行 → data.access.granted,payload {subject, domains, tool}(同样不泄目标)。
    bus 未装配 → 跳过发射:上面全部未 bind_signals 的用例即零破坏锚。"""
    bus = _FakeBus()
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    tools = LocalPythonToolRegistry.with_builtins()
    tools.bind_signals(bus)
    tools.bind_data_policy(
        DataPolicy(
            domains={"fs.shared": DataDomain(name="fs.shared", sensitivity=INTERNAL)},
            whitelists={"user:t": ("fs.*",)},
            boundaries={"fs.shared": ("fs", str(tmp_path))},
        )
    )
    tools.register_fs_domain(DataDomain(name="fs.shared", sensitivity=INTERNAL), tmp_path)
    ctx = _dispatch_ctx(_frame(_p(CONFIDENTIAL)), workdir=tmp_path)
    result = asyncio.run(
        tools.dispatch(ToolCall(id="c1", name="system.file.read", args={"path": "a.txt"}), ctx)
    )
    assert result.ok
    granted = [s for s in bus.emitted if s.name == DATA_ACCESS_GRANTED]
    assert len(granted) == 1
    payload = granted[0].payload
    assert payload == {"subject": "user:t", "domains": ["fs.shared"], "tool": "system.file.read"}
    assert "a.txt" not in json.dumps(payload, ensure_ascii=False)


def test_authz_record_written_into_credentials(tmp_path):
    """判据回写(§3.2):authZ 判定摘要写进 ``ctx.credentials["_authz"]``
    ("_" 前缀与 WS1 用户凭证键防撞名);principal 未注入(单用户语义)时检查
    不触及本调用 → 不落 "_authz"(零破坏)。"""
    seen: list = []
    tools = LocalPythonToolRegistry()

    @tools.tool(name="peek", permission=Permission.READ, data_domains=["fs.*"])
    def peek(path: str, ctx) -> str:
        """捕获 credentials。Use when 测试判据回写;Do not use when 其他。"""
        seen.append(dict(ctx.credentials))
        return "ok"

    tools.bind_data_policy(DataPolicy(whitelists={"user:t": ("fs.*",)}))
    ctx = _dispatch_ctx(_frame(_p(CONFIDENTIAL)), workdir=tmp_path)
    result = asyncio.run(tools.dispatch(ToolCall(id="c1", name="peek", args={"path": "x.txt"}), ctx))
    assert result.ok
    authz = seen[0]["_authz"]
    assert authz["allowed"] is True
    assert authz["domains"] == [{"name": "fs.workdir", "sensitivity": PUBLIC}]

    # principal 为 None(v1 单用户语义):数据层不触及 → credentials 无 "_authz"
    ctx2 = _dispatch_ctx(_frame(None), workdir=tmp_path)
    result2 = asyncio.run(
        tools.dispatch(ToolCall(id="c2", name="peek", args={"path": "x.txt"}), ctx2)
    )
    assert result2.ok
    assert "_authz" not in seen[1]

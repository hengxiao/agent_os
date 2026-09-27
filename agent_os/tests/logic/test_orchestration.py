"""代码编排锚点测试(docs/CODE-ORCHESTRATION.md):沙箱脚本 + 工具系统调用。

固定约定:

- ``python_orchestrate`` 是**内核拦截式伪工具**(不进 Tool Registry):脚本在
  SANDBOX 执行,脚本内 ``ctx.call_tool``/``ctx.invoke`` 经 syscall 通道陷入内核,
  由 ``_dispatch_call`` 代为分发——白名单、ToolGuard veto、信号、记账全部沿用;
- **无权限提升**(§2.3):可调集合 = 调用帧 manifest 白名单;白名单外折叠为
  PERMISSION_DENIED **返回脚本**(不崩整次编排);
- 中间变量留在沙箱,只有脚本的 ``result`` 回到帧上下文(§1.1 token 经济);
- Fail-Safe Default:``RunConfig.orchestrate`` 缺省 False,需显式开启;
- 限额:``limits.max_tool_calls`` 单次 syscall 上限;超限回报已执行清单;
- SANDBOX code 技能经同一通道获得 ctx,与 TRUSTED 档行为等价(§5)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from agent_os.api.v1 import (
    ORCHESTRATE_TOOL,
    POST_LOGIC_EXEC,
    POST_TOOL_CALL,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Mode,
    Permission,
    Role,
    RunConfig,
    SkillFrame,
    SkillRef,
    Stop,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.errors import RunAborted
from agent_os.sidecars import CodeScanner, ToolGuard
from tests.helpers.kernels import assemble, auto_approve, record_all, sandbox_tools

SKILLS_YAML = """
skills:
  - name: test.driver
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [python_orchestrate, system.file.read, system.file.write], skills: [test.echo] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10, max_tool_calls: 20 }
    prompt: DRIVER
  - name: test.echo
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { text: { type: string } }
      required: [text]
    outputs:
      type: object
      properties: { echoed: { type: string } }
      required: [echoed]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: ECHO
"""


def _yaml(tmp_path, body: str = SKILLS_YAML):
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _final(payload) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def orchestrating_brain(script: str):
    """首轮发编排调用,拿到结果后把它作为最终答案回传(便于断言脚本产出)。"""

    def brain(req: ChatRequest) -> ChatResponse:
        if (req.messages[0].content or "").startswith("ECHO"):
            text = json.loads(req.messages[1].content)["text"]
            return _final({"echoed": text.upper()})
        tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
        if not tool_msgs:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(id="c1", name=ORCHESTRATE_TOOL, args={"code": script, "timeout": 30})
                    ],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return _final(json.loads(tool_msgs[-1].content))

    return brain


def _kernel(tmp_path, script: str, *, orchestrate: bool = True, sidecars=(), body=SKILLS_YAML):
    config = RunConfig(
        model="mock/x",
        orchestrate=orchestrate,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config,
        orchestrating_brain(script),
        _yaml(tmp_path, body),
        tools=sandbox_tools(builtins=True),
        sidecars=sidecars,
    )


def _run(kernel):
    return asyncio.run(kernel.run("test.driver", {}))


# ---------------------------------------------------------------------------
# 基本能力:脚本内调工具 / 调子技能
# ---------------------------------------------------------------------------


def test_script_calls_tool_via_syscall(tmp_path):
    """脚本经 ctx.call_tool 调白名单工具,结果回脚本;只有 result 回上下文。"""
    script = """
w = ctx.call_tool("system.file.write", {"path": "a.txt", "content": "hello syscall"})
assert w["ok"], w
r = ctx.call_tool("system.file.read", {"path": "a.txt"})
result = {"ok": r["ok"], "has": "syscall" in r["value"]}
"""
    kernel = _kernel(tmp_path, script)
    out = _run(kernel)["value"]
    assert out["result"] == {"ok": True, "has": True}
    assert out["calls"] == 2 and out["failed"] == []


def test_script_invokes_subskill(tmp_path):
    """脚本经 ctx.invoke 压子帧(白名单内),子帧结果回脚本。"""
    script = """
out = ctx.invoke("test.echo", {"text": "abc"})
result = {"echoed": out["echoed"]}
"""
    kernel = _kernel(tmp_path, script)
    assert _run(kernel)["value"]["result"] == {"echoed": "ABC"}


def test_loop_keeps_intermediates_out_of_context(tmp_path):
    """核心 token 经济锚点:10 次工具调用,父帧只多一条 tool result。"""
    script = """
total = 0
for i in range(5):
    ctx.call_tool("system.file.write", {"path": "f%d.txt" % i, "content": "x" * (i + 1)})
    r = ctx.call_tool("system.file.read", {"path": "f%d.txt" % i})
    total += r["value"].count("x")
result = {"total": total}
"""
    kernel = _kernel(tmp_path, script)
    seen = record_all(kernel)
    out = _run(kernel)["value"]
    assert out["result"] == {"total": 15} and out["calls"] == 10

    mock = kernel.providers.providers["mock"]
    tool_msgs = [m for req in mock.recorded for m in req.messages if m.role is Role.TOOL]
    # 去重(同一帧的消息在多轮请求里重复出现):父帧上下文里只有一条编排结果
    assert len({m.content for m in tool_msgs}) == 1
    # 但信号面看得到全部 10 次调用(sidecar 观察粒度不变)
    dispatched = [s for s in seen if s.name == POST_TOOL_CALL]
    assert len(dispatched) == 10


# ---------------------------------------------------------------------------
# 权限:无提升(§2.3)
# ---------------------------------------------------------------------------


def test_out_of_whitelist_tool_denied_into_script(tmp_path):
    """白名单外工具 → PERMISSION_DENIED 返回脚本,脚本可自行处理,不崩编排。"""
    script = """
r = ctx.call_tool("system.shell.exec", {"command": "echo pwned"})
result = {"ok": r["ok"], "kind": r["error"]["kind"]}
"""
    kernel = _kernel(tmp_path, script)
    out = _run(kernel)["value"]
    assert out["result"] == {"ok": False, "kind": "permission_denied"}
    assert out["failed"] == [{"name": "system.shell.exec", "error": "permission_denied"}]


def test_orchestrate_disabled_by_default(tmp_path):
    """Fail-Safe Default:未显式开启时拒绝执行(§5 配置开关)。"""
    kernel = _kernel(tmp_path, "result = 1", orchestrate=False)
    out = _run(kernel)
    assert out["ok"] is False and out["error"]["kind"] == "permission_denied"
    assert "未启用" in out["error"]["message"]


def test_tool_guard_vetoes_syscall(tmp_path):
    """ToolGuard 对 syscall 生效:veto 理由回到脚本(§2.3 同一闸门)。"""
    script = """
r = ctx.call_tool("system.file.write", {"path": "secret.env", "content": "x"})
result = {"ok": r["ok"], "kind": r["error"]["kind"], "msg": r["error"]["message"]}
"""
    guard = ToolGuard(rules=[("system.file.write", r"secret", "禁止写 secret 文件")])
    kernel = _kernel(tmp_path, script, sidecars=(guard,))
    out = _run(kernel)["value"]["result"]
    assert out["ok"] is False and out["kind"] == "vetoed"
    assert "禁止写 secret" in out["msg"]


def test_code_scanner_vetoes_orchestration_script(tmp_path):
    """CodeScanner 在 pre:logic.exec 扫描编排脚本源码,危险模式不执行(§9.5)。"""
    script = "import os\nos.system('echo pwned')\nresult = 1"
    kernel = _kernel(tmp_path, script, sidecars=(CodeScanner(),))
    seen = record_all(kernel)
    out = _run(kernel)
    assert out["ok"] is False and out["error"]["kind"] == "vetoed"
    assert not any(s.name == POST_LOGIC_EXEC for s in seen)


# ---------------------------------------------------------------------------
# 限额与失败语义(§4)
# ---------------------------------------------------------------------------


def test_max_tool_calls_limit(tmp_path):
    """超 syscall 上限 → 编排失败,回报已执行调用数(manifest limits 生效)。"""
    script = """
last = None
for i in range(50):
    r = ctx.call_tool("system.file.write", {"path": "x.txt", "content": str(i)})
    if not r["ok"]:
        last = r["error"]["message"]
result = {"last_error": last}
"""
    kernel = _kernel(tmp_path, script)  # manifest: max_tool_calls = 20
    out = _run(kernel)["value"]
    assert out["calls"] == 20, "超限后不再分发"
    assert out["limit_hit"] is True
    # 限额以结构化错误回脚本(不断管),脚本可自行收敛;拒绝不计入工具失败清单
    assert out["failed"] == []
    assert "上限" in out["result"]["last_error"]


def test_script_runtime_error_reports_executed_calls(tmp_path):
    """脚本崩溃:已执行副作用已发生,回报清单供模型决定补偿(§4.3)。"""
    script = """
ctx.call_tool("system.file.write", {"path": "done.txt", "content": "side effect"})
raise ValueError("脚本炸了")
"""
    kernel = _kernel(tmp_path, script)
    out = _run(kernel)
    assert out["ok"] is False
    assert out["value"]["calls"] == 1
    assert "脚本炸了" in (out["error"]["hint"] or "") or "脚本炸了" in out["error"]["message"]


def test_empty_code_rejected(tmp_path):
    kernel = _kernel(tmp_path, "   ")
    out = _run(kernel)
    assert out["ok"] is False and out["error"]["kind"] == "invalid_args"


# ---------------------------------------------------------------------------
# 呈现面:LLM 可见 schema(消融开关联动)
# ---------------------------------------------------------------------------


def _visible_tools(kernel) -> set[str]:
    """帧首次 build 时模型可见的工具名集合。"""
    frame = SkillFrame(frame_id="f1", run_id="r1", skill=SkillRef(name="test.driver"), input={})
    req = asyncio.run(kernel.context.build(frame))
    return {t["name"] for t in req.tools}


def test_schema_visible_only_when_enabled(tmp_path):
    """伪工具不在 registry,由 ContextManager 按声明 + 开关补进可见面(§2.1/§5)。"""
    assert ORCHESTRATE_TOOL in _visible_tools(_kernel(tmp_path, "result = 1"))
    assert ORCHESTRATE_TOOL not in _visible_tools(
        _kernel(tmp_path, "result = 1", orchestrate=False)
    )


# ---------------------------------------------------------------------------
# system.python.exec 回归:纯计算档行为不变
# ---------------------------------------------------------------------------


def test_python_exec_unchanged_no_ctx(tmp_path):
    """system.python.exec 仍是纯计算:脚本里没有 ctx(NameError → RUNTIME_ERROR)。"""
    from agent_os.api.v1 import ExecRequest
    from agent_os.logic.python_sandbox import PythonSandboxLogicKernel

    kernel = PythonSandboxLogicKernel()
    ok = asyncio.run(kernel.execute(ExecRequest(source="print(1 + 1)")))
    assert ok.error is None and "2" in ok.stdout

    bad = asyncio.run(kernel.execute(ExecRequest(source="ctx.call_tool('x', {})")))
    assert bad.error is not None and "NameError" in (bad.error.traceback or "")


# ---------------------------------------------------------------------------
# SANDBOX code 技能:经同一通道获得 ctx(§5 两档契约对齐)
# ---------------------------------------------------------------------------

SANDBOX_SKILL_YAML = """
skills:
  - name: test.driver
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:double_it
    logic: { mode: sandbox }
    inputs:
      type: object
      properties: { x: { type: integer } }
      required: [x]
    outputs:
      type: object
      properties: { doubled: { type: integer } }
      required: [doubled]
    permissions: { tools: [system.python.exec], skills: [] }
"""


def test_sandbox_code_skill_gets_ctx(tmp_path):
    """``logic: {mode: sandbox}`` 的 code 技能经 syscall 桥调工具——

    v1 语义下 ctx=None 会 AttributeError;现在与 TRUSTED 档行为等价。
    """
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(
        config,
        orchestrating_brain("result = 1"),
        _yaml(tmp_path, SANDBOX_SKILL_YAML),
        tools=sandbox_tools(),
    )
    assert asyncio.run(kernel.run("test.driver", {"x": 21})) == {"doubled": 42}


@pytest.mark.parametrize("force", [False, True])
def test_trusted_and_sandbox_equivalent(tmp_path, force):
    """同一 handler 在 TRUSTED / force_sandbox 两档结果一致(契约对齐锚点)。"""
    body = SANDBOX_SKILL_YAML.replace("    logic: { mode: sandbox }\n", "")
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    config.logic_policy.force_sandbox = force
    kernel = assemble(
        config, orchestrating_brain("result = 1"), _yaml(tmp_path, body), tools=sandbox_tools()
    )
    assert asyncio.run(kernel.run("test.driver", {"x": 5})) == {"doubled": 10}


# ---------------------------------------------------------------------------
# 端到端:checkpoint / replay 零改动(§3 持久化)
# ---------------------------------------------------------------------------


def test_replay_run_with_orchestration(tmp_path, capsys):
    """含编排调用的 run 可 replay 且 diff 为空——编排在父帧就是一条 tool call。"""
    from tests.helpers.config import run_cli, write_config

    cfg = write_config(
        tmp_path,
        brain="tests.logic.test_orchestration:replay_brain",
        skills=_yaml(tmp_path),
        builtins=True,
        orchestrate=True,
    )
    artifacts = str(tmp_path / "runs")
    rc, out = run_cli(capsys, "run", "test.driver", "--input", "{}",
                      "--config", str(cfg), "--artifacts", artifacts, "--json")
    assert rc == 0, out
    rc, rep = run_cli(capsys, "replay", out["run_id"],
                      "--config", str(cfg), "--artifacts", artifacts, "--json")
    assert rc == 0
    rc, d = run_cli(capsys, "diff", out["run_id"], rep["run_id"],
                    "--artifacts", artifacts, "--json")
    assert rc == 0 and d["result_equal"] is True and d["signals_equal"] is True


#: replay 测试用的模块级 brain(dotted path 需可 import)
replay_brain = orchestrating_brain(
    'r = ctx.call_tool("system.file.write", {"path": "o.txt", "content": "x"})\nresult = r["ok"]'
)


# ---------------------------------------------------------------------------
# §3.2 硬失败传播边界(CODE-ORCHESTRATION §6 锚点 7)
# ---------------------------------------------------------------------------


class _HardStop:
    """SYNC sidecar:在 pre:tool.call 返回 Stop → _dispatch_call 内部抛 RunAborted。"""

    name = "hardstop"
    subscriptions = ("pre:tool.call",)
    mode = Mode.SYNC
    priority = 10
    needs_free_text = False

    async def on_signal(self, sig, ctl):
        return Stop("测试:编排期间硬停止")


def test_hard_failure_escapes_orchestration(tmp_path):
    """编排期间的 RunAborted **不得**被降级为脚本可无视的错误。

    回归的是一个真实缺陷:``_serve_syscalls`` 曾用 ``except Exception`` 把
    RunAborted/MaxDepthExceeded 一并转成 ``{"kind": "internal"}`` 回给脚本,
    而 ``_run_orchestration`` 从不检查 server 任务——脚本捕获后照常算完,
    run 正常返回 ``{"done": true}``,预算超限与 ToolGuard 的 Stop 就此失效。
    """
    script = """
r = ctx.call_tool("system.file.write", {"path": "x.txt", "content": "1"})
result = {"swallowed": True, "err": (r.get("error") or {}).get("kind")}
"""
    kernel = _kernel(tmp_path, script, sidecars=(_HardStop(),))
    with pytest.raises(RunAborted, match="编排期间硬停止"):
        _run(kernel)


def test_ordinary_tool_failure_still_reaches_script(tmp_path):
    """对照组:**普通**失败仍折叠为脚本可处置的错误观察,不中止 run。"""
    script = """
r = ctx.call_tool("system.file.read", {"path": "not-there.txt"})
result = {"ok": r["ok"], "kind": r["error"]["kind"]}
"""
    out = _run(_kernel(tmp_path, script))["value"]
    assert out["result"]["ok"] is False
    assert out["result"]["kind"] == "not_found"


# ---------------------------------------------------------------------------
# W5-WS1 帧控制面:ctx.cancel / ctx.frame_status 经 syscall 桥(§2.2 kind 路由)
# ---------------------------------------------------------------------------

#: Usage 九字段(§14.1 冻结清单;frame_status 响应形态锚)
USAGE_KEYS = sorted(
    [
        "steps",
        "prompt_tokens",
        "completion_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "thinking_tokens",
        "cost",
        "ttft_ms",
        "total_ms",
    ]
)


def test_orchestration_script_ctx_cancel_frame_status(tmp_path):
    """编排脚本(_SyncCtx)经 syscall 桥调 cancel/frame_status:kind 路由两端注册,
    响应形态(未知帧 → 空 ack / ``status=None`` 同形字典)端到端成立;计入调用限额。"""
    script = """
st = ctx.frame_status("no-such-frame")
ack = ctx.cancel("no-such-frame", "脚本测试")
result = {"status": st["status"], "ack": ack, "usage_keys": sorted(st["usage"].keys())}
"""
    out = _run(_kernel(tmp_path, script))["value"]
    assert out["result"]["status"] is None and out["result"]["ack"] == []
    assert out["result"]["usage_keys"] == USAGE_KEYS
    assert out["calls"] == 2 and out["failed"] == []


SANDBOX_PROBE_YAML = """
skills:
  - name: test.sbx_probe
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:sandbox_cancel_probe
    logic: { mode: sandbox }
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [], skills: [] }
"""


def test_sandbox_code_skill_ctx_cancel_frame_status(tmp_path):
    """SANDBOX 档 code 技能(_AsyncCtx)经同一 syscall 桥调到 cancel/frame_status——
    与 TRUSTED 档契约对齐(§5);真实帧语义由 tests/kernel/test_ctx_cancel_status.py 覆盖。
    """
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(
        config,
        orchestrating_brain("result = 1"),
        _yaml(tmp_path, SANDBOX_PROBE_YAML),
        tools=sandbox_tools(),
    )
    result = asyncio.run(kernel.run("test.sbx_probe", {}))
    assert result == {"ack": [], "status": None, "usage_keys": USAGE_KEYS}


# ---------------------------------------------------------------------------
# §3.4 编排原语:ctx.spawn / ctx.wait / ctx.parallel 经 syscall 桥
# ---------------------------------------------------------------------------

SPAWN_SKILLS_YAML = """
skills:
  - name: test.driver
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [python_orchestrate], skills: [test.echo, test.slow_echo] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10, max_tool_calls: 20 }
    prompt: DRIVER
  - name: test.echo
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { text: { type: string } }
      required: [text]
    outputs:
      type: object
      properties: { echoed: { type: string } }
      required: [echoed]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: ECHO
  - name: test.slow_echo
    version: 1.0.0
    kind: prompt
    description: 调一个慢工具再给最终答案(spawn 后 frame_status 轮询命中在跑时间窗)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [test.slow_tool], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: SLOW
  - name: test.outside
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: OUTSIDE
"""


def spawn_brain(script: str):
    """orchestrating_brain 的 spawn 测试变体:额外应答 test.echo / test.slow_echo 子帧。"""

    def brain(req: ChatRequest) -> ChatResponse:
        first = req.messages[0].content or ""
        if first.startswith("ECHO"):
            text = json.loads(req.messages[1].content)["text"]
            return _final({"echoed": text.upper()})
        if first.startswith("SLOW"):
            if not any(m.role is Role.TOOL for m in req.messages):
                return ChatResponse(
                    message=Message(
                        role=Role.ASSISTANT,
                        tool_calls=[
                            ToolCall(id="slow-1", name="test.slow_tool", args={"seconds": 1})
                        ],
                    ),
                    finish_reason="tool_calls",
                    usage=ChatUsage(prompt=1, completion=1),
                )
            return _final({"done": True})
        tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
        if not tool_msgs:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(id="c1", name=ORCHESTRATE_TOOL, args={"code": script, "timeout": 30})
                    ],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return _final(json.loads(tool_msgs[-1].content))

    return brain


def _spawn_tools():
    """sandbox 工具 + test.slow_tool(async 睡眠,spawn 子帧的在跑时间窗)。"""
    reg = sandbox_tools(builtins=True)

    @reg.tool(name="test.slow_tool", permission=Permission.READ, timeout=30)
    async def slow_tool(seconds: int) -> str:
        """睡眠 seconds 秒后返回(spawn 子帧的在跑时间窗)。"""
        await asyncio.sleep(seconds)
        return "slept"

    return reg


def _spawn_kernel(tmp_path, script: str, *, body=SPAWN_SKILLS_YAML):
    config = RunConfig(
        model="mock/x",
        orchestrate=True,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config,
        spawn_brain(script),
        _yaml(tmp_path, body),
        tools=_spawn_tools(),
        # spawn 升权闸(docs/ESCALATION.md §3):自动批准通道等价于生产宿主里人每次放行
        supervisor=auto_approve,
    )


def test_orchestration_script_spawn_wait(tmp_path):
    """编排脚本(_SyncCtx)经 syscall 桥 spawn/wait(§3.4):spawn 慢技能 →
    frame_status 观察 → wait 收值,子帧返回值端到端回脚本。"""
    script = """
fid = ctx.spawn("test.slow_echo", {})
st = ctx.frame_status(fid)
value = ctx.wait(fid)
result = {"frame_id": fid, "seen": st["status"], "value": value}
"""
    out = _run(_spawn_kernel(tmp_path, script))["value"]
    assert out["result"]["seen"] in ("pending", "running")
    assert out["result"]["value"] == {"done": True}
    assert out["calls"] == 3 and out["failed"] == []


def test_spawn_out_of_whitelist_denied_into_script(tmp_path):
    """spawn 白名单外技能 → 折叠为脚本可捕获的 RuntimeError,run 不崩(§2.3 无提升)。"""
    script = """
try:
    ctx.spawn("test.outside", {})
    result = {"raised": False}
except RuntimeError as e:
    result = {"raised": True, "msg": str(e)}
"""
    out = _run(_spawn_kernel(tmp_path, script))["value"]
    assert out["result"]["raised"] is True
    assert "白名单" in out["result"]["msg"]
    assert out["calls"] == 1


def test_wait_cancelled_frame_raises_cancelled(tmp_path):
    """wait 被取消的子帧 → RuntimeError 带 cancelled 前缀(脚本可捕获继续结算)。"""
    script = """
fid = ctx.spawn("test.slow_echo", {})
ctx.cancel(fid, "脚本主动取消")
try:
    ctx.wait(fid)
    result = {"raised": False}
except RuntimeError as e:
    result = {"raised": True, "msg": str(e)}
"""
    out = _run(_spawn_kernel(tmp_path, script))["value"]
    assert out["result"]["raised"] is True
    assert out["result"]["msg"].startswith("cancelled: ")
    assert out["calls"] == 3


def test_parallel_invalid_batch_shape_raises(tmp_path):
    """parallel 批形态错(branches 非 list)→ invalid_args 折叠,脚本侧 RuntimeError。"""
    script = """
try:
    ctx.parallel("not-a-list")
    result = {"raised": False}
except RuntimeError as e:
    result = {"raised": True, "msg": str(e)}
"""
    out = _run(_spawn_kernel(tmp_path, script))["value"]
    assert out["result"]["raised"] is True
    assert "branches" in out["result"]["msg"]
    assert out["calls"] == 1


def test_spawn_wait_parallel_count_toward_limit(tmp_path):
    """spawn/wait/parallel 与工具调用共用同一计数:限额内各消耗一次,第 4 次被拒。"""
    script = """
fid = ctx.spawn("test.echo", {"text": "x"})
value = ctx.wait(fid)
batch = ctx.parallel([{"skill": "test.echo", "input": {"text": "y"}}])
try:
    ctx.spawn("test.echo", {"text": "z"})
    denied = None
except RuntimeError as e:
    denied = str(e)
result = {"value": value, "batch_ok": batch[0]["ok"], "denied": denied}
"""
    body = SPAWN_SKILLS_YAML.replace("max_tool_calls: 20", "max_tool_calls: 3")
    out = _run(_spawn_kernel(tmp_path, script, body=body))["value"]
    assert out["calls"] == 3 and out["limit_hit"] is True
    assert out["result"]["value"] == {"echoed": "X"}
    assert out["result"]["batch_ok"] is True
    assert "上限" in out["result"]["denied"]


SANDBOX_SPAWN_YAML = """
skills:
  - name: test.sbx_spawn
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:sandbox_spawn_wait_probe
    logic: { mode: sandbox }
    inputs:
      type: object
      properties: { skill: { type: string }, args: { type: object } }
      required: [skill]
    outputs: { type: object }
    permissions: { tools: [], skills: [test.echo] }
  - name: test.echo
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { text: { type: string } }
      required: [text]
    outputs:
      type: object
      properties: { echoed: { type: string } }
      required: [echoed]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: ECHO
"""

SANDBOX_PARALLEL_YAML = """
skills:
  - name: test.sbx_parallel
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:sandbox_parallel_probe
    logic: { mode: sandbox }
    inputs:
      type: object
      properties: { branches: { type: array }, kw: { type: object } }
      required: [branches]
    outputs: { type: object }
    permissions: { tools: [], skills: [test.echo] }
  - name: test.echo
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { text: { type: string } }
      required: [text]
    outputs:
      type: object
      properties: { echoed: { type: string } }
      required: [echoed]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: ECHO
"""


def _sandbox_probe_kernel(tmp_path, body):
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config,
        orchestrating_brain("result = 1"),
        _yaml(tmp_path, body),
        tools=sandbox_tools(),
        supervisor=auto_approve,
    )


def test_sandbox_code_skill_spawn_wait(tmp_path):
    """SANDBOX code 技能(_AsyncCtx)经 syscall 桥 spawn+wait 全流程取回子帧返回值。"""
    kernel = _sandbox_probe_kernel(tmp_path, SANDBOX_SPAWN_YAML)
    result = asyncio.run(kernel.run("test.sbx_spawn", {"skill": "test.echo", "args": {"text": "abc"}}))
    assert result["value"] == {"echoed": "ABC"}
    assert result["frame_id"]


def test_sandbox_code_skill_parallel(tmp_path):
    """SANDBOX parallel(§3.4):两分支结果按序返回;一分支失败折叠 ok=False 不炸批。"""
    kernel = _sandbox_probe_kernel(tmp_path, SANDBOX_PARALLEL_YAML)
    result = asyncio.run(
        kernel.run(
            "test.sbx_parallel",
            {
                "branches": [
                    {"skill": "test.echo", "input": {"text": "a"}},
                    {"skill": "test.echo", "input": {"text": "b"}},
                    {"skill": "test.echo", "input": {}},  # 缺 text:分支级预检失败
                ]
            },
        )
    )
    results = result["results"]
    assert [r["ok"] for r in results] == [True, True, False]
    assert results[0]["value"] == {"echoed": "A"}
    assert results[1]["value"] == {"echoed": "B"}
    assert results[2]["error"]["kind"] == "invalid_args"

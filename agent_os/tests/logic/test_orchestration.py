"""代码编排锚点测试(CODE-ORCHESTRATION.md):沙箱脚本 + 工具系统调用。

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
from tests.helpers.kernels import assemble, record_all, sandbox_tools

SKILLS_YAML = """
skills:
  - name: driver
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [python_orchestrate, fs_read, fs_write], skills: [echo] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10, max_tool_calls: 20 }
    prompt: DRIVER
  - name: echo
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
    return asyncio.run(kernel.run("driver", {}))


# ---------------------------------------------------------------------------
# 基本能力:脚本内调工具 / 调子技能
# ---------------------------------------------------------------------------


def test_script_calls_tool_via_syscall(tmp_path):
    """脚本经 ctx.call_tool 调白名单工具,结果回脚本;只有 result 回上下文。"""
    script = """
w = ctx.call_tool("fs_write", {"path": "a.txt", "content": "hello syscall"})
assert w["ok"], w
r = ctx.call_tool("fs_read", {"path": "a.txt"})
result = {"ok": r["ok"], "has": "syscall" in r["value"]}
"""
    kernel = _kernel(tmp_path, script)
    out = _run(kernel)["value"]
    assert out["result"] == {"ok": True, "has": True}
    assert out["calls"] == 2 and out["failed"] == []


def test_script_invokes_subskill(tmp_path):
    """脚本经 ctx.invoke 压子帧(白名单内),子帧结果回脚本。"""
    script = """
out = ctx.invoke("echo", {"text": "abc"})
result = {"echoed": out["echoed"]}
"""
    kernel = _kernel(tmp_path, script)
    assert _run(kernel)["value"]["result"] == {"echoed": "ABC"}


def test_loop_keeps_intermediates_out_of_context(tmp_path):
    """核心 token 经济锚点:10 次工具调用,父帧只多一条 tool result。"""
    script = """
total = 0
for i in range(5):
    ctx.call_tool("fs_write", {"path": "f%d.txt" % i, "content": "x" * (i + 1)})
    r = ctx.call_tool("fs_read", {"path": "f%d.txt" % i})
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
r = ctx.call_tool("shell_exec", {"command": "echo pwned"})
result = {"ok": r["ok"], "kind": r["error"]["kind"]}
"""
    kernel = _kernel(tmp_path, script)
    out = _run(kernel)["value"]
    assert out["result"] == {"ok": False, "kind": "permission_denied"}
    assert out["failed"] == [{"name": "shell_exec", "error": "permission_denied"}]


def test_orchestrate_disabled_by_default(tmp_path):
    """Fail-Safe Default:未显式开启时拒绝执行(§5 配置开关)。"""
    kernel = _kernel(tmp_path, "result = 1", orchestrate=False)
    out = _run(kernel)
    assert out["ok"] is False and out["error"]["kind"] == "permission_denied"
    assert "未启用" in out["error"]["message"]


def test_tool_guard_vetoes_syscall(tmp_path):
    """ToolGuard 对 syscall 生效:veto 理由回到脚本(§2.3 同一闸门)。"""
    script = """
r = ctx.call_tool("fs_write", {"path": "secret.env", "content": "x"})
result = {"ok": r["ok"], "kind": r["error"]["kind"], "msg": r["error"]["message"]}
"""
    guard = ToolGuard(rules=[("fs_write", r"secret", "禁止写 secret 文件")])
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
    r = ctx.call_tool("fs_write", {"path": "x.txt", "content": str(i)})
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
ctx.call_tool("fs_write", {"path": "done.txt", "content": "side effect"})
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
    frame = SkillFrame(frame_id="f1", run_id="r1", skill=SkillRef(name="driver"), input={})
    req = asyncio.run(kernel.context.build(frame))
    return {t["name"] for t in req.tools}


def test_schema_visible_only_when_enabled(tmp_path):
    """伪工具不在 registry,由 ContextManager 按声明 + 开关补进可见面(§2.1/§5)。"""
    assert ORCHESTRATE_TOOL in _visible_tools(_kernel(tmp_path, "result = 1"))
    assert ORCHESTRATE_TOOL not in _visible_tools(
        _kernel(tmp_path, "result = 1", orchestrate=False)
    )


# ---------------------------------------------------------------------------
# python_exec 回归:纯计算档行为不变
# ---------------------------------------------------------------------------


def test_python_exec_unchanged_no_ctx(tmp_path):
    """python_exec 仍是纯计算:脚本里没有 ctx(NameError → RUNTIME_ERROR)。"""
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
  - name: driver
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
    permissions: { tools: [python_exec], skills: [] }
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
    assert asyncio.run(kernel.run("driver", {"x": 21})) == {"doubled": 42}


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
    assert asyncio.run(kernel.run("driver", {"x": 5})) == {"doubled": 10}


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
    rc, out = run_cli(capsys, "run", "driver", "--input", "{}",
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
    'r = ctx.call_tool("fs_write", {"path": "o.txt", "content": "x"})\nresult = r["ok"]'
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
r = ctx.call_tool("fs_write", {"path": "x.txt", "content": "1"})
result = {"swallowed": True, "err": (r.get("error") or {}).get("kind")}
"""
    kernel = _kernel(tmp_path, script, sidecars=(_HardStop(),))
    with pytest.raises(RunAborted, match="编排期间硬停止"):
        _run(kernel)


def test_ordinary_tool_failure_still_reaches_script(tmp_path):
    """对照组:**普通**失败仍折叠为脚本可处置的错误观察,不中止 run。"""
    script = """
r = ctx.call_tool("fs_read", {"path": "not-there.txt"})
result = {"ok": r["ok"], "kind": r["error"]["kind"]}
"""
    out = _run(_kernel(tmp_path, script))["value"]
    assert out["result"]["ok"] is False
    assert out["result"]["kind"] == "not_found"

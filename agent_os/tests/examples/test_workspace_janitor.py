"""全真场景示例锚点测试:examples/workspace_janitor(工作区大扫除)。

与 support_desk 的本质区别:**零 mock 工具**——file.list/write/delete 与
shell.exec 全部真跑,fixture 只是测试数据(scratch 树)与演示进程(sleep);
爆炸半径由 ``RunConfig.workdir`` 沙箱圈住,测试结束清理进程与目录树。

剧情(ops.scan.workspace 根技能,L1):
  巡检 list → 两次 plan.write(L2,演示 approve-run:第一次问,第二次 Grant 命中)
  → cleanup.execute dry_run + 真删(L3,每次必问,无 approve-run)
  → service.stop kill 演示进程(L3,每次必问)。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolCall,
    ToolPolicy,
    derive_skill_tier,
    derive_tools_tier,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.helpers.kernels import PROJECT_ROOT

JANITOR_DIR = PROJECT_ROOT / "examples" / "workspace_janitor"

#: 应被清理的后缀(与 skills.yaml 根技能 prompt 的判据一致)
_STALE_SUFFIXES = (".tmp", ".cache")


def _fixture(tmp_path: Path) -> dict:
    """生成真实 scratch 树 + 演示进程;返回清单(测试结束经 _cleanup 收尾)。"""
    sys.path.insert(0, str(JANITOR_DIR))
    try:
        import make_fixture

        return make_fixture.create(tmp_path)
    finally:
        sys.path.remove(str(JANITOR_DIR))


def _cleanup(manifest: dict) -> None:
    """收尾:演示进程幂等 kill(剧情可能已停);scratch 树留给 tmp_path 回收。"""
    pid = int(manifest.get("pid") or 0)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def _process_alive(pid: int) -> bool:
    """进程是否真活着。

    不能只用 ``os.kill(pid, 0)``:被 kill 的 sleep 在父进程(pytest)wait 之前是
    僵尸,kill(pid, 0) 对僵尸不报错——按 ``/proc/<pid>/stat`` 的 state 判,Z 也算死。
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(")", 1)[-1].split()[0] != "Z"
    except OSError:
        return False


def _is_stale(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(_STALE_SUFFIXES) or ".log." in name


def janitor_brain(req: ChatRequest) -> ChatResponse:
    """数据驱动剧本:根帧按真实 list 结果找出陈旧文件再走全流程;

    子帧按各自输入参数执行(write/delete/kill 全真)。
    """
    system = req.messages[0].content if req.messages else ""

    def resp(content=None, calls=None):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=content or "",
                tool_calls=calls or [],
            ),
            finish_reason="tool_calls" if calls else "stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    tool_results = [
        json.loads(m.content) for m in req.messages if m.role is Role.TOOL
    ]

    if "# skill: ops.plan.write" in system:
        args = json.loads(req.messages[1].content)
        if not tool_results:
            return resp(calls=[
                ToolCall(id="w1", name="system.file.write",
                         args={"path": args["path"], "content": args["content"]})
            ])
        return resp(content=json.dumps({"written": args["path"]}))

    if "# skill: ops.cleanup.execute" in system:
        args = json.loads(req.messages[1].content)
        targets = list(args["targets"])
        if args.get("dry_run"):
            return resp(content=json.dumps({"destroyed": [], "would_destroy": targets}))
        done = [r for r in tool_results if r.get("ok")]
        if len(done) < len(targets):
            return resp(calls=[
                ToolCall(id=f"d{len(done)}", name="system.file.delete",
                         args={"path": targets[len(done)]})
            ])
        return resp(content=json.dumps({"destroyed": targets, "would_destroy": []}))

    if "# skill: ops.service.stop" in system:
        args = json.loads(req.messages[1].content)
        if args.get("dry_run"):
            return resp(content=json.dumps({"stopped": False, "pid": args["pid"]}))
        if not tool_results:
            return resp(calls=[
                ToolCall(id="k1", name="system.shell.exec",
                         args={"command": f"kill {args['pid']}"})
            ])
        ok = tool_results[-1].get("ok", False)
        return resp(content=json.dumps({"stopped": bool(ok), "pid": args["pid"]}))

    # —— 根帧(ops.scan.workspace)——
    calls = [tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls]
    names = [tc.name for tc in calls]
    args0 = json.loads(req.messages[1].content)
    scratch_rel = Path(args0["scratch_dir"]).name  # workdir 内的相对路径
    if "system.file.list" not in names:
        return resp(calls=[
            ToolCall(id="s1", name="system.file.list", args={"path": scratch_rel})
        ])
    listing = tool_results[0]["value"]
    stale = [e["path"] for e in listing["entries"] if not e["is_dir"] and _is_stale(e["path"])]
    plans = [n for n in names if n == "skill.ops.plan.write"]
    if not plans:
        return resp(calls=[
            ToolCall(id="p1", name="skill.ops.plan.write", args={
                "path": "plan.md",
                "content": "# 清理计划\n将删:\n" + "\n".join(f"- {p}" for p in stale),
            })
        ])
    if len(plans) == 1:
        return resp(calls=[
            ToolCall(id="p2", name="skill.ops.plan.write", args={
                "path": "inspect-log.md",
                "content": f"# 巡检日志\n扫描 {scratch_rel}:共 {listing['total']} 项,"
                           f"陈旧 {len(stale)} 项",
            })
        ])
    cleanups = [n for n in names if n == "skill.ops.cleanup.execute"]
    if not cleanups:
        return resp(calls=[
            ToolCall(id="c1", name="skill.ops.cleanup.execute",
                     args={"targets": stale, "dry_run": True})
        ])
    if len(cleanups) == 1:
        return resp(calls=[
            ToolCall(id="c2", name="skill.ops.cleanup.execute",
                     args={"targets": stale, "dry_run": False})
        ])
    if "skill.ops.service.stop" not in names:
        return resp(calls=[
            ToolCall(id="k1", name="skill.ops.service.stop",
                     args={"pid": args0["pid"], "dry_run": False})
        ])
    # 收尾:真删那次的结果与 stop 结果决定 status(skill 调用结果在 tool_results 里)
    cleanup_results = [r for r in tool_results if isinstance(r.get("value"), dict)
                       and "destroyed" in (r.get("value") or {})]
    real_cleanup = cleanup_results[-1] if cleanup_results else {"ok": False}
    destroyed = (real_cleanup.get("value") or {}).get("destroyed", []) if real_cleanup.get("ok") else []
    stop_results = [r for r in tool_results if isinstance(r.get("value"), dict)
                    and "stopped" in (r.get("value") or {})]
    stopped = bool(stop_results and stop_results[-1].get("ok")
                   and (stop_results[-1]["value"] or {}).get("stopped"))
    status = "done" if real_cleanup.get("ok") and stopped else "partial"
    return resp(content=json.dumps({
        "status": status,
        "stale_found": len(stale),
        "deleted": len(destroyed),
        "process_stopped": stopped,
    }))


def _build(tmp_path: Path, handler) -> tuple:
    """全真装配:内置工具面(零 mock)+ workdir 沙箱 = fixture 根目录。"""
    config = RunConfig(
        model="mock/janitor",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
        workdir=str(tmp_path),  # 爆炸半径圈在 fixture 树内
    )
    mock = MockProvider(janitor_brain)
    kernel = (
        KernelBuilder(config)
        .providers(mock)
        .tools(LocalPythonToolRegistry.with_builtins())
        .skills(LocalFileSkillRegistry(str(JANITOR_DIR / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(handler, timeout_s=5.0)
        .build()
    )
    return kernel, mock


def _questions(asked: list, skill: str) -> list:
    return [q for q in asked if q.context.get("skill") == skill]


@pytest.fixture()
def manifest(tmp_path):
    m = _fixture(tmp_path)
    yield m
    _cleanup(m)


def _run(kernel, manifest):
    return asyncio.run(
        kernel.run("ops.scan.workspace",
                   {"scratch_dir": manifest["scratch_dir"], "pid": manifest["pid"]})
    )


# ---------------------------------------------------------------------------
# 推导档(dogfood 验证:cleanup 必须 irreversible)
# ---------------------------------------------------------------------------


def test_tier_derivation(tmp_path):
    """推导档:plan=reversible、cleanup/stop=irreversible;scan 直接能力档=none。

    cleanup 的 irreversible 来自 system.file.delete 的显式标定(dogfood 修复,
    TIER-STANDARDS §1);修复前它只会推导出 reversible,approve-run 就能批量
    放行删除——本断言防回归。scan 的完整推导档(含 skills 递归)是 irreversible
    (它能叫到 L3),但**根帧 tier 取直接能力档**(derive_tools_tier,只看自己的
    tools)= none——这正是根帧调子技能会撞升权闸的原因(ESCALATION.md §2.2 实现注)。
    """
    skills = LocalFileSkillRegistry(str(JANITOR_DIR / "skills.yaml"))
    tools = LocalPythonToolRegistry.with_builtins()
    by_name = {m.name: m for m in skills.manifests()}
    tiers = {name: derive_skill_tier(m, tools, skills) for name, m in by_name.items()}
    assert tiers["ops.scan.workspace"] == "irreversible"  # 完整推导(可达面,lint 用)
    assert derive_tools_tier(by_name["ops.scan.workspace"], tools) == "none"  # 根帧 tier
    assert tiers["ops.plan.write"] == "reversible"
    assert tiers["ops.cleanup.execute"] == "irreversible"
    assert tiers["ops.service.stop"] == "irreversible"


# ---------------------------------------------------------------------------
# L1→L2:approve-run Grant(第一次问,第二次 grant 命中不再问)
# ---------------------------------------------------------------------------


def test_approve_run_grant_flow(tmp_path, manifest):
    """plan.write 连调两次:第一次挂起批准(approve-run),第二次 Grant 命中
    直接放行(grant-run 信号);两份文档真实落盘。"""
    asked = []

    async def handler(question):
        asked.append(question)
        answer = "approve-run" if question.context["skill"] == "ops.plan.write" else "approve-once"
        return {"answer": answer, "decided_by": "user:test"}

    kernel, _ = _build(tmp_path, handler)
    decisions = []

    async def rec(sig):
        decisions.append(sig.payload["decision"])

    kernel.signals.subscribe("post:skill.escalate", rec)
    result = _run(kernel, manifest)

    assert result["status"] == "done"
    plan_asks = _questions(asked, "ops.plan.write")
    assert len(plan_asks) == 1, "approve-run 后第二次写计划不得再问"
    assert plan_asks[0].options == ["approve-once", "approve-run", "deny"]
    assert "grant-run" in decisions, "第二次调用必须走 Grant 命中放行"
    assert (tmp_path / "plan.md").is_file()
    assert "将删" in (tmp_path / "plan.md").read_text(encoding="utf-8")
    assert (tmp_path / "inspect-log.md").is_file()


# ---------------------------------------------------------------------------
# L1→L3:cleanup 连删两次出两次 pending(L3 无 approve-run)
# ---------------------------------------------------------------------------


def test_l3_every_call_asks(tmp_path, manifest):
    """cleanup.execute 两次调用(dry_run + 真删)出两次独立 pending——
    不可逆操作每一次都必须单独过人眼(三档模型最硬的规则)。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, _ = _build(tmp_path, handler)
    result = _run(kernel, manifest)

    cleanup_asks = _questions(asked, "ops.cleanup.execute")
    assert len(cleanup_asks) == 2, "L3 两次调用必须出两次 pending"
    assert all(q.options == ["approve-once", "deny"] for q in cleanup_asks), (
        "L3 的 options 永不出现 approve-run"
    )
    assert result["status"] == "done"
    assert result["deleted"] == len(manifest["stale"])


# ---------------------------------------------------------------------------
# deny 路径:文件仍在 + PERMISSION_DENIED 观察 + denied 信号
# ---------------------------------------------------------------------------


def test_deny_keeps_files(tmp_path, manifest):
    """拒绝真删:文件原样存在;父帧收 PERMISSION_DENIED 观察(status=partial,
    不重试同一调用);skill.escalation.denied 信号载荷齐全。"""
    denied_signals = []

    async def handler(question):
        if question.context["skill"] == "ops.cleanup.execute":
            return {"answer": "deny", "decided_by": "user:test"}
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, mock = _build(tmp_path, handler)

    async def rec(sig):
        denied_signals.append(sig)

    kernel.signals.subscribe("skill.escalation.denied", rec)
    result = _run(kernel, manifest)

    assert result["status"] == "partial"
    assert result["deleted"] == 0
    for stale in manifest["stale"]:
        assert Path(stale).is_file(), f"被拒绝的删除不得发生: {stale}"
    for keep in manifest["keep"]:
        assert Path(keep).is_file()
    assert len(denied_signals) >= 1
    assert denied_signals[0].payload["skill"] == "ops.cleanup.execute"
    assert denied_signals[0].payload["tier"] == "irreversible"
    assert denied_signals[0].payload["decided_by"] == "user:test"
    # 父帧观察:拒绝以 permission_denied 工具结果进入根帧上下文
    denied_obs = [
        json.loads(m.content)
        for req in mock.recorded for m in req.messages
        if m.role is Role.TOOL and not json.loads(m.content).get("ok", True)
    ]
    assert any(o["error"]["kind"] == "permission_denied" for o in denied_obs)


# ---------------------------------------------------------------------------
# service.stop:批准后进程真的死了
# ---------------------------------------------------------------------------


def test_service_stop_kills_process(tmp_path, manifest):
    """approve-once 后演示进程被真 kill(os.kill(pid, 0) 查无此进程)。"""
    pid = manifest["pid"]
    assert _process_alive(pid)  # 前置:fixture 进程确实活着

    async def handler(question):
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, _ = _build(tmp_path, handler)
    result = _run(kernel, manifest)

    assert result["process_stopped"] is True
    assert not _process_alive(pid), "批准后演示进程必须真死"


# ---------------------------------------------------------------------------
# dry_run 一致性:预览清单 == 实际删除清单 == 磁盘真实变化
# ---------------------------------------------------------------------------


def test_dry_run_matches_destroyed(tmp_path, manifest):
    """L3"所见即所毁":dry_run 的将删清单 == 真删输入 == 磁盘上真实消失的文件集。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, _ = _build(tmp_path, handler)
    result = _run(kernel, manifest)

    cleanup_asks = _questions(asked, "ops.cleanup.execute")
    preview = next(q for q in cleanup_asks if q.context["params"]["dry_run"] is True)
    real = next(q for q in cleanup_asks if q.context["params"]["dry_run"] is False)
    assert preview.context["params"]["targets"] == real.context["params"]["targets"], (
        "预览一套删一套是不允许的(dry_run 与真实执行共享同一份清单)"
    )
    expected = {str(Path(p).relative_to(tmp_path)) for p in manifest["stale"]}
    assert set(real.context["params"]["targets"]) == expected
    gone = {p for p in manifest["stale"] if not Path(p).exists()}
    assert {str(Path(p).relative_to(tmp_path)) for p in gone} == expected
    for keep in manifest["keep"]:
        assert Path(keep).is_file(), "保留清单不得被误删"
    assert result["deleted"] == len(expected)


# ---------------------------------------------------------------------------
# 干净 context 不变量:升权子帧 messages 恰为 [USER(参数)]
# ---------------------------------------------------------------------------


def test_clean_context_invariant(tmp_path, manifest):
    """升权子帧(cleanup):首请求 = SYSTEM(自己的 prompt)+ USER(参数 JSON);
    父帧 prompt/巡检观察物理上不进子帧。"""
    async def handler(question):
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, mock = _build(tmp_path, handler)
    _run(kernel, manifest)

    child_reqs = [
        r for r in mock.recorded
        if r.messages and "# skill: ops.cleanup.execute" in r.messages[0].content
    ]
    assert child_reqs, "cleanup 子帧必须真实跑过"
    first = child_reqs[0]
    assert "ops.scan.workspace" not in first.messages[0].content, (
        "父帧 prompt 不得进升权子帧 SYSTEM"
    )
    user = first.messages[1]
    assert user.role is Role.USER
    params = json.loads(user.content)
    assert set(params) == {"targets", "dry_run"}, "首条 USER 恰为校验过的参数 JSON"


# ---------------------------------------------------------------------------
# 真实文件系统断言贯穿:全批准路径的端到端结果
# ---------------------------------------------------------------------------


def test_full_run_real_side_effects(tmp_path, manifest):
    """全批准端到端:陈旧文件真删、保留文件原样、计划两份落盘、进程真停。"""
    async def handler(question):
        answer = "approve-run" if question.context["skill"] == "ops.plan.write" else "approve-once"
        return {"answer": answer, "decided_by": "user:test"}

    kernel, _ = _build(tmp_path, handler)
    result = _run(kernel, manifest)

    assert result == {
        "status": "done",
        "stale_found": len(manifest["stale"]),
        "deleted": len(manifest["stale"]),
        "process_stopped": True,
    }
    for stale in manifest["stale"]:
        assert not Path(stale).exists()
    for keep in manifest["keep"]:
        assert Path(keep).is_file()
    assert (tmp_path / "plan.md").is_file()
    assert not _process_alive(manifest["pid"])

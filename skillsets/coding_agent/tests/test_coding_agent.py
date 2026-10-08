"""coding_agent skillset 锚点测试(阶段一 M1–M3)。

全真形态(同 workspace_janitor 先例):零 mock 工具——file/shell/todo 全部真跑,
爆炸半径由 ``RunConfig.workdir`` 沙箱圈住(tmp_path 下的 fixture 副本);mock 的只是
大脑(brains.py 确定性剧本)。verify 真跑 pytest:第一轮真失败、第二轮真通过。

固定约定:

- 装配走 tests/helpers/kernels.py 的 assemble + record_all;supervisor handler
  按 question.kind 分流(plan 问答 / escalation 升权 / tool-confirm 执行确认);
- HumanApproval 策略载体在场 → shell.exec 逐次过内核 tool-confirm 门;
  entry 调 L3 推导档的 fix_loop/collect_diff/review 触发升权门——两者都是
  设计意图,测试里按用例批准或拒绝;
- fixture 复制辅助见 conftest.py(copy_fixture),每个用例独立 workdir。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import brains
from agent_os.api.v1 import (
    Permission,
    Role,
    RunConfig,
    RunStatus,
    ToolPolicy,
    derive_skill_tier,
)
from agent_os.blackboard.local import LocalBlackboard
from agent_os.providers.mock import MockProvider
from agent_os.sidecars import BudgetGuard, HumanApproval, LoopDetector, ToolGuard
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.skills.manifest import validate_manifest
from agent_os.tools.local_registry import LocalPythonToolRegistry
from conftest import SKILLSET_DIR, copy_fixture
from tests.helpers.kernels import (
    ScriptedUserChannel,
    assemble,
    record_all,
    sandbox_tools,
)

SKILLS_YAML = SKILLSET_DIR / "skills.yaml"

TASK = "修复 calc 的 bug"
#: 验证命令在 workdir(fixture 副本)下执行;用跑测试的同一解释器(自带 pytest)
TEST_COMMAND = f"{sys.executable} -m pytest test_calc.py -x"


# ---------------------------------------------------------------------------
# 装配与 supervisor 通道
# ---------------------------------------------------------------------------


def _make_supervisor(
    asked: list,
    *,
    plan: str = "approve-once",
    confirm: str = "approve-once",
    escalate: str = "approve-once",
):
    """按 question.kind 分流的测试通道:plan=计划批准(gate 同款词汇),confirm=tool-confirm,escalate=升权。"""

    async def handler(question):
        asked.append(question)
        if question.kind == "question":
            return {"answer": plan, "decided_by": "test:supervisor"}
        if question.kind == "tool-confirm":
            return {"answer": confirm, "decided_by": "test:supervisor"}
        return {"answer": escalate, "decided_by": "test:supervisor"}

    return handler


def _build(work: Path, supervisor, brain=None, *, checkpoint_interval: int = 0, user_channel=None):
    """标准装配:MockProvider + 全真内置工具 + sidecar 组(与 agent-os.toml 对齐)。"""
    config = RunConfig(
        model="mock/coding",
        max_depth=8,
        max_steps=150,
        max_cost=3.0,
        compression="off",
        orchestrate=True,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=str(work),  # 爆炸半径圈在 fixture 副本内
        checkpoint_interval=checkpoint_interval,
    )
    mock = MockProvider(brain or brains.coding_brain)
    kernel = assemble(
        config,
        mock,
        SKILLS_YAML,
        tools=sandbox_tools(builtins=True),
        sidecars=(
            BudgetGuard(max_cost=3.0),
            LoopDetector(threshold=3, max_strikes=2),
            ToolGuard(rules=[("system.shell.exec", r"rm\s+-rf\s+/(\s|$)", "veto: 禁止删除根目录")]),
            HumanApproval(),
        ),
        blackboard=LocalBlackboard(),
        supervisor=supervisor,
        user_channel=user_channel,
    )
    return kernel, mock


def _run(kernel) -> dict:
    return asyncio.run(kernel.run("coding.agent.run", {"task": TASK, "test_command": TEST_COMMAND}))


def _entry_calls(recorded, skill: str = "coding.agent.run") -> list[tuple[str, dict, dict]]:
    """指定技能帧最终请求(全量历史)里的已完成调用 (name, args, payload) 序列。"""
    reqs = [
        r
        for r in recorded
        if r.messages and f"# skill: {skill}" in r.messages[0].content
    ]
    assert reqs, f"{skill} 帧必须真实跑过"
    names: dict[str, tuple[str, dict]] = {}
    out: list[tuple[str, dict, dict]] = []
    for m in reqs[-1].messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                names[tc.id] = (tc.name, dict(tc.args or {}))
        elif m.role is Role.TOOL and m.tool_call_id in names:
            name, args = names[m.tool_call_id]
            out.append((name, args, json.loads(m.content)))
    return out


def _frame_requests(recorded, skill: str) -> list:
    """某技能帧的全部 LLM 请求(一个请求 = 一次 LLM 调用)。"""
    return [
        r for r in recorded if r.messages and f"# skill: {skill}" in r.messages[0].content
    ]


# ---------------------------------------------------------------------------
# 1. 主流程 e2e(mock 大脑 + 全真工具):改错→真失败→真修复→真通过
# ---------------------------------------------------------------------------


def test_e2e_fix_done(tmp_path):
    work = copy_fixture(tmp_path)
    asked: list = []
    kernel, mock = _build(work, _make_supervisor(asked))
    seen = record_all(kernel)

    result = _run(kernel)

    assert result["status"] == "done"
    assert "calc.py" in result["changed_files"]
    assert result["tests"] == {"command": TEST_COMMAND, "ran": True, "passed": True}
    # fixture 文件真的被修好(内容层面 + 我们独立再跑一次 pytest 复核)
    assert "return a + b" in (work / "calc.py").read_text(encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "test_calc.py", "-x", "-q"],
        cwd=work,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout.decode(errors="replace")
    # 计划文档真落盘(workdir 内)
    assert (work / ".agent-os" / "plans" / "calc-bug.md").is_file()
    # 信号序列:升权门与 tool-confirm 门都真实出现过
    names = [s.name for s in seen]
    assert "pre:skill.escalate" in names
    assert "post:skill.escalate" in names
    kinds = [s.payload.get("kind") for s in seen if s.name == "supervisor.ask"]
    assert "escalation" in kinds, "entry 调 L3 子技能必须过升权门"
    assert "tool-confirm" in kinds, "shell.exec 必须过 tool-confirm 门"
    assert "question" in kinds, "计划必须经 ask_supervisor 人审"
    # todo 工具被调用过
    assert any(
        s.name == "post:tool.call" and s.payload.get("tool") == "system.task.todo_write"
        for s in seen
    )
    # 修复循环恰好 2 轮(改错 1 轮 + 真修 1 轮),verify 真跑了 2 次
    entry = _entry_calls(mock.recorded)
    fix = next(p for n, _a, p in entry if n == "skill.coding.fix_loop")
    assert fix["ok"] and fix["value"]["status"] == "done"
    assert fix["value"]["iterations"] == 2
    verify_reqs = _frame_requests(mock.recorded, "coding.verify")
    assert len(verify_reqs) == 4, "2 轮 × (发命令 + 出结论) 共 4 次 LLM 调用"


# ---------------------------------------------------------------------------
# 2. 修复循环上限:verify 恒失败 → 5 轮耗尽 → partial
# ---------------------------------------------------------------------------


def test_fix_loop_exhausts_to_partial(tmp_path):
    work = copy_fixture(tmp_path)
    kernel, mock = _build(work, _make_supervisor([]), brain=brains.failing_verify_brain)

    result = _run(kernel)

    assert result["status"] == "partial"
    assert result["tests"]["passed"] is False
    entry = _entry_calls(mock.recorded)
    fix = next(p for n, _a, p in entry if n == "skill.coding.fix_loop")
    assert fix["ok"], "循环耗尽是正常返回,不是帧失败"
    assert fix["value"]["status"] == "partial"
    assert fix["value"]["iterations"] == 5, "handler 内 max_iterations=5 硬上限"
    assert "calc.py" in fix["value"]["changed_files"]
    # verify 被调了 5 次(恒失败变体每帧 1 次 LLM 调用)
    assert len(_frame_requests(mock.recorded, "coding.verify")) == 5
    assert len(_frame_requests(mock.recorded, "coding.implement")) >= 5


# ---------------------------------------------------------------------------
# 3. 拒绝降级(M3 核心):tool-confirm 全拒 → run 不崩,partial 收尾,run 状态 DONE
# ---------------------------------------------------------------------------


def test_deny_degrades_and_run_stays_done(tmp_path):
    work = copy_fixture(tmp_path)
    asked: list = []
    kernel, _mock = _build(work, _make_supervisor(asked, confirm="deny"))
    seen = record_all(kernel)

    result = _run(kernel)

    assert result["status"] in ("partial", "failed")
    assert result["status"] == "partial", "verify 逐次被拒 → 修复循环耗尽 → partial"
    assert result["tests"]["ran"] is False, "shell.exec 全被拒,测试从未真实执行"
    assert result["tests"]["passed"] is False
    confirms = [q for q in asked if q.kind == "tool-confirm"]
    assert len(confirms) == 7, "verify×5 + collect_diff + review 的 shell.exec 逐次过门"
    # run 不崩:状态是 DONE(拒绝折叠为错误观察,不是 FAILED/ABORTED)
    run_id = next(s.run_id for s in seen if s.name == "run.started")
    assert kernel._runs[run_id].status is RunStatus.DONE
    # 被拒的调用从未真跑:workdir 里没有 pytest 缓存产物
    assert not (work / ".pytest_cache").exists()


# ---------------------------------------------------------------------------
# 4. 计划拒批:两版计划都不批 → failed,修复循环从未启动
# ---------------------------------------------------------------------------


def test_plan_rejected_twice_fails(tmp_path):
    work = copy_fixture(tmp_path)
    asked: list = []
    kernel, mock = _build(work, _make_supervisor(asked, plan="deny:请补充根因分析"))

    result = _run(kernel)

    assert result["status"] == "failed"
    assert result["tests"]["ran"] is False
    plan_asks = [q for q in asked if q.kind == "question"]
    assert len(plan_asks) == 2, "审批提问至多 2 次,恰好 2 次后被拒收尾"
    entry = _entry_calls(mock.recorded)
    plans = [c for c in entry if c[0] == "skill.coding.plan"]
    assert len(plans) == 2
    assert "feedback" in plans[1][1], "重出计划必须带 deny 冒号后的修改意见"
    assert "请补充根因分析" in plans[1][1]["feedback"]
    assert not any(c[0] == "skill.coding.fix_loop" for c in entry), "未批准不得进入修复循环"
    # calc.py 原样未动
    assert "return a - b" in (work / "calc.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. collect_diff 非 git 目录:available=false 不报错
# ---------------------------------------------------------------------------


def test_collect_diff_non_git_dir(tmp_path):
    work = copy_fixture(tmp_path)  # tmp_path 下的普通目录,非 git 仓库
    kernel, _mock = _build(work, _make_supervisor([]))

    result = asyncio.run(kernel.run("coding.collect_diff", {}))

    assert result["available"] is False
    assert result["note"], "必须说明 git 不可用/非仓库的原因"


# ---------------------------------------------------------------------------
# 6. manifest lint:skills.yaml 全部技能过 validate(含升权分档硬闸门)
# ---------------------------------------------------------------------------


def test_manifests_pass_lint():
    registry = LocalFileSkillRegistry(str(SKILLS_YAML))
    registry.load()
    tools = LocalPythonToolRegistry.with_builtins()
    manifests = registry.manifests()
    assert len(manifests) == 9
    for m in manifests:
        tier = derive_skill_tier(m, tools, registry)
        assert validate_manifest(m, tier) == [], f"{m.name} 的 lint 告警须为空"


# ---------------------------------------------------------------------------
# 7. 出货的 agent-os.toml 可装配(配置形态回归:sidecar 规则三元组等)
# ---------------------------------------------------------------------------


def test_shipped_toml_assembles(tmp_path, monkeypatch):
    """build_kernel 直接吃出货的 agent-os.toml(相对路径以仓库根为基准)。"""
    from agent_os.runtime.config import build_kernel

    monkeypatch.chdir(SKILLSET_DIR.parents[1])  # 仓库根
    kernel = build_kernel(SKILLSET_DIR / "agent-os.toml")
    assert kernel.human_approval is not None
    assert kernel.skills is not None


# ---------------------------------------------------------------------------
# 8. checkpoint 暂停/恢复(规划 §2.6.5):计划批准时挂起 → PAUSED 落盘 → resume 续跑完成
# ---------------------------------------------------------------------------


def test_pause_and_resume(tmp_path):
    """supervisor handler 在计划批准那次问答里 ``ctl.pause``:run 在下一个 safe point

    抛 RunPaused,execute_run 落 PAUSED + checkpoint.json;换同配置内核经
    execute_resume 续跑到 done。已结算的步骤(todo/plan/人审)在 resume 后不重复,
    无重复副作用。走 host/shared/artifacts 的 execute_run/execute_resume——与
    CLI ``agent-os run/resume`` 同一条产物路径。
    """
    from agent_os.host.shared.artifacts import execute_resume, execute_run

    work = copy_fixture(tmp_path)
    artifacts = tmp_path / "artifacts"
    asked: list = []
    holder: dict = {}
    paused_once = {"flag": False}

    async def pausing_handler(question):
        asked.append(question)
        if question.kind == "question":
            if not paused_once["flag"]:
                # 第一次被问(计划批准):照常回答,同时挂起——回答先结算,
                # RunPaused 在 entry 帧下一个 safe point 才抛(配对原子性不破)
                paused_once["flag"] = True
                await holder["kernel"].ctl.pause(question.run_id, "eval pause(测试挂起)")
            return {"answer": "approve-once", "decided_by": "test:supervisor"}
        return {"answer": "approve-once", "decided_by": "test:supervisor"}

    kernel1, _mock1 = _build(work, pausing_handler, checkpoint_interval=1)
    holder["kernel"] = kernel1
    seen1 = record_all(kernel1)

    record1 = execute_run(
        kernel1,
        "coding.agent.run",
        {"task": TASK, "test_command": TEST_COMMAND},
        artifacts_root=artifacts,
        host="test",
    )

    assert record1["status"] == "paused"
    assert any(s.name == "run.paused" for s in seen1)
    run_dir = artifacts / "runs" / record1["run_id"]
    checkpoint_path = run_dir / "checkpoint.json"
    assert checkpoint_path.is_file()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["run"]["status"] == "paused"
    # 挂起点在计划批准之后、修复循环之前:fixture 尚未被改
    assert "return a - b" in (work / "calc.py").read_text(encoding="utf-8")
    assert not any(c.kind != "question" for c in asked), "挂起前只发生过计划问答"

    # —— resume:第二个同配置内核(auto-approve 基座)续跑 ——
    kernel2, mock2 = _build(work, _make_supervisor([]), checkpoint_interval=1)
    record2 = execute_resume(kernel2, checkpoint_path, artifacts_root=artifacts, host="test")

    assert record2["run_id"] == record1["run_id"], "resume 续的是同一个 run"
    assert record2["status"] == "done"
    result = record2["result"]
    assert result["status"] == "done"
    assert "calc.py" in result["changed_files"]
    assert result["tests"]["passed"] is True
    assert "return a + b" in (work / "calc.py").read_text(encoding="utf-8")
    # 无重复副作用:已结算的 todo/plan/人审在续跑历史中恰各一次;修复循环恰 2 轮
    entry = _entry_calls(mock2.recorded)
    assert sum(1 for c in entry if c[0] == "system.task.todo_write") == 1
    assert sum(1 for c in entry if c[0] == "skill.coding.plan") == 1
    assert sum(1 for c in entry if c[0] == "ask_supervisor") == 1
    assert sum(1 for c in entry if c[0] == "skill.coding.fix_loop") == 1
    fix = next(p for n, _a, p in entry if n == "skill.coding.fix_loop")
    assert fix["value"]["status"] == "done" and fix["value"]["iterations"] == 2


# ---------------------------------------------------------------------------
# 9. coding.agent.chat:多轮会话入口(P2-M3;长存 run + user.ask 循环)
# ---------------------------------------------------------------------------


def test_chat_two_turns_e2e(tmp_path):
    """opening 首轮 → agent.run 子帧完整修复 → notify 汇报 → ask 接第二轮 → 再 ask
    答「退出」→ 收官 JSON(status=done、turns=2);user.ask 恰 2 次、agent.run 子帧 2 个。"""
    work = copy_fixture(tmp_path)
    user = ScriptedUserChannel(["再跑一遍测试确认修复", "退出"])
    kernel, mock = _build(work, _make_supervisor([]), user_channel=user)
    seen = record_all(kernel)

    result = asyncio.run(
        kernel.run(
            "coding.agent.chat",
            {"opening": f"修复 calc 的 bug,验证命令:{TEST_COMMAND}"},
        )
    )

    assert result["status"] == "done"
    assert result["turns"] == 2
    assert result["summary"]
    # 第一轮真修好了 fixture(第二轮是"再确认",文件保持修复态)
    assert "return a + b" in (work / "calc.py").read_text(encoding="utf-8")
    assert len(user.questions) == 2, "两轮任务之间与收官前各问一次"
    assert len(user.notifications) == 2, "每轮子帧返回后都 notify 汇报"
    assert any("done" in n and "calc.py" in n for n in user.notifications), (
        "汇报摘要须含子帧 status 与 changed_files"
    )
    chat = _entry_calls(mock.recorded, "coding.agent.chat")
    runs = [c for c in chat if c[0] == "skill.coding.agent.run"]
    assert len(runs) == 2
    assert all(c[2]["ok"] for c in runs)
    assert all(c[2]["value"]["status"] == "done" for c in runs)
    # 第二轮任务文本没给验证命令:chat 侧推断缺省命令(契约不动 agent.run 必填)
    assert TEST_COMMAND in runs[0][1]["test_command"]
    assert "pytest" in runs[1][1]["test_command"]
    # chat(直接能力面 reversible)调 agent.run(推导档 irreversible)触发升权门,
    # 两轮各一次(设计意图)
    escalations = [
        s
        for s in seen
        if s.name == "pre:skill.escalate" and s.payload.get("skill") == "coding.agent.run"
    ]
    assert len(escalations) == 2


def test_chat_no_opening_asks_first(tmp_path):
    """opening 缺省:开场即 user.ask → 答复即首轮任务 → 正常推进 → 「结束」收官。"""
    work = copy_fixture(tmp_path)
    user = ScriptedUserChannel([f"修复 calc 的 bug(验证:{TEST_COMMAND})", "结束"])
    kernel, mock = _build(work, _make_supervisor([]), user_channel=user)

    result = asyncio.run(kernel.run("coding.agent.chat", {}))

    assert result["status"] == "done"
    assert result["turns"] == 1
    assert len(user.questions) == 2, "开场即问 + 收官前一问"
    assert "要做什么" in user.questions[0]
    assert "return a + b" in (work / "calc.py").read_text(encoding="utf-8")
    chat = _entry_calls(mock.recorded, "coding.agent.chat")
    assert sum(1 for c in chat if c[0] == "skill.coding.agent.run") == 1
    # 首轮调用序列:第一次调用必须是 user.ask(开场即问,不是直接起任务)
    assert chat[0][0] == "system.user.ask"

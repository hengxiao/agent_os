"""逐分支预算(per-branch budget)内核强制锚点测试(docs/DESIGN.md §17 开放问题 1,
§17.1 显式拆分决策)。

固定约定:

- 缺省仍是按需抢占(共享 run 池 + 记账点事后检查);显式拆分 = 分支 dict 的
  ``budget={"max_steps", "max_cost"}`` 键(parallel_invoke)或 spawn_frame 的
  ``budget`` kwarg(LogicContext.spawn / syscall spawn 面故意不暴露,契约冻结);
- 校验 fail-closed:非 dict / 未知键 / 空 dict / bool / 非数值 / 非正值 →
  SkillLoadError(``where`` 前缀:``分支 {index}`` / ``spawn``);
- 执行点与 manifest limits 同址(``_check_subtree_budgets``,记账点沿祖先链):
  按字段覆盖——override 有的字段以 override 为准,缺的回落 manifest;
  ``budget.exceeded``/``budget.warning`` payload 带 ``"source"``
  (``"branch"``/``"manifest"``);超限价:分支 SubtreeCancelled,兄弟无感,run 存活;
- ``budget.warning``:用量 ≥80% 上限即发(每帧每字段恰好一次,``_budget_warned``
  防重),不占 ``_budget_tripped``——之后真实超限仍发 ``budget.exceeded`` 并走分档。

夹具全部 prompt 技能无工具白名单(test.bb_slow 除外,READ 工具),推导档不升权,
不装 supervisor;脑按帧输入 ``tag`` 路由。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from agent_os.api.v1 import (
    BUDGET_EXCEEDED,
    BUDGET_WARNING,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    FrameStatus,
    Message,
    Permission,
    Role,
    RunConfig,
    RunStatus,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.errors import SkillLoadError, SubtreeCancelled
from tests.helpers.kernels import assemble, record_all, sandbox_tools

BRANCH_BUDGET_YAML = """
skills:
  - name: test.bb_driver
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:sandbox_parallel_probe
    description: code 根(TRUSTED,无预算):按 input["branches"] 原样扇出,kw 透传。
    inputs: { type: object }
    outputs: { type: object }
    permissions:
      tools: []
      skills:
        [test.bb_plain, test.bb_leaf, test.bb_loop_plain, test.bb_warner,
         test.bb_manifest_loose, test.bb_costly, test.bb_slow]
  - name: test.bb_sandbox_driver
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:sandbox_parallel_probe
    logic: { mode: sandbox }
    description: code 根(SANDBOX):同一 handler 经 syscall 桥 parallel(桥穿透锚点)。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [test.bb_plain, test.bb_leaf] }
  - name: test.bb_root
    version: 1.0.0
    kind: prompt
    description: 一步零成本终答的根(spawn_frame/parallel_invoke 白盒调用的父帧)。
    inputs:
      type: object
      properties: { tag: { type: string } }
      required: [tag]
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [test.bb_plain] }
    model: { prefer: ["mock/x"] }
    prompt: 直接回答。
  - name: test.bb_plain
    version: 1.0.0
    kind: prompt
    description: 无 manifest limits;按 tag 一步终答(带/不带成本),分支预算挂它。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: 直接回答。
  - name: test.bb_leaf
    version: 1.0.0
    kind: prompt
    description: 无 limits 叶帧;零成本终答(兄弟分支/子帧用)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: 直接回答。
  - name: test.bb_costly
    version: 1.0.0
    kind: prompt
    description: manifest limits.max_cost=0.05(manifest 源对照分支)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_cost: 0.05 }
    prompt: 直接回答。
  - name: test.bb_manifest_loose
    version: 1.0.0
    kind: prompt
    description: manifest max_cost=10(宽松),分支预算 max_cost=0.05 覆盖收紧的对照。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_cost: 10 }
    prompt: 直接回答。
  - name: test.bb_loop_plain
    version: 1.0.0
    kind: prompt
    description: 无 limits;每步调 test.bb_leaf(分支 budget.max_steps 触发用)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [test.bb_leaf] }
    model: { prefer: ["mock/x"] }
    prompt: 反复调子技能。
  - name: test.bb_warner
    version: 1.0.0
    kind: prompt
    description: 无 limits;两步帧(先调 leaf 再终答),成本分档 0.4/0.2(预警锚点)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [test.bb_leaf] }
    model: { prefer: ["mock/x"] }
    prompt: 先调子技能再收尾。
  - name: test.bb_slow
    version: 1.0.0
    kind: prompt
    description: 调 test.bb_slow_tool(短睡眠)再终答(first_success 胜方时间窗)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [test.bb_slow_tool], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: 先调 test.bb_slow_tool,再回答。
"""

#: 带成本终答的一步 usage(max_cost=0.05/0.5 对 0.5 的语义:严格大于才触发)
COSTLY = 0.5


def _final(cost: float = 0.0) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1, cost=cost),
    )


def _call(name: str, args: dict, cost: float = 0.0) -> ChatResponse:
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            tool_calls=[ToolCall(id=f"c-{name}", name=name, args=args)],
        ),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1, cost=cost),
    )


def _bb_brain(req: ChatRequest) -> ChatResponse:
    """按帧输入 tag 路由的单脑(首条 USER 消息即帧输入,§3.1 固定形态)。

    - ``loop``:恒调 test.bb_leaf(零成本),max_steps 触发前不停;
    - ``warner``:无工具结果 → 调 test.bb_leaf 且本步带 0.4 成本;有 → 带 0.2
      成本终答(子树成本 0.4 → 0.6,先预警后超限);
    - ``slow``:首步调 test.bb_slow_tool(短睡眠时间窗),拿到结果后终答;
    - 其余:``costly`` 带 0.5 成本终答,``free`` 零成本终答。
    """
    data: dict = {}
    for m in req.messages:
        if m.role is Role.USER:
            try:
                data = json.loads(m.content)
            except json.JSONDecodeError:
                data = {}
            break
    tag = data.get("tag")
    tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
    if tag == "loop":
        return _call("skill.test.bb_leaf", {"tag": "free"})
    if tag == "warner":
        if not tool_msgs:
            return _call("skill.test.bb_leaf", {"tag": "free"}, cost=0.4)
        return _final(cost=0.2)
    if tag == "slow":
        if not tool_msgs:
            return _call("test.bb_slow_tool", {})
        return _final()
    return _final(cost=COSTLY if tag == "costly" else 0.0)


def _tools():
    """默认工具表:sandbox 工具 + test.bb_slow_tool(first_success 胜方时间窗)。"""
    reg = sandbox_tools()

    @reg.tool(name="test.bb_slow_tool", permission=Permission.READ, timeout=30)
    async def slow_tool() -> str:
        """睡一小段时间(first_success 中预算分支先触发的时间窗)。"""
        await asyncio.sleep(0.3)
        return "slept"

    return reg


def _build(tmp_path) -> object:
    """最小内核:prompt 技能推导档不升权,不装 supervisor;run 级上限留足余量。"""
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(BRANCH_BUDGET_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/x",
        max_cost=5.0,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(config, _bb_brain, p, tools=_tools())


def _frame_of(kernel, skill_name: str):
    """run 结束后按技能名取帧(每个用例中该技能名唯一)。"""
    return next(f for f in kernel.stack.tree() if f.skill.name == skill_name)


def _done_run(kernel):
    return next(iter(kernel._runs.values()))


# ---------------------------------------------------------------------------
# parallel 分支预算:超限取消该分支,兄弟无感,run 存活
# ---------------------------------------------------------------------------


def test_parallel_branch_budget_exceeded_sibling_unaffected(tmp_path):
    """分支 dict 带 ``budget={"max_cost": 0.05}``(技能自身无 manifest limits)→
    该分支子树中止(条目 ok=False,kind=cancelled,消息含 budget/分支预算),
    兄弟分支正常结算,run 落 DONE。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run(
            "test.bb_driver",
            {
                "branches": [
                    {
                        "skill": "test.bb_plain",
                        "input": {"tag": "costly"},
                        "budget": {"max_cost": 0.05},
                    },
                    {"skill": "test.bb_leaf", "input": {"tag": "free"}},
                ]
            },
        )
    )
    results = result["results"]
    assert results[0]["ok"] is False
    assert results[0]["error"]["kind"] == "cancelled"
    assert "budget" in results[0]["error"]["message"]
    assert "分支预算" in results[0]["error"]["message"]
    assert results[1]["ok"] is True and results[1]["value"] == {"done": True}
    assert _done_run(kernel).state.status is RunStatus.DONE
    branch_frame = kernel.stack.get(results[0]["frame_id"])
    assert branch_frame is not None and branch_frame.status is FrameStatus.FAILED
    assert isinstance(branch_frame.error, SubtreeCancelled)


def test_parallel_branch_budget_max_steps(tmp_path):
    """``budget={"max_steps": 1}`` 限分支根帧自身步数:第一步未超(1 > 1 不成立,
    但 1 ≥ 80%×1 → max_steps 预警一次),第二步记账时超限 → 分支取消。"""
    kernel = _build(tmp_path)
    seen = record_all(kernel)
    result = asyncio.run(
        kernel.run(
            "test.bb_driver",
            {
                "branches": [
                    {
                        "skill": "test.bb_loop_plain",
                        "input": {"tag": "loop"},
                        "budget": {"max_steps": 1},
                    },
                    {"skill": "test.bb_leaf", "input": {"tag": "free"}},
                ]
            },
        )
    )
    results = result["results"]
    assert results[0]["ok"] is False and results[0]["error"]["kind"] == "cancelled"
    assert "budget" in results[0]["error"]["message"]
    assert results[1]["ok"] is True
    loop_frame = _frame_of(kernel, "test.bb_loop_plain")
    assert loop_frame.usage.steps == 2, "第一步未超,第二步记账时触发"
    assert loop_frame.status is FrameStatus.FAILED
    exceeded = [s for s in seen if s.name == BUDGET_EXCEEDED]
    assert len(exceeded) == 1
    assert exceeded[0].payload["source"] == "branch"
    assert exceeded[0].payload["max_steps"] == 1 and exceeded[0].payload["max_cost"] is None
    warnings = [s for s in seen if s.name == BUDGET_WARNING]
    assert len(warnings) == 1 and warnings[0].payload["field"] == "max_steps"
    assert warnings[0].payload["source"] == "branch"


def test_parallel_no_budget_key_shared_pool_unchanged(tmp_path):
    """回归锚点:分支不给 budget 键 → 不登记 ``_branch_budgets``,行为与引入前
    逐字一致(共享 run 池 + manifest limits 事后检查:manifest 分支照旧取消,
    自由分支照常完成)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run(
            "test.bb_driver",
            {
                "branches": [
                    {"skill": "test.bb_costly", "input": {"tag": "costly"}},
                    {"skill": "test.bb_leaf", "input": {"tag": "free"}},
                ]
            },
        )
    )
    results = result["results"]
    assert results[0]["ok"] is False and results[0]["error"]["kind"] == "cancelled"
    assert "budget" in results[0]["error"]["message"]
    assert results[1]["ok"] is True and results[1]["value"] == {"done": True}
    assert _done_run(kernel).state.status is RunStatus.DONE
    assert kernel._branch_budgets == {}, "无 budget 键的分支不应登记逐分支预算"


def test_branch_budget_overrides_manifest_per_field(tmp_path):
    """按字段覆盖:manifest max_cost=10 的宽松技能被分支 budget max_cost=0.05
    收紧(覆盖生效,source=branch);同批 manifest-only 分支仍按 manifest 规则
    (source=manifest)——两个分支同 cost 0.5,结局相同但预算来源不同。"""
    kernel = _build(tmp_path)
    seen = record_all(kernel)
    result = asyncio.run(
        kernel.run(
            "test.bb_driver",
            {
                "branches": [
                    {
                        "skill": "test.bb_manifest_loose",
                        "input": {"tag": "costly"},
                        "budget": {"max_cost": 0.05},
                    },
                    {"skill": "test.bb_costly", "input": {"tag": "costly"}},
                    {"skill": "test.bb_leaf", "input": {"tag": "free"}},
                ]
            },
        )
    )
    results = result["results"]
    assert [r["ok"] for r in results] == [False, False, True]
    exceeded = {s.payload["frame_id"]: s.payload for s in seen if s.name == BUDGET_EXCEEDED}
    loose_frame = _frame_of(kernel, "test.bb_manifest_loose")
    costly_frame = _frame_of(kernel, "test.bb_costly")
    assert exceeded[loose_frame.frame_id]["source"] == "branch"
    assert exceeded[loose_frame.frame_id]["max_cost"] == 0.05, "override 覆盖 manifest 的 10"
    assert exceeded[costly_frame.frame_id]["source"] == "manifest"
    assert exceeded[costly_frame.frame_id]["max_cost"] == 0.05
    assert _done_run(kernel).state.status is RunStatus.DONE


# ---------------------------------------------------------------------------
# 校验:非法 budget 形态 → SkillLoadError(fail-closed,批形态错同档)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_budget",
    [
        {"max_steps": 1, "ttl": 5},  # 未知键
        {"max_steps": True},  # bool(int 子类,单列排除)
        {"max_cost": "0.5"},  # 非数值
        {"max_cost": 0},  # 零
        {"max_steps": -1},  # 负
        {},  # 空预算等于没给
    ],
)
def test_branch_budget_invalid_shape_rejected(tmp_path, bad_budget):
    """非法 budget 形态在批形态预检即 SkillLoadError(``分支 0`` 前缀)。"""
    kernel = _build(tmp_path)
    asyncio.run(kernel.run("test.bb_root", {"tag": "free"}))
    root = _frame_of(kernel, "test.bb_root")
    with pytest.raises(SkillLoadError, match="分支 0"):
        asyncio.run(
            kernel.parallel_invoke(
                root,
                [{"skill": "test.bb_leaf", "input": {"tag": "free"}, "budget": bad_budget}],
            )
        )


def test_branch_budget_non_dict_rejected(tmp_path):
    """budget 非 dict 同样在预检拒绝。"""
    kernel = _build(tmp_path)
    asyncio.run(kernel.run("test.bb_root", {"tag": "free"}))
    root = _frame_of(kernel, "test.bb_root")
    with pytest.raises(SkillLoadError, match="budget 应为 dict"):
        asyncio.run(
            kernel.parallel_invoke(
                root,
                [{"skill": "test.bb_leaf", "input": {"tag": "free"}, "budget": 0.5}],
            )
        )


# ---------------------------------------------------------------------------
# spawn_frame budget kwarg / 信号 payload 与防重 / 预警
# ---------------------------------------------------------------------------


def test_spawn_frame_budget_kwarg(tmp_path):
    """``spawn_frame(..., budget={"max_cost": 0.05})``(kernel 级 kwarg):子帧超限
    → wait_frame 上抛 SubtreeCancelled;非法 budget 在调用点即 SkillLoadError
    (``spawn`` 前缀,帧未创建)。"""
    kernel = _build(tmp_path)
    asyncio.run(kernel.run("test.bb_root", {"tag": "free"}))
    root = _frame_of(kernel, "test.bb_root")

    async def _spawn_and_wait() -> None:
        fid = await kernel.spawn_frame(
            root, "test.bb_plain", {"tag": "costly"}, budget={"max_cost": 0.05}
        )
        await kernel.wait_frame(fid)

    with pytest.raises(SubtreeCancelled, match="budget"):
        asyncio.run(_spawn_and_wait())
    with pytest.raises(SkillLoadError, match="spawn"):
        asyncio.run(
            kernel.spawn_frame(root, "test.bb_plain", {"tag": "free"}, budget={"max_cost": 0})
        )


def test_budget_exceeded_payload_source_branch_fires_once(tmp_path):
    """``budget.exceeded`` payload:``source="branch"`` + 有效上限值(override 值);
    ``_budget_tripped`` 防重——重复检查(白盒直调)不再发信号、不再抛。"""
    kernel = _build(tmp_path)
    seen = record_all(kernel)
    asyncio.run(
        kernel.run(
            "test.bb_driver",
            {
                "branches": [
                    {
                        "skill": "test.bb_plain",
                        "input": {"tag": "costly"},
                        "budget": {"max_cost": 0.05},
                    },
                ]
            },
        )
    )
    branch_frame = _frame_of(kernel, "test.bb_plain")
    signals = [s for s in seen if s.name == BUDGET_EXCEEDED]
    assert len(signals) == 1
    payload = signals[0].payload
    assert payload["frame_id"] == branch_frame.frame_id
    assert payload["source"] == "branch"
    assert payload["max_cost"] == 0.05 and payload["max_steps"] is None
    assert payload["subtree_cost"] == COSTLY and payload["subtree_steps"] == 1

    asyncio.run(kernel._check_subtree_budgets(branch_frame))
    assert len([s for s in seen if s.name == BUDGET_EXCEEDED]) == 1


def test_budget_warning_fires_at_80_percent_before_exceeded(tmp_path):
    """``budget.warning``:第一步子树成本 0.4(=80%×0.5)→ 预警恰好一次
    (field=max_cost,source=branch),不占 ``_budget_tripped``;第二步真实超限
    0.6 > 0.5 仍发 ``budget.exceeded`` 并取消分支;预警先于 exceeded。"""
    kernel = _build(tmp_path)
    seen = record_all(kernel)
    asyncio.run(
        kernel.run(
            "test.bb_driver",
            {
                "branches": [
                    {
                        "skill": "test.bb_warner",
                        "input": {"tag": "warner"},
                        "budget": {"max_cost": 0.5},
                    },
                ]
            },
        )
    )
    warnings = [s for s in seen if s.name == BUDGET_WARNING]
    assert len(warnings) == 1, "每帧每字段恰好一次"
    wp = warnings[0].payload
    assert wp["field"] == "max_cost" and wp["source"] == "branch"
    assert wp["max_cost"] == 0.5 and wp["max_steps"] is None
    assert wp["subtree_cost"] == 0.4
    exceeded = [s for s in seen if s.name == BUDGET_EXCEEDED]
    assert len(exceeded) == 1
    assert exceeded[0].payload["source"] == "branch"
    assert exceeded[0].payload["subtree_cost"] == pytest.approx(0.6)
    assert seen.index(warnings[0]) < seen.index(exceeded[0]), "预警先于 exceeded"
    warner = _frame_of(kernel, "test.bb_warner")
    assert warner.status is FrameStatus.FAILED
    assert isinstance(warner.error, SubtreeCancelled)
    # 预警/触发均防重:白盒重查不再发
    asyncio.run(kernel._check_subtree_budgets(warner))
    assert len([s for s in seen if s.name == BUDGET_WARNING]) == 1
    assert len([s for s in seen if s.name == BUDGET_EXCEEDED]) == 1


# ---------------------------------------------------------------------------
# syscall 桥穿透 / first_success
# ---------------------------------------------------------------------------


def test_sandbox_bridge_passes_budget_through(tmp_path):
    """SANDBOX 档 code 技能经 syscall 桥 ``ctx.parallel``:分支 dict 的 budget 键
    随 JSON 原样穿透(桥零改动),内核侧照常执行逐分支预算。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run(
            "test.bb_sandbox_driver",
            {
                "branches": [
                    {
                        "skill": "test.bb_plain",
                        "input": {"tag": "costly"},
                        "budget": {"max_cost": 0.05},
                    },
                    {"skill": "test.bb_leaf", "input": {"tag": "free"}},
                ]
            },
        )
    )
    results = result["results"]
    assert results[0]["ok"] is False and results[0]["error"]["kind"] == "cancelled"
    assert "budget" in results[0]["error"]["message"]
    assert results[1]["ok"] is True and results[1]["value"] == {"done": True}
    assert _done_run(kernel).state.status is RunStatus.DONE


def test_first_success_budgeted_branch_cancelled_not_winner(tmp_path):
    """first_success:预算分支超限被自家预算取消(kind=cancelled,消息含 budget,
    不是胜方锁定的取消),慢分支正常锁定为唯一胜方——恰一个 ok=True。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run(
            "test.bb_driver",
            {
                "branches": [
                    {
                        "skill": "test.bb_plain",
                        "input": {"tag": "costly"},
                        "budget": {"max_cost": 0.05},
                    },
                    {"skill": "test.bb_slow", "input": {"tag": "slow"}},
                ],
                "kw": {"mode": "first_success"},
            },
        )
    )
    results = result["results"]
    assert results[0]["ok"] is False
    assert results[0]["error"]["kind"] == "cancelled"
    assert "budget" in results[0]["error"]["message"]
    assert results[1]["ok"] is True and results[1]["value"] == {"done": True}
    assert sum(1 for r in results if r["ok"]) == 1, "幂等结算:恰一个 ok=True"
    assert _done_run(kernel).state.status is RunStatus.DONE

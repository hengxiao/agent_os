"""帧/子树级预算内核强制锚点测试(manifest ``limits.max_cost``/``max_steps`` 执行点)。

固定约定:

- 预算挂帧(manifest ``limits``),记账点(``account()`` 与压缩排干)沿
  ``parent_id`` 祖先链穿透检查;判定口径:``max_cost`` = 子树求和
  (``_collect_subtree`` 收集 + 读侧聚合,写路径不动——父帧 usage 不含子帧),
  ``max_steps`` = 帧自身步数(该帧 agent loop 迭代上限);超限即触发;
- 触发分档:预算帧是根 → BudgetExceeded 炸 run(§3.1 步骤 7 同语义);是当前帧 →
  SubtreeCancelled,invoke 边界折叠为父帧 interrupted 错误观察(消息含 "budget"),
  父帧与 run 继续;是祖先 → ``cancel_subtree`` 级联取消(§5.2 语义:后台帧
  ``task.cancel()``、调用链 prompt 帧在 safe point 终结;code 帧不检查帧级 stop
  标志,其终结随 invoke/wait 边界穿透);
- ``budget.exceeded`` 信号每预算帧恰好一次(``_budget_tripped`` 防重),payload
  带 frame_id/skill/max_cost/max_steps/subtree_cost/subtree_steps;
- 链上无预算声明 → 快路径直接返回(零预算 run 零开销,由全量回归隐式覆盖)。

夹具全部技能无工具白名单(推导档 none),不经升权闸;脑按帧输入 ``tag`` 路由。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from agent_os.api.v1 import (
    BUDGET_EXCEEDED,
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
from agent_os.kernel.errors import BudgetExceeded, SubtreeCancelled
from tests.helpers.kernels import assemble, record_all

BUDGET_YAML = """
skills:
  - name: test.bg_parent
    version: 1.0.0
    kind: prompt
    description: 按 input 调一个白名单子技能,拿到结果(含错误观察)后收尾。
    inputs:
      type: object
      properties:
        tag: { type: string }
        child: { type: string }
        child_input: { type: object }
      required: [tag]
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [test.bg_costly, test.bg_loop, test.bg_code_mid] }
    model: { prefer: ["mock/x"] }
    prompt: 按指令调一个子技能并收尾。
  - name: test.bg_costly
    version: 1.0.0
    kind: prompt
    description: 一步带成本终答,自身小 max_cost(自预算触发子树中止)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_cost: 0.05 }
    prompt: 直接回答。
  - name: test.bg_loop
    version: 1.0.0
    kind: prompt
    description: 每步调 test.bg_leaf,limits.max_steps=1(第二步记账时触发自预算)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [test.bg_leaf] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 1 }
    prompt: 反复调子技能。
  - name: test.bg_mid
    version: 1.0.0
    kind: prompt
    description: 中层(无预算):调 test.bg_leaf 带成本终答,用于祖孙链穿透。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [test.bg_leaf] }
    model: { prefer: ["mock/x"] }
    prompt: 调子技能后收尾。
  - name: test.bg_leaf
    version: 1.0.0
    kind: prompt
    description: 叶帧(无预算):按 tag 带/不带成本终答。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: 直接回答。
  - name: test.bg_root
    version: 1.0.0
    kind: prompt
    description: 根技能带小 max_cost(根帧超限 → BudgetExceeded 炸 run)。
    inputs: { type: object }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_cost: 0.05 }
    prompt: 直接回答。
  - name: test.bg_code_mid
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:invoke_one
    description: code 中层带 max_cost:孙帧花费沿祖先链触发本帧预算。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [test.bg_mid] }
    limits: { max_cost: 0.05 }
  - name: test.bg_spawn_mid
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:spawn_or_report
    description: code 带 max_cost:后台帧花费触发本帧预算,wait 方收 SubtreeCancelled。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [test.bg_leaf] }
    limits: { max_cost: 0.05 }
  - name: test.bg_driver
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:invoke_one
    description: code 根(无预算):invoke 白名单技能,驱动祖孙链/spawn 用例。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [test.bg_code_mid, test.bg_spawn_mid] }
  - name: test.bg_parallel
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:sandbox_parallel_probe
    description: code 根(无预算):parallel 驱动,验证分支预算的批内隔离。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [test.bg_costly, test.bg_leaf] }
"""

#: 带成本/零成本终答的两种 usage(触发阈值用严格大于,max_cost=0.05 对 0.5 必超)
COSTLY = 0.5


def _final(cost: float = 0.0) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1, cost=cost),
    )


def _call(name: str, args: dict) -> ChatResponse:
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            tool_calls=[ToolCall(id=f"c-{name}", name=name, args=args)],
        ),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _budget_brain(req: ChatRequest) -> ChatResponse:
    """按帧输入 tag 路由的单脑(首条 USER 消息即帧输入,§3.1 固定形态)。

    - ``parent``:无工具结果 → 调 ``input["child"]``;有 → 零成本终答;
    - ``mid``:无工具结果 → 调 test.bg_leaf(带成本);有 → 终答;
    - ``loop``:恒调 test.bg_leaf(零成本),max_steps 触发前不停;
    - ``costly``:带 0.5 成本终答;其余(``free``):零成本终答。
    """
    data: dict = {}
    for m in req.messages:
        if m.role is Role.USER:
            data = json.loads(m.content)
            break
    tag = data.get("tag")
    tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
    if tag == "parent":
        if not tool_msgs:
            return _call(f"skill.{data['child']}", dict(data.get("child_input") or {"tag": "free"}))
        return _final()
    if tag == "mid":
        if not tool_msgs:
            return _call("skill.test.bg_leaf", {"tag": "costly"})
        return _final()
    if tag == "loop":
        return _call("skill.test.bg_leaf", {"tag": "free"})
    return _final(cost=COSTLY if tag == "costly" else 0.0)


def _build(tmp_path) -> object:
    """最小内核:全部技能无工具白名单(推导档 none),不涉升权闸。"""
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(BUDGET_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(config, _budget_brain, p)


def _frame_of(kernel, skill_name: str):
    """run 结束后按技能名取帧(每个用例中技能名唯一)。"""
    return next(f for f in kernel.stack.tree() if f.skill.name == skill_name)


def _requests_by_tag(recorded, tag: str):
    """按首条 USER 消息的 tag 取该帧的全部 LLM 请求。"""
    out = []
    for req in recorded:
        for m in req.messages:
            if m.role is Role.USER:
                try:
                    if json.loads(m.content).get("tag") == tag:
                        out.append(req)
                except (json.JSONDecodeError, AttributeError):
                    pass
                break
    return out


def _parent_error_observation(mock_recorded) -> dict:
    """父帧第二次请求中折叠的子帧错误观察(tool 结果消息体)。"""
    parent_reqs = _requests_by_tag(mock_recorded, "parent")
    assert len(parent_reqs) == 2, "父帧应恰好两次 LLM 调用(调子技能 + 收尾)"
    tool_msgs = [m for m in parent_reqs[1].messages if m.role is Role.TOOL]
    assert len(tool_msgs) == 1
    return json.loads(tool_msgs[0].content)


# ---------------------------------------------------------------------------
# max_cost:子树级中止(非根)不炸 run;根帧超限炸 run
# ---------------------------------------------------------------------------


def test_child_max_cost_trips_subtree_cancel_run_survives(tmp_path):
    """子技能 limits.max_cost 小 → invoke 边界折叠 ok=False interrupted(消息含
    budget),父帧与 run 继续到正常终态(子树级中止,非炸 run)。"""
    from agent_os.providers.mock import MockProvider

    mock = MockProvider(_budget_brain)
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(BUDGET_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(config, mock, p)
    result = asyncio.run(
        kernel.run(
            "test.bg_parent",
            {"tag": "parent", "child": "test.bg_costly", "child_input": {"tag": "costly"}},
        )
    )
    assert result == {"done": True}
    run = next(iter(kernel._runs.values()))
    assert run.state.status is RunStatus.DONE

    child = _frame_of(kernel, "test.bg_costly")
    assert child.status is FrameStatus.FAILED
    assert isinstance(child.error, SubtreeCancelled) and "budget" in str(child.error)

    payload = _parent_error_observation(mock.recorded)
    assert payload["ok"] is False
    assert payload["error"]["kind"] == "interrupted"
    assert "budget" in payload["error"]["message"]


def test_root_max_cost_raises_budget_exceeded(tmp_path):
    """根技能 limits.max_cost 小 → kernel.run 抛 BudgetExceeded,run 落 aborted。"""
    kernel = _build(tmp_path)
    with pytest.raises(BudgetExceeded, match="budget"):
        asyncio.run(kernel.run("test.bg_root", {"tag": "costly"}))
    run = next(iter(kernel._runs.values()))
    assert run.state.status is RunStatus.ABORTED


def test_ancestor_budget_cancels_subtree_across_invoke_chain(tmp_path):
    """祖孙链:祖父(code 帧)max_cost 管孙帧花费——叶帧记账穿透触发祖父预算,
    祖父子树级联取消;run 不炸,根帧拿到含 budget 的错误观察后正常收尾。

    边界固定:叶帧在触发当步正常完成(safe point 语义);中层 prompt 帧在下一
    safe point 抛 SubtreeCancelled;code 帧不检查帧级 stop 标志,其失败随
    invoke 边界(子帧终态错误观察 → SkillLoadError)穿透。
    """
    from agent_os.providers.mock import MockProvider

    mock = MockProvider(_budget_brain)
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(BUDGET_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(config, mock, p)
    seen = record_all(kernel)
    result = asyncio.run(
        kernel.run(
            "test.bg_parent",
            {
                "tag": "parent",
                "child": "test.bg_code_mid",
                "child_input": {"skill": "test.bg_mid", "args": {"tag": "mid"}},
            },
        )
    )
    assert result == {"done": True}
    run = next(iter(kernel._runs.values()))
    assert run.state.status is RunStatus.DONE

    leaf = _frame_of(kernel, "test.bg_leaf")
    mid = _frame_of(kernel, "test.bg_mid")
    code_mid = _frame_of(kernel, "test.bg_code_mid")
    assert leaf.status is FrameStatus.DONE  # 触发当步完成(取消在 safe point 生效)
    assert leaf.usage.cost == COSTLY
    assert mid.status is FrameStatus.FAILED and isinstance(mid.error, SubtreeCancelled)
    assert code_mid.status is FrameStatus.FAILED  # 经 invoke 边界 SkillLoadError 穿透

    signals = [s for s in seen if s.name == BUDGET_EXCEEDED]
    assert len(signals) == 1
    assert signals[0].payload["frame_id"] == code_mid.frame_id
    assert signals[0].payload["subtree_cost"] == COSTLY

    payload = _parent_error_observation(mock.recorded)
    assert payload["ok"] is False and "budget" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# spawn / parallel:后台帧花费触发祖先预算;分支预算批内隔离
# ---------------------------------------------------------------------------


def test_spawned_frame_spend_trips_ancestor_budget(tmp_path):
    """spawn:后台帧花费触发祖先(code 帧)预算 → 子树取消,wait 方收
    SubtreeCancelled(spawn_or_report 捕获取消收尾);code 帧自身不检查 stop
    标志,正常结算,run 不炸。"""
    kernel = _build(tmp_path)
    seen = record_all(kernel)
    result = asyncio.run(
        kernel.run(
            "test.bg_driver",
            {"skill": "test.bg_spawn_mid", "args": {"skill": "test.bg_leaf", "args": {"tag": "costly"}}},
        )
    )
    value = result["value"]
    assert value["cancelled"] is True, "wait 方应收 SubtreeCancelled 并回报 cancelled"
    run = next(iter(kernel._runs.values()))
    assert run.state.status is RunStatus.DONE
    spawn_mid = _frame_of(kernel, "test.bg_spawn_mid")
    assert spawn_mid.status is FrameStatus.DONE
    signals = [s for s in seen if s.name == BUDGET_EXCEEDED]
    assert len(signals) == 1 and signals[0].payload["frame_id"] == spawn_mid.frame_id


def test_parallel_branch_budget_isolated(tmp_path):
    """parallel:某分支子技能带小 max_cost → 该分支子树中止(分支条目 ok=False,
    kind=cancelled,消息含 budget),兄弟分支正常结算,run 不炸。"""
    kernel = _build(tmp_path)
    result = asyncio.run(
        kernel.run(
            "test.bg_parallel",
            {
                "branches": [
                    {"skill": "test.bg_costly", "input": {"tag": "costly"}},
                    {"skill": "test.bg_leaf", "input": {"tag": "free"}},
                ]
            },
        )
    )
    results = result["results"]
    assert results[0]["ok"] is False
    assert results[0]["error"]["kind"] == "cancelled"
    assert "budget" in results[0]["error"]["message"]
    assert results[1]["ok"] is True and results[1]["value"] == {"done": True}
    run = next(iter(kernel._runs.values()))
    assert run.state.status is RunStatus.DONE


# ---------------------------------------------------------------------------
# max_steps 帧级执行点 / budget.exceeded 信号恰好一次(防重)
# ---------------------------------------------------------------------------


def test_frame_max_steps_trips_subtree_cancel(tmp_path):
    """子技能 limits.max_steps=1:第二步记账时帧自身步数超限 → 子树级中止
    (invoke 边界折叠,父帧/run 继续)。"""
    from agent_os.providers.mock import MockProvider

    mock = MockProvider(_budget_brain)
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(BUDGET_YAML), encoding="utf-8")
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(config, mock, p)
    result = asyncio.run(
        kernel.run(
            "test.bg_parent",
            {"tag": "parent", "child": "test.bg_loop", "child_input": {"tag": "loop"}},
        )
    )
    assert result == {"done": True}
    loop_frame = _frame_of(kernel, "test.bg_loop")
    assert loop_frame.usage.steps == 2, "第一步未超(1 > 1 不成立),第二步记账时触发"
    assert loop_frame.status is FrameStatus.FAILED
    assert isinstance(loop_frame.error, SubtreeCancelled) and "budget" in str(loop_frame.error)
    payload = _parent_error_observation(mock.recorded)
    assert payload["ok"] is False and "budget" in payload["error"]["message"]


def test_budget_exceeded_signal_fires_exactly_once(tmp_path):
    """budget.exceeded 每预算帧恰好一次:payload 形状固定;``_budget_tripped``
    防重——重复检查(子树花费仍超限)不再发信号、不再抛。"""
    kernel = _build(tmp_path)
    seen = record_all(kernel)
    asyncio.run(
        kernel.run(
            "test.bg_parent",
            {"tag": "parent", "child": "test.bg_costly", "child_input": {"tag": "costly"}},
        )
    )
    child = _frame_of(kernel, "test.bg_costly")
    signals = [s for s in seen if s.name == BUDGET_EXCEEDED]
    assert len(signals) == 1
    payload = signals[0].payload
    assert payload["frame_id"] == child.frame_id
    assert payload["skill"] == "local:test.bg_costly@1.0.0"
    assert payload["max_cost"] == 0.05 and payload["max_steps"] is None
    assert payload["subtree_cost"] == COSTLY and payload["subtree_steps"] == 1

    # 防重:预算帧已登记,记账点之外的重复检查(白盒直调)幂等
    asyncio.run(kernel._check_subtree_budgets(child))
    assert len([s for s in seen if s.name == BUDGET_EXCEEDED]) == 1

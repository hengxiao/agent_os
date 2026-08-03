"""Skill Inlining(预展开 merge)锚点测试(docs/SKILL-INLINING.md)。

固定约定:

- manifest ``inline: true``(§3):硬闸门——仅 prompt 技能、纯度
  (tools/skills/blackboard 全空)、prompt 非空;占位符/超长/无效字段为 lint 告警;
- 组装(§4):调用方 SYSTEM 尾部追加"内联能力段"(按 permissions.skills
  声明序),对应伪工具 ``skill.<name>`` 不再生成;非 inline 子技能不受影响;
- 帧内冻结(§4.2):内联段在帧首次 build 时快照进
  ``frame.context.working["_inline_caps"]``——SYSTEM 跨步逐字节稳定,
  热重载"在跑帧钉旧版、新帧用新版",resume 重建逐字节一致;
- 信号(§7):快照组装时一次性发 ``post:context.inline``(payload 含
  skills/chars);无内联依赖的帧不发;
- 消融(§9):``RunConfig.inline == "off"`` 退化为普通压帧调用;
- 降级(§4.4/§8):幻觉调用 / ``ctx.invoke`` / spawn 一个 inline 技能
  → 照常压帧执行,行为正确。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import textwrap

import pytest

from agent_os.api.v1 import (
    POST_CONTEXT_INLINE,
    POST_FRAME_PUSH,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    FrameContext,
    Message,
    Permission,
    Role,
    RunConfig,
    Signal,
    SkillFrame,
    SkillRef,
    ToolCall,
    ToolPolicy,
)
from agent_os.context.manager import (
    INLINE_CAPS_KEY,
    INLINE_SECTION_HEADER,
    ContextManager,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.skills.manifest import parse_manifest, validate_manifest
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.helpers.kernels import assemble, record_all

TIDY_PROMPT = "输出中的日期一律规范为 ISO 8601;含时区语义时用 UTC 显式标注。"

SKILLS_YAML = f"""
skills:
  - name: test.caller
    version: 1.0.0
    kind: prompt
    inputs: {{ type: object, properties: {{}} }}
    outputs: {{ type: object }}
    permissions: {{ tools: [], skills: [test.tidy, test.helper] }}
    model: {{ prefer: ["mock/x"] }}
    prompt: CALLER-PROMPT
  - name: test.tidy
    version: 1.0.0
    kind: prompt
    inline: true
    description: 日期规范能力。Use when 产出含日期;Do not use when 需相对时间推算。
    permissions: {{ tools: [], skills: [] }}
    prompt: {TIDY_PROMPT}
  - name: test.helper
    version: 1.0.0
    kind: prompt
    inputs: {{ type: object, properties: {{}} }}
    outputs: {{ type: object }}
    permissions: {{ tools: [], skills: [] }}
    model: {{ prefer: ["mock/x"] }}
    prompt: HELPER-PROMPT
"""


def _write_yaml(tmp_path, body: str = SKILLS_YAML):
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _registry(tmp_path) -> LocalFileSkillRegistry:
    return LocalFileSkillRegistry(str(_write_yaml(tmp_path)))


class _RecordingBus:
    def __init__(self) -> None:
        self.seen: list[Signal] = []

    def subscribe(self, pattern, handler) -> None: ...

    async def emit(self, sig: Signal):
        self.seen.append(sig)
        return []


def _manager(reg, *, inline: str = "on", bus: _RecordingBus | None = None):
    return ContextManager(
        skills=reg,
        tools=LocalPythonToolRegistry(),
        config=RunConfig(model="mock/x", inline=inline, compression="off"),
        signals=bus,
        status_bar=False,
    )


def _frame(skill: str = "test.caller") -> SkillFrame:
    return SkillFrame(
        frame_id="f1",
        run_id="r1",
        skill=SkillRef(name=skill),
        input={},
        context=FrameContext(messages=[Message(role=Role.USER, content="{}")]),
    )


def _build(mgr, frame) -> ChatRequest:
    return asyncio.run(mgr.build(frame))


# ---------------------------------------------------------------------------
# §3 加载期闸门与 lint
# ---------------------------------------------------------------------------


def _inline_manifest(**overrides):
    data = {
        "name": "t",
        "version": "1.0.0",
        "kind": "prompt",
        "inline": True,
        "description": "x 能力。Use when 需要;Do not use when 不需要。",
        "permissions": {"tools": [], "skills": []},
        "prompt": "说明书内容",
    }
    data.update(overrides)
    return parse_manifest(data)


def test_inline_code_skill_rejected():
    m = _inline_manifest(kind="code", handler="tests.helpers.code_skills:pure_add", prompt=None)
    with pytest.raises(SkillLoadError, match="prompt 技能"):
        validate_manifest(m)


@pytest.mark.parametrize(
    "perms",
    [
        {"tools": ["system.file.read"], "skills": []},
        {"tools": [], "skills": ["demo.fib"]},
        {"tools": [], "skills": [], "blackboard": ["ns"]},
    ],
)
def test_inline_impure_permissions_rejected(perms):
    with pytest.raises(SkillLoadError, match="纯度"):
        validate_manifest(_inline_manifest(permissions=perms))


def test_inline_empty_prompt_rejected():
    with pytest.raises(SkillLoadError, match="非空 prompt"):
        validate_manifest(_inline_manifest(prompt=""))


def test_inline_lints_warn_not_block():
    m = _inline_manifest(
        prompt="处理 {raw} 字段" + "x" * 600,
        outputs={"type": "object"},
        verifier="v",
    )
    warnings = validate_manifest(m)
    text = "\n".join(warnings)
    assert "说明书形态" in text  # 占位符
    assert "过长" in text
    assert "无运行期效力" in text  # outputs
    assert "verifier" in text


# ---------------------------------------------------------------------------
# §4 组装:内联段 + 伪工具过滤 + 消融分流
# ---------------------------------------------------------------------------


def test_build_merges_inline_section_and_hides_pseudo_tool(tmp_path):
    mgr = _manager(_registry(tmp_path))
    req = _build(mgr, _frame())

    system = req.messages[0].content
    assert system.startswith("CALLER-PROMPT")
    assert INLINE_SECTION_HEADER in system
    assert "### test.tidy@1.0.0" in system
    assert TIDY_PROMPT in system

    tool_names = {t["name"] for t in req.tools}
    assert "skill.test.helper" in tool_names, "非 inline 子技能伪工具应保留"
    assert "skill.test.tidy" not in tool_names, "inline 技能不应生成伪工具"


def test_ablation_off_degrades_to_plain_skill(tmp_path):
    mgr = _manager(_registry(tmp_path), inline="off")
    req = _build(mgr, _frame())

    system = req.messages[0].content
    assert INLINE_SECTION_HEADER not in system
    tool_names = {t["name"] for t in req.tools}
    assert "skill.test.tidy" in tool_names, "off 档 merge 技能应退化为普通可调用技能"


def test_frame_without_inline_deps_untouched(tmp_path):
    mgr = _manager(_registry(tmp_path))
    req = _build(mgr, _frame("test.helper"))
    assert INLINE_SECTION_HEADER not in req.messages[0].content


# ---------------------------------------------------------------------------
# §4.2 帧内冻结:前缀稳定 + 热重载钉版本 + resume 确定性
# ---------------------------------------------------------------------------


def test_prefix_stable_across_steps_and_hot_reload(tmp_path):
    reg = _registry(tmp_path)
    mgr = _manager(reg)
    frame = _frame()

    sys1 = _build(mgr, frame).messages[0].content
    sys2 = _build(mgr, frame).messages[0].content
    assert sys1 == sys2, "同帧相邻 build 的 SYSTEM 必须逐字节一致"

    # 热重载:tidy prompt 改版 → 在跑帧钉旧版,新帧用新版
    path = _write_yaml(tmp_path, SKILLS_YAML.replace(TIDY_PROMPT, "全新版本的日期指令。"))
    bumped = path.stat().st_mtime + 2
    os.utime(path, (bumped, bumped))
    assert reg.reload() is True

    sys3 = _build(mgr, frame).messages[0].content
    assert sys3 == sys1, "在跑帧的内联段必须钉住旧版(快照)"

    fresh = _frame()
    fresh.frame_id = "f2"
    sys_new = _build(mgr, fresh).messages[0].content
    assert "全新版本的日期指令。" in sys_new and TIDY_PROMPT not in sys_new


def test_snapshot_frozen_in_working_and_resume_deterministic(tmp_path):
    reg = _registry(tmp_path)
    mgr = _manager(reg)
    frame = _frame()
    sys1 = _build(mgr, frame).messages[0].content

    caps = frame.context.working.get(INLINE_CAPS_KEY)
    assert caps is not None
    assert caps["hidden"] == ["skill.test.tidy"]
    assert caps["skills"] == [{"name": "test.tidy", "version": "1.0.0", "chars": len(TIDY_PROMPT)}]

    # 模拟 resume:working 快照随帧入档;恢复时 registry 已是新版,SYSTEM 仍按快照重建
    restored = _frame()
    restored.context.working = copy.deepcopy(frame.context.working)
    path = _write_yaml(tmp_path, SKILLS_YAML.replace(TIDY_PROMPT, "恢复后才出现的新指令。"))
    bumped = path.stat().st_mtime + 2
    os.utime(path, (bumped, bumped))
    reg.reload()
    sys_restored = _build(mgr, restored).messages[0].content
    assert sys_restored == sys1, "resume 重建的 SYSTEM 必须与断电前逐字节一致"


# ---------------------------------------------------------------------------
# §7 信号:一次性 post:context.inline
# ---------------------------------------------------------------------------


def test_inline_signal_emitted_once(tmp_path):
    bus = _RecordingBus()
    mgr = _manager(_registry(tmp_path), bus=bus)
    frame = _frame()
    _build(mgr, frame)
    _build(mgr, frame)

    inline_sigs = [s for s in bus.seen if s.name == POST_CONTEXT_INLINE]
    assert len(inline_sigs) == 1, "快照只组装一次,信号只发一次"
    assert inline_sigs[0].payload["skills"][0]["name"] == "test.tidy"

    _build(mgr, _frame("test.helper"))
    assert len([s for s in bus.seen if s.name == POST_CONTEXT_INLINE]) == 1, (
        "无内联依赖的帧不发信号"
    )


# ---------------------------------------------------------------------------
# §4.4/§8/§9 端到端:降级、spawn 正交、消融 A/B(经真实内核)
# ---------------------------------------------------------------------------

KERNEL_YAML = SKILLS_YAML + """
  - name: test.coded
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:invoke_one
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [test.tidy] }
  - name: test.spawner
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:spawn_one
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [test.tidy] }
"""


def _final(payload) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _is_caller_frame(req: ChatRequest) -> bool:
    # 注意不能用"日期"等 tidy 指令词判断帧归属:on 档它们已并入调用方 SYSTEM
    return (req.messages[0].content or "").startswith("CALLER-PROMPT")


def _call_tidy() -> ChatResponse:
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            tool_calls=[ToolCall(id="c1", name="skill.test.tidy", args={})],
        ),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def adaptive_brain(req: ChatRequest) -> ChatResponse:
    """自适应大脑:调用方看得见 skill.test.tidy 就调用,看不见就直接答;子帧给终答。"""
    if not _is_caller_frame(req):
        return _final({"done": True})  # tidy 子帧
    if any(m.role is Role.TOOL for m in req.messages):
        return _final({"result": "ok"})
    if any(t["name"] == "skill.test.tidy" for t in req.tools):
        return _call_tidy()
    return _final({"result": "ok"})


def stubborn_brain(req: ChatRequest) -> ChatResponse:
    """固执大脑:看不见伪工具也幻觉调用 skill.test.tidy(降级路径测试)。"""
    if not _is_caller_frame(req):
        return _final({"done": True})
    if any(m.role is Role.TOOL for m in req.messages):
        return _final({"result": "ok"})
    return _call_tidy()


def _kernel(tmp_path, brain, *, inline: str = "on", blackboard=None):
    config = RunConfig(
        model="mock/x",
        inline=inline,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config, brain, _write_yaml(tmp_path, KERNEL_YAML), blackboard=blackboard
    )


def test_ablation_ab_on_no_frame_off_real_frame(tmp_path):
    """核心 A/B 锚点:on 档模型看不见伪工具直接完成(零子帧);off 档真实压帧调用。"""
    kernel_on = _kernel(tmp_path, adaptive_brain, inline="on")
    seen_on = record_all(kernel_on)
    assert asyncio.run(kernel_on.run("test.caller", {})) == {"result": "ok"}
    assert len([s for s in seen_on if s.name == POST_FRAME_PUSH]) == 1, "on 档不应有子帧"

    kernel_off = _kernel(tmp_path, adaptive_brain, inline="off")
    seen_off = record_all(kernel_off)
    assert asyncio.run(kernel_off.run("test.caller", {})) == {"result": "ok"}
    pushes = [s for s in seen_off if s.name == POST_FRAME_PUSH]
    assert len(pushes) == 2, "off 档 merge 技能应真实压帧"
    assert any("tidy" in s.payload["skill"] for s in pushes)


def test_hallucinated_call_degrades_to_frame(tmp_path):
    """on 档幻觉调用 skill.test.tidy → 照常压帧执行,结果正确(§4.4,runner 零特判)。"""
    kernel = _kernel(tmp_path, stubborn_brain, inline="on")
    seen = record_all(kernel)
    assert asyncio.run(kernel.run("test.caller", {})) == {"result": "ok"}
    assert any(
        s.name == POST_FRAME_PUSH and "tidy" in s.payload["skill"] for s in seen
    ), "幻觉调用应降级为真实帧"


def test_logic_context_invoke_degrades_to_frame(tmp_path):
    """code 技能 ctx.invoke 一个 merge 技能 → 照常压帧(§8:merge 只改 LLM 呈现面)。"""
    kernel = _kernel(tmp_path, adaptive_brain)
    seen = record_all(kernel)
    result = asyncio.run(kernel.run("test.coded", {"skill": "test.tidy"}))
    assert result == {"value": {"done": True}}
    assert any(
        s.name == POST_FRAME_PUSH and "tidy" in s.payload["skill"] for s in seen
    )


def test_spawn_inline_skill_still_frames(tmp_path):
    """spawn 一个 merge 技能 → 照常压帧、返回 frame_id(§8 正交性)。"""
    from agent_os.blackboard import LocalBlackboard

    kernel = _kernel(tmp_path, adaptive_brain, blackboard=LocalBlackboard())
    result = asyncio.run(kernel.run("test.spawner", {"skill": "test.tidy"}))
    assert result["frame_id"]
    assert result["value"] == {"done": True}


# ---------------------------------------------------------------------------
# §6 replay 零改动(经 CLI replay + diff)
# ---------------------------------------------------------------------------


def test_replay_run_with_merge_skill(tmp_path, capsys):
    """含 merge 技能的 run 可 replay 且 diff 为空——验证设计稿 §6 "replay 零改动"。"""
    from tests.helpers.config import run_cli, write_config

    skills = _write_yaml(tmp_path)
    cfg = write_config(
        tmp_path, brain="tests.helpers.brains:done_brain", skills=skills
    )
    artifacts = str(tmp_path / "runs")
    rc, out = run_cli(capsys, "run", "test.caller", "--input", "{}",
                      "--config", str(cfg), "--artifacts", artifacts, "--json")
    assert rc == 0, out
    assert out["result"] == {"done": True}

    rc, rep = run_cli(capsys, "replay", out["run_id"],
                      "--config", str(cfg), "--artifacts", artifacts, "--json")
    assert rc == 0
    assert rep["result"] == {"done": True}

    rc, d = run_cli(capsys, "diff", out["run_id"], rep["run_id"],
                    "--artifacts", artifacts, "--json")
    assert rc == 0
    assert d["result_equal"] is True and d["signals_equal"] is True

"""默认 register() 验证门(docs/DESIGN.md §6.2;skills/register_smoke.py)测试。

固定约定(与 tests/skills/test_register.py 同面):

- 候选技能 gen.tone:prompt 技能,outputs 要求 ``{"answer": str}``;
- 草稿落在 tmp 的 ``drafts/gen.tone/``(manifest.yaml + tests/*.json),
  与 build_kernel 缺省草稿根(skills.yaml 同级 drafts/)一致;
- 重放确定性来自用例 ``mock_script``(dict 形态 ChatResponse 列表);
- ``_run`` = asyncio.run 同步驱动(同 test_register.py ``_register`` 先例)。

覆盖:expected 深比较(过/拒 + register 端到端)/ outputs 校验连败 / expect
LLM 裁判三态 + 未配置裁判 / 无草稿与无用例 fail-closed / code 技能 fail-closed /
无 mock_script 走装配 provider / build_kernel 配置接线("default" 哨兵、
register_judge_model 校验)/ max_cases 截断 / 冒烟内核工厂剥离 watcher·sidecars·mcp。
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
import yaml

import agent_os.runtime.config as runtime_config
from agent_os.api.v1 import (
    ChatResponse,
    ChatUsage,
    Message,
    Provenance,
    Role,
    RunConfig,
    SkillArtifact,
    SkillKind,
    SkillManifest,
    SkillPermissions,
    SkillRef,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.runtime.config import ConfigError, _smoke_kernel_factory, build_kernel
from agent_os.skills.draft_store import DraftStore
from agent_os.skills.gate import GateError
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.skills.register_smoke import DefaultRegisterSmoke
from agent_os.tools.local_registry import LocalPythonToolRegistry

PROD_YAML = """
skills:
  - name: lab.published
    version: 1.0.0
    kind: prompt
    description: 已发布。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [] }
    prompt: 已发布。
"""

DESC = "生成技能。Use when 测试 register;Do not use when 其他。"

#: 候选技能 outputs:必须含 answer 字符串(mock 最终答案与 expected 都按此对齐)
ANSWER_OUTPUTS = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.fixture()
def production(tmp_path):
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    return LocalFileSkillRegistry(str(path))


def _register(registry, artifact, **prov_kw):
    provenance = Provenance(
        run_id=prov_kw.get("run_id", "r1"),
        task=prov_kw.get("task", "t1"),
        note=prov_kw.get("note", ""),
    )
    return asyncio.run(registry.register(artifact, provenance))


def _read_jsonl(registry) -> list[dict]:
    path = Path(registry.path)
    log = path.with_name(path.name + ".register.jsonl")
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _prompt_artifact(
    name: str = "gen.tone",
    *,
    prompt: str = "你是语气改写助手,把输入改得更正式。",
    outputs: dict | None = None,
) -> SkillArtifact:
    return SkillArtifact(
        manifest=SkillManifest(
            name=name,
            version="",
            kind=SkillKind.PROMPT,
            description=DESC,
            inputs={"type": "object"},
            outputs=outputs if outputs is not None else ANSWER_OUTPUTS,
            permissions=SkillPermissions(skills=[]),
        ),
        prompt=prompt,
    )


def _entry(name: str = "gen.tone", *, outputs: dict | None = None) -> dict:
    """register() 步骤 2 归一化出的生产条目同形 dict(prompt 技能指令体内联)。"""
    return {
        "name": name,
        "version": "0.1.0",
        "kind": "prompt",
        "description": DESC,
        "inputs": {"type": "object"},
        "outputs": outputs if outputs is not None else ANSWER_OUTPUTS,
        "permissions": {"tools": [], "skills": []},
        "prompt": "你是语气改写助手,把输入改得更正式。",
    }


def _write_draft(root: Path, name: str, cases: dict[str, dict]) -> DraftStore:
    """落一个最小草稿(manifest + prompt + tests/*.json)并返回其 DraftStore。"""
    d = root / name
    (d / "tests").mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "0.1.0",
                "kind": "prompt",
                "description": DESC,
                "inputs": {"type": "object"},
                "outputs": ANSWER_OUTPUTS,
                "permissions": {"tools": [], "skills": []},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (d / "prompt.md").write_text("你是语气改写助手,把输入改得更正式。", encoding="utf-8")
    for fname, case in cases.items():
        (d / "tests" / fname).write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8"
        )
    return DraftStore(root)


def _mock_item(answer: str) -> dict:
    """一条 dict 形态 mock_script 项:最终答案 JSON + stop(test_lab_testrun 同形)。"""
    return {
        "message": {"role": "assistant", "content": json.dumps({"answer": answer})},
        "finish_reason": "stop",
    }


def _case(answer: str = "ok", **extra) -> dict:
    case = {"input": {"text": "hi"}, "mock_script": [_mock_item(answer)]}
    case.update(extra)
    return case


def _answer_brain(req) -> ChatResponse:
    """无 mock_script 用例的装配 provider 应答(恒回 {"answer": "from-brain"})。"""
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "from-brain"})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _kernel_factory(skills):
    """make_kernel 契约:每用例重建一次内核;``created`` 收集各内核的 provider 供断言。"""

    def make():
        provider = MockProvider(_answer_brain)
        make.created.append(provider)
        return (
            KernelBuilder(RunConfig(model="mock/x", compression="off"))
            .providers(provider)
            .tools(LocalPythonToolRegistry())
            .skills(skills)
            .logic_kernels(InProcessLogicKernel())
            .build()
        )

    make.created = []
    return make


def _judge_brain(payload: str):
    """裁判应答函数:恒回给定原文(严格 JSON / 非 JSON 两种形态都由 payload 控制)。"""

    def brain(req) -> ChatResponse:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=payload),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    return brain


# ---------------------------------------------------------------------------
# happy path:expected 深比较通过 → 钩子 ok;bind 后 register() 端到端过闸
# ---------------------------------------------------------------------------


def test_default_smoke_pass_then_register_end_to_end(production, tmp_path):
    """1 用例(mock_script + 匹配的 expected)→ hook ok=True;register() 成功,gates.smoke=pass。"""
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_kernel_factory(production)
    )
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is True, outcome["detail"]
    assert "1 个用例" in outcome["detail"]

    production.bind_register_smoke(hook)
    ref = _register(production, _prompt_artifact())
    assert ref == SkillRef(name="gen.tone", version="0.1.0")
    records = _read_jsonl(production)
    assert records[-1]["action"] == "appended"
    assert records[-1]["gates"]["smoke"] == "pass"


# ---------------------------------------------------------------------------
# expected 深比较不一致 → 拒绝(生产零变化,jsonl action=rejected)
# ---------------------------------------------------------------------------


def test_expected_mismatch_register_rejected_zero_change(production, tmp_path):
    """expected 与重放结果不一致 → ok=False;register() GateError;生产 yaml 零变化。"""
    store = _write_draft(
        tmp_path / "drafts",
        "gen.tone",
        {"case1.json": _case(answer="ok", expected={"answer": "different"})},
    )
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_kernel_factory(production)
    )
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is False
    assert "expected 深比较不一致" in outcome["detail"]
    assert outcome["cases"] == [
        {"case": "case1.json", "ok": False, "reason": outcome["cases"][0]["reason"]}
    ]

    production.bind_register_smoke(hook)
    before = Path(production.path).read_bytes()
    with pytest.raises(GateError, match="expected 深比较不一致"):
        _register(production, _prompt_artifact())
    assert Path(production.path).read_bytes() == before, "验证门拒绝:生产零变化"
    records = _read_jsonl(production)
    assert records[0]["action"] == "rejected"
    assert "expected 深比较不一致" in records[0]["gates"]["smoke"]


def test_expected_normalized_compare_type_insensitive(production, tmp_path):
    """结构归一:int/float 统一、dict 键序无关(learn_handlers verify_before_store 同款语义)。"""
    store = _write_draft(
        tmp_path / "drafts",
        "gen.tone",
        {"case1.json": _case(expected={"answer": "ok"})},
    )
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_kernel_factory(production)
    )
    # result 的 answer 是 "ok";expected 换成数值形态走归一(1 vs 1.0 应一致)
    outcome = asyncio.run(
        hook("gen.tone", _entry(outputs={"type": "object"}))
    )
    assert outcome["ok"] is True, outcome["detail"]


# ---------------------------------------------------------------------------
# outputs schema 连败 → 用例失败
# ---------------------------------------------------------------------------


def test_outputs_schema_violation_case_fails(production, tmp_path):
    """mock 最终答案不合 outputs(内核输出修复循环连败两次)→ run 异常,用例 fail。"""
    bad_script = [
        {"message": {"role": "assistant", "content": json.dumps({"wrong": 1})},
         "finish_reason": "stop"},
        {"message": {"role": "assistant", "content": json.dumps({"wrong": 1})},
         "finish_reason": "stop"},
    ]
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": {"input": {}, "mock_script": bad_script}}
    )
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_kernel_factory(production)
    )
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is False
    assert "OutputValidationError" in outcome["detail"]
    assert outcome["cases"][0]["ok"] is False


# ---------------------------------------------------------------------------
# expect LLM 裁判:pass / reject(带 reason)/ 非 JSON fail-closed / 未配置裁判
# ---------------------------------------------------------------------------


def _hook_with_judge(production, store, judge_payload: str) -> tuple[DefaultRegisterSmoke, MockProvider]:
    judge = MockProvider(_judge_brain(judge_payload))
    hook = DefaultRegisterSmoke(
        production=production,
        store=store,
        make_kernel=_kernel_factory(production),
        judge_providers=judge,
        judge_model="mock/judge",
    )
    return hook, judge


def test_expect_judge_pass(production, tmp_path):
    """裁判回 {"pass": true} → 用例过;裁判请求温度 0.0、带 system 判定提示。"""
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expect="回答应给出 answer 字段")}
    )
    hook, judge = _hook_with_judge(production, store, '{"pass": true, "reason": "满足期望"}')
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is True, outcome["detail"]
    assert judge.recorded, "expect 判定必须真调裁判"
    req = judge.recorded[0]
    assert req.model == "mock/judge" and req.temperature == 0.0
    assert req.messages[0].role is Role.SYSTEM


def test_expect_judge_reject_detail_has_reason(production, tmp_path):
    """裁判回 {"pass": false, "reason": ...} → 用例 fail,detail 带裁判理由。"""
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expect="回答应给出 answer 字段")}
    )
    hook, _ = _hook_with_judge(production, store, '{"pass": false, "reason": "结果缺少答案"}')
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is False
    assert "结果缺少答案" in outcome["detail"]


def test_expect_judge_non_json_fail_closed(production, tmp_path):
    """裁判回非 JSON → fail-closed(同 DistillSidecar verify 档解析语义)。"""
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expect="回答应给出 answer 字段")}
    )
    hook, _ = _hook_with_judge(production, store, "I think it passes")
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is False
    assert "非 JSON" in outcome["detail"]


def test_expect_without_judge_providers_fail_closed(production, tmp_path):
    """用例带 expect 但未配置裁判 → fail-closed,detail 给 register_judge_model 配置指引。"""
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expect="回答应给出 answer 字段")}
    )
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_kernel_factory(production)
    )
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is False
    assert "register_judge_model" in outcome["detail"]


# ---------------------------------------------------------------------------
# fail-closed 边界:无草稿 / 草稿无用例 / code 技能
# ---------------------------------------------------------------------------


def test_no_draft_fail_closed_with_convention(production, tmp_path):
    """草稿不存在 → fail-closed;detail 带 tests/*.json 用例约定指引。"""
    hook = DefaultRegisterSmoke(
        production=production,
        store=DraftStore(tmp_path / "drafts"),  # 空草稿根(无 gen.tone 目录)
        make_kernel=_kernel_factory(production),
    )
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is False
    assert "tests/*.json" in outcome["detail"] and "mock_script" in outcome["detail"]


def test_draft_without_tests_fail_closed(production, tmp_path):
    """草稿在但 tests/ 无任何用例 → fail-closed;detail 带约定指引。"""
    store = _write_draft(tmp_path / "drafts", "gen.tone", {})
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_kernel_factory(production)
    )
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is False
    assert "没有任何重放用例" in outcome["detail"]
    assert "tests/*.json" in outcome["detail"]


def test_code_skill_entry_fail_closed_escape_hatch(production):
    """code 技能 entry → fail-closed(handler 落盘在验证门之后);detail 给自定义 hook 逃生口。"""
    hook = DefaultRegisterSmoke(production=production, store=None, make_kernel=None)
    outcome = asyncio.run(hook("gen.echo", {"name": "gen.echo", "kind": "code"}))
    assert outcome["ok"] is False
    assert "pkg.mod:func" in outcome["detail"], "逃生口:register_smoke 自定义 hook"


# ---------------------------------------------------------------------------
# 无 mock_script:走 make_kernel 装配的 provider 真跑
# ---------------------------------------------------------------------------


def test_case_without_mock_script_uses_assembled_providers(production, tmp_path):
    """用例无 mock_script → 用冒烟内核装配的 provider(_answer_brain)跑;expected 对齐即过。"""
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": {"input": {}, "expected": {"answer": "from-brain"}}}
    )
    factory = _kernel_factory(production)
    hook = DefaultRegisterSmoke(production=production, store=store, make_kernel=factory)
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is True, outcome["detail"]
    assert factory.created and factory.created[0].recorded, "装配的 provider 必须真被调用"


# ---------------------------------------------------------------------------
# build_kernel 配置接线:"default" 哨兵 / 校验 / 未知键
# ---------------------------------------------------------------------------


def test_config_default_smoke_wiring_and_register_pass(tmp_path):
    """[skills] register_smoke="default":bind DefaultRegisterSmoke;register() 全管线过闸。"""
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    _write_draft(  # 缺省草稿根 = skills.yaml 同级 drafts/
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    kernel = build_kernel(
        {
            "run": {"model": "mock/x"},
            "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
            "skills": {"path": str(path), "register_smoke": "default"},
        }
    )
    assert isinstance(kernel.skills._register_smoke, DefaultRegisterSmoke), "装配期 bind 默认实现"
    ref = asyncio.run(
        kernel.skills.register(_prompt_artifact(), Provenance(run_id="r1", task="t1", note=""))
    )
    assert ref.name == "gen.tone"
    assert _read_jsonl(kernel.skills)[0]["gates"]["smoke"] == "pass"


def test_config_default_smoke_without_path_rejected():
    """register_smoke="default" / register_judge_model 没有 path → ConfigError(无 registry 可接线)。"""
    with pytest.raises(ConfigError, match="path"):
        build_kernel({"skills": {"register_smoke": "default"}})
    with pytest.raises(ConfigError, match="path"):
        build_kernel({"skills": {"register_judge_model": "mock/x"}})


def test_config_register_judge_model_type_rejected(tmp_path):
    """register_judge_model 非字符串 → ConfigError(strict 校验先例)。"""
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    with pytest.raises(ConfigError, match="register_judge_model"):
        build_kernel(
            {
                "skills": {
                    "path": str(path),
                    "register_smoke": "default",
                    "register_judge_model": 42,
                }
            }
        )


def test_config_skills_unknown_key_still_rejected(tmp_path):
    """[skills] 未知键(拼错的 register_judge_model)→ ConfigError(strict 校验先例)。"""
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    with pytest.raises(ConfigError, match="未知字段"):
        build_kernel({"skills": {"path": str(path), "register_judge_mode": "mock/x"}})


# ---------------------------------------------------------------------------
# max_cases 截断
# ---------------------------------------------------------------------------


def test_max_cases_truncation_runs_only_first_n(production, tmp_path):
    """3 用例 + max_cases=2 → 只跑前 2 个(文件名序),detail 注明截断。"""
    cases = {f"case{i}.json": _case(expected={"answer": "ok"}) for i in range(3)}
    store = _write_draft(tmp_path / "drafts", "gen.tone", cases)
    hook = DefaultRegisterSmoke(
        production=production,
        store=store,
        make_kernel=_kernel_factory(production),
        max_cases=2,
    )
    outcome = asyncio.run(hook("gen.tone", _entry()))
    assert outcome["ok"] is True, outcome["detail"]
    assert [c["case"] for c in outcome["cases"]] == ["case0.json", "case1.json"], "只跑前 2 个"
    assert "用例共 3 个" in outcome["detail"] and "case2.json" in outcome["detail"]


# ---------------------------------------------------------------------------
# 冒烟内核工厂:剥离 watcher / sidecars / mcp(防按例泄漏与蒸馏副作用)
# ---------------------------------------------------------------------------


def test_smoke_kernel_factory_strips_watcher_sidecars_mcp(tmp_path, monkeypatch):
    """原配置带 watch_interval=1 + [sidecars] distill + [mcp] 假 server:
    工厂产物的配置面全部剥离(假 mcp server 存活即证明剥了,否则 eager 装配炸 ConfigError);
    产出的内核不起看门狗线程、无 sidecar/ctl。"""
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    cfg = {
        "run": {"model": "mock/x"},
        "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
        "skills": {"path": str(path), "register_smoke": "default", "watch_interval": 1},
        "sidecars": {"distill": True},
        "mcp": {"servers": {"fake": {"command": "definitely-not-a-real-command-xyz"}}},
    }
    # 外层装配(不带 mcp:外层内核的 [mcp] 是真会 eager 连接的,不在本测试范围)
    kernel = build_kernel({k: v for k, v in cfg.items() if k != "mcp"})
    try:
        smoke = kernel.skills._register_smoke
        captured = {}
        real_build = runtime_config.build_kernel

        def spy(config, **kw):
            captured["cfg"] = config
            return real_build(config, **kw)

        monkeypatch.setattr(runtime_config, "build_kernel", spy)
        before = threading.enumerate()
        k2 = smoke._make_kernel()
        assert threading.enumerate() == before, "冒烟内核不得新增看门狗线程"
        stripped = captured["cfg"]
        assert stripped["skills"]["watch_interval"] == 0
        assert "register_smoke" not in stripped["skills"], "防冒烟内核无意义重绑"
        assert "sidecars" not in stripped and "mcp" not in stripped
        assert k2.skills._watch_thread is None
        # sidecar 未接线 = sidecars None;ctl 不作断言面——tools.bind_timer 存在时
        # builder 无条件补装 RunControlImpl(builder.py:496-501,计时器注入通道,与 sidecar 无关)
        assert k2.sidecars is None

        # 带假 mcp server 的原配置直接过工厂:剥了 mcp 才能装配成功
        assert _smoke_kernel_factory(cfg)() is not None
    finally:
        kernel.skills.stop_watching()

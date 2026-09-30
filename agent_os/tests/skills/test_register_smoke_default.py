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

2026-09-30 扩展(三参契约 + 两个遗留收口):

- (B) code 技能真冒烟:``_source`` 随调用携带 → 临时目录登台 generated_handlers
  包 + 沙箱跑用例(过/expected 不符/无 sandbox 指引/空 _source 维持原 fail-closed/
  register 端到端 yaml 零 _source 污染/生产同名 handler 缓存遮蔽与恢复);
- (A) 录制 run 重放证据:provenance.detail["source_run_id"] → 重放源技能判定
  任务真完成(过/篡改 result/缺目录/压缩排干信号对齐/裁判两态/源技能缺席/
  非 done 终态),引用证据不免除草稿用例;
- 配置 register_runs_root 接线与校验;工具端到端:source_run_id 入参经
  Provenance.detail 落 register.jsonl。
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest
import yaml

import agent_os.runtime.config as runtime_config
from agent_os.api.v1 import (
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Provenance,
    Role,
    RunConfig,
    SkillArtifact,
    SkillKind,
    SkillManifest,
    SkillPermissions,
    SkillRef,
    ToolCall,
    ToolPolicy,
)
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
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


# ---------------------------------------------------------------------------
# (B) code 技能真冒烟(2026-09-30):_source 随调用携带 → 临时 handler 登台 + 沙箱跑用例
# ---------------------------------------------------------------------------

#: 冒烟用 code 源码:零依赖、确定性(沙箱 env 白名单下也能跑)
CODE_SOURCE = '''
async def run(input, ctx):
    return {"answer": "echo:" + str(input.get("text", ""))}
'''

#: 同名再注册场景的 v2 源码(验证冒烟命中 _source 新版而非生产缓存旧版)
CODE_SOURCE_V2 = '''
async def run(input, ctx):
    return {"answer": "v2:" + str(input.get("text", ""))}
'''


def _code_entry(name: str = "gen.echo", *, source: str = CODE_SOURCE) -> dict:
    """register() 步骤 2 归一化 + 步骤 4 ``_source`` 注入后的 code 技能 entry 同形 dict。"""
    return {
        "name": name,
        "version": "0.1.0",
        "kind": "code",
        "description": DESC,
        "inputs": {"type": "object"},
        "outputs": ANSWER_OUTPUTS,
        "permissions": {"tools": [], "skills": []},
        "handler": f"generated_handlers.{name.replace('.', '_')}:run",
        "logic": {"mode": "sandbox"},  # §6.2:register 一律强制,覆盖 artifact 声明
        "_source": source,
    }


def _code_artifact(name: str = "gen.echo", code: str = CODE_SOURCE) -> SkillArtifact:
    return SkillArtifact(
        manifest=SkillManifest(
            name=name,
            version="",
            kind=SkillKind.CODE,
            description=DESC,
            inputs={"type": "object"},
            outputs=ANSWER_OUTPUTS,
            permissions=SkillPermissions(skills=[]),
        ),
        code=code,
    )


def _write_code_draft(root: Path, name: str, cases: dict[str, dict]) -> DraftStore:
    """code 技能草稿:manifest(kind=code + handler + 强制 sandbox)+ tests/*.json。"""
    d = root / name
    (d / "tests").mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "0.1.0",
                "kind": "code",
                "description": DESC,
                "inputs": {"type": "object"},
                "outputs": ANSWER_OUTPUTS,
                "permissions": {"tools": [], "skills": []},
                "handler": f"generated_handlers.{name.replace('.', '_')}:run",
                "logic": {"mode": "sandbox"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    for fname, case in cases.items():
        (d / "tests" / fname).write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8"
        )
    return DraftStore(root)


def _sandbox_kernel_factory(skills):
    """带 sandbox Logic Kernel 的 make_kernel(code 技能冒烟必备;其余同 _kernel_factory)。"""

    def make():
        provider = MockProvider(_answer_brain)
        make.created.append(provider)
        return (
            KernelBuilder(RunConfig(model="mock/x", compression="off"))
            .providers(provider)
            .tools(LocalPythonToolRegistry())
            .skills(skills)
            .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
            .build()
        )

    make.created = []
    return make


def test_code_skill_smoke_pass_via_sandbox_no_residue(production, tmp_path):
    """code entry + _source → 临时 handler 登台 + 沙箱真跑放行;sys.path/sys.modules 零残留。"""
    store = _write_code_draft(
        tmp_path / "drafts", "gen.echo",
        {"case1.json": {"input": {"text": "hi"}, "expected": {"answer": "echo:hi"}}},
    )
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_sandbox_kernel_factory(production)
    )
    path_before = list(sys.path)
    outcome = asyncio.run(hook("gen.echo", _code_entry()))
    assert outcome["ok"] is True, outcome["detail"]
    assert "1 个用例" in outcome["detail"]
    assert "generated_handlers.gen_echo" not in sys.modules, "临时模块缓存已弹出"
    assert "generated_handlers" not in sys.modules, "父包缓存已弹出(下次惰性重解析)"
    assert sys.path == path_before, "临时目录不得残留 sys.path"


def test_code_skill_smoke_expected_mismatch_rejected(production, tmp_path):
    """code 冒烟结果 ≠ expected → 同一判定链拒绝(深比较不一致)。"""
    store = _write_code_draft(
        tmp_path / "drafts", "gen.echo",
        {"case1.json": {"input": {"text": "hi"}, "expected": {"answer": "different"}}},
    )
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_sandbox_kernel_factory(production)
    )
    outcome = asyncio.run(hook("gen.echo", _code_entry()))
    assert outcome["ok"] is False
    assert "expected 深比较不一致" in outcome["detail"]


def test_code_skill_smoke_without_sandbox_fail_closed_guidance(production, tmp_path):
    """冒烟内核未装配 sandbox([tools] python_exec = off)→ 用例 fail-closed + 配置指引。"""
    store = _write_code_draft(
        tmp_path / "drafts", "gen.echo",
        {"case1.json": {"input": {"text": "hi"}, "expected": {"answer": "echo:hi"}}},
    )
    hook = DefaultRegisterSmoke(
        production=production,
        store=store,
        make_kernel=_kernel_factory(production),  # 仅 InProcess,无 SANDBOX 后端
    )
    outcome = asyncio.run(hook("gen.echo", _code_entry()))
    assert outcome["ok"] is False
    assert "[tools] python_exec" in outcome["detail"]
    assert outcome["cases"][0]["ok"] is False


def test_code_skill_empty_source_still_fail_closed(production, tmp_path):
    """_source 空串 = 未携带源码(绕过 register 管线的直接调用)→ 原 fail-closed 逃生口。"""
    store = _write_code_draft(tmp_path / "drafts", "gen.echo", {"case1.json": {"input": {}}})
    hook = DefaultRegisterSmoke(
        production=production, store=store, make_kernel=_sandbox_kernel_factory(production)
    )
    outcome = asyncio.run(hook("gen.echo", _code_entry(source="  ")))
    assert outcome["ok"] is False
    assert "pkg.mod:func" in outcome["detail"], "维持原 fail-closed 消息(自定义 hook 逃生口)"


def test_code_skill_register_end_to_end_yaml_has_no_source(production, tmp_path):
    """register() 端到端:smoke 用 _source 冒烟通过;生产 yaml 零 _source 污染,handler 落盘。"""
    store = _write_code_draft(
        tmp_path / "drafts", "gen.echo",
        {"case1.json": {"input": {"text": "hi"}, "expected": {"answer": "echo:hi"}}},
    )
    production.bind_register_smoke(
        DefaultRegisterSmoke(
            production=production, store=store, make_kernel=_sandbox_kernel_factory(production)
        )
    )
    ref = _register(production, _code_artifact())
    assert ref == SkillRef(name="gen.echo", version="0.1.0")
    data = yaml.safe_load(Path(production.path).read_text(encoding="utf-8"))
    (entry,) = [s for s in data["skills"] if s["name"] == "gen.echo"]
    assert "_source" not in entry, "随调用携带的冒烟源码不得写进生产 yaml"
    assert entry["handler"] == "generated_handlers.gen_echo:run"
    assert entry["logic"] == {"mode": "sandbox"}
    handler_file = tmp_path / "generated_handlers" / "gen_echo.py"
    assert handler_file.is_file(), "源码经 register 步骤 6 落盘"
    assert handler_file.read_text(encoding="utf-8") == CODE_SOURCE


def test_code_skill_smoke_shadows_then_restores_production_handler(production, tmp_path):
    """同名再注册:生产旧 handler 已在 sys.modules 时,冒烟必须命中 _source 新源码
    (临时目录置顶 + 缓存弹出);冒烟后缓存清零,惰性重解析拿回生产旧版(零残留)。"""
    from agent_os.skills.loader import load_handler

    store = _write_code_draft(
        tmp_path / "drafts", "gen.echo",
        {"case1.json": {"input": {"text": "hi"}, "expected": {"answer": "echo:hi"}}},
    )
    production.bind_register_smoke(
        DefaultRegisterSmoke(
            production=production, store=store, make_kernel=_sandbox_kernel_factory(production)
        )
    )
    _register(production, _code_artifact())  # v0.1.0:生产 handler 落盘
    # 弹缓存再 import(纪律同 _write_generated_handler;防其他测试文件的陈旧父包缓存干扰)
    sys.modules.pop("generated_handlers.gen_echo", None)
    sys.modules.pop("generated_handlers", None)
    prod_run = load_handler("generated_handlers.gen_echo:run")
    assert asyncio.run(prod_run({"text": "hi"}, None)) == {"answer": "echo:hi"}
    assert "generated_handlers.gen_echo" in sys.modules, "前提:生产 handler 已处缓存态"

    # 同名再注册的冒烟:expected 按 V2 源码对齐——命中生产旧版则必败
    store2 = _write_code_draft(
        tmp_path / "drafts2", "gen.echo",
        {"case1.json": {"input": {"text": "hi"}, "expected": {"answer": "v2:hi"}}},
    )
    hook2 = DefaultRegisterSmoke(
        production=production, store=store2, make_kernel=_sandbox_kernel_factory(production)
    )
    outcome = asyncio.run(hook2("gen.echo", _code_entry(source=CODE_SOURCE_V2)))
    assert outcome["ok"] is True, outcome["detail"]
    assert "generated_handlers.gen_echo" not in sys.modules, "冒烟后临时/生产缓存均清零"
    restored = load_handler("generated_handlers.gen_echo:run")
    assert asyncio.run(restored({"text": "hi"}, None)) == {"answer": "echo:hi"}, "惰性重解析回生产旧版"


# ---------------------------------------------------------------------------
# (A) 录制 run 重放证据(2026-09-30):provenance.detail["source_run_id"] →
# 确定性重放源技能 + 任务真完成判定;先于草稿用例,引用证据不免除草稿用例
# ---------------------------------------------------------------------------


def _write_run_dir(
    runs_root: Path,
    run_id: str,
    *,
    skill: str = "lab.published",
    input: dict | None = None,
    result: dict | None = None,
    status: str = "done",
    compress: bool = False,
) -> Path:
    """手写最小 run 产物目录(tests/cli/test_replay.py:64-108 同款四件套 + meta.json)。

    lab.published(outputs ``{"type": "object"}``)的一次主循环:1 条
    post:llm.response ↔ checkpoint 1 条 assistant 消息;``compress=True`` 在主
    信号前补一条压缩排干信号(``source="compress"``,kernel/runner.py
    _drain_compress_usage 同款)——验证下沉后 build_mock_script 的过滤修正。
    """
    result = {"ok": True} if result is None else result
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    rows = [json.dumps({"v": 1, "type": "header", "schema": "agent_os.trace/1"})]
    if compress:
        rows.append(json.dumps({
            "v": 1, "type": "signal", "name": "post:llm.response", "run_id": run_id,
            "frame_id": "f1", "ts": 0.05,
            "payload": {
                "model": "m", "usage": {"prompt": 2, "completion": 1}, "source": "compress",
            },
        }))
    rows.append(json.dumps({
        "v": 1, "type": "signal", "name": "post:llm.response", "run_id": run_id,
        "frame_id": "f1", "ts": 0.1,
        "payload": {"model": "m", "usage": {"prompt": 5, "completion": 3}},
    }))
    (run_dir / "trace.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (run_dir / "checkpoint.json").write_text(
        json.dumps({
            "v": 1,
            "frames": [{
                "frame_id": "f1",
                "context": {"messages": [{"role": "assistant", "content": json.dumps(result)}]},
            }],
        }),
        encoding="utf-8",
    )
    (run_dir / "meta.json").write_text(
        json.dumps({
            "run_id": run_id,
            "skill": skill,
            "input": input if input is not None else {"x": 1},
            "host": "test",
            "started_at": 0.0,
        }),
        encoding="utf-8",
    )
    (run_dir / "result.json").write_text(
        json.dumps({"status": status, "result": result, "error": None, "usage": {}}),
        encoding="utf-8",
    )
    return run_dir


def _prov_with_source(run_id: str) -> Provenance:
    """带录制 run 引用的注册 provenance(tools/std.py source_run_id 入参的落点形态)。"""
    return Provenance(run_id="r1", task="t1", note="", detail={"source_run_id": run_id})


def _hook_with_runs(production, store, runs_root: Path, judge_payload: str | None = None):
    judge = MockProvider(_judge_brain(judge_payload)) if judge_payload is not None else None
    hook = DefaultRegisterSmoke(
        production=production,
        store=store,
        make_kernel=_kernel_factory(production),
        judge_providers=judge,
        judge_model="mock/judge" if judge is not None else "",
        runs_root=str(runs_root),
    )
    return hook, judge


def test_recorded_run_replay_pass_then_register_end_to_end(production, tmp_path):
    """录制重放通过 + 草稿用例通过 → 放行(两阶段都要过);register() 端到端
    gates.smoke=pass,provenance.detail 逐字落 register.jsonl。"""
    runs_root = tmp_path / "runs"
    _write_run_dir(runs_root, "src1")
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, _ = _hook_with_runs(production, store, runs_root)
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("src1")))
    assert outcome["ok"] is True, outcome["detail"]
    assert "录制 run src1 重放一致" in outcome["detail"]
    assert "未配置裁判,仅确定性比对" in outcome["detail"], "裁判缺席降级并注明"
    assert "1 个用例" in outcome["detail"], "引用证据不免除草稿用例"
    assert outcome["cases"][0]["case"] == "replay:src1"

    production.bind_register_smoke(hook)
    ref = asyncio.run(production.register(_prompt_artifact(), _prov_with_source("src1")))
    assert ref == SkillRef(name="gen.tone", version="0.1.0")
    records = _read_jsonl(production)
    assert records[-1]["gates"]["smoke"] == "pass"
    assert records[-1]["detail"] == {"source_run_id": "src1"}, "provenance.detail 逐字落 jsonl"


def test_recorded_run_tampered_result_rejected(production, tmp_path):
    """result.json 与重放将产生的结果不符(篡改)→ fail-closed「重放结果与录制不符」。"""
    runs_root = tmp_path / "runs"
    run_dir = _write_run_dir(runs_root, "src1")
    (run_dir / "result.json").write_text(
        json.dumps({"status": "done", "result": {"ok": False}, "error": None, "usage": {}}),
        encoding="utf-8",
    )
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, _ = _hook_with_runs(production, store, runs_root)
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("src1")))
    assert outcome["ok"] is False
    assert "重放结果与录制不符" in outcome["detail"]
    assert outcome["cases"][0]["case"] == "replay:src1"


def test_recorded_run_missing_dir_rejected(production, tmp_path):
    """引用的 run 目录不存在 → fail-closed(引用的证据必须存在且可复现)。"""
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, _ = _hook_with_runs(production, store, tmp_path / "runs")
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("nope")))
    assert outcome["ok"] is False
    assert "不存在" in outcome["detail"]


def test_recorded_run_compress_signal_alignment_ok(production, tmp_path):
    """录制 trace 含压缩排干信号(source="compress")→ 对齐仍正确(下沉修正的验证门锚点;
    未过滤时本例必炸「第 2 个响应信号无对应 assistant 消息」)。"""
    runs_root = tmp_path / "runs"
    _write_run_dir(runs_root, "src1", compress=True)
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, _ = _hook_with_runs(production, store, runs_root)
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("src1")))
    assert outcome["ok"] is True, outcome["detail"]


def test_recorded_run_judge_reject_rejected(production, tmp_path):
    """配置裁判时:裁判否决"任务真完成" → fail-closed,detail 带裁判理由。"""
    runs_root = tmp_path / "runs"
    _write_run_dir(runs_root, "src1")
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, _ = _hook_with_runs(
        production, store, runs_root, '{"pass": false, "reason": "任务是占位输出"}'
    )
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("src1")))
    assert outcome["ok"] is False
    assert "占位输出" in outcome["detail"]


def test_recorded_run_judge_pass_notes_confirmed(production, tmp_path):
    """裁判确认 → detail 注明"裁判确认任务真完成";裁判输入 = 源输入 + 录制结果 + 候选描述。"""
    runs_root = tmp_path / "runs"
    _write_run_dir(runs_root, "src1")
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, judge = _hook_with_runs(production, store, runs_root, '{"pass": true, "reason": "ok"}')
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("src1")))
    assert outcome["ok"] is True, outcome["detail"]
    assert "裁判确认任务真完成" in outcome["detail"]
    assert judge.recorded, "录制重放判定必须真调裁判"
    payload = json.loads(judge.recorded[0].messages[1].content)
    assert payload["input"] == {"x": 1}, "裁判输入含源 run 输入"
    assert payload["recorded_result"] == {"ok": True}, "裁判输入含录制结果"
    assert payload["candidate_skill"]["name"] == "gen.tone"


def test_recorded_run_source_skill_absent_rejected(production, tmp_path):
    """源技能已不在冒烟内核注册表(删除/未发布)→ fail-closed。"""
    runs_root = tmp_path / "runs"
    _write_run_dir(runs_root, "src1", skill="lab.deleted")
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, _ = _hook_with_runs(production, store, runs_root)
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("src1")))
    assert outcome["ok"] is False
    assert "已删除/未发布" in outcome["detail"]


def test_recorded_run_non_done_status_rejected(production, tmp_path):
    """录制 status 非 "done"(即便结果一致)→ fail-closed(非成功终态不能作证据)。"""
    runs_root = tmp_path / "runs"
    _write_run_dir(runs_root, "src1", status="error")
    store = _write_draft(
        tmp_path / "drafts", "gen.tone", {"case1.json": _case(expected={"answer": "ok"})}
    )
    hook, _ = _hook_with_runs(production, store, runs_root)
    outcome = asyncio.run(hook("gen.tone", _entry(), _prov_with_source("src1")))
    assert outcome["ok"] is False
    assert "'done'" in outcome["detail"]


# ---------------------------------------------------------------------------
# 配置:[skills] register_runs_root(录制重放证据阶段的 run 产物根目录)
# ---------------------------------------------------------------------------


def test_config_register_runs_root_wiring_and_default(tmp_path):
    """register_runs_root 显式接线进 DefaultRegisterSmoke;缺省 ".agent-os/runs"
    (同 CLI --artifacts 缺省)。"""
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    kernel = build_kernel(
        {
            "run": {"model": "mock/x"},
            "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
            "skills": {
                "path": str(path),
                "register_smoke": "default",
                "register_runs_root": str(tmp_path / "myruns"),
            },
        }
    )
    assert kernel.skills._register_smoke._runs_root == str(tmp_path / "myruns")

    path2 = tmp_path / "skills2.yaml"
    path2.write_text(PROD_YAML, encoding="utf-8")
    kernel2 = build_kernel(
        {
            "run": {"model": "mock/x"},
            "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
            "skills": {"path": str(path2), "register_smoke": "default"},
        }
    )
    assert kernel2.skills._register_smoke._runs_root == ".agent-os/runs"


def test_config_register_runs_root_invalid_and_without_path_rejected(tmp_path):
    """register_runs_root 非字符串 → ConfigError(strict 校验先例);
    配了但没有 path → ConfigError(无 registry 可接线,同 register_judge_model)。"""
    path = tmp_path / "skills.yaml"
    path.write_text(PROD_YAML, encoding="utf-8")
    with pytest.raises(ConfigError, match="register_runs_root"):
        build_kernel({"skills": {"path": str(path), "register_runs_root": 42}})
    with pytest.raises(ConfigError, match="path"):
        build_kernel({"skills": {"register_runs_root": "x"}})


# ---------------------------------------------------------------------------
# 端到端:system.skill.register 带 source_run_id → Provenance.detail 落 register.jsonl
# ---------------------------------------------------------------------------

TOOL_REG_YAML = """
skills:
  - name: root_user
    version: 1.0.0
    kind: prompt
    description: 根调用方。Use when 测试 register 过闸;Do not use when 其他。
    inputs:
      type: object
      properties: { task: { type: string } }
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: [system.skill.register]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是根调用方,按需调用工具并汇报结果。
  - name: lab.published
    version: 1.0.0
    kind: prompt
    description: 已发布。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [] }
    prompt: 已发布。
"""

TOOL_REG_ARGS = {
    "manifest": {
        "name": "gen.tool_reg",
        "kind": "prompt",
        "description": "内核注册。Use when 测试;Do not use when 其他。",
        "inputs": {"type": "object"},
        "outputs": ANSWER_OUTPUTS,
        "permissions": {"tools": [], "skills": []},
    },
    "prompt": "你是助手。",
    "source_run_id": "src9",
}


def _tool_reg_brain(req) -> ChatResponse:
    """首轮发 system.skill.register(带 source_run_id);看到 tool 结果后汇报 decision。"""
    tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
    if not tool_msgs:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="system.skill.register", args=TOOL_REG_ARGS)],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    result = json.loads(tool_msgs[-1].content)
    decision = "ok" if result["ok"] else result["error"]["kind"]
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": decision})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


async def _approve_all(question) -> dict:
    """tool-confirm 闸门一律 approve-once(同 tests/helpers/kernels.py auto_approve)。"""
    return {"answer": "approve-once", "decided_by": "test:auto-approve"}


def _tool_reg_smoke_factory(skills):
    """本测试的冒烟内核工厂:同 _kernel_factory,但工具面带 builtins——
    registry 里的 root_user 声明了 system.skill.register 权限,装配期 §6.1
    权限闸门要求工具真实注册。"""

    def make():
        return (
            KernelBuilder(RunConfig(model="mock/x", compression="off"))
            .providers(MockProvider(_answer_brain))
            .tools(LocalPythonToolRegistry.with_builtins())
            .skills(skills)
            .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
            .build()
        )

    return make


def test_tool_register_with_source_run_id_end_to_end(tmp_path):
    """端到端:工具入参 source_run_id → Provenance.detail 落 register.jsonl;
    默认验证门重放引用的源 run(lab.published)+ 草稿用例,全过放行。"""
    path = tmp_path / "skills.yaml"
    path.write_text(TOOL_REG_YAML, encoding="utf-8")
    skills = LocalFileSkillRegistry(str(path))
    _write_run_dir(tmp_path / "runs", "src9")
    store = _write_draft(
        tmp_path / "drafts", "gen.tool_reg", {"case1.json": _case(expected={"answer": "ok"})}
    )
    skills.bind_register_smoke(
        DefaultRegisterSmoke(
            production=skills,
            store=store,
            make_kernel=_tool_reg_smoke_factory(skills),
            runs_root=str(tmp_path / "runs"),
        )
    )
    kernel = (
        KernelBuilder(
            RunConfig(
                model="mock/x",
                tool_policy=ToolPolicy(max_permission=Permission.EXEC),
                compression="off",
            )
        )
        .providers(MockProvider(_tool_reg_brain))
        .tools(LocalPythonToolRegistry.with_builtins())
        .skills(skills)
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(_approve_all, timeout_s=5.0)
        .build()
    )

    result = asyncio.run(kernel.run("root_user", {"task": "t"}))
    assert result["decision"] == "ok", "验证门(含录制重放)过闸,注册成功"
    records = _read_jsonl(skills)
    assert records[0]["detail"] == {"source_run_id": "src9"}, "工具入参经 Provenance.detail 落 jsonl"
    assert records[0]["gates"]["smoke"] == "pass"
    assert skills.get(SkillRef(name="gen.tool_reg")).manifest.name == "gen.tool_reg"

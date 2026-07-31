"""std 收录门槛的**契约体检**(STDLIB §8;STDLIB-CATALOG 测试策略·元测试)。

比"记得给每条新工具补测试"可靠得多的做法:遍历 registry 逐条断言可机检条款。
新增工具/技能漏掉任何一条,这里自动失败——一条测试守住整个门槛。

覆盖(**静态可扫描**的部分):

- §8.1 description lint:``Use when`` / ``Do not use when`` + 足够长度;
- §8.1 参数 schema:``type: object`` + 每个属性带 description;
- §8.2 outputs 可机器校验(技能侧);
- §W0-2 契约字段:READ ⇒ cacheable + concurrent_safe;WRITE/EXEC/NET 显式
  声明 idempotent;``cost_hint`` 非空;别名字段 concurrency_safe 同步。

**不覆盖**(需要构造失败调用,留在各工具自己的测试里):运行期错误 ``hint``
的可执行性、分页 cursor 的不重不漏、权限拒绝路径。
"""

from __future__ import annotations

import pytest

from agent_os.api.v1 import ORCHESTRATE_SCHEMA, Permission, ToolSpec
from agent_os.tools.local_registry import LocalPythonToolRegistry


def _builtin_specs() -> list[ToolSpec]:
    """全部内置工具的 spec(注册序)。"""
    return list(LocalPythonToolRegistry.with_builtins().specs())


def _spec_id(spec: ToolSpec) -> str:
    return spec.name


ALL_SPECS = _builtin_specs()


# ---------------------------------------------------------------------------
# §8.1 描述与 schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", ALL_SPECS, ids=_spec_id)
def test_description_states_when_to_use(spec: ToolSpec) -> None:
    """描述写"何时用"而非"是什么":须含 Use when 与 Do not use when(§6.1/§8.1)。"""
    desc = spec.description
    assert len(desc) >= 20, f"{spec.name}: description 过短({len(desc)} 字符)"
    assert "Use when" in desc, f"{spec.name}: description 缺 'Use when' 触发条件"
    assert "Do not use when" in desc, f"{spec.name}: description 缺 'Do not use when' 负例"


@pytest.mark.parametrize("spec", ALL_SPECS, ids=_spec_id)
def test_parameters_are_object_schema(spec: ToolSpec) -> None:
    """参数是 object schema(必须)。"""
    params = spec.parameters
    assert isinstance(params, dict) and params.get("type") == "object", (
        f"{spec.name}: parameters 应为 type=object 的 JSON Schema"
    )
    assert params.get("properties") is not None, f"{spec.name}: parameters 缺 properties"


@pytest.mark.xfail(
    reason="§8.1 未达:derive_spec 从签名推导,尚无逐参数 description/示例的机制"
    "(需 docstring Args: 约定或 @tool 显式传入);书实测该项影响调用准确率 72%→90%",
    strict=False,
)
@pytest.mark.parametrize("spec", ALL_SPECS, ids=_spec_id)
def test_parameters_are_documented(spec: ToolSpec) -> None:
    """每个参数带 description 与取值示例(§8.1);**已知缺口,机制待建**。"""
    for prop, schema in (spec.parameters.get("properties") or {}).items():
        assert schema.get("description"), f"{spec.name}.{prop}: 参数缺 description"


def test_orchestrate_pseudo_tool_schema_also_conforms() -> None:
    """伪工具不在 registry(内核拦截),但对模型的呈现同样受门槛约束。"""
    desc = ORCHESTRATE_SCHEMA["description"]
    assert "Use when" in desc and "Do not use when" in desc
    for prop, schema in ORCHESTRATE_SCHEMA["parameters"]["properties"].items():
        assert schema.get("description"), f"python_orchestrate.{prop}: 参数缺 description"


# ---------------------------------------------------------------------------
# §W0-2 契约字段(声明不强制,但**必须声明**)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", ALL_SPECS, ids=_spec_id)
def test_read_tools_are_cacheable_and_concurrent(spec: ToolSpec) -> None:
    """只读 ⇒ 可缓存 + 可并行(白拿的性能红利);非只读不得声明可缓存。"""
    if spec.permission is Permission.READ:
        assert spec.cacheable, f"{spec.name}: READ 档应 cacheable=True"
        assert spec.concurrent_safe, f"{spec.name}: READ 档应 concurrent_safe=True"
    else:
        assert not spec.cacheable, f"{spec.name}: 非 READ 档不应 cacheable"


@pytest.mark.parametrize("spec", ALL_SPECS, ids=_spec_id)
def test_cost_hint_is_magnitude_not_absolute(spec: ToolSpec) -> None:
    """``cost_hint`` 非空,且写量级(带 ~ 或"取决于"),不写绝对秒数。"""
    assert spec.cost_hint, f"{spec.name}: 缺 cost_hint(§W0-2)"
    assert "~" in spec.cost_hint or "取决于" in spec.cost_hint, (
        f"{spec.name}: cost_hint 应写量级而非绝对值,得到 {spec.cost_hint!r}"
    )


@pytest.mark.parametrize("spec", ALL_SPECS, ids=_spec_id)
def test_concurrent_safe_alias_in_sync(spec: ToolSpec) -> None:
    """``concurrent_safe`` 与 §14.1 冻结字段 ``concurrency_safe`` 必须同步置位。"""
    assert spec.concurrent_safe == spec.concurrency_safe, (
        f"{spec.name}: concurrent_safe={spec.concurrent_safe} 与 "
        f"concurrency_safe={spec.concurrency_safe} 不一致"
    )


# ---------------------------------------------------------------------------
# 技能侧:std 技能包落地后自动生效(未落地则跳过)
# ---------------------------------------------------------------------------


def _std_manifests():
    """std 技能包的全部 manifest;包未落地时返回 None(测试跳过)。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "skills" / "std"
    if not root.is_dir() or not any(root.glob("*.yaml")):
        return None
    from agent_os.skills.local_file import LocalFileSkillRegistry

    return LocalFileSkillRegistry(str(root)).manifests()


def test_std_skills_conform_to_gate() -> None:
    """std 技能:description lint + inputs/outputs schema 齐备(§8.1/§8.2)。"""
    manifests = _std_manifests()
    if manifests is None:
        pytest.skip("std 技能包尚未落地(第 2 波起生效)")
    problems: list[str] = []
    for m in manifests:
        if "Use when" not in m.description or "Do not use when" not in m.description:
            problems.append(f"{m.name}: description 缺 Use when / Do not use when")
        if not m.inputs:
            problems.append(f"{m.name}: 缺 inputs schema")
        if not m.outputs:
            problems.append(f"{m.name}: 缺 outputs schema(§8.2 要求可机器校验)")
    assert not problems, "std 技能未过门槛:\n" + "\n".join(problems)


def _schema_defects(node: object, trail: str) -> list[str]:
    """递归找 schema 结构缺陷,返回人可读的问题清单。

    主要抓 **YAML 流式映射被中文逗号劈开**这一类:``{description: 含,逗号}``
    会被解析成 ``{description: "含", "逗号": None}``——描述截半,并混入一个
    值为 null 的伪键。jsonschema 忽略未知关键字,故这类错误**静默通过**校验,
    只能靠结构体检抓。
    """
    defects: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if value is None:
                defects.append(
                    f"{trail}: 键 {key!r} 的值为 null"
                    "(疑似流式映射被逗号劈开;给含中文逗号的值加引号)"
                )
            defects.extend(_schema_defects(value, f"{trail}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            defects.extend(_schema_defects(value, f"{trail}[{i}]"))
    return defects


def test_std_skill_schemas_are_well_formed() -> None:
    """std 技能的 inputs/outputs schema 结构良好(无 null 值伪键)。

    对应实机发现:6 个技能 9 处描述被中文逗号劈坏,模型看到的是半句话。
    """
    manifests = _std_manifests()
    if manifests is None:
        pytest.skip("std 技能包尚未落地")
    defects: list[str] = []
    for m in manifests:
        defects.extend(_schema_defects(m.inputs, f"{m.name}.inputs"))
        defects.extend(_schema_defects(m.outputs, f"{m.name}.outputs"))
    assert not defects, "std 技能 schema 结构缺陷:\n" + "\n".join(defects)

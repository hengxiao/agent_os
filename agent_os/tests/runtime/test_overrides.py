"""K1 run 覆盖选项注册表锚点测试(docs/RUNNERS.md §2.5)。

固定约定:

- 注册表自检:每个 spec.field ∈ config.py ``_RUN_FIELDS``(P3 fail-closed);
  flag = toml 键 kebab-case(P5);``web_fields()`` 只含 web=True 字段(P6);
- 优先级链(P4):flag > env(``AGENT_OS_MODEL``)> toml > 默认;
- ``apply_overrides`` 只动 ``cfg["run"]`` 的副本(D3 锚点:共享 cfg 不被污染,
  空 overrides 原样返回)。
"""

from __future__ import annotations

import pytest

from agent_os.runtime.config import _RUN_FIELDS
from agent_os.runtime.overrides import (
    OVERRIDE_SPECS,
    OverrideError,
    apply_overrides,
    cli_flag,
    resolve_effective,
    resolve_provenance,
    validate_overrides,
    web_fields,
)


def test_specs_fields_subset_of_run_fields():
    """注册表自检(P3):每个 spec.field 都是 [run] 权威字段(导入期断言的运行时复述)。"""
    assert OVERRIDE_SPECS, "注册表不应为空"
    for spec in OVERRIDE_SPECS:
        assert spec.field in _RUN_FIELDS, spec.field


def test_cli_flags_kebab_case_convention():
    """命名约定(P5):flag = toml 键 kebab-case(max_steps → --max-steps)。"""
    for spec in OVERRIDE_SPECS:
        flag = cli_flag(spec)
        assert flag is not None, f"{spec.field} 首批全部对 CLI 开放"
        assert flag == "--" + spec.field.replace("_", "-")


def test_web_fields_derivation_excludes_blast_radius_fields():
    """安全分级(P6):workdir/read_paths 为 web=False,不进 web 白名单。"""
    assert web_fields() == ("model", "max_cost", "max_steps", "inline", "checkpoint_interval")
    assert "workdir" not in web_fields()
    assert "read_paths" not in web_fields()


def test_apply_overrides_empty_returns_same_object():
    """空 overrides → 原样返回 cfg(D3:零拷贝零改动)。"""
    cfg = {"run": {"model": "mock/toml"}}
    assert apply_overrides(cfg, {}) is cfg


def test_apply_overrides_does_not_mutate_shared_cfg():
    """D3 锚点语义:合并只动返回 dict 的 run 段副本,共享 cfg 原样不动。"""
    cfg = {"run": {"model": "mock/toml"}, "providers": {"mock": {"brain": "x:y"}}}
    merged = apply_overrides(cfg, {"max_steps": 5})
    assert merged is not cfg
    assert merged["run"]["max_steps"] == 5
    assert merged["run"]["model"] == "mock/toml"
    assert cfg == {"run": {"model": "mock/toml"}, "providers": {"mock": {"brain": "x:y"}}}
    assert merged["providers"] is cfg["providers"], "其余段共享引用(同宿主私有副本先例)"


def test_apply_overrides_unknown_field_rejected():
    """fail-closed(P3):未注册字段 OverrideError。"""
    with pytest.raises(OverrideError, match="未注册/未开放"):
        apply_overrides({"run": {}}, {"bogus": 1})


def test_apply_overrides_allowed_scope_rejects_web_false_fields():
    """本面开放集(P6):web scope 下 workdir/read_paths 被拒(虽已注册)。"""
    with pytest.raises(OverrideError, match="workdir"):
        apply_overrides({"run": {}}, {"workdir": "/tmp"}, allowed=web_fields())


def test_validate_overrides_coercion_and_choices():
    """类型 coercion 与枚举校验:max_steps "5" → 5;inline 非 on|off → OverrideError。"""
    assert validate_overrides({"max_steps": "5"}) == {"max_steps": 5}
    assert validate_overrides({"max_cost": 1}) == {"max_cost": 1.0}
    with pytest.raises(OverrideError, match="on\\|off"):
        validate_overrides({"inline": "maybe"})
    with pytest.raises(OverrideError, match="int"):
        validate_overrides({"max_steps": 2.5})


def test_validate_workdir_and_read_paths(tmp_path):
    """路径校验:workdir 须已存在且是目录;read_paths 每个路径须存在。"""
    with pytest.raises(OverrideError, match="路径不存在或不是目录"):
        validate_overrides({"workdir": str(tmp_path / "nope")})
    file = tmp_path / "f.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(OverrideError, match="路径不存在或不是目录"):
        validate_overrides({"workdir": str(file)})  # 是文件不是目录
    assert validate_overrides({"workdir": str(tmp_path)}) == {"workdir": str(tmp_path)}
    with pytest.raises(OverrideError, match="路径不存在"):
        validate_overrides({"read_paths": [str(tmp_path), str(tmp_path / "nope")]})
    assert validate_overrides({"read_paths": [str(tmp_path)]}) == {"read_paths": [str(tmp_path)]}


def test_priority_chain_flag_env_toml_default(monkeypatch):
    """优先级链(P4):flag > env(AGENT_OS_MODEL)> toml > 默认。"""
    cfg = {"run": {"model": "mock/toml"}}
    monkeypatch.setenv("AGENT_OS_MODEL", "mock/env")
    # flag > env
    assert resolve_effective({"model": "mock/flag"})["model"] == "mock/flag"
    # env > toml:env 补全后进合并
    effective = resolve_effective({})
    assert effective["model"] == "mock/env"
    assert apply_overrides(cfg, effective)["run"]["model"] == "mock/env"
    # toml > 默认:无 flag 无 env → 空覆盖集,toml 值原样保留
    monkeypatch.delenv("AGENT_OS_MODEL")
    assert resolve_effective({}) == {}
    assert apply_overrides(cfg, {})["run"]["model"] == "mock/toml"
    # 默认:toml 亦无 → run 段无该键(落 RunConfig 默认)
    assert "model" not in apply_overrides({"run": {}}, {})["run"]


def test_resolve_provenance_sources(monkeypatch):
    """provenance(P4 审计面):flag > env > toml;三者皆无的字段不出现。"""
    cfg_run = {"model": "mock/toml", "max_steps": 100}
    monkeypatch.setenv("AGENT_OS_MODEL", "mock/env")
    assert resolve_provenance(cfg_run, {"model": "mock/flag"})["model"] == "flag"
    assert resolve_provenance(cfg_run, {})["model"] == "env"
    monkeypatch.delenv("AGENT_OS_MODEL")
    prov = resolve_provenance(cfg_run, {})
    assert prov == {"model": "toml", "max_steps": "toml"}
    assert resolve_provenance({}, {}) == {}


def test_env_invalid_value_rejected(monkeypatch):
    """env 别名值走同一校验管线(P3):非法值 OverrideError 且消息带变量名、不回显值。"""
    monkeypatch.setenv("AGENT_OS_MODEL", "")
    with pytest.raises(OverrideError, match="AGENT_OS_MODEL"):
        resolve_effective({})

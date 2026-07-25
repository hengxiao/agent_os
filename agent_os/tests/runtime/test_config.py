"""配置装配锚点测试(RUNNERS.md §2.1;runtime/config.py 的错误归类与扩展点)。

固定约定:

- 配置缺失/畸形/未知字段/装配失败 → ``ConfigError``(宿主归退出码 4);
- ``[tools] python_exec`` 启用即把 RunConfig 权限上限提到 EXEC(§8.2 配置即授权);
- ``[tools.custom] module = "pkg.mod:func"``:importlib 加载后调 ``func(registry)``,
  失败抛 ``ConfigError``;声明即授权,权限上限同步提到 EXEC;
- ``load_skillsets(root)``:只收 ``<root>/<set>/skills.yaml`` 存在的子目录,按名排序。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_os.api.v1 import Permission
from agent_os.runtime.config import (
    ConfigError,
    build_kernel,
    load_config,
    load_skillsets,
)
from tests.helpers.kernels import FIB_SKILLS_YAML


def _base_cfg(**overrides):
    cfg = {
        "run": {"model": "mock/fib", "compression": "off"},
        "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
        "tools": {"python_exec": "subprocess"},
        "skills": {"path": str(FIB_SKILLS_YAML)},
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# load_config:文件层错误
# ---------------------------------------------------------------------------


def test_missing_config_file_rejected(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.toml")


def test_malformed_toml_rejected(tmp_path):
    bad = tmp_path / "agent-os.toml"
    bad.write_text("[run\nmodel = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="畸形"):
        load_config(bad)


# ---------------------------------------------------------------------------
# 未知字段/枚举的硬拒绝
# ---------------------------------------------------------------------------


def test_unknown_run_field_rejected():
    with pytest.raises(ConfigError, match="未知字段"):
        build_kernel(_base_cfg(run={"model": "m", "max_depht": 3}))


def test_unknown_provider_rejected():
    with pytest.raises(ConfigError, match="未知 provider"):
        build_kernel(_base_cfg(providers={"gemini": {}}))


def test_unknown_sidecar_rejected():
    with pytest.raises(ConfigError, match="未知 sidecar"):
        build_kernel(_base_cfg(sidecars={"watchdog": {}}))


def test_bad_tool_guard_rule_shape_rejected():
    with pytest.raises(ConfigError, match="tool_guard_rules"):
        build_kernel(_base_cfg(sidecars={"tool_guard_rules": [["shell_exec", "rm"]]}))


def test_unknown_python_exec_backend_rejected():
    with pytest.raises(ConfigError, match="python_exec"):
        build_kernel(_base_cfg(tools={"python_exec": "wasm"}))


def test_bad_mock_brain_dotted_path_rejected():
    with pytest.raises(ConfigError, match="dotted path"):
        build_kernel(_base_cfg(providers={"mock": {"brain": "not-a-path"}}))


def test_unimportable_mock_brain_rejected():
    with pytest.raises(ConfigError, match="无法加载"):
        build_kernel(_base_cfg(providers={"mock": {"brain": "tests.helpers.nope:fn"}}))


def test_non_callable_dotted_path_rejected():
    with pytest.raises(ConfigError, match="不可调用"):
        build_kernel(
            _base_cfg(providers={"mock": {"brain": "tests.helpers.custom_tools:NOT_CALLABLE"}})
        )


# ---------------------------------------------------------------------------
# 权限上限的"配置即授权"(§8.2)
# ---------------------------------------------------------------------------


def test_python_exec_elevates_permission_to_exec():
    kernel = build_kernel(_base_cfg())
    assert kernel.config.tool_policy.max_permission is Permission.EXEC
    result = asyncio.run(kernel.run("fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}


def test_python_exec_off_keeps_default_permission():
    # fib skills.yaml 声明 python_exec,关掉后装配期权限闸门会拒绝,故不接技能
    cfg = _base_cfg(tools={"python_exec": "off"})
    del cfg["skills"]
    kernel = build_kernel(cfg)
    assert kernel.config.tool_policy.max_permission < Permission.EXEC


# ---------------------------------------------------------------------------
# [tools.custom] 注册钩子
# ---------------------------------------------------------------------------


def test_custom_tools_hook_registers_and_elevates():
    cfg = _base_cfg(
        tools={"python_exec": "off", "custom": {"module": "tests.helpers.custom_tools:register"}}
    )
    del cfg["skills"]  # 同上:fib 清单声明 python_exec,off 档不接技能
    kernel = build_kernel(cfg)
    assert kernel.tools.has("echo_text")
    assert kernel.config.tool_policy.max_permission is Permission.EXEC


def test_custom_tools_hook_failure_is_config_error():
    cfg = _base_cfg(
        tools={"custom": {"module": "tests.helpers.custom_tools:bad_register"}}
    )
    with pytest.raises(ConfigError, match="注册钩子执行失败"):
        build_kernel(cfg)


# ---------------------------------------------------------------------------
# load_skillsets
# ---------------------------------------------------------------------------


def test_load_skillsets_scans_only_valid_sets(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "skills.yaml").write_text("skills: []", encoding="utf-8")
    (tmp_path / "beta").mkdir()  # 无 skills.yaml,不收
    (tmp_path / "zeta").mkdir()
    (tmp_path / "zeta" / "skills.yaml").write_text("skills: []", encoding="utf-8")
    (tmp_path / "loose.txt").write_text("x", encoding="utf-8")  # 非目录,不收

    sets = load_skillsets(tmp_path)
    assert list(sets) == ["alpha", "zeta"]
    assert sets["alpha"] == tmp_path / "alpha"


def test_load_skillsets_missing_root_rejected(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_skillsets(tmp_path / "nope")

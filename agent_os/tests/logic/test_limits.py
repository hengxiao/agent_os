"""merge_limits 锚点测试(docs/DESIGN.md §9.1;M5):三级限额逐字段取最严。

固定约定:``None`` = 该级未设(不约束,取另一级);两级都设取更小值;
RunConfig / manifest / 调用方三级取紧即链式调用;返回新实例,不改入参。
"""

from __future__ import annotations

from agent_os.api.v1 import ResourceLimits
from agent_os.logic.limits import merge_limits


def test_none_means_unset_takes_other_level():
    base = ResourceLimits(wall_time=10, cpu_time=None, memory_mb=256, stdout_bytes=None)
    override = ResourceLimits(wall_time=None, cpu_time=5, memory_mb=None, stdout_bytes=1000)
    merged = merge_limits(base, override)
    assert merged.wall_time == 10
    assert merged.cpu_time == 5
    assert merged.memory_mb == 256
    assert merged.stdout_bytes == 1000


def test_both_set_takes_min_fieldwise():
    base = ResourceLimits(wall_time=10, cpu_time=8, memory_mb=256, stdout_bytes=2000)
    override = ResourceLimits(wall_time=30, cpu_time=5, memory_mb=128, stdout_bytes=3000)
    merged = merge_limits(base, override)
    # 取紧 = 逐字段取更小(更严),不是 override 整体覆盖
    assert merged.wall_time == 10
    assert merged.cpu_time == 5
    assert merged.memory_mb == 128
    assert merged.stdout_bytes == 2000


def test_three_level_chain_tightest_wins():
    """RunConfig / manifest / 调用方三级取紧矩阵(链式调用)。"""
    run_cfg = ResourceLimits(wall_time=60, cpu_time=30, memory_mb=512, stdout_bytes=None)
    manifest = ResourceLimits(wall_time=45, cpu_time=None, memory_mb=1024, stdout_bytes=5000)
    caller = ResourceLimits(wall_time=None, cpu_time=10, memory_mb=256, stdout_bytes=8000)
    merged = merge_limits(merge_limits(run_cfg, manifest), caller)
    assert merged.wall_time == 45  # manifest 最严
    assert merged.cpu_time == 10  # 调用方最严
    assert merged.memory_mb == 256  # 调用方最严
    assert merged.stdout_bytes == 5000  # 只有 manifest 设了


def test_all_none_yields_all_none():
    merged = merge_limits(ResourceLimits(), ResourceLimits())
    assert merged.wall_time is None
    assert merged.cpu_time is None
    assert merged.memory_mb is None
    assert merged.stdout_bytes is None


def test_inputs_not_mutated():
    base = ResourceLimits(wall_time=10, memory_mb=256)
    override = ResourceLimits(wall_time=5, memory_mb=128)
    merged = merge_limits(base, override)
    assert merged is not base and merged is not override
    assert base.wall_time == 10 and base.memory_mb == 256, "入参不得被改"
    assert override.wall_time == 5 and override.memory_mb == 128


def test_exec_limits_defaults_fill_when_nothing_set():
    """内核默认是回填而非上限:manifest 与调用方都未设时回 DEFAULT_EXEC_LIMITS。"""
    from agent_os.logic.limits import DEFAULT_EXEC_LIMITS, exec_limits

    limits = exec_limits()
    assert limits == DEFAULT_EXEC_LIMITS


def test_exec_limits_caller_cannot_loosen_manifest_ceiling():
    """§9.1 三级取紧:调用方 timeout 不能放松 manifest 声明的上限。"""
    from agent_os.logic.limits import exec_limits

    limits = exec_limits(manifest_timeout=30, caller_timeout=300)
    assert limits.wall_time == 30 and limits.cpu_time == 30
    # 未设字段回填默认
    assert limits.memory_mb == 256 and limits.stdout_bytes == 100_000


def test_exec_limits_caller_tightens_and_manifest_applies_without_caller():
    from agent_os.logic.limits import exec_limits

    assert exec_limits(manifest_timeout=120, caller_timeout=10).wall_time == 10
    # 编排路径此前完全忽略 manifest timeout(恒 60);接线后声明生效
    assert exec_limits(manifest_timeout=120).wall_time == 120

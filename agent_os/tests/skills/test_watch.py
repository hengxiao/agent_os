"""LocalFileSkillRegistry 轮询热重载看门狗 + ``[skills]`` watch_interval 配置接线测试。

固定约定:

- ``start_watching(interval)`` 起 daemon 线程:修改/新增源 yaml 后无需手动 reload,
  轮询等待(上限 5s)内新版自动生效;
- 改出坏 yaml → reload 抛 SkillLoadError 被吞掉记 log:旧表不动、看门狗不死;
- 默认不 watch:构造后无看门狗线程;
- ``stop_watching`` 幂等,停后改动不再自动生效;``start_watching`` 幂等,
  ``interval_s <= 0`` → ValueError;
- ``[skills] watch_interval``:合法值装配后确实在 watch;非法值/无 path → ConfigError;
  缺省不 watch(零破坏回归)。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from agent_os.kernel.errors import SkillLoadError
from agent_os.runtime.config import ConfigError, build_kernel
from agent_os.skills.local_file import LocalFileSkillRegistry

ALPHA_V1 = """
skills:
  - name: alpha.one
    version: 1.0.0
    kind: prompt
    description: 甲技能。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [] }
    prompt: 第一版。
"""

BETA = """
skills:
  - name: beta.two
    version: 1.0.0
    kind: prompt
    description: 乙技能。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [] }
    prompt: 乙。
"""

# 悬空子技能依赖 → _load_all 抛 SkillLoadError(watcher 的 reload 失败路径)
BROKEN = """
skills:
  - name: alpha.one
    version: 1.0.0
    kind: prompt
    description: 坏技能。Use when x;Do not use when y。
    inputs: { type: object }
    outputs: { type: object }
    permissions: { tools: [], skills: [no.such.dep] }
    prompt: 坏。
"""


def _rewrite(path: Path, content: str) -> None:
    """写入并强制 mtime 严格前进(防粗粒度文件系统同 mtime 漏检)。"""
    prev = path.stat().st_mtime if path.exists() else time.time()
    path.write_text(content, encoding="utf-8")
    os.utime(path, (prev + 1.0, prev + 1.0))


def _wait_for(pred, timeout: float = 5.0, interval: float = 0.02) -> bool:
    """轮询断言辅助:timeout 内 pred 成真 → True。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _has(registry: LocalFileSkillRegistry, name: str) -> bool:
    try:
        registry.get_by_name(name)
        return True
    except SkillLoadError:
        return False


@pytest.fixture()
def dir_registry(tmp_path):
    """目录形态 registry(一个 a.yaml)+ skills 目录;测试后确保停看。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "a.yaml").write_text(ALPHA_V1, encoding="utf-8")
    registry = LocalFileSkillRegistry(str(skills_dir))
    yield registry, skills_dir
    registry.stop_watching()


# ---------------------------------------------------------------------------
# 看门狗行为
# ---------------------------------------------------------------------------


def test_watcher_picks_up_modify_and_add(dir_registry):
    """interval 0.1 启动后:修改既有 yaml 与新增 yaml 都在 5s 内自动生效(未手动 reload)。"""
    registry, skills_dir = dir_registry
    registry.start_watching(0.1)
    assert registry._watch_thread is not None and registry._watch_thread.is_alive()
    assert registry._watch_thread.daemon, "daemon 线程不阻进程退出"

    _rewrite(skills_dir / "a.yaml", ALPHA_V1.replace("第一版", "第二版"))
    assert _wait_for(lambda: "第二版" in (registry.get_by_name("alpha.one").prompt or "")), (
        "看门狗在 5s 内自动 reload 出修改后的新版"
    )

    _rewrite(skills_dir / "b.yaml", BETA)
    assert _wait_for(lambda: _has(registry, "beta.two")), "新增 yaml 文件同样触发自动 reload"


def test_default_no_watching(dir_registry):
    """默认不 watch:构造后无看门狗线程(属性 + 进程线程表双重断言)。"""
    registry, _ = dir_registry
    assert registry._watch_thread is None
    assert not any(t.name == "agent-os-skills-watch" for t in threading.enumerate())


def test_watcher_bad_yaml_keeps_old_table(dir_registry):
    """改出坏 yaml → 等两个间隔以上:旧表仍在(get 老技能正常)、看门狗不死在角落。"""
    registry, skills_dir = dir_registry
    registry.start_watching(0.1)
    _rewrite(skills_dir / "a.yaml", BROKEN)
    time.sleep(0.35)  # ≥ 3 个轮询间隔
    skill = registry.get_by_name("alpha.one")
    assert "第一版" in (skill.prompt or ""), "reload 失败保留旧表(与手动 reload 语义对齐)"
    assert registry._watch_thread.is_alive(), "SkillLoadError 被吞掉,看门狗不死"


def test_start_watching_validates_interval_and_idempotent(dir_registry):
    """interval_s <= 0 → ValueError;重复 start 幂等(已在看则忽略,不换间隔不重启)。"""
    registry, _ = dir_registry
    with pytest.raises(ValueError, match="间隔"):
        registry.start_watching(0)
    with pytest.raises(ValueError, match="间隔"):
        registry.start_watching(-1)
    registry.start_watching(0.2)
    first = registry._watch_thread
    registry.start_watching(0.05)  # 已在看:忽略
    assert registry._watch_thread is first
    registry.stop_watching()
    assert registry._watch_thread is None and not first.is_alive()


def test_stop_watching_freezes_table(dir_registry):
    """stop_watching(幂等)后改动不再自动生效。"""
    registry, skills_dir = dir_registry
    registry.start_watching(0.1)
    registry.stop_watching()
    registry.stop_watching()  # 幂等:重复 stop 不炸
    _rewrite(skills_dir / "a.yaml", ALPHA_V1.replace("第一版", "第二版"))
    time.sleep(0.35)
    assert "第一版" in (registry.get_by_name("alpha.one").prompt or ""), (
        "停看后改动不再自动生效"
    )


# ---------------------------------------------------------------------------
# [skills] watch_interval 配置接线
# ---------------------------------------------------------------------------


def _skills_cfg(skills_dir: Path, **kw) -> dict:
    return {"skills": {"path": str(skills_dir), **kw}}


def test_config_watch_interval_starts_watcher(dir_registry):
    """watch_interval > 0:build_kernel 装配后 registry 确实在 watch(属性断言)。"""
    _, skills_dir = dir_registry
    kernel = build_kernel(_skills_cfg(skills_dir, watch_interval=0.2))
    try:
        assert kernel.skills is not None
        assert kernel.skills._watch_thread is not None
        assert kernel.skills._watch_thread.is_alive()
    finally:
        kernel.skills.stop_watching()


def test_config_watch_default_not_watching(dir_registry):
    """缺省 watch_interval = 0:装配后不 watch(零破坏回归)。"""
    _, skills_dir = dir_registry
    kernel = build_kernel(_skills_cfg(skills_dir))
    assert kernel.skills is not None and kernel.skills._watch_thread is None


def test_config_watch_interval_invalid_rejected(dir_registry):
    """非法 watch_interval(负数/字符串/布尔)→ ConfigError。"""
    _, skills_dir = dir_registry
    for bad in (-1, "0.5", True):
        with pytest.raises(ConfigError, match="watch_interval"):
            build_kernel(_skills_cfg(skills_dir, watch_interval=bad))


def test_config_watch_without_path_rejected():
    """watch_interval/register_smoke 配了但没有 path → ConfigError(无 registry 可接线)。"""
    with pytest.raises(ConfigError, match="path"):
        build_kernel({"skills": {"watch_interval": 0.5}})
    with pytest.raises(ConfigError, match="path"):
        build_kernel({"skills": {"register_smoke": "tests.helpers.register_smoke:smoke_ok"}})


def test_config_skills_unknown_field_rejected(dir_registry):
    """[skills] 未知字段(拼错的 watch_interval)→ ConfigError(strict 校验先例)。"""
    _, skills_dir = dir_registry
    with pytest.raises(ConfigError, match="未知字段"):
        build_kernel(_skills_cfg(skills_dir, watch_interva=0.5))

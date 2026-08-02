"""内置工具副作用档标注守门(TIER-STANDARDS §1 命名规则;dogfood 单测)。

delete/kill/stop/remove 类工具按"改了能不能挽回"判 = L3 irreversible,
必须显式标 ``side_effect="irreversible"``——WRITE 默认推导只有 reversible,
漏标会把删除类工具降进 approve-run 的批量放行面(L2),这是硬性安全面。
"""

from __future__ import annotations

from agent_os.api.v1 import derive_side_effect
from agent_os.tools.local_registry import LocalPythonToolRegistry


def test_delete_kill_stop_class_tools_are_irreversible():
    """delete/kill/stop 类内置工具推导档 == irreversible(显式声明,非默认推导巧合)。"""
    registry = LocalPythonToolRegistry.with_builtins()
    specs = {s.name: s for s in registry.specs()}
    # 命名的动词即承诺:这些工具不存在 reversible 语义(TIER-STANDARDS §1 判定树)
    assert specs["system.file.delete"].side_effect == "irreversible"
    assert derive_side_effect(specs["system.file.delete"]) == "irreversible"
    assert specs["system.shell.exec"].side_effect == "irreversible"
    assert derive_side_effect(specs["system.shell.exec"]) == "irreversible"


def test_fs_write_tools_stay_reversible():
    """对照:写入类(可覆盖/可补偿)仍是 reversible——不被上面的守门误伤上调。"""
    registry = LocalPythonToolRegistry.with_builtins()
    specs = {s.name: s for s in registry.specs()}
    assert derive_side_effect(specs["system.file.write"]) == "reversible"
    assert derive_side_effect(specs["system.file.mkdir"]) == "reversible"

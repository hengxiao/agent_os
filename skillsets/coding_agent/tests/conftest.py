"""coding_agent skillset 测试的共享基础设施。

两件事(仿 agent_os/conftest.py 对 std/ 的做法):
1. 把 skillset 目录与 agent_os 仓库目录钉上 sys.path——skills.yaml 的
   ``handler: handlers:<fn>`` 与 mock 档 ``brain = "brains:coding_brain"`` 都是
   dotted path,LocalFileSkillRegistry / config 加载不注入包所在目录;
   ``tests.helpers.*`` 导入需要 agent_os 仓库目录在 sys.path;
2. fixture 目标仓库复制助手:每个用例拿到 tmp_path 下的独立副本作 workdir,
   避免测试间污染(修复循环会真改 calc.py)。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SKILLSET_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
AGENT_OS_ROOT = SKILLSET_DIR.parents[1] / "agent_os"

# fixtures/ 是被测目标仓库(其 test_calc.py 故意失败),不是本套件的用例,不得收集
collect_ignore = ["fixtures"]

for _p in (str(SKILLSET_DIR), str(AGENT_OS_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: fixture 目标仓库的文件清单(复制辅助按名白名单,不整树拷贝)
_FIXTURE_FILES = ("calc.py", "test_calc.py", "README.md")


def copy_fixture(dst: Path) -> Path:
    """把 fixture 迷你仓库复制到 ``dst``(须已存在),返回 ``dst``。"""
    for name in _FIXTURE_FILES:
        shutil.copy2(FIXTURES_DIR / name, dst / name)
    return dst

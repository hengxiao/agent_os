"""pytest 根 conftest:把 std 技能包目录钉上 sys.path。

锚点 tests/skills/test_std_transform.py 经 KernelBuilder + LocalFileSkillRegistry
装配 std/skills.yaml,handler 是 "transform:<fn>" dotted path;LocalFileSkillRegistry
**不**注入 skills.yaml 所在目录(skills_100 的做法是测试内 sys.path.insert,而锚点
是冻结交付件不可改),故在此注入,等价于 skills_100 的组装方式。
"""

from __future__ import annotations

import sys
from pathlib import Path

STD_DIR = str(Path(__file__).resolve().parent / "std")
if STD_DIR not in sys.path:
    sys.path.insert(0, STD_DIR)

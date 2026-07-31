"""pytest 根 conftest:把 std 技能包目录钉上 sys.path。

锚点 tests/skills/test_std_transform.py 经 KernelBuilder + LocalFileSkillRegistry
装配 std/ 目录(域分包 *.yaml 合并加载),handler 是 "transform:<fn>" dotted path;
LocalFileSkillRegistry **不**注入包所在目录(skills_100 的做法是测试内
sys.path.insert,而锚点是冻结交付件不可改),故在此注入,等价于 skills_100 的组装方式。
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

STD_DIR = str(Path(__file__).resolve().parent / "std")
if STD_DIR not in sys.path:
    sys.path.insert(0, STD_DIR)

# travel_planner 锚点(tests/examples/test_travel_planner.py,冻结交付件)的 live smoke
# 用 ``urllib.request.urlopen`` 缺省 UA(Python-urllib/x.y)探测 wikivoyage.org 可达性;
# Wikimedia 对缺省 UA 一律 403(机器人政策要求可识别 UA),本机实测可达却被误判为
# 不可达而 skip。在此把全局 opener 的 UA 换成可识别串(与 travel_tools 同一串),
# 只加请求头,不改变任何其他行为;全仓仅此一处 urllib 外呼,无连带影响。
_urlopen_opener = urllib.request.build_opener()
_urlopen_opener.addheaders = [
    ("User-Agent", "agent-os-travel-planner/0.1 (live demo; contact: local-run)")
]
urllib.request.install_opener(_urlopen_opener)

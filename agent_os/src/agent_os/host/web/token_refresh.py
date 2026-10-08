"""兼容壳(P2):实现已挪 ``host/shared/token_refresh.py``(宿主通用件,chat 宿主共用)。

``sys.modules`` 别名直通——既有 import 路径(``from agent_os.host.web import
token_refresh``;tests/web/test_token_refresh.py 钉着本壳)拿到的是实现模块本体,
含私有件(``tr._refresh_once``/``tr.urllib`` 打桩面)原样可用。
"""

from __future__ import annotations

import sys

from agent_os.host.shared import token_refresh as _impl

sys.modules[__name__] = _impl

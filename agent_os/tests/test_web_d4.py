"""D4 后端锚点测试:tools 列表端点(WEB-UI.md §6.2)。

固定约定:``GET /api/tools`` 返回共享 tools registry 的全量 ToolSpec 摘要
(name/description/permission/parameters 及执行属性),供 Tools 浏览器。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from tests.test_cli_r1 import _write_config

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_tools_endpoint_lists_tool_specs(tmp_path):
    client = TestClient(create_app(_write_config(tmp_path), artifacts_root=tmp_path / "runs"))
    r = client.get("/api/tools")
    assert r.status_code == 200
    tools = {t["name"]: t for t in r.json()}
    assert "python_exec" in tools
    assert tools["python_exec"]["permission"] == "EXEC"
    assert tools["python_exec"]["parameters"]["type"] == "object"
    assert "fs_read" in tools
    assert tools["fs_read"]["permission"] == "READ"

"""MCP 真实 server 互测(stdio;npx @modelcontextprotocol/server-filesystem)。

互测对象:官方参考实现(npm 包 @modelcontextprotocol/server-filesystem),
验证 stdio 适配器对**真实 server**(非 tests/helpers 罐头对端)的握手 /
tools/list / tools/call / 目录越权拒绝全链路。

skip 护栏:``npx`` 不在 PATH 或环境变量 ``AGENT_OS_MCP_INTEROP=0`` → pytest.skip
(无 node/无网环境不阻塞测试面);npx 在场但启动失败 → **fail 不 skip**
(本机已实测 npx/node/网络可达,启动失败即回归)。

注意:子进程不继承宿主 env(§8.3 最小授权),npx 起 node 需要 PATH/HOME,
经 ``{env = "VAR"}`` 间接引用显式传入(连接时现读,不落盘明文)。
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest

from agent_os.api.v1 import (
    Permission,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolPolicy,
)
from agent_os.tools.local_registry import LocalPythonToolRegistry
from agent_os.tools.mcp import McpServerSpec, connect_and_register


def _npx() -> str:
    """skip 护栏:互测显式关闭或 npx 缺席 → skip(其余失败一律 fail)。"""
    if os.environ.get("AGENT_OS_MCP_INTEROP") == "0":
        pytest.skip("AGENT_OS_MCP_INTEROP=0:真实 server 互测显式关闭")
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx 不在 PATH(互测需要 node/npm 环境)")
    return npx


def _dispatch_ctx():
    frame = SkillFrame(frame_id="f1", run_id="r1")
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=[],
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


def test_filesystem_server_interop(tmp_path):
    """tools/list 含 read_file;读允许目录内文件内容逐字对;读目录外路径被拒。"""
    npx = _npx()
    allowed = tmp_path / "share"
    allowed.mkdir()
    target = allowed / "hello.txt"
    content = "你好 MCP interop\n第二行 plain"
    target.write_text(content, encoding="utf-8")

    async def main():
        registry = LocalPythonToolRegistry()
        spec = McpServerSpec(
            name="fs",
            command=[npx, "-y", "@modelcontextprotocol/server-filesystem", str(allowed)],
            env={
                # 子进程不继承宿主 env:npx 起 node 所需的最小变量显式间接传入
                "PATH": {"env": "PATH"},
                "HOME": {"env": "HOME"},
            },
            connect_timeout=90.0,  # 首次 npx 下载包较慢
            timeout=30.0,
        )
        clients = await connect_and_register(registry, [spec])
        try:
            names = [s.name for s in registry.specs()]
            assert names, "tools/list 为空"
            assert "mcp.fs.read_file" in names, f"tools/list 缺 read_file: {names}"
            ok = await registry.dispatch(
                ToolCall(id="r1", name="mcp.fs.read_file", args={"path": str(target)}),
                _dispatch_ctx(),
            )
            assert ok.ok, ok.error
            # 真实 server 回 structuredContent(归一化优先于 text 拼接,同 test_mcp.py 锚点):
            # value 可能是 {"content": ...} dict 或纯文本,两种形态都验逐字回读
            value = ok.value
            text = value.get("content") if isinstance(value, dict) else value
            assert isinstance(text, str) and content in text, f"文件内容未逐字回读: {value!r}"
            # 允许目录外路径:server 侧拒绝(isError → 工具级失败)
            denied = await registry.dispatch(
                ToolCall(id="r2", name="mcp.fs.read_file", args={"path": "/etc/hostname"}),
                _dispatch_ctx(),
            )
            assert not denied.ok, "允许目录外路径必须被 server 拒绝"
        finally:
            for client in clients:
                await client.close()

    asyncio.run(main())

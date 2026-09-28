"""MCP stdio 适配器锚点测试(tools/mcp.py;docs/DESIGN.md §8.3 供应链清单)。

固定约定:

- ``[mcp.servers.<name>]`` 段 eager 装配:装配期拉起子进程 initialize 握手 +
  tools/list,工具以 ``mcp.<server>.<tool>`` 注册进 LocalPythonToolRegistry,
  走全量 dispatch 管线(白名单/超时/authZ 语义不变);
- 连接失败 → ConfigError 快速失败(坏 server 不静默缺席);
- spec 转换:permission 缺省 READ、``untrusted_source=True``、``concurrency_safe=False``;
  description 注入命中整段弃用占位、干净描述超 500 字符截断;
- 断管/进程死 → 下次调用重连一次重试一次,再失败 INTERNAL;超时 → TIMEOUT retryable
  且连接被杀重建;``close()`` 杀进程组不留孤儿(atexit 兜底);
- 子进程不继承宿主 env,``{env = "VAR"}`` 间接引用连接时现读 os.environ。

对端:``tests/helpers/mcp_server.py``(罐头 JSON-RPC stdio 假服务器)。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    Permission,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.runtime.config import ConfigError, build_kernel
from agent_os.tools.local_registry import LocalPythonToolRegistry
from agent_os.tools.mcp import (
    McpError,
    McpServerSpec,
    connect_and_register,
)

#: 罐头假服务器路径(python -u 起,行分隔协议)
SERVER = Path(__file__).resolve().parents[1] / "helpers" / "mcp_server.py"

#: 测试专用 env 名/值(避免与真实环境变量撞名;值带可识别标记)
ENV_VAR = "AGENT_OS_TEST_MCP_TOKEN"
ENV_VALUE = "TESTSECRET-MCP-TOKEN"


def _spec(**kw) -> McpServerSpec:
    """默认装配规格:fake server,缺省全默认(permission=READ/timeout=30)。"""
    return McpServerSpec(name="fake", command=[sys.executable, "-u", str(SERVER)], **kw)


def _dispatch_ctx(*, allowed: list[str] | None = None, max_perm: Permission = Permission.EXEC):
    frame = SkillFrame(frame_id="f1", run_id="r1")
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=allowed if allowed is not None else [],
        tool_policy=ToolPolicy(max_permission=max_perm),
    )


async def _assemble(spec: McpServerSpec | None = None, registry: LocalPythonToolRegistry | None = None):
    """连一个 server 并返回 (registry, clients);调用方负责 finally close。"""
    registry = registry or LocalPythonToolRegistry()
    clients = await connect_and_register(registry, [spec or _spec()])
    return registry, clients


async def _close(clients) -> None:
    for client in clients:
        await client.close()


def _server_pids() -> set[str]:
    """当前存活的假服务器 pid 集合(孤儿检测用;照 test_builtins.py _sleep_pids 先例)。"""
    out = subprocess.run(
        ["pgrep", "-f", "helpers/mcp_server.py"], capture_output=True, text=True, check=False
    ).stdout
    return set(out.split())


# ---------------------------------------------------------------------------
# 1. eager 装配:注册与 spec 转换
# ---------------------------------------------------------------------------


def test_eager_registration_and_spec_fields():
    """装配期连接 + tools/list 注册:mcp.fake.* 在场;spec 权限/超时/不可信标记正确。"""

    async def main():
        registry, clients = await _assemble()
        try:
            assert registry.has("mcp.fake.echo")
            assert registry.has("mcp.fake.fail")
            echo = registry.get("mcp.fake.echo").spec
            assert echo.permission is Permission.READ  # server 级缺省最小授权
            assert echo.timeout == 30.0
            assert echo.untrusted_source is True  # 第三方产出恒标记(§8.3)
            assert echo.concurrency_safe is False
            assert echo.confirm is False
            assert echo.parameters["properties"]["text"]["type"] == "string"  # inputSchema 透传
            assert len(registry._mcp_clients) == 1  # 生命周期锚点挂上
        finally:
            await _close(clients)

    asyncio.run(main())


def test_invalid_tool_name_skipped():
    """非法字符工具名(含空格/标点)跳过注册,不影响其余工具面。"""

    async def main():
        registry, clients = await _assemble()
        try:
            assert not registry.has("mcp.fake.bad name!")
            assert registry.has("mcp.fake.echo")  # 其余工具不受影响
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 2. 白名单:WRITE 档 mcp 工具受帧白名单约束(READ 档豁免,同内置语义)
# ---------------------------------------------------------------------------


def test_whitelist_denies_write_tier():
    """whitelist 不含 mcp.* 时,WRITE 档 server 工具 → PERMISSION_DENIED;列入后放行。"""

    async def main():
        registry, clients = await _assemble(_spec(permission=Permission.WRITE))
        try:
            denied = await registry.dispatch(
                ToolCall(id="w1", name="mcp.fake.echo", args={"text": "x"}), _dispatch_ctx()
            )
            assert not denied.ok
            assert denied.error is not None and denied.error.kind is ToolErrorKind.PERMISSION_DENIED
            allowed = await registry.dispatch(
                ToolCall(id="w2", name="mcp.fake.echo", args={"text": "x"}),
                _dispatch_ctx(allowed=["mcp.fake.echo"]),
            )
            assert allowed.ok, allowed.error
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 3. 调用归一化:echo 文本 / fail isError / structuredContent 优先 / 非 text 占位
# ---------------------------------------------------------------------------


def test_echo_and_fail_normalization():
    """echo → 文本结果;fail(isError=true)→ INTERNAL 错误且消息带 text。"""

    async def main():
        registry, clients = await _assemble()
        try:
            ok = await registry.dispatch(
                ToolCall(id="e1", name="mcp.fake.echo", args={"text": "你好"}), _dispatch_ctx()
            )
            assert ok.ok, ok.error
            assert ok.value == "你好"
            fail = await registry.dispatch(
                ToolCall(id="e2", name="mcp.fake.fail", args={}), _dispatch_ctx()
            )
            assert not fail.ok
            assert fail.error is not None and fail.error.kind is ToolErrorKind.INTERNAL
            assert "boom" in fail.error.message
        finally:
            await _close(clients)

    asyncio.run(main())


def test_structured_content_priority_and_non_text_placeholder():
    """structuredContent 优先于 content 文本拼接;非 text 块显式占位不静默丢。"""

    async def main():
        registry, clients = await _assemble()
        try:
            structured = await registry.dispatch(
                ToolCall(id="s1", name="mcp.fake.structured", args={"a": 1}), _dispatch_ctx()
            )
            assert structured.ok and structured.value == {"echo": {"a": 1}}
            image = await registry.dispatch(
                ToolCall(id="s2", name="mcp.fake.image", args={}), _dispatch_ctx()
            )
            assert image.ok and image.value == "[不支持的 MCP 内容类型: image]"
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 4. 超时:TIMEOUT retryable,连接被杀(下次调用重建)
# ---------------------------------------------------------------------------


def test_timeout_kills_connection_and_retryable():
    """slow 工具 + timeout=0.5 → TIMEOUT retryable=True;超时后连接已死,下次调用重连成功。"""

    async def main():
        registry, clients = await _assemble(_spec(timeout=0.5))
        try:
            result = await registry.dispatch(
                ToolCall(id="t1", name="mcp.fake.slow", args={}), _dispatch_ctx()
            )
            assert not result.ok
            assert result.error is not None and result.error.kind is ToolErrorKind.TIMEOUT
            assert result.error.retryable is True
            assert not clients[0]._alive, "超时后的连接不可信(迟到响应错位),必须已杀"
            # 连接重建:后续调用照常(echo 立即回)
            ok = await registry.dispatch(
                ToolCall(id="t2", name="mcp.fake.echo", args={"text": "复活"}), _dispatch_ctx()
            )
            assert ok.ok and ok.value == "复活"
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 5. 注入扫描:evil 描述整段弃用占位;verbose 干净超长描述截断
# ---------------------------------------------------------------------------


def test_evil_description_dropped_and_verbose_truncated():
    """命中注入短语 → 描述整段移除为占位(宁缺毋滥);干净但超长 → 截断并标记。"""

    async def main():
        registry, clients = await _assemble()
        try:
            evil = registry.get("mcp.fake.evil").spec.description
            assert "忽略" not in evil and "系统提示词" not in evil
            assert "已移除" in evil  # 显式占位标记
            verbose = registry.get("mcp.fake.verbose").spec.description
            assert verbose.endswith("…[截断]")
            assert len(verbose) <= 500 + len("…[截断]")
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 6. env 间接引用:连接时现读 os.environ;宿主 env 不继承
# ---------------------------------------------------------------------------


def test_env_indirection_and_no_inherit(monkeypatch):
    """{env = "VAR"} 间接引用现读 os.environ 命中;未配置的宿主变量(PATH)不进子进程。"""

    async def main():
        registry, clients = await _assemble(_spec(env={"MCP_TEST": {"env": ENV_VAR}}))
        try:
            hit = await registry.dispatch(
                ToolCall(id="v1", name="mcp.fake.echo_env", args={"var": "MCP_TEST"}),
                _dispatch_ctx(),
            )
            assert hit.ok and hit.value == ENV_VALUE
            no_inherit = await registry.dispatch(
                ToolCall(id="v2", name="mcp.fake.echo_env", args={"var": "PATH"}),
                _dispatch_ctx(),
            )
            # 供应链最小授权:子进程不继承宿主 env,PATH 未显式配置即缺席
            assert no_inherit.ok and no_inherit.value == ""
        finally:
            await _close(clients)

    monkeypatch.setenv(ENV_VAR, ENV_VALUE)
    asyncio.run(main())


def test_env_indirection_missing_var_fails_fast(monkeypatch):
    """间接引用的 env 变量缺席 → 装配期 McpError(eager 快速失败,不静默缺席)。"""
    monkeypatch.delenv(ENV_VAR, raising=False)

    async def main():
        with pytest.raises(McpError, match=ENV_VAR):
            await connect_and_register(
                LocalPythonToolRegistry(), [_spec(env={"MCP_TEST": {"env": ENV_VAR}})]
            )

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 7. 进程生命周期:close 无孤儿;外部杀进程 → 重连成功;连断两次 → INTERNAL
# ---------------------------------------------------------------------------


def test_close_leaves_no_orphans():
    """close() 杀进程组,不留孤儿(对照 pgrep 快照,照 test_builtins.py 先例)。"""

    async def main():
        before = _server_pids()
        _, clients = await _assemble()
        assert clients[0]._alive
        await _close(clients)
        await asyncio.sleep(0.2)  # 等 OS 收尸
        assert _server_pids() - before == set(), "close 后仍有存活的 server 进程"
        await _close(clients)  # 幂等:重复 close 是 no-op

    asyncio.run(main())


def test_reconnect_after_external_kill():
    """外部杀掉 server 进程 → 下次调用自动重连(新 pid)并成功;旧尸首已收。"""

    async def main():
        registry, clients = await _assemble()
        try:
            client = clients[0]
            old_pid = client._proc.pid
            client._proc.kill()  # 模拟 server 崩溃(zombie 由重连路径 wait 收掉)
            result = await registry.dispatch(
                ToolCall(id="k1", name="mcp.fake.echo", args={"text": "复活"}), _dispatch_ctx()
            )
            assert result.ok and result.value == "复活"
            assert client._proc.pid != old_pid, "重连须起新进程"
        finally:
            await _close(clients)

    asyncio.run(main())


def test_double_break_gives_internal():
    """die 工具(不应答即退):第一次断管 → 重连 → 第二次又死 → INTERNAL(重试只一次)。"""

    async def main():
        registry, clients = await _assemble()
        try:
            result = await registry.dispatch(
                ToolCall(id="d1", name="mcp.fake.die", args={}), _dispatch_ctx()
            )
            assert not result.ok
            assert result.error is not None and result.error.kind is ToolErrorKind.INTERNAL
            assert "重连后仍失败" in result.error.message
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 8. config 段解析:全键 / 缺 command / 未知键 / 缺段零破坏 / 非法名 / eager 失败
# ---------------------------------------------------------------------------


def _mcp_cfg(**server_kw) -> dict:
    return {"mcp": {"servers": {"fake": {"command": [sys.executable, "-u", str(SERVER)], **server_kw}}}}


def test_config_full_parse_and_wiring():
    """全键解析:permission/timeout/confirm/connect_timeout/env 落到 spec;接线进 kernel。"""
    kernel = build_kernel(
        _mcp_cfg(permission="write", timeout=5, confirm=True, connect_timeout=5, env={"A": "1"})
    )
    try:
        spec = kernel.tools.get("mcp.fake.echo").spec
        assert spec.permission is Permission.WRITE
        assert spec.timeout == 5.0
        assert spec.confirm is True
        assert len(kernel.tools._mcp_clients) == 1
    finally:
        asyncio.run(_close(kernel.tools._mcp_clients))


def test_config_missing_command_rejected():
    with pytest.raises(ConfigError, match="command"):
        build_kernel({"mcp": {"servers": {"fake": {}}}})


def test_config_unknown_keys_rejected():
    with pytest.raises(ConfigError, match="未知字段"):
        build_kernel(_mcp_cfg(bogus=1))
    with pytest.raises(ConfigError, match="未知字段"):
        build_kernel({"mcp": {"bogus": {}}})  # [mcp] 段本身的未知键也拒


def test_config_absent_section_zero_breakage():
    """缺 [mcp] 段:装配正常,registry._mcp_clients 为空(零破坏)。"""
    kernel = build_kernel({})
    assert kernel.tools._mcp_clients == []


def test_config_bad_server_name_rejected():
    with pytest.raises(ConfigError, match="非法"):
        build_kernel({"mcp": {"servers": {"bad name": {"command": ["x"]}}}})


def test_config_bad_permission_and_env_shape_rejected():
    with pytest.raises(ConfigError, match="permission"):
        build_kernel(_mcp_cfg(permission="admin"))
    with pytest.raises(ConfigError, match="env"):
        build_kernel(_mcp_cfg(env={"A": {"bogus": "X"}}))


def test_config_connect_failure_fast():
    """eager:server 起不来 → ConfigError 快速失败(不是运行时才发现工具缺席)。"""
    with pytest.raises(ConfigError, match="eager"):
        build_kernel({"mcp": {"servers": {"fake": {"command": ["/nonexistent/mcp-bin"]}}}})


# ---------------------------------------------------------------------------
# 9. 与内置工具面共存不撞名;同名 server 重复注册拒绝覆盖
# ---------------------------------------------------------------------------


def test_coexists_with_builtins_and_no_override():
    """with_builtins 工具面 + mcp.fake.* 共存;重复注册同名 MCP 工具 → McpError 拒覆盖。"""

    async def main():
        registry = LocalPythonToolRegistry.with_builtins()
        assert not any(s.name.startswith("mcp.") for s in registry.specs())
        registry, clients = await _assemble(registry=registry)
        try:
            assert registry.has("mcp.fake.echo")
            assert registry.has("system.shell.exec")  # 内置工具面原样在场
            # 同名 server 再装一次:mcp.fake.echo 已注册 → 拒覆盖并清理新进程
            with pytest.raises(McpError, match="拒绝覆盖"):
                await connect_and_register(registry, [_spec()])
        finally:
            await _close(clients)

    asyncio.run(main())

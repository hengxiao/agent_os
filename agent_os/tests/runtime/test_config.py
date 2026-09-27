"""配置装配锚点测试(docs/RUNNERS.md §2.1;runtime/config.py 的错误归类与扩展点)。

固定约定:

- 配置缺失/畸形/未知字段/装配失败 → ``ConfigError``(宿主归退出码 4);
- ``[tools] python_exec`` 启用即把 RunConfig 权限上限提到 EXEC(§8.2 配置即授权);
- ``[tools.custom] module = "pkg.mod:func"``:importlib 加载后调 ``func(registry)``,
  失败抛 ``ConfigError``;声明即授权,权限上限同步提到 EXEC;
- ``load_skillsets(root)``:只收 ``<root>/<set>/skills.yaml`` 存在的子目录,按名排序。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_os.api.v1 import (
    CONFIDENTIAL,
    INTERNAL,
    Permission,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolPolicy,
)
from agent_os.memory.local_file import LocalFileMemoryService
from agent_os.runtime.config import (
    ConfigError,
    build_kernel,
    load_config,
    load_skillsets,
    web_token_map,
)
from agent_os.sidecars import HumanApproval
from tests.helpers.kernels import FIB_SKILLS_YAML


def _base_cfg(**overrides):
    cfg = {
        "run": {"model": "mock/fib", "compression": "off"},
        "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
        "tools": {"python_exec": "subprocess"},
        "skills": {"path": str(FIB_SKILLS_YAML)},
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# load_config:文件层错误
# ---------------------------------------------------------------------------


def test_missing_config_file_rejected(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.toml")


def test_malformed_toml_rejected(tmp_path):
    bad = tmp_path / "agent-os.toml"
    bad.write_text("[run\nmodel = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="畸形"):
        load_config(bad)


# ---------------------------------------------------------------------------
# 未知字段/枚举的硬拒绝
# ---------------------------------------------------------------------------


def test_unknown_run_field_rejected():
    with pytest.raises(ConfigError, match="未知字段"):
        build_kernel(_base_cfg(run={"model": "m", "max_depht": 3}))


def test_unknown_provider_rejected():
    with pytest.raises(ConfigError, match="未知 provider"):
        build_kernel(_base_cfg(providers={"gemini": {}}))


def test_unknown_sidecar_rejected():
    with pytest.raises(ConfigError, match="未知 sidecar"):
        build_kernel(_base_cfg(sidecars={"watchdog": {}}))


def test_bad_tool_guard_rule_shape_rejected():
    with pytest.raises(ConfigError, match="tool_guard_rules"):
        build_kernel(_base_cfg(sidecars={"tool_guard_rules": [["system.shell.exec", "rm"]]}))


# ---------------------------------------------------------------------------
# [sidecars] human_approval(WS2):策略载体装配 + supervisor 通道策略补缺
# ---------------------------------------------------------------------------


def test_human_approval_true_form_defaults():
    """true 形态 = 缺省策略(timeout=600, on_timeout="deny");实例传给 Kernel。"""
    kernel = build_kernel(_base_cfg(sidecars={"human_approval": True}))
    ha = kernel.human_approval
    assert isinstance(ha, HumanApproval)
    assert ha.timeout == 600.0
    assert ha.on_timeout == "deny"


def test_human_approval_table_form_and_supervisor_policy_mapping():
    """表形态映射到闸门共用 supervisor 通道:timeout 直取;allow → default_answer
    且兜底答案 "approve-once"(on_timeout 语义映射见 KernelBuilder.build)。"""
    async def handler(question):
        return {"answer": "approve-once"}

    kernel = build_kernel(
        _base_cfg(sidecars={"human_approval": {"timeout": 5, "on_timeout": "allow"}}),
        supervisor_handler=handler,
    )
    assert kernel.human_approval.timeout == 5.0
    assert kernel.human_approval.on_timeout == "allow"
    sup = kernel.supervisor
    assert sup is not None
    assert sup._timeout_s == 5.0
    assert sup._on_timeout == "default_answer"
    assert sup._default_answer == "approve-once"


def test_human_approval_explicit_supervisor_config_wins():
    """回归锚:显式 [supervisor] 策略字段优先于 HumanApproval 补缺。"""
    async def handler(question):
        return {"answer": "approve-once"}

    kernel = build_kernel(
        _base_cfg(
            sidecars={"human_approval": {"timeout": 5, "on_timeout": "allow"}},
            supervisor={"timeout_s": 7.0, "on_timeout": "fail"},
        ),
        supervisor_handler=handler,
    )
    assert kernel.supervisor._timeout_s == 7.0
    assert kernel.supervisor._on_timeout == "fail"


def test_human_approval_bad_shape_rejected():
    with pytest.raises(ConfigError, match="human_approval"):
        build_kernel(_base_cfg(sidecars={"human_approval": "yes"}))


def test_human_approval_unknown_subkey_rejected():
    with pytest.raises(ConfigError, match="human_approval"):
        build_kernel(_base_cfg(sidecars={"human_approval": {"timeot": 5}}))


def test_human_approval_bad_on_timeout_rejected():
    with pytest.raises(ConfigError, match="on_timeout"):
        build_kernel(_base_cfg(sidecars={"human_approval": {"on_timeout": "maybe"}}))


def test_unknown_python_exec_backend_rejected():
    with pytest.raises(ConfigError, match="python_exec"):
        build_kernel(_base_cfg(tools={"python_exec": "wasm"}))


def test_bad_mock_brain_dotted_path_rejected():
    with pytest.raises(ConfigError, match="dotted path"):
        build_kernel(_base_cfg(providers={"mock": {"brain": "not-a-path"}}))


def test_unimportable_mock_brain_rejected():
    with pytest.raises(ConfigError, match="无法加载"):
        build_kernel(_base_cfg(providers={"mock": {"brain": "tests.helpers.nope:fn"}}))


def test_non_callable_dotted_path_rejected():
    with pytest.raises(ConfigError, match="不可调用"):
        build_kernel(
            _base_cfg(providers={"mock": {"brain": "tests.helpers.custom_tools:NOT_CALLABLE"}})
        )


# ---------------------------------------------------------------------------
# 权限上限的"配置即授权"(§8.2)
# ---------------------------------------------------------------------------


def test_python_exec_elevates_permission_to_exec():
    kernel = build_kernel(_base_cfg())
    assert kernel.config.tool_policy.max_permission is Permission.EXEC
    result = asyncio.run(kernel.run("demo.fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}


def test_python_exec_off_keeps_default_permission():
    # fib skills.yaml 声明 python_exec,关掉后装配期权限闸门会拒绝,故不接技能
    cfg = _base_cfg(tools={"python_exec": "off"})
    del cfg["skills"]
    kernel = build_kernel(cfg)
    assert kernel.config.tool_policy.max_permission < Permission.EXEC


# ---------------------------------------------------------------------------
# [tools.custom] 注册钩子
# ---------------------------------------------------------------------------


def test_custom_tools_hook_registers_and_elevates():
    cfg = _base_cfg(
        tools={"python_exec": "off", "custom": {"module": "tests.helpers.custom_tools:register"}}
    )
    del cfg["skills"]  # 同上:fib 清单声明 python_exec,off 档不接技能
    kernel = build_kernel(cfg)
    assert kernel.tools.has("echo_text")
    assert kernel.config.tool_policy.max_permission is Permission.EXEC


def test_custom_tools_hook_failure_is_config_error():
    cfg = _base_cfg(
        tools={"custom": {"module": "tests.helpers.custom_tools:bad_register"}}
    )
    with pytest.raises(ConfigError, match="注册钩子执行失败"):
        build_kernel(cfg)


# ---------------------------------------------------------------------------
# load_skillsets
# ---------------------------------------------------------------------------


def test_load_skillsets_scans_only_valid_sets(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "skills.yaml").write_text("skills: []", encoding="utf-8")
    (tmp_path / "beta").mkdir()  # 无 skills.yaml,不收
    (tmp_path / "zeta").mkdir()
    (tmp_path / "zeta" / "skills.yaml").write_text("skills: []", encoding="utf-8")
    (tmp_path / "loose.txt").write_text("x", encoding="utf-8")  # 非目录,不收

    sets = load_skillsets(tmp_path)
    assert list(sets) == ["alpha", "zeta"]
    assert sets["alpha"] == tmp_path / "alpha"


def test_load_skillsets_missing_root_rejected(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_skillsets(tmp_path / "nope")


# ---------------------------------------------------------------------------
# [providers.kimi] / [providers.anthropic] 子键透传(WS5)
# ---------------------------------------------------------------------------


def _provider(kernel, name):
    return kernel.providers.providers[name]


def test_kimi_subkeys_passed_through():
    kernel = build_kernel(
        _base_cfg(
            providers={
                "mock": {"brain": "tests.helpers.brains:fib_brain"},
                "kimi": {"base_url": "https://api.moonshot.cn/v1", "api_key": "k-test"},
            }
        )
    )
    kimi = _provider(kernel, "kimi")
    assert kimi.base_url == "https://api.moonshot.cn/v1"
    assert kimi.api_key == "k-test"  # 显式钉死,不再走 env


def test_kimi_default_keeps_dynamic_env_key():
    """回归锚:缺省构造行为不变——默认端点 + 动态 env 解析(token_refresh 续期即生效)。"""
    kernel = build_kernel(
        _base_cfg(providers={"mock": {"brain": "tests.helpers.brains:fib_brain"}, "kimi": {}})
    )
    kimi = _provider(kernel, "kimi")
    assert kimi.base_url == "https://api.moonshot.ai/v1"
    assert kimi._dynamic_key is True


def test_unknown_kimi_subkey_rejected():
    with pytest.raises(ConfigError, match=r"\[providers\.kimi\] 含未知字段"):
        build_kernel(_base_cfg(providers={"kimi": {"baseurl": "x"}}))


def test_anthropic_subkeys_passed_through():
    kernel = build_kernel(
        _base_cfg(
            providers={
                "mock": {"brain": "tests.helpers.brains:fib_brain"},
                "anthropic": {
                    "api_key": "a-test",
                    "base_url": "https://proxy.example.com",
                    "default_max_tokens": 8192,
                    "anthropic_version": "2023-06-01",
                },
            }
        )
    )
    claude = _provider(kernel, "anthropic")
    assert claude.api_key == "a-test"
    assert claude.base_url == "https://proxy.example.com"
    assert claude.default_max_tokens == 8192
    assert claude.anthropic_version == "2023-06-01"


def test_anthropic_default_unchanged():
    """回归锚:子表缺省时构造行为不变(默认端点/max_tokens/version)。"""
    kernel = build_kernel(
        _base_cfg(providers={"mock": {"brain": "tests.helpers.brains:fib_brain"}, "anthropic": {}})
    )
    claude = _provider(kernel, "anthropic")
    assert claude.base_url == "https://api.anthropic.com"
    assert claude.default_max_tokens == 4096
    assert claude.anthropic_version == "2023-06-01"


def test_unknown_anthropic_subkey_rejected():
    with pytest.raises(ConfigError, match=r"\[providers\.anthropic\] 含未知字段"):
        build_kernel(_base_cfg(providers={"anthropic": {"max_tokens": 8192}}))


# ---------------------------------------------------------------------------
# [retry] 段:max_attempts / backoff_base / stream_idle_timeout(WS1 流式看门狗)
# ---------------------------------------------------------------------------


def test_retry_section_wires_manager_params():
    kernel = build_kernel(
        _base_cfg(retry={"max_attempts": 2, "backoff_base": 0.1, "stream_idle_timeout": 5})
    )
    mgr = kernel.providers
    assert mgr.max_attempts == 2
    assert mgr.backoff_base == 0.1
    assert mgr.stream_idle_timeout == 5


def test_no_retry_section_keeps_manager_defaults():
    """缺段零破坏:ProviderManager 默认值逐字不动。"""
    kernel = build_kernel(_base_cfg())
    mgr = kernel.providers
    assert mgr.max_attempts == 3
    assert mgr.backoff_base == 0.5
    assert mgr.stream_idle_timeout == 30.0


def test_retry_unknown_field_rejected():
    """严格未知字段(同 _prices/_credentials 先例):超时时长拼错会静默失去看门狗调节。"""
    with pytest.raises(ConfigError, match=r"\[retry\] 含未知字段"):
        build_kernel(_base_cfg(retry={"stream_idle_timeot": 5}))


# ---------------------------------------------------------------------------
# [credentials] 段(WS1):env 变量名形态;非法形态/未知键 ConfigError;缺段零破坏
# ---------------------------------------------------------------------------


def test_credentials_section_binds_scope(monkeypatch):
    """合法形态落表:凭证名 → env 变量名;resolver 每次调用现读 env(动态解析)。"""
    monkeypatch.setenv("AGENT_OS_TEST_CFG_TOKEN", "tok-1")
    kernel = build_kernel(_base_cfg(credentials={"github": {"env": "AGENT_OS_TEST_CFG_TOKEN"}}))
    resolver = kernel.tools._credentials_resolver
    assert resolver is not None, "[credentials] 段存在即应 bind"
    assert resolver(None, ["github"]) == {"github": "tok-1"}
    monkeypatch.setenv("AGENT_OS_TEST_CFG_TOKEN", "tok-2")  # token 续期
    assert resolver(None, ["github"]) == {"github": "tok-2"}, "不得装配期快照"
    monkeypatch.delenv("AGENT_OS_TEST_CFG_TOKEN")
    assert resolver(None, ["github"]) == {}, "env 缺席 → 键不出现"


def test_credentials_non_table_value_rejected():
    with pytest.raises(ConfigError, match=r"\[credentials\] 'github' 应为表"):
        build_kernel(_base_cfg(credentials={"github": "GITHUB_TOKEN"}))


def test_credentials_unknown_subkey_rejected():
    with pytest.raises(ConfigError, match=r"\[credentials\] 'github' 含未知字段"):
        build_kernel(_base_cfg(credentials={"github": {"env": "GITHUB_TOKEN", "token": "x"}}))


def test_credentials_empty_env_rejected():
    with pytest.raises(ConfigError, match=r"\[credentials\] 'github' 的 env 须为非空字符串"):
        build_kernel(_base_cfg(credentials={"github": {"env": ""}}))


def test_no_credentials_section_no_bind():
    """回归锚:缺 [credentials] 段完全不 bind(dispatch 注入空 credentials,零破坏)。"""
    kernel = build_kernel(_base_cfg())
    assert kernel.tools._credentials_resolver is None


# ---------------------------------------------------------------------------
# [data] 段(D2,docs/DATA-AUTHZ.md §3.1/§3.2):落表/缺省 confidential/恰一校验/严格拒绝
# ---------------------------------------------------------------------------


def test_data_section_binds_policy_and_boundaries(tmp_path):
    """合法形态落表:domains → DataPolicy + registry 边界注册(fs/net 分族);
    sensitivity 缺省 confidential(忘了配 = 最严,§3.1);principals 白名单落表。"""
    kernel = build_kernel(
        _base_cfg(
            tools={"builtins": True, "python_exec": "subprocess"},
            data={
                "domains": [
                    {"name": "fs.shared", "path_prefix": str(tmp_path)},  # sensitivity 缺省
                    {
                        "name": "net.intra",
                        "sensitivity": "internal",
                        "url_prefix": "https://intra.example.com/",
                    },
                ],
                "principals": {"user:alice": {"domains": ["fs.*", "net.intra"]}},
            },
        )
    )
    reg = kernel.tools
    policy = reg._data_policy
    assert policy is not None, "[data] 段存在即应 bind"
    assert policy.domains["fs.shared"].sensitivity == CONFIDENTIAL, "sensitivity 缺省 = 最严"
    assert policy.domains["net.intra"].sensitivity == INTERNAL
    assert policy.whitelist_for("user:alice") == ("fs.*", "net.intra")
    assert policy.whitelist_for("user:bob") == (), "未配置 subject → 空表(fail closed)"
    assert any(d.name == "fs.shared" for _, d in reg._fs_domains), "fs 边界按 path_prefix 注册"
    assert any(d.name == "net.intra" for _, d in reg._net_domains), "net 边界按 url_prefix 注册"


def test_no_data_section_no_bind():
    """回归锚(§8 D1 实现注 1):缺 [data] 段完全不 bind,registry 维持 D1 未配置语义。"""
    kernel = build_kernel(_base_cfg(tools={"builtins": True, "python_exec": "subprocess"}))
    assert kernel.tools._data_policy is None


def test_data_domain_requires_exactly_one_prefix():
    """path_prefix/url_prefix 恰居其一(域边界二选一):都给或都不给 → ConfigError。"""
    with pytest.raises(ConfigError, match="恰居其一"):
        build_kernel(
            _base_cfg(
                data={
                    "domains": [
                        {"name": "fs.x", "path_prefix": "/a", "url_prefix": "https://b/"}
                    ]
                }
            )
        )
    with pytest.raises(ConfigError, match="恰居其一"):
        build_kernel(_base_cfg(data={"domains": [{"name": "fs.x"}]}))


def test_data_unknown_fields_rejected():
    """严格先例(同 _prices/_credentials):[data] 顶层/domains 项/principals 项
    未知键 → ConfigError——域边界拼错会静默失去拦截,宁可装配期炸掉。"""
    with pytest.raises(ConfigError, match=r"\[data\] 含未知字段"):
        build_kernel(_base_cfg(data={"domain": []}))
    with pytest.raises(ConfigError, match="domains 项含未知字段"):
        build_kernel(
            _base_cfg(data={"domains": [{"name": "fs.x", "path_prefix": "/a", "acl": "r"}]})
        )
    with pytest.raises(ConfigError, match=r'\[data\.principals\."user:a"\] 含未知字段'):
        build_kernel(
            _base_cfg(
                data={
                    "domains": [{"name": "fs.x", "path_prefix": "/a"}],
                    "principals": {"user:a": {"domain": ["fs.*"]}},
                }
            )
        )


def test_data_principals_domains_shape_rejected():
    """白名单值须为非空字符串数组(glob 域名模式);非法形态 → ConfigError。"""
    with pytest.raises(ConfigError, match="glob 域名模式"):
        build_kernel(
            _base_cfg(
                data={
                    "domains": [{"name": "fs.x", "path_prefix": "/a"}],
                    "principals": {"user:a": {"domains": "fs.*"}},
                }
            )
        )


def test_data_duplicate_domain_rejected():
    with pytest.raises(ConfigError, match="域名重复"):
        build_kernel(
            _base_cfg(
                data={
                    "domains": [
                        {"name": "fs.x", "path_prefix": "/a"},
                        {"name": "fs.x", "url_prefix": "https://b/"},
                    ]
                }
            )
        )


def test_empty_data_section_warns_fail_closed(caplog):
    """空 [data] 段(无 domains 无 principals)= 绑空策略 → fail-closed 全拒:
    装配期 warning 提示是否有意(行为不变,策略照常 bind)。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="agent_os.runtime.config"):
        kernel = build_kernel(_base_cfg(data={}))
    assert kernel.tools._data_policy is not None, "空段也照常 bind(行为不变)"
    assert any(
        "[data] 段为空" in record.message and "fail-closed" in record.message
        for record in caplog.records
    ), "空 [data] 段须发装配期 warning(全拒是否有意)"


def test_nonempty_data_section_no_empty_warning(caplog, tmp_path):
    """有 domains/principals 的正常 [data] 段不触发空段 warning。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="agent_os.runtime.config"):
        build_kernel(
            _base_cfg(
                data={
                    "domains": [{"name": "fs.x", "path_prefix": str(tmp_path)}],
                    "principals": {"user:a": {"domains": ["fs.*"]}},
                }
            )
        )
    assert not any("[data] 段为空" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# [blob] 段(M3,§8.4/§7.2):dir → FileBlobStore;缺段 = 进程内 InMemoryBlobStore(零破坏)
# ---------------------------------------------------------------------------


def test_blob_section_wires_file_store(tmp_path):
    """[blob] dir → registry 的 spill store 换成 FileBlobStore,put/get 落盘往返。"""
    from agent_os.tools.blob import FileBlobStore

    kernel = build_kernel(_base_cfg(blob={"dir": str(tmp_path / "blobs")}))
    store = kernel.tools._blob
    assert isinstance(store, FileBlobStore), "[blob] 段存在即应装配文件版 blob store"

    async def main():
        ref = await store.put(b"spilled", "run-1")
        return ref, await store.get(ref)

    ref, data = asyncio.run(main())
    assert ref.startswith("blob://run-1/") and data == b"spilled"


def test_no_blob_section_keeps_in_memory():
    """缺 [blob] 段 = 进程内 InMemoryBlobStore(零破坏)。"""
    from agent_os.tools.blob import InMemoryBlobStore

    kernel = build_kernel(_base_cfg())
    assert isinstance(kernel.tools._blob, InMemoryBlobStore)


def test_blob_section_unknown_field_rejected():
    """严格先例(同 _prices/_credentials):dir 拼错会静默落到内存版,spill 不持久。"""
    with pytest.raises(ConfigError, match=r"\[blob\] 含未知字段"):
        build_kernel(_base_cfg(blob={"path": "/tmp/blobs"}))


def test_blob_section_requires_nonempty_dir():
    with pytest.raises(ConfigError, match=r"\[blob\] dir 须为非空字符串"):
        build_kernel(_base_cfg(blob={}))
    with pytest.raises(ConfigError, match=r"\[blob\] dir 须为非空字符串"):
        build_kernel(_base_cfg(blob={"dir": 123}))


# ---------------------------------------------------------------------------
# [web.tokens] 段(D3-lite,docs/DATA-AUTHZ.md §2.2):token → subject 映射表
# ---------------------------------------------------------------------------


def test_web_tokens_map_lands():
    """合法形态落表;缺段/空表 → 空映射(单用户语义不变)。"""
    assert web_token_map({"web": {"tokens": {"tok-1": "user:alice", "tok-2": "service:ci"}}}) == {
        "tok-1": "user:alice",
        "tok-2": "service:ci",
    }
    assert web_token_map({}) == {}
    assert web_token_map({"web": {}}) == {}


def test_web_tokens_invalid_value_rejected():
    """映射值须为非空字符串 subject;非表/非法形态 → ConfigError
    (token 表拼错会静默退回单用户——认证面宁可装配期炸掉,同 _credentials 先例)。"""
    with pytest.raises(ConfigError, match="非空字符串 subject"):
        web_token_map({"web": {"tokens": {"tok-1": ""}}})
    with pytest.raises(ConfigError, match="非空字符串 subject"):
        web_token_map({"web": {"tokens": {"tok-1": 42}}})
    with pytest.raises(ConfigError, match=r"\[web\.tokens\] 应为表"):
        web_token_map({"web": {"tokens": ["tok-1"]}})


# ---------------------------------------------------------------------------
# [memory] 段(M6,docs/DESIGN.md §11.2):段存在才接线;缺段不 bind;严格未知字段
# ---------------------------------------------------------------------------


def test_memory_section_wires_service_and_tools(tmp_path):
    """[memory] 段存在:LocalFileMemoryService 接线到 kernel.memory 并经 bind_memory 注入工具面。"""
    kernel = build_kernel(
        _base_cfg(
            tools={"builtins": True, "python_exec": "subprocess"},
            memory={"dir": str(tmp_path / "memory")},
        )
    )
    assert isinstance(kernel.memory, LocalFileMemoryService)
    assert kernel.memory.root == str(tmp_path / "memory")
    assert kernel.tools._memory is kernel.memory, "bind_memory 装配(同 bind_skills 先例)"
    assert kernel.tools.has("system.memory.search") and kernel.tools.has("system.memory.write")


def test_no_memory_section_no_bind():
    """回归锚(同 [credentials] 先例):缺 [memory] 段完全不接线,零破坏。"""
    kernel = build_kernel(_base_cfg(tools={"builtins": True, "python_exec": "subprocess"}))
    assert kernel.memory is None
    assert kernel.tools._memory is None


def test_memory_unknown_field_rejected():
    """严格先例(同 _prices/_credentials):dir 拼错会静默写到别的目录,宁可装配期炸掉。"""
    with pytest.raises(ConfigError, match=r"\[memory\] 含未知字段"):
        build_kernel(_base_cfg(memory={"dirs": "./memory"}))


def test_memory_write_search_roundtrip_via_kernel(tmp_path):
    """kernel 级闭环:build_kernel 带 [memory] → memory_write → memory_search 可检索。"""
    kernel = build_kernel(
        _base_cfg(
            tools={"builtins": True, "python_exec": "subprocess"},
            memory={"dir": str(tmp_path / "memory")},
        )
    )
    ctx = ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=["system.memory.write"],  # WRITE 档占帧白名单;READ 档不占
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=tmp_path,
    )

    async def main():
        written = await kernel.tools.dispatch(
            ToolCall(
                id="w",
                name="system.memory.write",
                args={"content": "flaky 测试先复跑再判失败", "tags": ["lesson"]},
            ),
            ctx,
        )
        found = await kernel.tools.dispatch(
            ToolCall(id="s", name="system.memory.search", args={"query": "flaky"}), ctx
        )
        return written, found

    written, found = asyncio.run(main())
    assert written.ok, written.error
    assert found.ok, found.error
    assert any("flaky" in r["content"] for r in found.value["results"]), "写入后应可检索闭环"

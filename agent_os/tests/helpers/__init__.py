"""测试公共件包(非测试文件,pytest 不在此收集)。

- ``brains``:可经 dotted path 引用的 MockProvider 应答函数;
- ``code_skills``:code 技能 handlers(dotted path 惰性加载);
- ``kernels``:内核组装快捷方式(sandbox 工具注册表 + KernelBuilder 链);
- ``config``:agent-os.toml 写入 / CLI 调用;
- ``web``:Web TestClient 工厂与轮询。
"""

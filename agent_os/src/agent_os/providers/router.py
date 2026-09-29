"""DefaultModelRouter(docs/DESIGN.md §4.2):ModelRouter 契约的 v1 默认实现。

契约层(``agent_os.api.v1.providers.ModelRouter``)把模型路由定为预留扩展点
("输入任务特征,输出模型与参数;v1 以静态 ``prefer`` 为默认实现")。本类即该
默认实现:KernelBuilder 在 ProviderManager 建成后装配一个,注入三处原内联
解析点(``ContextManager.build`` / ``MinimalContextManager.build`` /
``KernelLogicContext.chat``);宿主可经 ``[providers] router = "pkg.mod:Class"``
换成自定义实现(runtime/config.py,dotted path 无参实例化)。

探测语义(逐候选):

- 候选链 = ``[*prefer, config_model]``(去重保序,空串剔除)——技能 manifest 的
  ``model.prefer`` 在前,``RunConfig.model`` 兜底,与原三处内联解析的候选序
  逐字一致(``ModelPolicy``: "prefer 按序探测能力匹配");
- ``ProviderManager.resolve`` 失败(前缀未注册,``ProviderError``)→ 跳过;
- ``req.tools`` 非空 → 要求 ``supports_tools``;``req.messages`` 任一
  ``m.parts`` 非空 → 要求 ``supports_vision``(多模态消息,WS1);
- 首个全过的候选 → ``(candidate, {"temperature": req.temperature})``
  (v1 不改写采样参数,原样直通)。

**fail-open**:全不过时不抛错——记 warning 并返回 ``(链首, {...})``,保持
"模型串原样发出去、由 ProviderManager 在调用期硬报错"的现状语义(路由是择优
不是闸门,错误归位不变);链为空(prefer 与 config_model 都缺)→ 返回
``("", {...})``,上游照旧(裸装配)。契约层协议(api/v1/providers.py)本文件不碰。
"""

from __future__ import annotations

import logging
from typing import Any

from agent_os.api.v1 import ChatRequest, ProviderError

_log = logging.getLogger(__name__)


class DefaultModelRouter:
    """``agent_os.api.v1.ModelRouter`` 协议的 v1 默认实现(静态 prefer 链 + caps 探测)。

    ``mgr``:ProviderManager(或同形:``resolve(model) -> Provider``);``config_model``
    为装配期快照(``RunConfig.model``),作候选链兜底。语义见模块 docstring。
    """

    def __init__(self, mgr: Any, config_model: str = "") -> None:
        self._mgr = mgr
        self._config_model = config_model or ""

    def _candidates(self, prefer: list[str] | None) -> list[str]:
        """候选链:``[*prefer, config_model]`` 去重保序,空串剔除。"""
        chain: list[str] = []
        for candidate in [*(prefer or []), self._config_model]:
            if candidate and candidate not in chain:
                chain.append(candidate)
        return chain

    async def route(
        self, req: ChatRequest, prefer: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """按候选链择优(语义见模块 docstring);fail-open,不抛错。"""
        chain = self._candidates(prefer)
        needs_tools = bool(req.tools)
        needs_vision = any(m.parts for m in req.messages)
        for candidate in chain:
            try:
                provider = self._mgr.resolve(candidate)
            except ProviderError:
                continue  # 前缀未注册 → 下一候选(最终全不过走 fail-open,语义不变)
            caps = provider.capabilities()
            if needs_tools and not caps.supports_tools:
                continue
            if needs_vision and not caps.supports_vision:
                continue
            return candidate, {"temperature": req.temperature}
        if not chain:
            # prefer 与 config_model 都缺:裸装配现状(model="" 原样上送,调用期硬报错)
            return "", {"temperature": req.temperature}
        _log.warning(
            "模型路由 fail-open:候选链 %s 全部不可用(前缀未注册或 caps 不满足 "
            "tools=%s vision=%s),回落链首 %r——请求原样上送,由 ProviderManager 调用期报错",
            chain,
            needs_tools,
            needs_vision,
            chain[0],
        )
        return chain[0], {"temperature": req.temperature}

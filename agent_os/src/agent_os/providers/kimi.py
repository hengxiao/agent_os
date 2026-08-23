"""KimiProvider(docs/DESIGN.md §4.3;Moonshot AI Kimi 接口适配)。

Kimi API 是 OpenAI 兼容协议(官方文档:`POST {base_url}/chat/completions`,
支持 tool use 与 thinking 模型的 ``reasoning_content``),故本适配器是
:class:`~agent_os.providers.openai_compatible.OpenAICompatibleProvider` 的
预配置子类:

- ``name = "kimi"``(模型路由前缀,如 ``kimi/kimi-k2-thinking`` → 实际 model ``kimi-k2-thinking``);
- 默认 base_url = ``https://api.moonshot.ai/v1``(国际站;国内站传
  ``https://api.moonshot.cn/v1``);
- API key 优先取构造参数,其次环境变量 ``MOONSHOT_API_KEY`` / ``KIMI_API_KEY``;
- 思维链 round-trip 已由父类实现(``reasoning_content`` 逐字回传,
  Kimi K2/Thinking 系列不回传会报错,§4.1 cot_protocol)。
"""

from __future__ import annotations

import os

import httpx

from agent_os.api.v1 import ProviderCaps
from agent_os.providers.openai_compatible import OpenAICompatibleProvider

#: 国际站默认端点;国内站 = ``https://api.moonshot.cn/v1``
DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"


class KimiProvider(OpenAICompatibleProvider):
    """Moonshot Kimi 接口(OpenAI 兼容协议,§4.3 内置适配器)。

    key 解析(2026-08-24,15 分钟 OAuth token 教训):显式 api_key 钉死;
    未显式给则**每次调用现读环境变量**——run-web.sh 的 token_refresh 线程
    会周期续期 os.environ["MOONSHOT_API_KEY"],若像基类一样构造期冻结,
    长跑进程 15 分钟后必然 401(generate 链实测反复踩中)。"""

    name: str = "kimi"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        *,
        name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(base_url=base_url, api_key=api_key, name=name or self.name, client=client)
        self._dynamic_key = api_key is None  # 未显式给 key → 动态解析(见类注)

    @property
    def api_key(self) -> str | None:
        if getattr(self, "_dynamic_key", False):
            return os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY")
        return getattr(self, "_pinned_key", None)

    @api_key.setter
    def api_key(self, v: str | None) -> None:
        self._pinned_key = v

    def capabilities(self) -> ProviderCaps:
        caps = super().capabilities()
        caps.supports_json_mode = True
        return caps


__all__ = ["DEFAULT_BASE_URL", "KimiProvider"]

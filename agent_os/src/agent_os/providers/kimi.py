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
    """Moonshot Kimi 接口(OpenAI 兼容协议,§4.3 内置适配器)。"""

    name: str = "kimi"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        *,
        name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key or os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY")
        super().__init__(base_url=base_url, api_key=key, name=name or self.name, client=client)

    def capabilities(self) -> ProviderCaps:
        caps = super().capabilities()
        caps.supports_json_mode = True
        return caps


__all__ = ["DEFAULT_BASE_URL", "KimiProvider"]

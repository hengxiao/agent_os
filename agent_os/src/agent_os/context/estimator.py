"""token 估算器(docs/DESIGN.md §7.6;M3)。

char/4 粗估 + 按 provider 校准系数 + 多模态口径(图像 part 按 ``IMAGE_PART_TOKENS``
统一粗估,无分辨率数据);provider caps 带 ``token_counter`` 时经它精确计数
(§4.2 "优先用 provider 精确 tokenizer,否则统一估算器(口径唯一)")。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_os.api.v1 import Message, ProviderError

_log = logging.getLogger(__name__)

#: 每条消息的固定开销(token;角色/字段等元数据粗估)
PER_MESSAGE_OVERHEAD = 4

#: 单个多模态 part 的粗估 token 数(WS1):无分辨率数据的统一口径;
#: 精确计量留开口给 provider usage / ``token_counter``(§4.2 口径唯一)
IMAGE_PART_TOKENS = 1024


class TokenEstimator:
    """统一估算器:Context 子系统与 ProviderManager 共用同一口径(§4.2)。

    精确口径挂点::meth:`bind_providers` 注入 ProviderManager 后,
    :meth:`estimate` 带 ``model`` 且该 model 前缀可 resolve、provider caps 带
    ``token_counter``(签名约定 ``Callable[[str], int]``,text → tokens)时,
    文本部分(content 与 tool_calls 参数 JSON)经 counter 精确计数;counter 缺席、
    模型前缀未注册或 counter 调用抛错都回 char/4 粗估——估算绝不杀 run。
    """

    def __init__(self, calibration: float = 1.0) -> None:
        self.calibration = calibration
        #: ProviderManager(可选,经 :meth:`bind_providers` 注入);None = 恒粗估
        self._providers: Any = None

    def bind_providers(self, providers: Any) -> None:
        """注入 ProviderManager(精确口径挂点,§4.2);可重复调用,后者覆盖前者。"""
        self._providers = providers

    def estimate_text(self, text: str) -> int:
        """char/4 粗估(§7.6);空串也计 1(消息存在本身有成本)。"""
        return max(1, len(text) // 4)

    def estimate_message(self, msg: Message) -> int:
        """单条消息 = 固定开销 + content 估算 + tool_calls 参数 JSON 长度折算
        + parts 多模态折算(``len(parts) * IMAGE_PART_TOKENS``;None 零开销)。"""
        total = PER_MESSAGE_OVERHEAD + self.estimate_text(msg.content or "")
        for call in msg.tool_calls:
            args = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
            total += self.estimate_text(args)
        if msg.parts:
            total += len(msg.parts) * IMAGE_PART_TOKENS
        return int(total * self.calibration)

    def _counter_for(self, model: str) -> Any:
        """解析 model 对应 provider 的精确 counter;任一环缺席 → None(回粗估)。

        ``resolve``/``capabilities`` 走 getattr 防御:duck 型 providers(测试替身
        等)缺任一环节都按无 counter 处理——估算绝不杀 run。
        """
        if not model or self._providers is None:
            return None
        resolve = getattr(self._providers, "resolve", None)
        if resolve is None:
            return None
        try:
            provider = resolve(model)
        except ProviderError:
            return None  # 前缀未注册:当无 counter(与 fail-open 同姿势,不杀估算)
        capabilities = getattr(provider, "capabilities", None)
        if capabilities is None:
            return None
        return getattr(capabilities(), "token_counter", None)

    def _estimate_message_precise(self, msg: Message, counter: Any) -> int:
        """counter 精确口径的单条消息:文本部分(content / tool_calls 参数 JSON)
        各经 ``counter(text)``;固定开销、parts 折算与 calibration 与粗估同式。

        parts 维持 ``IMAGE_PART_TOKENS`` 粗估:真实图像 token 只能由 provider
        usage 给出,build 前不可估(counter 签名只吃文本)。
        """
        total = PER_MESSAGE_OVERHEAD + counter(msg.content or "")
        for call in msg.tool_calls:
            args = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
            total += counter(args)
        if msg.parts:
            total += len(msg.parts) * IMAGE_PART_TOKENS
        return int(total * self.calibration)

    def estimate(self, messages: list[Message], model: str = "") -> int:
        """总量估算;``model`` 非空且已绑 providers 时尝试精确口径(见类 docstring)。

        ``model`` 的归属是近似:调用方给候选模型(prefer[0] 或 config.model),
        不经 router 终选——估算只需量级正确,口径分歧在 provider 间本就可接受。
        counter 对某条消息抛错 → 该消息回粗估并记 warning,其余消息仍精确。
        """
        counter = self._counter_for(model)
        if counter is None:
            return sum(self.estimate_message(m) for m in messages)
        total = 0
        for m in messages:
            try:
                total += self._estimate_message_precise(m, counter)
            except Exception as exc:  # noqa: BLE001 — 估算绝不杀 run:该消息回粗估
                _log.warning(
                    "estimator:token_counter 计数失败,该消息回粗估(model=%r): %r",
                    model,
                    exc,
                )
                total += self.estimate_message(m)
        return total

    def per_provider_factor(self, name: str) -> float:
        """按 provider 的校准系数(§7.6 "按 provider 校准系数"的预留接口)。

        各家 tokenizer 口径与 char/4 的系统性偏差在此折算;baseline 恒 1.0,
        真实系数随 ProviderManager 接入精确 tokenizer(§4.2 ``token_counter``)后标定。
        """
        return 1.0

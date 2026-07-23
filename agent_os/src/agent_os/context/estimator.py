"""token 估算器(DESIGN.md §7.6;M3)。

char/4 粗估 + 按 provider 校准系数 + 多模态口径(图像按分辨率公式);
接口预留精确 tokenizer(provider ``token_counter`` 优先,§4.2 口径唯一)。
"""

from __future__ import annotations

import json

from agent_os.api.v1 import Message

#: 每条消息的固定开销(token;角色/字段等元数据粗估)
PER_MESSAGE_OVERHEAD = 4


class TokenEstimator:
    """统一估算器:Context 子系统与 ProviderManager 共用同一口径(§4.2)。"""

    def __init__(self, calibration: float = 1.0) -> None:
        self.calibration = calibration

    def estimate_text(self, text: str) -> int:
        """char/4 粗估(§7.6);空串也计 1(消息存在本身有成本)。"""
        return max(1, len(text) // 4)

    def estimate_message(self, msg: Message) -> int:
        """单条消息 = 固定开销 + content 估算 + tool_calls 参数 JSON 长度折算。"""
        total = PER_MESSAGE_OVERHEAD + self.estimate_text(msg.content or "")
        for call in msg.tool_calls:
            args = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
            total += self.estimate_text(args)
        return int(total * self.calibration)

    def estimate(self, messages: list[Message]) -> int:
        return sum(self.estimate_message(m) for m in messages)

    def per_provider_factor(self, name: str) -> float:
        """按 provider 的校准系数(§7.6 "按 provider 校准系数"的预留接口)。

        各家 tokenizer 口径与 char/4 的系统性偏差在此折算;baseline 恒 1.0,
        真实系数随 ProviderManager 接入精确 tokenizer(§4.2 ``token_counter``)后标定。
        """
        return 1.0

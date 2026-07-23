"""token 估算器(DESIGN.md §7.6;M3)。

char/4 粗估 + 按 provider 校准系数 + 多模态口径(图像按分辨率公式);
接口预留精确 tokenizer(provider ``token_counter`` 优先,§4.2 口径唯一)。
"""

from __future__ import annotations

from agent_os.api.v1 import Message


class TokenEstimator:
    """统一估算器:Context 子系统与 ProviderManager 共用同一口径(§4.2)。"""

    def __init__(self, calibration: float = 1.0) -> None:
        self.calibration = calibration

    def estimate_message(self, msg: Message) -> int:
        raise NotImplementedError("M3")

    def estimate(self, messages: list[Message]) -> int:
        raise NotImplementedError("M3")

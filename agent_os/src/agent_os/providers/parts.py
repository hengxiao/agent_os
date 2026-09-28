"""多模态 parts 的 wire 序列化公共件(WS1;blob→base64 预解析 + 降级占位)。

``BlobStore.get`` 是 async,而各适配器的请求序列化是同步的——故统一在
``chat()``/``stream()`` 开头经 :func:`resolve_image_parts` 预解析出结果表,
同步序列化侧只消费结果表(成功 = wire block;失败/未解析 = 显式占位行)。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import Any

from agent_os.api.v1 import BlobStore, ContentPart, Message

_log = logging.getLogger("agent_os.providers.parts")


async def resolve_image_parts(
    messages: list[Message],
    blob: BlobStore,
    make_block: Callable[[str, str], dict[str, Any]],
) -> dict[int, list[dict[str, Any] | None]]:
    """逐消息预解析 image parts:``blob.get(ref)`` → base64 → ``make_block(mime, b64)``。

    返回 ``{id(message): 与 parts 等长的结果表}``——成功为 wire block,失败为
    None(序列化侧落占位行)。仅带 parts 的消息进表(无 parts 零开销);单 part
    失败记 warning 降级,不炸整请求。键用 ``id(msg)``:适配器过滤消息(如
    Claude 摘出 system)后下标移位,身份键不受影响。
    """
    resolved: dict[int, list[dict[str, Any] | None]] = {}
    for m in messages:
        if not m.parts:
            continue
        blocks: list[dict[str, Any] | None] = []
        for part in m.parts:
            try:
                data = await blob.get(part.ref)
            except Exception:  # noqa: BLE001 — 单 part 失败降级占位,不炸整请求
                _log.warning("part 解析失败(降级占位):ref=%r", part.ref, exc_info=True)
                blocks.append(None)
                continue
            blocks.append(make_block(part.mime, base64.b64encode(data).decode("ascii")))
        resolved[id(m)] = blocks
    return resolved


def placeholders_for(
    parts: list[ContentPart], blocks: list[dict[str, Any] | None] | None
) -> str:
    """解析失败(或未解析)part 的显式占位行,逐 part 一行(``\\n`` 开头,附 content 尾)。

    ``blocks=None``(无 vision caps / blob 未接线,未走预解析)→ 全部 part 占位。
    """
    resolved = blocks if blocks is not None else [None] * len(parts)
    return "".join(
        f"\n[图片 {p.mime} ref={p.ref} 未随请求发送]"
        for p, b in zip(parts, resolved, strict=True)
        if b is None
    )


def image_blocks(blocks: list[dict[str, Any] | None] | None) -> list[dict[str, Any]]:
    """结果表中成功解析的 wire block(保持 parts 顺序)。"""
    return [b for b in blocks or [] if b is not None]

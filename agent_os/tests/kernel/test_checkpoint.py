"""WS1 锚点测试:checkpoint 的 Message.parts 往返(kernel/checkpoint.py)。

固定约定:

- ``_message_to_dict``:parts 非空才落 ``"parts"`` 键(additive,schema v1 不变);
- ``_message_from_dict``:旧档(无 parts 键)读出 None;有键逐条重建 ContentPart
  (逐键缺省容错,同 tool_calls 口径)。
"""

from __future__ import annotations

import json

from agent_os.api.v1 import ContentPart, Message, Role
from agent_os.kernel.checkpoint import _message_from_dict, _message_to_dict


def test_message_parts_checkpoint_round_trip():
    """带 parts 的消息:dump → JSON → load 后 parts 逐字段一致;无 parts 不落键。"""
    msg = Message(
        role=Role.USER,
        content="看这张图",
        parts=[ContentPart(mime="image/jpeg", ref="blob://run-1/abc")],
    )
    data = _message_to_dict(msg)
    assert data["parts"] == [{"type": "image", "mime": "image/jpeg", "ref": "blob://run-1/abc"}]
    restored = _message_from_dict(json.loads(json.dumps(data)))  # 经 JSON 层往返
    assert restored.parts == msg.parts

    plain = _message_to_dict(Message(role=Role.USER, content="纯文本"))
    assert "parts" not in plain, "parts 为空/None 时不落键(additive)"
    assert _message_from_dict(plain).parts is None


def test_message_from_dict_legacy_checkpoint_without_parts():
    """旧档(无 parts 键)读出 parts=None;条目缺键逐键取缺省(同 tool_calls 口径)。"""
    legacy = {"role": "user", "content": "hi", "source": "external"}
    assert _message_from_dict(legacy).parts is None
    partial = _message_from_dict({**legacy, "parts": [{"ref": "blob://r/x"}]})
    assert partial.parts == [ContentPart(ref="blob://r/x")]

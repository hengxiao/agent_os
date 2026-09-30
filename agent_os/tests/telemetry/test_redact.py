"""PII 脱敏 hook 锚点测试(telemetry/redact.py + sink 接线;docs/DESIGN.md §10.2)。

固定约定:

- 五形态 regex 快筛:email/phone_cn/id_card_cn/bank_card/api_key → ``[KIND]``
  大写占位;重叠区间按模式表序先到先得(18 位身份证先于 16-19 位卡号认领);
- ``redact_payload`` 递归 walk dict/list/tuple,str 脱敏,其余类型原样;
  幂等;容器重建新对象(不改写入参);dict 键不脱敏(WAL schema 稳定);
- sink 接线:``JsonlTelemetrySink(redact=True)``(§10.2 逐字"默认关闭")时
  WAL 行与注册的 exporter 收**同一份**脱敏副本;缺省 redact=False 逐字保真。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from agent_os.api.v1 import Signal
from agent_os.telemetry import JsonlTelemetrySink, redact_payload

# ---------------------------------------------------------------------------
# 五形态快筛
# ---------------------------------------------------------------------------


def test_email_masked():
    assert redact_payload("联系我 boss@corp.com 谢谢") == "联系我 [EMAIL] 谢谢"


def test_phone_cn_masked():
    assert redact_payload("电话 13812345678 备用") == "电话 [PHONE_CN] 备用"
    # 12 位/非 1[3-9] 开头不误伤
    assert redact_payload("编号 12345678901 保留") == "编号 12345678901 保留"


def test_id_card_cn_masked():
    assert redact_payload("身份证 11010119900307777X") == "身份证 [ID_CARD_CN]"


def test_bank_card_masked():
    assert redact_payload("卡号 6222020200112233445") == "卡号 [BANK_CARD]"


def test_api_key_masked():
    assert redact_payload("key = sk-ABCDEFGHIJKLMNOP1234") == "key = [API_KEY]"
    assert redact_payload("AKIAIOSFODNN7EXAMPLE 泄露") == "[API_KEY] 泄露"
    assert redact_payload("Bearer abcdefgh12345678.token") == "[API_KEY]"


def test_overlap_id_card_wins_over_bank_card():
    """18 位身份证命中 16-19 位卡号区间:表序在前者认领(只脱敏一次,不双重占位)。"""
    assert redact_payload("id 110101199003077776 end") == "id [ID_CARD_CN] end"


# ---------------------------------------------------------------------------
# 递归 walk / 类型纪律 / 幂等
# ---------------------------------------------------------------------------


def test_nested_dict_list_tuple_walked():
    payload = {
        "args": {"text": "邮箱 a@b.co", "n": 42, "flag": True, "nil": None},
        "items": ["电话 13812345678", ("嵌套 sk-ABCDEFGHIJKLMNOP1234", 7)],
    }
    out = redact_payload(payload)
    assert out["args"]["text"] == "邮箱 [EMAIL]"
    assert out["args"]["n"] == 42 and out["args"]["flag"] is True and out["args"]["nil"] is None
    assert out["items"][0] == "电话 [PHONE_CN]"
    assert out["items"][1] == ("嵌套 [API_KEY]", 7)
    assert isinstance(out["items"][1], tuple), "tuple 保型"
    assert payload["args"]["text"] == "邮箱 a@b.co", "入参不被改写(容器重建)"


def test_non_strings_untouched_and_keys_preserved():
    payload = {"email_field": 3.14, 2: "a@b.co"}
    out = redact_payload(payload)
    assert out == {"email_field": 3.14, 2: "[EMAIL]"}, "值脱敏;键(字段名)原样保留"
    assert redact_payload(12345) == 12345
    assert redact_payload(None) is None


def test_idempotent():
    once = redact_payload("boss@corp.com / 13812345678 / sk-ABCDEFGHIJKLMNOP1234")
    assert redact_payload(once) == once


def test_payload_shaped_inputs():
    """真实信号载荷形态:pre:tool.call 的 args 与 run.aborted 的 error 串。"""
    pre_tool = {"depth": 1, "frame_id": "f1", "skill": "s", "tool": "system.python.exec",
                "args": {"code": "send('boss@corp.com')"}}
    out = redact_payload(pre_tool)
    assert out["args"]["code"] == "send('[EMAIL]')"
    assert out["tool"] == "system.python.exec"
    aborted = {"error": "RuntimeError: 通知 13812345678 失败"}
    assert redact_payload(aborted) == {"error": "RuntimeError: 通知 [PHONE_CN] 失败"}


# ---------------------------------------------------------------------------
# sink 接线(WAL 与 exporter 同一份脱敏副本)
# ---------------------------------------------------------------------------


@dataclass
class _RecordingExporter:
    """最小 Exporter 假身:记录 export 收到的 Signal。"""

    name: str = "rec"
    seen: list[Signal] = field(default_factory=list)

    async def export(self, sig: Signal) -> None:
        self.seen.append(sig)

    async def close(self) -> None:
        pass


def _record_one(sink: JsonlTelemetrySink, payload: dict[str, Any]) -> None:
    asyncio.run(sink.record(Signal(name="run.aborted", run_id="r1", payload=payload)))


def test_sink_redact_on_masks_wal_and_exporter(tmp_path):
    exporter = _RecordingExporter()
    sink = JsonlTelemetrySink(str(tmp_path), exporters=[exporter], redact=True)
    _record_one(sink, {"error": "请联系 admin@corp.com"})
    line = json.loads((tmp_path / "r1.jsonl").read_text(encoding="utf-8").splitlines()[1])
    assert line["payload"]["error"] == "请联系 [EMAIL]", "WAL 落盘的是脱敏副本"
    assert exporter.seen[0].payload["error"] == "请联系 [EMAIL]", "exporter 收同一份脱敏副本"


def test_sink_redact_default_off_keeps_verbatim(tmp_path):
    exporter = _RecordingExporter()
    sink = JsonlTelemetrySink(str(tmp_path), exporters=[exporter])
    _record_one(sink, {"error": "请联系 admin@corp.com"})
    line = json.loads((tmp_path / "r1.jsonl").read_text(encoding="utf-8").splitlines()[1])
    assert line["payload"]["error"] == "请联系 admin@corp.com", "默认关闭:WAL 逐字保真(§10.2)"
    assert exporter.seen[0].payload["error"] == "请联系 admin@corp.com"

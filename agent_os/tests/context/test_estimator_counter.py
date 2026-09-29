"""token_counter 精确口径挂点测试(docs/DESIGN.md §4.2 "优先用 provider 精确
tokenizer,否则统一估算器(口径唯一)";§7.6)。

固定约定:

- ``TokenEstimator.bind_providers(providers)`` 注入 ProviderManager 后,
  ``estimate(messages, model=...)`` 在该 model 前缀可 resolve 且 provider caps 带
  ``token_counter``(``Callable[[str], int]``,text → tokens)时,文本部分精确计数;
- counter 缺席 / 前缀未注册 / counter 抛错 → 回 char/4 粗估(估算绝不杀 run);
- parts 折算(``IMAGE_PART_TOKENS``)、消息固定开销与 calibration 在两种口径下同式;
- ContextManager 构造时 providers 在场自动绑定 estimator,cap 判定走精确口径。
"""

from __future__ import annotations

import asyncio
import logging
import textwrap

from agent_os.api.v1 import (
    PRE_COMPRESS,
    ContentPart,
    FrameContext,
    Message,
    Role,
    RunConfig,
    Signal,
    SkillFrame,
    SkillRef,
    ToolCall,
)
from agent_os.context.estimator import IMAGE_PART_TOKENS, TokenEstimator
from agent_os.context.manager import ContextManager
from agent_os.context.rolling_window import RollingWindowCompressor
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

CHATTY_YAML = """
skills:
  - name: chatty
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    permissions: { tools: [], skills: [] }
    context_policy: { max_tokens: 200, compress: truncate }
    model: { prefer: ["mock/x"] }
    prompt: 闲聊
"""


def _bound_estimator(*providers: MockProvider, calibration: float = 1.0) -> TokenEstimator:
    """绑了 ProviderManager 的估算器(精确口径挂点)。"""
    est = TokenEstimator(calibration=calibration)
    est.bind_providers(ProviderManager(list(providers)))
    return est


def test_counter_used_when_resolvable():
    """counter=lambda s: len(s) 且 model 可 resolve → 文本部分精确值(区别于 char/4)。"""
    est = _bound_estimator(MockProvider(token_counter=len))
    msgs = [Message(role=Role.USER, content="x" * 40)]
    precise = est.estimate(msgs, model="mock/x")
    rough = TokenEstimator().estimate(msgs)
    assert precise == 4 + 40  # 固定开销 + counter(content)
    assert rough == 4 + 40 // 4
    assert precise != rough


def test_counter_absent_falls_back_to_rough_verbatim():
    """caps 无 token_counter → 粗估,与无 bind 的值逐字相等。"""
    est = _bound_estimator(MockProvider())
    msgs = [
        Message(role=Role.SYSTEM, content="指令"),
        Message(role=Role.USER, content="x" * 400),
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[ToolCall(id="c1", name="t", args={"a": 1})],
        ),
    ]
    assert est.estimate(msgs, model="mock/x") == TokenEstimator().estimate(msgs)
    assert est.estimate(msgs, model="mock/x") == est.estimate(msgs)  # 无 model 同值


def test_counter_error_falls_back_per_message(caplog):
    """counter 对某条消息抛错 → 该消息回粗估 + warning,其余消息仍精确,不炸。"""
    def counter(text: str) -> int:
        if text == "boom":
            raise RuntimeError("counter 故障")
        return len(text)

    est = _bound_estimator(MockProvider(token_counter=counter))
    msgs = [
        Message(role=Role.USER, content="ok"),  # 精确:4 + 2
        Message(role=Role.USER, content="boom"),  # 回粗估:4 + max(1, 4//4)
    ]
    with caplog.at_level(logging.WARNING, logger="agent_os.context.estimator"):
        total = est.estimate(msgs, model="mock/x")
    assert total == (4 + 2) + (4 + 1)
    assert any("token_counter" in r.getMessage() for r in caplog.records)


def test_counter_dispatch_by_model():
    """按 model 分流:第二 provider 才有 counter → model=第二精确、model=第一粗估、
    model 前缀未注册也粗估。"""
    est = _bound_estimator(
        MockProvider(name="plain"),
        MockProvider(name="vis", token_counter=len),
    )
    msgs = [Message(role=Role.USER, content="x" * 40)]
    rough = TokenEstimator().estimate(msgs)
    assert est.estimate(msgs, model="vis/y") == 4 + 40
    assert est.estimate(msgs, model="plain/x") == rough
    assert est.estimate(msgs, model="ghost/z") == rough  # 前缀未注册 → ProviderError → 粗估


def test_image_parts_surcharge_kept_with_counter():
    """counter 在场 parts 也维持 IMAGE_PART_TOKENS 粗估(真实图像 token 只有
    provider usage 能给,build 前不可估)。"""
    est = _bound_estimator(MockProvider(token_counter=len))
    msg = Message(role=Role.USER, content="x" * 40, parts=[ContentPart(), ContentPart()])
    assert est.estimate([msg], model="mock/x") == 4 + 40 + 2 * IMAGE_PART_TOKENS


def test_calibration_still_applies_with_counter():
    """calibration 仍乘在精确总量上(与粗估同式)。"""
    est = _bound_estimator(MockProvider(token_counter=len), calibration=2.0)
    msgs = [Message(role=Role.USER, content="x" * 40)]
    assert est.estimate(msgs, model="mock/x") == int((4 + 40) * 2.0)


# ---------------------------------------------------------------------------
# ContextManager 集成:cap 判定走精确口径
# ---------------------------------------------------------------------------


class _RecordingBus:
    def __init__(self) -> None:
        self.seen: list[Signal] = []

    def subscribe(self, pattern, handler) -> None: ...

    async def emit(self, sig: Signal):
        self.seen.append(sig)
        return []


def _registry(tmp_path) -> LocalFileSkillRegistry:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(CHATTY_YAML), encoding="utf-8")
    return LocalFileSkillRegistry(str(tmp_path / "skills.yaml"))


def _manager(reg, providers: ProviderManager | None) -> tuple[ContextManager, _RecordingBus]:
    bus = _RecordingBus()
    mgr = ContextManager(
        skills=reg,
        tools=LocalPythonToolRegistry(),
        config=RunConfig(compression="truncate", max_cost=2.0),
        compressor=RollingWindowCompressor(),
        providers=providers,
        estimator=TokenEstimator(),
        signals=bus,
        default_max_tokens=4000,
        target_ratio=0.5,
    )
    return mgr, bus


def _frame(messages: list[Message], pinned: list[str] | None = None) -> SkillFrame:
    return SkillFrame(
        frame_id="f1",
        run_id="r1",
        skill=SkillRef(name="chatty"),
        input={},
        context=FrameContext(messages=list(messages), pinned=pinned or []),
    )


def _diverging_messages() -> list[Message]:
    """粗估/精确分歧的消息集(counter=len 时):sys 常驻 + 一条会被整组驱逐的
    大消息 + 一条小消息(驱逐后精确值回落 cap 内,不触 §7.1 硬上限)。

    - 粗估:5 + 104 + 5 = 114 ≤ cap 200 → 不触发压缩;
    - 精确(counter=len):6 + 404 + 8 = 418 > cap 200 → 触发压缩;
      驱逐大消息后精确 6 + 8 = 14 ≤ 200。
    """
    return [
        Message(role=Role.SYSTEM, content="指令", meta={"id": "sys-0"}),
        Message(role=Role.USER, content="x" * 400),
        Message(role=Role.USER, content="y" * 4),
    ]


def test_maintain_cap_uses_precise_counter(tmp_path):
    """cap 判定走精确口径:同一消息集粗估 114 ≤ cap 200 不压,精确 418 > 200 触发压缩。"""
    reg = _registry(tmp_path)
    providers = ProviderManager([MockProvider(token_counter=len)])
    mgr, bus = _manager(reg, providers)
    frame = _frame(_diverging_messages(), pinned=["sys-0"])

    asyncio.run(mgr.maintain(frame))

    pre = [s for s in bus.seen if s.name == PRE_COMPRESS]
    assert len(pre) == 1, "精确口径 418 > cap 200,必须触发压缩"
    assert pre[0].payload["estimate"] == 6 + 404 + 8  # pre 载荷是精确估算值
    assert pre[0].payload["cap"] == 200
    assert frame.context.token_estimate == 6 + 8  # 驱逐大消息后精确重估
    assert any(m.meta.get("id") == "sys-0" for m in frame.context.messages)  # pinned 永驻


def test_maintain_cap_rough_without_counter(tmp_path):
    """对照组:同一水位无 counter → 粗估 114 ≤ cap 200,不触发压缩。"""
    reg = _registry(tmp_path)
    providers = ProviderManager([MockProvider()])
    mgr, bus = _manager(reg, providers)
    frame = _frame(_diverging_messages(), pinned=["sys-0"])

    asyncio.run(mgr.maintain(frame))

    assert not any(s.name == PRE_COMPRESS for s in bus.seen)
    assert frame.context.token_estimate == 5 + 104 + 5  # 估算照常更新(粗估口径)

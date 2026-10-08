"""SessionRunner(P2-M1)锚点测试:线程模型/问答通道/pause-resume 重问/插话/chunk 扇出/store 联动。

固定约定(docs/RUNNERS.md §2 宿主契约;host/coding_cli/session.py 模块 docstring):

- run 在 worker 线程(execute_run/execute_resume 阻塞式 asyncio.run);UI 侧经
  ``events()`` 队列消费渲染事件(类型枚举稳定),经 ``pending_questions()``/
  ``answer()`` 结算问答——跨线程结算走 loop.call_soon_threadsafe(InboxChannel 先例);
- pause/stop 除 ctl 置旗外把 RunPaused/RunAborted 注入收件箱挂起等待点
  (fail_all):挂起期间未决问题保留进 checkpoint,resume 以新 question_id 重问;
- 所有等待带 timeout(threading 代码防 flaky),轮询用 10ms 间隔。

mock brain 为模块级函数(dotted-path 加载协议,同 tests/helpers/brains.py 先例)。
"""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path
from typing import Any

import pytest

from agent_os.api.v1 import ChatChunk, ChatResponse, ChatUsage, Message, Role, ToolCall
from agent_os.host.coding_cli import SessionRunner, SessionStore
from tests.helpers.config import write_config as _write_config

# ---------------------------------------------------------------------------
# mock brain(模块级:dotted-path 加载协议)
# ---------------------------------------------------------------------------


def _final(payload: dict) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _calls(tc: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _tool_calls_of(req) -> list[str]:
    return [
        tc.name
        for m in req.messages
        if m.role is Role.ASSISTANT
        for tc in m.tool_calls
    ]


def ask_brain(req) -> ChatResponse:
    """先 ask_supervisor(带 options),再按裁决给 decision(test 1/3/6 用)。"""
    if "ask_supervisor" not in _tool_calls_of(req):
        return _calls(
            ToolCall(
                id="c1",
                name="ask_supervisor",
                args={
                    "question": "批准上线吗?",
                    "context": {"amount": 1},
                    "options": ["approve", "reject"],
                },
            )
        )
    result = json.loads(next(m.content for m in reversed(req.messages) if m.role is Role.TOOL))
    answer = result["value"]["answer"] if result.get("ok") else "fallback"
    return _final({"decision": answer})


def user_channel_brain(req) -> ChatResponse:
    """先 notify(单向)再 user.ask(挂起等答复),按答复收尾(test 2 用)。"""
    calls = _tool_calls_of(req)
    if "system.user.notify" not in calls:
        return _calls(ToolCall(id="n1", name="system.user.notify", args={"message": "开始处理"}))
    if "system.user.ask" not in calls:
        return _calls(ToolCall(id="u1", name="system.user.ask", args={"question": "要加默认值吗?"}))
    result = json.loads(next(m.content for m in reversed(req.messages) if m.role is Role.TOOL))
    return _final({"answer": result["value"]})


#: inject_brain 的观察记录(模块级状态,同 cut_brain 先例;每个用例前 reset)
INJECT_SEEN: list[bool] = []

#: test 4 注入文本的哨兵值
INJECT_MARKER = "中途插话标记-P2M1"


def inject_brain(req) -> ChatResponse:
    """第一步 user.ask 挂起(给 UI 注入窗口);下一步上下文应含注入文本的批头消息。"""
    if "system.user.ask" not in _tool_calls_of(req):
        return _calls(ToolCall(id="u1", name="system.user.ask", args={"question": "等我一下"}))
    seen = any(
        INJECT_MARKER in (m.content or "") for m in req.messages if m.role is Role.USER
    )
    INJECT_SEEN.append(seen)
    return _final({"answer": "好了" if seen else "没看到"})


# ---------------------------------------------------------------------------
# 组装与等待辅助(所有等待带 timeout,防 flaky)
# ---------------------------------------------------------------------------

SKILLS_YAML = """
skills:
  - name: test.ask
    version: 1.0.0
    kind: prompt
    description: 向上级请示。Use when 需要裁决。
    inputs:
      type: object
      properties: { amount: { type: integer } }
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions: { tools: [ask_supervisor], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: 先请示再回答。
  - name: test.user
    version: 1.0.0
    kind: prompt
    description: 与用户对话。Use when 需要用户作答。
    inputs:
      type: object
      properties: {}
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions: { tools: [system.user.ask, system.user.notify], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: 先通知再提问。
  - name: test.done
    version: 1.0.0
    kind: prompt
    description: 最小收尾技能。Use when 只需跑通一轮。
    inputs:
      type: object
      properties: {}
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4 }
    prompt: 直接收尾。
"""


def _runner(
    tmp_path: Path,
    *,
    brain: str,
    session_id: str | None = None,
    store: SessionStore | None = None,
) -> tuple[SessionRunner, SessionStore]:
    """写技能清单与配置,组一个 SessionRunner(brain 为完整 dotted path)。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    skills = tmp_path / "skills.yaml"
    skills.write_text(SKILLS_YAML, encoding="utf-8")
    cfg = _write_config(tmp_path, brain=brain, skills=skills)
    store = store or SessionStore(tmp_path / "store")
    runner = SessionRunner(
        cfg,
        artifacts_root=tmp_path / "arts",
        session_store=store,
        session_id=session_id,
    )
    return runner, store


def _brain(name: str) -> str:
    """本模块 brain 的 dotted path(模块级函数,importlib 加载协议)。"""
    return f"tests.coding_cli.test_session_runner:{name}"


def _wait_for(pred, *, timeout: float = 5.0, what: str = ""):
    """轮询直到 pred() 为真(10ms 间隔;超时 AssertionError,不给 sleep 死等)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"等待超时({timeout}s): {what}")


def _wait_pending(runner: SessionRunner) -> list[dict[str, Any]]:
    return _wait_for(lambda: runner.pending_questions(), what="pending 问题未出现")


def _wait_end(runner: SessionRunner, *, timeout: float = 10.0):
    """收事件直到 run.end;返回 (run.end 事件, 已见事件列表)。"""
    events = runner.events()
    seen: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=0.1)
        except queue.Empty:
            continue
        seen.append(event)
        if event["type"] == "run.end":
            return event, seen
    raise AssertionError(f"run.end 未到达;已见 {[e['type'] for e in seen]}")


# ---------------------------------------------------------------------------
# 1. supervisor inbox 问答闭环
# ---------------------------------------------------------------------------


def test_supervisor_question_answer_roundtrip(tmp_path):
    runner, _ = _runner(tmp_path, brain=_brain("ask_brain"))
    runner.start("test.ask", {"amount": 1})

    pending = _wait_pending(runner)
    row = pending[0]
    assert row["channel"] == "supervisor"
    assert row["question"] == "批准上线吗?"
    assert row["options"] == ["approve", "reject"]

    assert runner.answer(row["question_id"], "approve") is True
    end, seen = _wait_end(runner)
    assert end["status"] == "done", end.get("error")
    assert end["result"] == {"decision": "approve"}
    # 事件契约:question 事件先于 run.end;帧进出事件在场
    types = [e["type"] for e in seen]
    assert "question" in types and types.index("question") < types.index("run.end")
    assert "frame.push" in types and "frame.pop" in types
    qevent = next(e for e in seen if e["type"] == "question")
    assert qevent["channel"] == "supervisor" and qevent["question_id"] == row["question_id"]


# ---------------------------------------------------------------------------
# 2. InboxUserChannel:ask 跨线程结算 + notify 进渲染队列
# ---------------------------------------------------------------------------


def test_user_channel_ask_and_notify(tmp_path):
    runner, _ = _runner(tmp_path, brain=_brain("user_channel_brain"))
    runner.start("test.user", {})

    pending = _wait_pending(runner)
    row = pending[0]
    assert row["channel"] == "user"
    assert row["question"] == "要加默认值吗?"
    assert row["question_id"].startswith("user-")

    assert runner.answer(row["question_id"], "要") is True
    end, seen = _wait_end(runner)
    assert end["status"] == "done", end.get("error")
    assert end["result"] == {"answer": "要"}
    notify = next(e for e in seen if e["type"] == "notify")
    assert notify["text"] == "开始处理"
    # notify(单向)先于 question(挂起)到达(brain 调用序)
    types = [e["type"] for e in seen]
    assert types.index("notify") < types.index("question")


# ---------------------------------------------------------------------------
# 3. pause → checkpoint/turn 落盘 → resume 重问(新 question_id)→ 答 → done
# ---------------------------------------------------------------------------


def test_pause_resume_reasks_with_new_question_id(tmp_path):
    store = SessionStore(tmp_path / "store")
    runner, store = _runner(tmp_path, brain=_brain("ask_brain"), session_id="s3", store=store)
    runner.start("test.ask", {"amount": 5})

    first = _wait_pending(runner)[0]
    assert runner.pause("先停停") is True
    end, _ = _wait_end(runner)
    assert end["status"] == "paused", end.get("error")
    run_id = end["run_id"]
    assert (tmp_path / "arts" / "runs" / run_id / "checkpoint.json").is_file()

    # SessionStore turn 落盘:status=paused、checkpoint_path 非空(resume 锚点)
    doc = store.load("s3")
    turn = doc["turns"][-1]
    assert turn["status"] == "paused"
    assert turn["checkpoint_path"], "paused turn 须带 checkpoint_path"
    assert Path(turn["checkpoint_path"]).is_file()
    assert turn["input"] == {"amount": 5}

    # 挂起清空了收件箱(pending 随 RunPaused 收场);resume 后以新 question_id 重问
    assert runner.pending_questions() == []
    runner.resume()
    second = _wait_pending(runner)[0]
    assert second["question"] == "批准上线吗?", "resume 应重问同一问题"
    assert second["question_id"] != first["question_id"], "重问须用新 question_id(_settle_pending_ask)"

    assert runner.answer(second["question_id"], "reject") is True
    end2, _ = _wait_end(runner)
    assert end2["status"] == "done", end2.get("error")
    assert end2["result"] == {"decision": "reject"}
    assert end2["run_id"] == run_id, "resume 写回原 run"
    doc = store.load("s3")
    assert [t["status"] for t in doc["turns"]] == ["paused", "done"]


# ---------------------------------------------------------------------------
# 4. inject_user_message:批头消息进根帧上下文
# ---------------------------------------------------------------------------


def test_inject_user_message_reaches_next_step(tmp_path):
    INJECT_SEEN.clear()
    runner, _ = _runner(tmp_path, brain=_brain("inject_brain"))
    runner.start("test.user", {})

    pending = _wait_pending(runner)  # brain 挂在 user.ask 上,run 保持 running
    assert runner.inject_user_message(INJECT_MARKER) is True
    assert runner.answer(pending[0]["question_id"], "好了") is True

    end, _ = _wait_end(runner)
    assert end["status"] == "done", end.get("error")
    assert INJECT_SEEN == [True], "注入文本应经批头消息出现在下一步上下文(working 事件队列排干)"


# ---------------------------------------------------------------------------
# 5. chunk 扇出:流式脚本 → 保序 chunk 事件
# ---------------------------------------------------------------------------


def test_chunk_events_fanout_in_order(tmp_path, monkeypatch):
    """流式 chunk 经 "*" 订阅行化进事件队列(seq/text 保序)。

    配置流的 mock provider 无 stream_scripts 入口(装配层只收 brain dotted path),
    测试经 monkeypatch 包一层装配钩子换流式 provider(host 侧
    ``artifacts.PeriodicCheckpointer`` 替换先例);生产路径不受影响。
    """
    import agent_os.host.coding_cli.session as session_mod
    from agent_os.providers.manager import ProviderManager
    from agent_os.providers.mock import MockProvider

    real_build_kernel = session_mod.build_kernel
    script = [
        ('{"done": tr', 0.0),
        ('ue}', 0.0),
        ChatChunk(finish_reason="stop", usage=ChatUsage(prompt=1, completion=1)),
    ]

    def _build_with_stream(config: Any, **kw: Any):
        kernel = real_build_kernel(config, **kw)
        kernel.providers = ProviderManager([MockProvider(name="mock", stream_scripts=[script])])
        return kernel

    monkeypatch.setattr(session_mod, "build_kernel", _build_with_stream)
    runner, _ = _runner(tmp_path, brain=_brain("ask_brain"))  # brain 不走 chat(流式消费)
    runner.start("test.done", {})

    end, seen = _wait_end(runner)
    assert end["status"] == "done", end.get("error")
    assert end["result"] == {"done": True}
    chunks = [e for e in seen if e["type"] == "chunk"]
    assert [c["seq"] for c in chunks] == [0, 1, 2]
    assert [c["text"] for c in chunks] == ['{"done": tr', 'ue}', ""]
    assert all(c["skill"] for c in chunks)


# ---------------------------------------------------------------------------
# 6. 无 ctl 退化:pause/stop/inject 返回 False 不崩
# ---------------------------------------------------------------------------


def test_pause_inject_stop_degrade_without_ctl(tmp_path):
    runner, _ = _runner(tmp_path, brain=_brain("ask_brain"))
    # 无活跃 run:全部 False(防御)
    assert runner.pause() is False
    assert runner.stop() is False
    assert runner.inject_user_message("x") is False

    runner.start("test.ask", {"amount": 1})
    _wait_pending(runner)
    runner._kernel.ctl = None  # 模拟不塞 _CtlBridge 的装配形态(ctl 缺席)
    assert runner.pause() is False
    assert runner.stop() is False
    assert runner.inject_user_message("x") is False
    # ctl 缺席不改变问答通道:答掉让 run 正常走完(不留悬挂 worker 线程)
    assert runner.answer(runner.pending_questions()[0]["question_id"], "approve") is True
    end, _ = _wait_end(runner)
    assert end["status"] == "done", end.get("error")


# ---------------------------------------------------------------------------
# 7. SessionStore 联动:done turn 记录
# ---------------------------------------------------------------------------


def test_done_turn_appended_to_store(tmp_path):
    store = SessionStore(tmp_path / "store")
    runner, store = _runner(tmp_path, brain="tests.helpers.brains:done_brain", session_id="s7", store=store)
    runner.start("test.done", {})

    end, _ = _wait_end(runner)
    assert end["status"] == "done", end.get("error")
    doc = store.load("s7")
    assert doc["skill"] == "test.done", "会话文档应在首轮惰性创建"
    assert len(doc["turns"]) == 1
    turn = doc["turns"][0]
    assert turn["run_id"] == end["run_id"]
    assert turn["status"] == "done"
    assert turn["summary"], "summary 非空"
    assert turn["checkpoint_path"] is None, "done 不带 resume 锚点"


def test_resume_without_paused_turn_rejected(tmp_path):
    """resume 边界:无 paused turn → RuntimeError;纯内存会话(session_id=None)→ RuntimeError。"""
    store = SessionStore(tmp_path / "store")
    runner, store = _runner(tmp_path, brain=_brain("ask_brain"), session_id="s8", store=store)
    store.create("s8", "test.done", "")
    with pytest.raises(RuntimeError, match="没有可恢复的 paused turn"):
        runner.resume()

    ephemeral, _ = _runner(tmp_path / "ephemeral", brain=_brain("ask_brain"))
    with pytest.raises(RuntimeError, match="纯内存会话"):
        ephemeral.resume()

"""R4 锚点测试:Web RCA(RUNNERS.md §4.3/§4.4;失败定位 + usage 面板 + stop/resume/reload)。

固定约定:

- ``GET /api/runs/{id}/rca``:首个错误的结构化定位——
  ``first_error: {"kind": "vetoed"|"tool_error"|"aborted", "frame_id", "skill",
  "call": {"name","args"}|null, "message"}``;veto/工具错误从 checkpoint 帧上下文的
  错误观察(ok=false 工具结果,按帧入栈序)定位,aborted 取 result.json 的 error;
- ``GET /api/runs/{id}/usage``:run 汇总 + 按帧分列(九字段 Usage 子集);
- ``POST /api/runs/{id}/stop``:RunControl.stop,run 在下一个 safe point 中止;
- ``POST /api/runs/{id}/resume``:从该 run 的 checkpoint.json 恢复;
- ``POST /api/skills/reload``:热重载技能文件;
- 配置新增 ``[sidecars] tool_guard_rules = [[tool, pattern, reason], ...]`` → ToolGuard。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)
from agent_os.host.web.app import create_app
from tests.test_fib_agent import fib_brain

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_YAML = PROJECT_ROOT / "skills" / "skills.yaml"

CONFIG_TEMPLATE = """
[run]
model = "mock/x"
max_depth = 8
max_steps = 200
max_cost = 100.0
compression = "off"

[providers.mock]
brain = "{brain}"

[tools]
builtins = true
python_exec = "subprocess"

[skills]
path = "{skills}"

[telemetry]
dir = "{telemetry}"
{extra}
"""

DANGER_YAML = """
skills:
  - name: danger
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [shell_exec], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10 }
    prompt: 测试用 danger 技能。
"""


# ---------------------------------------------------------------------------
# 测试用 brains(必须是模块级 dotted path 可引用的 callable)
# ---------------------------------------------------------------------------


def danger_brain(req: ChatRequest) -> ChatResponse:
    """先尝试危险命令(被 ToolGuard veto),再给出最终答案。"""
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="shell_exec", args={"command": "rm -rf /"})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def web_loop_brain(req: ChatRequest) -> ChatResponse:
    """无限循环调用(用于 stop 测试)。"""
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            tool_calls=[ToolCall(id="c1", name="python_exec", args={"code": "print(1)"})],
        ),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


_cut_state = {"calls": 0}


def web_cut_brain(req: ChatRequest) -> ChatResponse:
    """第 6 次调用"断电"(用于 resume 测试);其后按 fib_brain 正常应答。"""
    _cut_state["calls"] += 1
    if _cut_state["calls"] == 6:
        raise RuntimeError("模拟断电")
    return fib_brain(req)


def _write_config(tmp_path: Path, *, brain: str, skills: Path | None = None, extra: str = "") -> Path:
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TEMPLATE.format(
            brain=brain,
            skills=skills or SKILLS_YAML,
            telemetry=tmp_path / "traces",
            extra=extra,
        ),
        encoding="utf-8",
    )
    return cfg


def _client(tmp_path: Path, **kw) -> TestClient:
    return TestClient(create_app(_write_config(tmp_path, **kw), artifacts_root=tmp_path / "runs"))


def _wait_status(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "failed", "aborted"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内结束")


# ---------------------------------------------------------------------------
# RCA:veto 定位
# ---------------------------------------------------------------------------


def test_rca_locates_veto(tmp_path):
    """ToolGuard veto 的 run(最终 done)也能定位到首个 veto(§4.4 失败定位)。"""
    extra = (
        "\n[sidecars]\n"
        'tool_guard_rules = [["shell_exec", "rm -rf", "禁止危险命令 rm -rf"]]\n'
    )
    skills = tmp_path / "danger.yaml"
    skills.write_text(DANGER_YAML, encoding="utf-8")
    client = _client(tmp_path, brain="tests.test_web_r4:danger_brain", skills=skills, extra=extra)

    r = client.post("/api/runs", json={"skill": "danger", "input": {}, "wait": True})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    rca = client.get(f"/api/runs/{run_id}/rca").json()
    err = rca["first_error"]
    assert err is not None and err["kind"] == "vetoed"
    assert err["call"]["name"] == "shell_exec"
    assert "禁止危险命令" in err["message"]
    assert err["frame_id"]


def test_rca_aborted_run(tmp_path):
    """fib(10) 深度超限中止 → rca 定位 aborted 与最深帧。"""
    client = _client(tmp_path, brain="tests.test_fib_agent:fib_brain")
    r = client.post("/api/runs", json={"skill": "fib", "input": {"n": 10}, "wait": True})
    run_id = r.json()["run_id"]
    assert r.json()["status"] in ("failed", "aborted")

    rca = client.get(f"/api/runs/{run_id}/rca").json()
    err = rca["first_error"]
    assert err is not None and err["kind"] == "aborted"
    assert "MaxDepthExceeded" in err["message"]


# ---------------------------------------------------------------------------
# usage 面板
# ---------------------------------------------------------------------------


def test_usage_endpoint_per_frame(tmp_path):
    client = _client(tmp_path, brain="tests.test_fib_agent:fib_brain")
    r = client.post("/api/runs", json={"skill": "fib", "input": {"n": 3}, "wait": True})
    run_id = r.json()["run_id"]
    usage = client.get(f"/api/runs/{run_id}/usage").json()
    assert usage["run"]["steps"] == 4
    assert len(usage["frames"]) == 2
    by_depth = sorted(usage["frames"], key=lambda f: f.get("depth", 0))
    assert by_depth[0]["steps"] == 3 and by_depth[1]["steps"] == 1


# ---------------------------------------------------------------------------
# stop / resume / reload
# ---------------------------------------------------------------------------


def test_stop_run(tmp_path):
    """无限循环 run:POST stop → 下一个 safe point 中止(§4.3)。"""
    client = _client(tmp_path, brain="tests.test_web_r4:web_loop_brain")
    r = client.post("/api/runs", json={"skill": "fib", "input": {"n": 2}})
    run_id = r.json()["run_id"]
    time.sleep(0.3)
    r = client.post(f"/api/runs/{run_id}/stop")
    assert r.status_code == 200
    detail = _wait_status(client, run_id)
    assert detail["status"] == "aborted"


def test_resume_run(tmp_path):
    """断电 run 经 POST resume 恢复完成(§4.3)。"""
    _cut_state["calls"] = 0
    client = _client(tmp_path, brain="tests.test_web_r4:web_cut_brain")
    r = client.post("/api/runs", json={"skill": "fib", "input": {"n": 5}, "wait": True})
    run_id = r.json()["run_id"]
    assert r.json()["status"] in ("failed", "aborted")

    r = client.post(f"/api/runs/{run_id}/resume")
    assert r.status_code == 200
    assert r.json()["result"] == {"seq": [0, 1, 1, 2, 3]}


def test_skills_reload(tmp_path):
    skills = tmp_path / "skills.yaml"
    skills.write_text(SKILLS_YAML.read_text(encoding="utf-8"), encoding="utf-8")
    client = _client(tmp_path, brain="tests.test_fib_agent:fib_brain", skills=skills)

    r = client.post("/api/skills/reload")
    assert r.status_code == 200 and r.json()["reloaded"] is False  # mtime 未变

    skills.write_text(
        skills.read_text(encoding="utf-8").replace('version: 1.0.0', 'version: 1.0.1', 1),
        encoding="utf-8",
    )
    bumped = skills.stat().st_mtime + 2  # 粗粒度文件系统上也能区分
    os.utime(skills, (bumped, bumped))
    r = client.post("/api/skills/reload")
    assert r.status_code == 200 and r.json()["reloaded"] is True

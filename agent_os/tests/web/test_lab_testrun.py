"""L3 锚点测试:Skill Lab test-run 与 G4 冒烟(docs/SKILL-DEV.md §1.1/§1.4/§1.5)。

- overlay 装配(docs/SKILL-DEV.md §2.4 所见即所得):test-run 用"生产 + 草稿层
  (草稿优先)"装配真 run,生产 run 不受影响;
- POST test-run:input 直给 / case 文件({input, mock_script?})两形;
  mock_script 走 replay MockProvider(确定性重放);
- GET check:status/result/outputs 校验结果;
- G4:无用例 warn / 通过 info / outputs 不合 fail。
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Role
from agent_os.host.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: brain 看到的 SYSTEM prompt(config 以 dotted path 加载本函数;断言行走它)
SEEN_SYSTEMS: list[str] = []


def lab_brain(req) -> ChatResponse:
    """极简应答:记录 SYSTEM 后交付 {"answer": "ok"}。"""
    SEEN_SYSTEMS.append(req.messages[0].content if req.messages else "")
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "ok"})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


SKILLS_YAML = """
skills:
  - name: lab.weather
    version: 1.0.0
    kind: prompt
    description: 查天气(生产版)。Use when 需要天气;Do not use when 其他。
    inputs:
      type: object
      properties: { city: { type: string } }
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions:
      tools: []
      skills: []
    model: { prefer: ["mock/x"] }
    prompt: |
      生产版 PROMPT-MARK。
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.web.test_lab_testrun:lab_brain"

[tools]
builtins = true
python_exec = "off"

[skills]
path = "{skills}"

[lab]
drafts_root = "{drafts}"
"""

_DRAFT_MANIFEST = {
    "name": "lab.weather",
    "version": "0.1.0",
    "kind": "prompt",
    "description": "查天气(草稿版)。Use when 需要天气;Do not use when 其他。",
    "inputs": {"type": "object", "properties": {"city": {"type": "string"}}},
    "outputs": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    },
    "permissions": {"tools": [], "skills": []},
}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    SEEN_SYSTEMS.clear()
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    c = TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))
    r = c.post("/api/lab/drafts", json={"name": "lab.weather"})
    assert r.status_code == 201, r.text
    c.put(
        "/api/lab/drafts/lab.weather",
        json={"manifest": _DRAFT_MANIFEST, "prompt": "草稿版 DRAFT-MARK。", "handler": None},
    )
    c._tmp_path = tmp_path  # 用例文件落盘用
    return c


def _wait_done(client: TestClient, name: str, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check = client.get(f"/api/lab/drafts/{name}/runs/{run_id}/check").json()
        if check["status"] in ("done", "failed", "aborted"):
            return check
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内结束")


def _put_case(client: TestClient, case: dict, fname: str = "case1.json") -> None:
    tmp_path = client._tmp_path
    tests_dir = tmp_path / "drafts" / "lab.weather" / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / fname).write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# test-run:input / case 两形 + overlay 草稿优先 + outputs 校验
# ---------------------------------------------------------------------------


def test_test_run_input_form_uses_draft_version(client):
    """input 直给试跑:overlay 草稿优先——brain 看到草稿版 prompt(不是生产版);
    run 走完 run_manager 现状(记录/SSE 同面),outputs 校验 ok。"""
    r = client.post("/api/lab/drafts/lab.weather/test-run", json={"input": {"city": "北京"}})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    check = _wait_done(client, "lab.weather", run_id)
    assert check["status"] == "done"
    assert check["result"] == {"answer": "ok"}
    assert check["outputs_check"] == {"ok": True, "error": None}
    assert any("DRAFT-MARK" in s for s in SEEN_SYSTEMS), "试跑必须跑草稿版(overlay 优先)"
    assert not any("PROMPT-MARK" in s for s in SEEN_SYSTEMS)


def test_test_run_case_form_with_mock_script(client):
    """case 文件 + mock_script:replay MockProvider 确定性重放(不碰装配的 brain)。"""
    _put_case(client, {
        "input": {"city": "上海"},
        "mock_script": [
            {
                "message": {"role": "assistant", "content": json.dumps({"answer": "from-mock"})},
                "finish_reason": "stop",
            }
        ],
    })
    r = client.post("/api/lab/drafts/lab.weather/test-run", json={"case": "case1.json"})
    assert r.status_code == 200, r.text
    check = _wait_done(client, "lab.weather", r.json()["run_id"])
    assert check["status"] == "done"
    assert check["result"] == {"answer": "from-mock"}, "mock_script 确定性重放"
    assert check["outputs_check"]["ok"] is True

    # case 不存在 → 404;坏用例名 → 404
    r = client.post("/api/lab/drafts/lab.weather/test-run", json={"case": "no.json"})
    assert r.status_code == 404


def test_test_run_outputs_failure_marks_run_failed(client):
    """outputs 不合(mock 返回缺字段):内核 outputs 校验让 run 失败,check 呈现。"""
    _put_case(client, {
        "input": {},
        "mock_script": [
            # 内核 outputs 校验连败两次才判失败(§3.2 输出修复循环),脚本要供两段
            {"message": {"role": "assistant", "content": json.dumps({"wrong": 1})},
             "finish_reason": "stop"},
            {"message": {"role": "assistant", "content": json.dumps({"wrong": 1})},
             "finish_reason": "stop"},
        ],
    }, fname="bad.json")
    r = client.post("/api/lab/drafts/lab.weather/test-run", json={"case": "bad.json"})
    check = _wait_done(client, "lab.weather", r.json()["run_id"])
    assert check["status"] == "failed"
    assert "outputs" in (check["error"] or "")


# ---------------------------------------------------------------------------
# G4:无用例 warn / 通过 info / outputs 不合 fail(validate 端点)
# ---------------------------------------------------------------------------


def test_g4_no_cases_warns(client):
    """G4:无用例 → warn(不 fail,可 ack 提交;§2.3)。"""
    r = client.post("/api/lab/drafts/lab.weather/validate")
    assert r.status_code == 200, r.text
    g4 = r.json()["gates"]["g4"]
    assert g4["status"] == "warn"
    assert any("无冒烟用例" in f["message"] for f in g4["findings"])


def test_g4_pass_and_fail(client):
    """G4:通过用例 → pass(info);outputs 不合 → fail(带用例名与原因)。"""
    _put_case(client, {
        "input": {},
        "mock_script": [
            {"message": {"role": "assistant", "content": json.dumps({"answer": "ok"})},
             "finish_reason": "stop"}
        ],
    }, fname="good.json")
    report = client.post("/api/lab/drafts/lab.weather/validate").json()
    assert report["gates"]["g4"]["status"] == "pass"
    assert any("good.json" in f["message"] for f in report["gates"]["g4"]["findings"])

    _put_case(client, {
        "input": {},
        "mock_script": [
            # 同 test-run:outputs 连败两次才判失败,脚本供两段
            {"message": {"role": "assistant", "content": json.dumps({"wrong": 1})},
             "finish_reason": "stop"},
            {"message": {"role": "assistant", "content": json.dumps({"wrong": 1})},
             "finish_reason": "stop"},
        ],
    }, fname="bad.json")
    report2 = client.post("/api/lab/drafts/lab.weather/validate").json()
    assert report2["gates"]["g4"]["status"] == "fail"
    assert report2["status"] == "fail"
    assert any("bad.json" in f["message"] for f in report2["gates"]["g4"]["findings"])

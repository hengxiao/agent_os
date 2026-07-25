"""多 skill set 锚点测试:一个站点托管多个技能包(每子文件夹一个 set)。

模型(``--skillsets <root>`` 或 ``[skillsets] dir``):

```
<root>/<set_name>/skills.yaml   # 必须
       [<set_name>/agent-os.toml] # 可选 set 级装配(继承全局)
       [<set_name>/*.py]          # 可选,装配时注入 sys.path(解决 dotted path 依赖 PYTHONPATH)
```

约定:
- ``GET /api/skillsets`` 列出全部 set;
- ``POST /api/runs`` 接受 ``skill_set`` 字段,用该 set 的装配执行;run 记录含 ``skill_set``;
- ``GET /api/skills?skill_set=<name>`` 按 set 过滤;
- 不传 ``skill_set`` 时用默认 set(唯一 set 或全局 [skills] 配置,向后兼容)。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

GLOBAL_TOML = """
[run]
model = "mock/x"
max_depth = 8
max_steps = 200
max_cost = 100.0
compression = "off"

[telemetry]
dir = "{telemetry}"
"""

SET_A_TOML = """
[run]
model = "mock/a"

[providers.mock]
brain = "brains:alpha_brain"

[tools]
builtins = false
python_exec = "off"
"""

SET_A_SKILLS = """
skills:
  - name: alpha_echo
    version: 1.0.0
    kind: prompt
    description: 返回输入原文。Use when 测试 set A。
    inputs:
      type: object
      properties: { text: { type: string } }
      required: [text]
    outputs:
      type: object
      properties: { echo: { type: string } }
      required: [echo]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/a"] }
    prompt: 原样返回输入。
"""

SET_B_SKILLS = """
skills:
  - name: beta_add
    version: 1.0.0
    kind: code
    handler: beta_handlers:add
    inputs:
      type: object
      properties: { a: { type: integer }, b: { type: integer } }
      required: [a, b]
    outputs:
      type: object
      properties: { sum: { type: integer } }
      required: [sum]
    permissions: { tools: [], skills: [] }
"""


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "skillsets"
    (root / "set_a").mkdir(parents=True)
    (root / "set_b").mkdir(parents=True)
    (root / "set_a" / "agent-os.toml").write_text(SET_A_TOML, encoding="utf-8")
    (root / "set_a" / "skills.yaml").write_text(SET_A_SKILLS, encoding="utf-8")
    (root / "set_a" / "brains.py").write_text(
        textwrap.dedent(
            '''
            import json
            from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Role

            def alpha_brain(req):
                text = next(json.loads(m.content)["text"] for m in req.messages if m.role is Role.USER)
                return ChatResponse(
                    message=Message(role=Role.ASSISTANT, content=json.dumps({"echo": text})),
                    finish_reason="stop",
                    usage=ChatUsage(prompt=1, completion=1),
                )
            '''
        ),
        encoding="utf-8",
    )
    (root / "set_b" / "skills.yaml").write_text(SET_B_SKILLS, encoding="utf-8")
    (root / "set_b" / "beta_handlers.py").write_text(
        "async def add(input, ctx):\n    return {\"sum\": input[\"a\"] + input[\"b\"]}\n",
        encoding="utf-8",
    )
    return root


def _client(tmp_path: Path) -> TestClient:
    root = _make_root(tmp_path)
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(GLOBAL_TOML.format(telemetry=tmp_path / "traces"), encoding="utf-8")
    return TestClient(
        create_app(cfg, artifacts_root=tmp_path / "runs", skillsets_dir=root)
    )


def test_skillsets_listed(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/skillsets")
    assert r.status_code == 200
    sets = {s["name"] for s in r.json()}
    assert sets == {"set_a", "set_b"}


def test_run_with_skill_set_and_record_tagged(tmp_path):
    """set A 的 prompt 技能:brain dotted path 无需 PYTHONPATH,run 记录带 skill_set。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "alpha_echo", "input": {"text": "hi"}, "skill_set": "set_a", "wait": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    assert body["result"] == {"echo": "hi"}
    assert body["skill_set"] == "set_a"


def test_run_code_skill_in_other_set(tmp_path):
    """set B 的 code 技能(handler 模块在 set 目录内)。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "beta_add", "input": {"a": 19, "b": 23}, "skill_set": "set_b", "wait": True},
    )
    assert r.json()["status"] == "done"
    assert r.json()["result"] == {"sum": 42}
    assert r.json()["skill_set"] == "set_b"


def test_skills_scoped_by_set(tmp_path):
    client = _client(tmp_path)
    a = client.get("/api/skills", params={"skill_set": "set_a"}).json()
    b = client.get("/api/skills", params={"skill_set": "set_b"}).json()
    assert [s["name"] for s in a] == ["alpha_echo"]
    assert [s["name"] for s in b] == ["beta_add"]


def test_runs_list_carries_skill_set(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json={"skill": "beta_add", "input": {"a": 1, "b": 2}, "skill_set": "set_b", "wait": True})
    runs = client.get("/api/runs").json()
    assert runs and runs[0]["skill_set"] == "set_b"


def test_unknown_skill_set_rejected(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "beta_add", "input": {"a": 1, "b": 2}, "skill_set": "nope", "wait": True},
    )
    assert r.status_code in (400, 404, 422)

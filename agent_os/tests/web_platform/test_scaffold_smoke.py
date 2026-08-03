"""N3 首稿用例(O3,B2;docs/FLOWS-OPTIMIZATION.md 循环 3)。

- skeleton_from_schema:inputs schema → 合 schema 示例骨架(launch-dialog 同款语义);
- scaffold.approve 批准首稿 → tests/smoke.json 落盘且合 schema;
- G4 不再必然 warn:有用例 + 冒烟执行器 → 非 warn。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from agent_os.skills.draft_store import skeleton_from_schema, smoke_case_from_schema
from agent_os.skills.gate import validate_draft

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SKILLS_YAML = """
skills:
  - name: weather.query
    version: 1.0.0
    kind: prompt
    description: 按天气推荐晚餐。Use when 查晚餐;Do not use when 其他。
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
      你是晚餐规划师。
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


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def test_skeleton_from_schema():
    """类型逐条对齐 launch-dialog:object 递归/integer ceil(minimum)/number minimum/其余占位。"""
    assert skeleton_from_schema({"type": "object", "properties": {
        "query": {"type": "string"},
        "n": {"type": "integer", "minimum": 1.5},
        "x": {"type": "number", "minimum": 0.5},
        "flag": {"type": "boolean"},
        "tags": {"type": "array"},
    }}) == {"query": "", "n": 2, "x": 0.5, "flag": False, "tags": []}
    assert skeleton_from_schema({"type": "object"}) == {}
    assert skeleton_from_schema(None) == {}
    assert smoke_case_from_schema({"type": "object", "properties": {"q": {"type": "string"}}}) == {
        "input": {"q": ""}
    }
    assert smoke_case_from_schema({"type": "string"}) is None, "残缺 schema 不造用例"


def test_scaffold_approve_writes_smoke_case(client):
    """批准首稿 → tests/smoke.json 落盘;内容合 inputs schema(query: string)。"""
    r = client.post(
        "/platform/api/cards/action",
        json={"action_id": "scaffold.approve",
              "payload": {"name": "lab.dinner", "template": "prompt_query"}},
    )
    assert r.status_code == 200, r.text
    draft = client.get("/api/lab/drafts/lab.dinner").json()
    assert "smoke.json" in draft["tests"], "首稿即带冒烟用例"
    import json

    case = json.loads(draft["tests"]["smoke.json"])
    assert isinstance(case["input"]["query"], str), "骨架合 schema(query: string)"


def test_g4_not_warn_with_scaffold_smoke(tmp_path):
    """G4:首稿有用例 + 冒烟执行器 → 不再 warn(B2 的第一黄灯消除)。"""
    from agent_os.skills.draft_store import DraftStore
    from agent_os.skills.local_file import LocalFileSkillRegistry
    from agent_os.tools.local_registry import LocalPythonToolRegistry

    prod_yaml = tmp_path / "skills.yaml"
    prod_yaml.write_text("skills: []\n", encoding="utf-8")
    store = DraftStore(tmp_path / "drafts")
    store.create("lab.dinner", template="prompt_query")
    draft = store.read("lab.dinner")
    case = smoke_case_from_schema((draft["manifest"] or {}).get("inputs"))
    store.save(
        "lab.dinner",
        manifest=draft["manifest"],
        prompt=draft["prompt"],
        handler=draft["handler"],
        tests={"smoke.json": case},
    )
    report = validate_draft(
        store.read("lab.dinner"),
        production=LocalFileSkillRegistry(str(prod_yaml)),
        tools=LocalPythonToolRegistry.with_builtins(),
        smoke_runner=lambda case: {"ok": True},
    )
    assert report["gates"]["g4"]["status"] == "pass", "有用例则 G4 不 warn(B2)"

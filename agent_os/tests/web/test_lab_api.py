"""L1 锚点测试:Skill Lab API(docs/SKILL-DEV.md §1.5)。

端点:GET/POST /api/lab/drafts、GET/PUT/DELETE /api/lab/drafts/{name}、
GET /api/lab/drafts/{name}/tier(含 ?tools=&skills= 未保存白名单覆盖)。
drafts_root 取 ``[lab].drafts_root``(本文件用 tmp_path 钉死,不碰实例目录)。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SKILLS_YAML = """
skills:
  - name: common.text.word_count
    version: 1.0.0
    kind: prompt
    description: 数字数。Use when 统计词数;Do not use when 其他。
    inputs:
      type: object
      properties: { text: { type: string } }
    outputs:
      type: object
      properties: { count: { type: integer } }
    permissions:
      tools: []
      skills: []
    model: { prefer: ["mock/x"] }
    prompt: |
      你是词数统计员。
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.kernel.test_supervisor:expense_brain"

[tools]
builtins = true
python_exec = "off"

[skills]
path = "{skills}"

[lab]
drafts_root = "{drafts}"
"""


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def _create(client: TestClient, name: str = "weather.query") -> dict:
    r = client.post("/api/lab/drafts", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD 全端点
# ---------------------------------------------------------------------------


def test_crud_round_trip(tmp_path):
    """建 → 读 → 列表(含 tier/mtime)→ 存(.bak)→ 删;删除后 404。"""
    client = _client(tmp_path)
    draft = _create(client)
    assert draft["manifest"]["name"] == "weather.query"
    assert draft["parse_error"] is None

    r = client.get("/api/lab/drafts/weather.query")
    assert r.status_code == 200
    assert r.json()["manifest"]["kind"] == "prompt"

    rows = client.get("/api/lab/drafts").json()
    assert len(rows) == 1
    assert rows[0]["name"] == "weather.query"
    assert rows[0]["tier"] == "none"  # 空模板无白名单 → L1
    assert rows[0]["mtime"] > 0

    manifest = draft["manifest"] | {"description": "查天气。Use when x;Do not use when y"}
    r = client.put(
        "/api/lab/drafts/weather.query",
        json={"manifest": manifest, "prompt": "你是天气员。", "handler": None,
              "tests": {"case1.json": {"input": {}}}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["prompt"] == "你是天气员。"
    assert (tmp_path / "drafts" / "weather.query" / "manifest.yaml.bak").is_file()

    r = client.delete("/api/lab/drafts/weather.query")
    assert r.status_code == 200
    assert client.get("/api/lab/drafts/weather.query").status_code == 404
    assert client.get("/api/lab/drafts").json() == []


def test_create_from_production_and_conflict(tmp_path):
    """from 生产复制;重名 409;from 不存在 404;坏名字 400。"""
    client = _client(tmp_path)
    r = client.post("/api/lab/drafts", json={"name": "lab.word_count", "from_skill": "common.text.word_count"})
    assert r.status_code == 201, r.text
    assert r.json()["manifest"]["version"] == "1.0.0"

    r = client.post("/api/lab/drafts", json={"name": "lab.word_count"})
    assert r.status_code == 409
    r = client.post("/api/lab/drafts", json={"name": "lab.x", "from_skill": "no.such"})
    assert r.status_code == 404
    r = client.post("/api/lab/drafts", json={"name": "../etc"})
    assert r.status_code == 400


def test_read_noncompliant_draft_no_500(tmp_path):
    """草稿暂时不合规:read 返回原文 + parse_error,不是 500(§1.2 容错)。"""
    client = _client(tmp_path)
    _create(client)
    (tmp_path / "drafts" / "weather.query" / "manifest.yaml").write_text(
        "kind: [unclosed", encoding="utf-8"
    )
    r = client.get("/api/lab/drafts/weather.query")
    assert r.status_code == 200
    assert r.json()["parse_error"]
    # 列表也不被拖垮:tier 置 None
    rows = client.get("/api/lab/drafts").json()
    assert rows[0]["tier"] is None


# ---------------------------------------------------------------------------
# /tier:实时推导档 + 来源明细 + 未保存白名单覆盖
# ---------------------------------------------------------------------------


def _save_with_tools(client: TestClient, tools: list[str]) -> None:
    draft = client.get("/api/lab/drafts/weather.query").json()
    manifest = draft["manifest"] | {
        "description": "查天气。Use when x;Do not use when y",
        "permissions": {"tools": tools, "skills": []},
    }
    r = client.put("/api/lab/drafts/weather.query",
                   json={"manifest": manifest, "prompt": "p", "handler": None})
    assert r.status_code == 200


def test_tier_detail_and_override(tmp_path):
    """tier:tools 取 max + top 明细;?tools= 用未保存的白名单覆盖(编辑器实时刷新)。"""
    client = _client(tmp_path)
    _create(client)
    _save_with_tools(client, ["system.file.write"])
    detail = client.get("/api/lab/drafts/weather.query/tier").json()
    assert detail["tier"] == "reversible"
    assert [s["name"] for s in detail["top"]] == ["system.file.write"]
    assert detail["parse_error"] is None

    # 编辑器把 file.delete 加进白名单但还没保存:query 覆盖 → irreversible
    detail2 = client.get(
        "/api/lab/drafts/weather.query/tier",
        params={"tools": "system.file.write,system.file.delete"},
    ).json()
    assert detail2["tier"] == "irreversible"
    by_name = {s["name"]: s["tier"] for s in detail2["sources"]}
    assert by_name["system.file.delete"] == "irreversible"
    # 磁盘上的草稿未被覆盖写污染
    assert client.get("/api/lab/drafts/weather.query/tier").json()["tier"] == "reversible"


def test_tier_skill_source_detail(tmp_path):
    """tier 明细覆盖 skills 来源:白名单里的子技能档递归进 sources。"""
    client = _client(tmp_path)
    _create(client)
    draft = client.get("/api/lab/drafts/weather.query").json()
    manifest = draft["manifest"] | {
        "description": "查天气。Use when x;Do not use when y",
        "permissions": {"tools": [], "skills": ["common.text.word_count"]},
    }
    client.put("/api/lab/drafts/weather.query",
               json={"manifest": manifest, "prompt": "p", "handler": None})
    detail = client.get("/api/lab/drafts/weather.query/tier").json()
    assert detail["tier"] == "none"  # word_count 无工具 → L1
    assert detail["sources"][0]["kind"] == "skill"
    assert detail["sources"][0]["name"] == "common.text.word_count"


def test_tier_endpoints_errors(tmp_path):
    """tier/保存/删除的错误归类:不存在 404,坏名字 400。"""
    client = _client(tmp_path)
    assert client.get("/api/lab/drafts/no.such/tier").status_code == 404
    assert client.put("/api/lab/drafts/no.such", json={"manifest": {}}).status_code == 404
    assert client.delete("/api/lab/drafts/no.such").status_code == 404
    assert client.get("/api/lab/drafts/..%2Fetc").status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# P1:closure API(docs/SKILL-PACKAGES.md §4.2)
# ---------------------------------------------------------------------------


def test_closure_api_statuses_and_alias(tmp_path):
    """closure:四态成员 + 根推导档;drafts 别名端点同构;根不存在 404。"""
    client = _client(tmp_path)
    # 根草稿引用:生产同空间(common.text.word_count,同 common? 否——根是 lab.*,
    # 故生产引用全部 external)+ 悬空
    draft = _create(client)
    manifest = draft["manifest"] | {
        "description": "巡检。Use when x;Do not use when y(凑长度过 lint)",
        "permissions": {"tools": ["system.file.read"], "skills": ["common.text.word_count", "lab.ghost"]},
    }
    r = client.put("/api/lab/drafts/weather.query",
                   json={"manifest": manifest, "prompt": "p", "handler": None})
    assert r.status_code == 200

    for path in ("/api/lab/packages/weather.query/closure", "/api/lab/drafts/weather.query/closure"):
        r = client.get(path)
        assert r.status_code == 200, path
        body = r.json()
        assert body["root"] == "weather.query"
        by_name = {m["name"]: m for m in body["members"]}
        assert by_name["weather.query"]["status"] == "draft"
        assert by_name["weather.query"]["depth"] == 0
        assert by_name["common.text.word_count"]["status"] == "external"  # 跨第一段命名空间
        assert by_name["lab.ghost"]["status"] == "missing"
        assert by_name["lab.ghost"]["tier"] is None
        assert body["errors"] == []

    assert client.get("/api/lab/packages/no.such/closure").status_code == 404


# ---------------------------------------------------------------------------
# L2:validate + promote(docs/SKILL-DEV.md §1.4/§1.5)
# ---------------------------------------------------------------------------


def _save_manifest(client: TestClient, manifest: dict, prompt: str = "你是天气员。") -> None:
    r = client.put(
        "/api/lab/drafts/weather.query",
        json={"manifest": manifest, "prompt": prompt, "handler": None},
    )
    assert r.status_code == 200, r.text


def _put_smoke_case(tmp_path: Path) -> None:
    """落一个 mock_script 冒烟用例(G4 通过形;outputs 与 _good_weather_manifest 同形)。"""
    tests_dir = tmp_path / "drafts" / "weather.query" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "case1.json").write_text(
        json.dumps(
            {
                "input": {},
                "mock_script": [
                    {"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _good_weather_manifest(**over):
    m = {
        "name": "weather.query",
        "version": "0.1.0",
        "kind": "prompt",
        "description": "查天气。Use when 需要天气;Do not use when 其他。",
        "inputs": {"type": "object", "properties": {"city": {"type": "string"}}},
        "outputs": {"type": "object", "properties": {}},
        "permissions": {"tools": [], "skills": []},
    }
    return m | over


def test_validate_endpoint_report_shape(tmp_path):
    """validate:五关结构 + G4 冒烟实跑(L3);报告落盘 drafts/<name>/gate/。"""
    client = _client(tmp_path)
    _create(client)
    _save_manifest(client, _good_weather_manifest())
    # G4(L3):带一个 mock_script 冒烟用例,报告才全绿(无用例是 warn)
    _put_smoke_case(tmp_path)
    r = client.post("/api/lab/drafts/weather.query/validate")
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["report_id"]
    assert report["status"] == "pass"
    assert set(report["gates"]) == {"g1", "g2", "g3", "g4", "g5"}
    assert report["gates"]["g4"]["status"] == "pass"
    assert report["gates"]["g5"]["status"] == "pass"  # L5:提示词卫生实关(无诱导即过)
    assert report["manifest_hash"]
    gate_dir = tmp_path / "drafts" / "weather.query" / "gate"
    assert list(gate_dir.glob("*.json")), "报告必须落盘"


def test_validate_fail_blocks_and_promote_rejections(tmp_path):
    """L2 缺 reversal → G3 fail → promote 409;warn 未 ack → 409;假报告 404。"""
    client = _client(tmp_path)
    _create(client)
    _save_manifest(
        client, _good_weather_manifest(permissions={"tools": ["system.file.write"], "skills": []})
    )
    report = client.post("/api/lab/drafts/weather.query/validate").json()
    assert report["status"] == "fail"
    assert any(
        "reversal" in f["message"] for f in report["gates"]["g3"]["findings"]
    )

    r = client.post(
        "/api/lab/drafts/weather.query/promote",
        json={"report_id": report["report_id"], "warnings_ack": True},
    )
    assert r.status_code == 409, r.text

    # warn(description 太短)未 ack → 409;假 report_id → 404
    _save_manifest(client, _good_weather_manifest(description="太短"))
    warn_report = client.post("/api/lab/drafts/weather.query/validate").json()
    assert warn_report["status"] == "warn"
    r = client.post(
        "/api/lab/drafts/weather.query/promote",
        json={"report_id": warn_report["report_id"], "warnings_ack": False},
    )
    assert r.status_code == 409
    r = client.post(
        "/api/lab/drafts/weather.query/promote",
        json={"report_id": "bogus-id", "warnings_ack": True},
    )
    assert r.status_code == 404


def test_promote_end_to_end_and_stale_report(tmp_path):
    """promote 全路径:写生产 + .bak + GET /api/skills 可见(reload)+ 记录;
    报告后再改草稿 → 哈希错位 409;二次 promote → version bump。"""
    client = _client(tmp_path)
    _create(client)
    _save_manifest(client, _good_weather_manifest())
    _put_smoke_case(tmp_path)  # G4 全绿才不需要 ack(无用例 = warn)
    report = client.post("/api/lab/drafts/weather.query/validate").json()

    r = client.post(
        "/api/lab/drafts/weather.query/promote",
        json={"report_id": report["report_id"], "warnings_ack": False},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["version"] == "0.1.0"
    assert result["action"] == "appended"
    assert (tmp_path / "skills.yaml.bak").is_file()
    names = [s["name"] for s in client.get("/api/skills").json()]
    assert "weather.query" in names, "reload 后生产技能列表必须可见"
    promos = (tmp_path / "drafts" / "weather.query" / "gate" / "promotions.jsonl").read_text(
        encoding="utf-8"
    )
    assert report["report_id"] in promos

    # 报告后再改草稿:旧报告作废
    _save_manifest(client, _good_weather_manifest(), prompt="改过了")
    r = client.post(
        "/api/lab/drafts/weather.query/promote",
        json={"report_id": report["report_id"], "warnings_ack": False},
    )
    assert r.status_code == 409
    assert "不一致" in r.json()["detail"]

    # 重新检查 → 二次 promote:同名 replace + patch bump
    report2 = client.post("/api/lab/drafts/weather.query/validate").json()
    r2 = client.post(
        "/api/lab/drafts/weather.query/promote",
        json={"report_id": report2["report_id"], "warnings_ack": False},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["version"] == "0.1.1"
    assert r2.json()["action"] == "replaced"

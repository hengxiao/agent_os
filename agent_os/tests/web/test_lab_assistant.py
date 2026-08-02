"""L4 锚点测试:lab.draft.* 工具组与 skill.dev.assistant(docs/SKILL-DEV.md §1.1/§2.2)。

- 五工具的档:read/list/validate/test_run = none,write = reversible(显式声明);
- **工具面没有 promote/delete**(§1.1:能改不能发,注册表断言);
- write:整体/局部字段/坏字段/不存在草稿;validate 只读报告;
- e2e:scripted brain 驱动 assistant 读 → 改 → validate → test_run → 汇报。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import (
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    derive_side_effect,
)
from agent_os.host.web.app import create_app
from agent_os.skills.draft_store import DraftStore
from agent_os.skills.lab_assistant import ASSISTANT_NAME, assistant_skill
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.lab_tools import LAB_DRAFT_TOOLS, register_lab_tools
from agent_os.tools.local_registry import LocalPythonToolRegistry

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _reg(tmp_path, kernel_factory=lambda: None):
    store = DraftStore(tmp_path / "drafts")
    store.create("weather.query")
    registry = LocalPythonToolRegistry()
    production = LocalFileSkillRegistry(str(_prod_yaml(tmp_path)))
    register_lab_tools(
        registry,
        store=store,
        production=production,
        tools_registry=LocalPythonToolRegistry.with_builtins(),
        kernel_factory=kernel_factory,
    )
    return registry, store


def _prod_yaml(tmp_path) -> Path:
    p = tmp_path / "skills.yaml"
    p.write_text("skills: []\n", encoding="utf-8")
    return p


def _dispatch(registry, name, args):
    frame = SkillFrame(frame_id="f1", run_id="r1")
    ctx = ToolDispatchContext(frame=frame, allowed_tools=list(LAB_DRAFT_TOOLS))
    return asyncio.run(registry.dispatch(ToolCall(id="c1", name=name, args=args), ctx))


# ---------------------------------------------------------------------------
# 工具面:恰好五件、档推导、无 promote/delete
# ---------------------------------------------------------------------------


def test_tool_surface_exactly_five_and_tiers(tmp_path):
    """组内恰好五件;read/list/validate/test_run=none、write=reversible(显式声明)。"""
    registry, _ = _reg(tmp_path)
    names = sorted(LAB_DRAFT_TOOLS)
    assert names == [
        "lab.draft.list",
        "lab.draft.read",
        "lab.draft.test_run",
        "lab.draft.validate",
        "lab.draft.write",
    ]
    assert not any("promote" in n or "delete" in n for n in names), "能改不能发(§1.1)"
    specs = {s.name: s for s in registry.specs() if s.name.startswith("lab.draft.")}
    assert set(specs) == set(names)
    for n in ("lab.draft.read", "lab.draft.list", "lab.draft.validate", "lab.draft.test_run"):
        assert specs[n].permission is Permission.READ
        assert specs[n].side_effect == "none"
        assert derive_side_effect(specs[n]) == "none"
    assert specs["lab.draft.write"].permission is Permission.WRITE
    assert specs["lab.draft.write"].side_effect == "reversible"
    assert derive_side_effect(specs["lab.draft.write"]) == "reversible"


def test_assistant_manifest_whitelist(tmp_path):
    """assistant 权限面恰好是五件工具、无子技能;outputs 契约 {reply}。"""
    skill = assistant_skill()
    assert skill.manifest.name == ASSISTANT_NAME
    assert sorted(skill.manifest.permissions.tools) == sorted(LAB_DRAFT_TOOLS)
    assert skill.manifest.permissions.skills == []
    assert "reply" in skill.manifest.outputs["properties"]
    assert "promote" in skill.prompt, "prompt 必须写明没有 promote 能力"


# ---------------------------------------------------------------------------
# write / validate 行为
# ---------------------------------------------------------------------------


def test_write_partial_field_and_whole_manifest(tmp_path):
    """write:局部字段改 → 落盘 + .bak;整体 manifest 替换;坏字段/不存在 → 结构化错误。"""
    registry, store = _reg(tmp_path)
    r = _dispatch(registry, "lab.draft.write", {
        "draft": "weather.query",
        "field": "description",
        "value": "查天气。Use when 需要天气;Do not use when 其他。",
    })
    assert r.ok
    assert r.value["changed"] == "description"
    saved = store.read("weather.query")
    assert saved["manifest"]["description"].startswith("查天气")
    assert (store.root / "weather.query" / "manifest.yaml.bak").is_file()

    r2 = _dispatch(registry, "lab.draft.write", {
        "draft": "weather.query",
        "manifest": {"name": "weather.query", "version": "0.2.0", "kind": "prompt",
                     "description": "x", "inputs": {}, "outputs": {},
                     "permissions": {"tools": [], "skills": []}},
        "prompt": "新版 prompt。",
    })
    assert r2.ok and r2.value["changed"] == "manifest"
    assert store.read("weather.query")["prompt"] == "新版 prompt。"

    r3 = _dispatch(registry, "lab.draft.write", {"draft": "weather.query", "field": "name", "value": "x"})
    assert not r3.ok and r3.error.kind.value == "invalid_args", "name 由目录钉死,不可改"
    r4 = _dispatch(registry, "lab.draft.write", {"draft": "no.such", "prompt": "x"})
    assert not r4.ok and r4.error.kind.value == "not_found"


def test_validate_tool_readonly_report(tmp_path):
    """validate:五关结果只读返回(不落盘);L2 缺 reversal 的 fail 透出。"""
    registry, store = _reg(tmp_path)
    store.save("weather.query", manifest={
        "name": "weather.query", "version": "0.1.0", "kind": "prompt",
        "description": "查天气。Use when 需要天气;Do not use when 其他。",
        "inputs": {"type": "object", "properties": {"p": {"type": "string"}}},
        "outputs": {"type": "object"},
        "permissions": {"tools": ["system.file.write"], "skills": []},
    }, prompt="p")
    r = _dispatch(registry, "lab.draft.validate", {"draft": "weather.query"})
    assert r.ok
    assert r.value["status"] == "fail"
    assert r.value["tier"] == "reversible"
    assert any("reversal" in f for f in r.value["gates"]["g3"]["findings"])
    assert not (store.root / "weather.query" / "gate").exists(), "工具版不落盘(只读)"


# ---------------------------------------------------------------------------
# e2e:assistant 读 → 改 → validate → test_run → 汇报
# ---------------------------------------------------------------------------

SEEN: list[str] = []


def assistant_brain(req) -> ChatResponse:
    """assistant 帧:read → write → validate → test_run → 汇报;草稿帧:直接交付。"""
    system = req.messages[0].content if req.messages else ""
    if "Skill Lab 的开发助手" not in system:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "ok"})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )
    calls = [tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls]
    names = [tc.name for tc in calls]
    if names:
        SEEN.append(names[-1])  # 累计名单的末位即本步新调用
    seq = ["lab.draft.read", "lab.draft.write", "lab.draft.validate", "lab.draft.test_run"]
    if len(names) < len(seq):
        nxt = seq[len(names)]
        args = {"draft": "weather.query"}
        if nxt == "lab.draft.write":
            args |= {"field": "description", "value": "查天气。Use when 需要天气;Do not use when 其他。"}
        if nxt == "lab.draft.test_run":
            args |= {"input": {}}
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id=f"t{len(names)}", name=nxt, args=args)]),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"reply": "已更新 description 并自查通过"})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.web.test_lab_assistant:assistant_brain"

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
    SEEN.clear()
    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"), encoding="utf-8")
    c = TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))
    c.post("/api/lab/drafts", json={"name": "weather.query"})
    c._tmp_path = tmp_path
    return c


def test_assistant_end_to_end(client):
    """POST /api/lab/assistant → run 完成:草稿真被改、回复回来了、四步工具按序。"""
    r = client.post("/api/lab/assistant", json={"request": "补上 description 并自查", "draft": "weather.query"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    deadline = time.monotonic() + 10
    record = None
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "failed", "aborted"):
            record = detail
            break
        time.sleep(0.1)
    assert record and record["status"] == "done", record
    assert record["result"] == {"reply": "已更新 description 并自查通过"}
    assert SEEN == ["lab.draft.read", "lab.draft.write", "lab.draft.validate", "lab.draft.test_run"]
    saved = client.get("/api/lab/drafts/weather.query").json()
    assert saved["manifest"]["description"].startswith("查天气"), "assistant 的 write 真落盘"

    # 草稿不存在 → 404
    r = client.post("/api/lab/assistant", json={"request": "x", "draft": "no.such"})
    assert r.status_code == 404

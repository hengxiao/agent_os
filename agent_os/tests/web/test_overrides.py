"""K1 Web run 覆盖选项锚点测试(docs/RUNNERS.md §2.5/§4.3)。

固定约定:

- ``POST /api/runs`` 的 ``overrides`` 白名单从注册表派生(P2:``web_fields()``);
  白名单外字段 fail-closed 归 400(P3),``web=False`` 的注册字段
  (workdir/read_paths)不对本面开放(P6);
- 合法覆盖(model/max_cost 等)照常生效,provenance 以 ``"api"`` 来源写 meta.json;
  无覆盖时 meta.json 无 ``overrides`` 键。
"""

from __future__ import annotations

import json

import pytest

from tests.helpers.web import make_client as _client

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _read_meta(tmp_path, run_id: str) -> dict:
    path = tmp_path / "runs" / "runs" / run_id / "meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_web_overrides_unknown_field_400(tmp_path):
    """fail-closed(P3):未注册字段归 400(逐个点名),run 不起。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True, "overrides": {"bogus": 1}},
    )
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_web_overrides_workdir_not_open(tmp_path):
    """安全分级(P6):workdir/read_paths 虽已注册但 web=False,本面归 400。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True,
              "overrides": {"workdir": str(tmp_path)}},
    )
    assert r.status_code == 400
    assert "workdir" in r.json()["detail"]
    r2 = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True,
              "overrides": {"read_paths": [str(tmp_path)]}},
    )
    assert r2.status_code == 400
    assert "read_paths" in r2.json()["detail"]


def test_web_overrides_invalid_value_400(tmp_path):
    """值校验走注册表同一管线(P2):枚举/类型非法归 400。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True, "overrides": {"inline": "maybe"}},
    )
    assert r.status_code == 400
    r2 = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True,
              "overrides": {"max_steps": "abc"}},
    )
    assert r2.status_code == 400


def test_web_overrides_model_max_cost_still_work(tmp_path):
    """web=True 字段照常覆盖(D3 面不收缩);provenance 统一记 "api" 落 meta.json。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True,
              "overrides": {"model": "mock/fib", "max_cost": 5.0}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    meta = _read_meta(tmp_path, body["run_id"])
    assert meta["overrides"] == {"model": "api", "max_cost": "api"}


def test_web_run_without_overrides_has_no_meta_key(tmp_path):
    """无覆盖 → meta.json 无 overrides 键(空段不写键)。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True},
    )
    assert r.status_code == 200, r.text
    meta = _read_meta(tmp_path, r.json()["run_id"])
    assert "overrides" not in meta

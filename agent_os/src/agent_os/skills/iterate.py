"""迭代模式(docs/LAB-ITERATION.md;Flow C 样板):diff 纯函数。

working vs candidate 的成员级 diff:
- 字段级(manifest dict):新增/修改/删除,带旧值/新值;
- prompt 行级:``difflib.SequenceMatcher`` 的增/删/同(红绿行);
- 成员级:added(候选新增)/ removed(候选缺失)/ changed / same。

纯函数,不碰 store——输入是 ``{member: {"manifest": dict, "prompt": str}}``
两个字典,便于单测;store 侧组装在 ``collect_package_docs``。
"""

from __future__ import annotations

import difflib
import json
from typing import Any

__all__ = ["collect_package_docs", "manifest_diff", "package_diff", "text_line_diff"]


def manifest_diff(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[dict[str, Any]]:
    """字段级 diff(顶层字段三态;嵌套值整体比较,不做深层递归——评审面顶层足够)。"""
    old = old or {}
    new = new or {}
    out: list[dict[str, Any]] = []
    for key in sorted(set(old) | set(new)):
        if key == "name":  # 名字由目录钉死,不参与评审 diff
            continue
        in_old, in_new = key in old, key in new
        if in_old and not in_new:
            out.append({"kind": "removed", "path": key, "old": old[key], "new": None})
        elif in_new and not in_old:
            out.append({"kind": "added", "path": key, "old": None, "new": new[key]})
        elif json.dumps(old[key], ensure_ascii=False, sort_keys=True) != json.dumps(
            new[key], ensure_ascii=False, sort_keys=True
        ):
            out.append({"kind": "changed", "path": key, "old": old[key], "new": new[key]})
    return out


def text_line_diff(old: str, new: str) -> list[dict[str, str]]:
    """行级 diff(difflib 词级操作码 → add/del/same 行;红绿行渲染的数据源)。"""
    out: list[dict[str, str]] = []
    matcher = difflib.SequenceMatcher(None, (old or "").splitlines(), (new or "").splitlines())
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend({"kind": "same", "text": line} for line in (old or "").splitlines()[i1:i2])
        elif tag == "delete":
            out.extend({"kind": "del", "text": line} for line in (old or "").splitlines()[i1:i2])
        elif tag == "insert":
            out.extend({"kind": "add", "text": line} for line in (new or "").splitlines()[j1:j2])
        else:  # replace = 先删后增
            out.extend({"kind": "del", "text": line} for line in (old or "").splitlines()[i1:i2])
            out.extend({"kind": "add", "text": line} for line in (new or "").splitlines()[j1:j2])
    return out


def package_diff(
    working: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """成员级 diff:working(现状)vs candidate(候选)。

    返回 ``{"members": [{member, status, fields, prompt_diff, tests}],
     "has_changes": bool}``;tests 按文件名比(新增 = 绿)。
    """
    members = []
    has_changes = False
    for name in sorted(set(working) | set(candidate)):
        old = working.get(name)
        new = candidate.get(name)
        if old is None:
            status = "added"
        elif new is None:
            status = "removed"
        else:
            status = "changed"
        fields = manifest_diff(
            (old or {}).get("manifest"), (new or {}).get("manifest")
        )
        prompt_diff = (
            text_line_diff((old or {}).get("prompt", ""), (new or {}).get("prompt", ""))
            if old is not None and new is not None
            else []
        )
        old_tests = set((old or {}).get("tests") or {})
        new_tests = set((new or {}).get("tests") or {})
        tests = {
            "added": sorted(new_tests - old_tests),
            "removed": sorted(old_tests - new_tests),
        }
        changed = bool(
            status != "changed"
            or fields
            or any(d["kind"] != "same" for d in prompt_diff)
            or tests["added"]
            or tests["removed"]
        )
        if old is not None and new is not None and not changed:
            status = "same"
        has_changes = has_changes or changed
        members.append(
            {
                "member": name,
                "status": status,
                "fields": fields,
                "prompt_diff": prompt_diff,
                "tests": tests,
            }
        )
    return {"members": members, "has_changes": has_changes}


def collect_package_docs(store: Any, pkg: str, members: list[str]) -> dict[str, dict[str, Any]]:
    """store 侧组装(diff 输入):working 全成员 {manifest, prompt, tests 文件名集}。"""
    docs: dict[str, dict[str, Any]] = {}
    for member in members:
        data = store.read(member)
        docs[member] = {
            "manifest": data["manifest"] or {},
            "prompt": data["prompt"],
            "tests": sorted((data["tests"] or {}).keys()),
        }
    return docs


def edit_members(store: Any, production: Any, tools: Any, pkg: str) -> list[str]:
    """编辑闭包的 draft 成员(working 集;快照/diff/平台编排共用)。"""
    from agent_os.skills.closure import compute_closure

    closure = compute_closure(pkg, store, production, tools, mode="edit")
    return [m["name"] for m in closure["members"] if m["status"] == "draft"]


def candidate_diff(store: Any, production: Any, tools: Any, pkg: str) -> dict[str, Any]:
    """working vs candidate 的结构化 diff(host/web 与 web_platform 共用)。"""
    working = collect_package_docs(store, pkg, edit_members(store, production, tools, pkg))
    candidate: dict[str, Any] = {}
    for member in store.candidate_members(pkg):
        data = store.read_candidate_member(pkg, member)
        candidate[member] = {
            "manifest": data["manifest"] or {},
            "prompt": data["prompt"],
            "tests": sorted((data["tests"] or {}).keys()),
        }
    return package_diff(working, candidate)


def run_iterate(
    kernel: Any,
    *,
    store: Any,
    production: Any,
    tools_registry: Any,
    name: str,
    comments: list[dict[str, Any]],
    note: str,
) -> dict[str, Any]:
    """迭代生成执行体(host/web 的 iterate 端点与 web_platform 共用).

    调用方负责:装配好内核(overlay 注入 iterator 技能)并先落批注;
    本函数注册迭代工具面、跑生成 run、返回 diff。provider 故障原样上抛
    (路由层归 503"助手暂不可用")。
    """
    import asyncio
    import json as _json

    from agent_os.skills.lab_assistant import ITERATOR_NAME
    from agent_os.tools.lab_tools import register_iterate_tools

    register_iterate_tools(
        kernel.tools, store=store, production=production, tools_registry=tools_registry, pkg=name
    )
    request_text = _json.dumps({"note": note, "comments": comments}, ensure_ascii=False)
    result = asyncio.run(kernel.run(ITERATOR_NAME, {"request": request_text, "draft": name}))
    return {
        "candidate": True,
        "reply": (result or {}).get("reply", ""),
        "diff": candidate_diff(store, production, tools_registry, name),
    }

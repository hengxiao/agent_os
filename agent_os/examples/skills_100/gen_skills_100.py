"""skills_100 压测示例的 skills.yaml 生成器(调试用途)。

生成恰好 100 个技能(88 prompt + 12 code),依赖构成无环 DAG:

- 聚类:ingest×10 / clean×10 / extract×10 / analyze×15 / report×10 / qa×10 /
  io×10 / notify×5 / audit×10 / util×10;其中 analyze 簇含 13 个 prompt 与
  mega_pipeline、hub_agg 两个 code,util 簇即 chain_00..chain_09(全 code);
- 边:簇内链(i→i+1)、跨簇边(analyze→extract、report→analyze、qa→report)
  与若干菱形(qa_00 → report_00/report_01 → analyze_00);
- 深链:mega_pipeline → chain_00 → … → chain_09 → hub_agg(锚点 12 帧、
  depth 12、结果 {"total": 45})。

生成器内置拓扑校验(Kahn),成环即报错退出。用法::

    python gen_skills_100.py    # 重写同目录 skills.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

OUT = Path(__file__).with_name("skills.yaml")

#: 技能命名空间
PREFIX = "project.skills_100"

#: prompt 技能聚类(共 88 个):簇名 → 数量
PROMPT_CLUSTERS: list[tuple[str, int]] = [
    ("ingest", 10),
    ("clean", 10),
    ("extract", 10),
    ("analyze", 13),
    ("report", 10),
    ("qa", 10),
    ("io", 10),
    ("notify", 5),
    ("audit", 10),
]

#: 各 prompt 簇声明的工具面(均为注册表内置工具,过 §6.1 权限闸门)
CLUSTER_TOOLS: dict[str, list[str]] = {
    "ingest": ["system.net.http_fetch"],
    "clean": ["system.python.exec"],
    "extract": ["system.file.read"],
    "analyze": ["system.python.exec"],
    "report": ["system.file.read"],
    "qa": ["system.python.exec"],
    "io": ["system.file.read", "system.shell.exec"],
    "notify": ["system.net.http_fetch"],
    "audit": ["system.file.read"],
}

#: 簇内链之外的额外依赖(跨簇边与菱形;全部指向已生成技能,方向保持无环)
EXTRA_EDGES: dict[str, list[str]] = {
    "analyze_05": ["extract_02"],  # 跨簇:analyze → extract
    "analyze_09": ["extract_05"],  # 跨簇:analyze → extract
    "report_03": ["analyze_07"],  # 跨簇:report → analyze
    "qa_02": ["report_04"],  # 跨簇:qa → report
    "qa_00": ["report_00", "report_01"],  # 菱形:qa_00 → report_00/01 → analyze_00
    "report_00": ["analyze_00"],
    "report_01": ["analyze_00"],
}

_HEADER = """\
# 本文件由 gen_skills_100.py 生成,请勿手改(重跑生成器会覆盖)。
# skills_100 压测技能包:恰好 100 个技能(88 prompt + 12 code),依赖为无环 DAG。
# 聚类:ingest×10 / clean×10 / extract×10 / analyze×15(13 prompt + mega_pipeline
# 与 hub_agg 2 个 code)/ report×10 / qa×10 / io×10 / notify×5 / audit×10 /
# util×10(= chain_00..chain_09,全 code)。
# 边:簇内链 i→i+1;跨簇 analyze→extract、report→analyze、qa→report;菱形若干。
# 深链:mega_pipeline → chain_00 → … → chain_09 → hub_agg(12 帧,{"total": 45})。
"""


def _dotted(name: str) -> str:
    return f"{PREFIX}.{name}"


def _prompt_entry(name: str, cluster: str, index: int, deps: list[str]) -> dict[str, Any]:
    return {
        "name": _dotted(name),
        "version": "1.0.0",
        "kind": "prompt",
        "description": (
            f"{cluster} 阶段第 {index} 步处理器。Use when 流水线进入 {cluster} 阶段"
            f"第 {index} 步;Do not use when 需要其他阶段或其他步骤。"
        ),
        "inputs": {"type": "object", "properties": {"payload": {"type": "string"}}},
        "outputs": {"type": "object", "properties": {"result": {"type": "string"}}},
        "permissions": {"tools": CLUSTER_TOOLS[cluster], "skills": deps},
        "model": {"prefer": ["mock/ops"]},
        "limits": {"max_steps": 12, "timeout": 60},
        "prompt": f"# skill: {_dotted(name)}\n处理输入 JSON,给出本步骤结果。\n",
    }


def _code_entry(
    name: str,
    deps: list[str],
    inputs: dict[str, Any],
    outputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": _dotted(name),
        "version": "1.0.0",
        "kind": "code",
        "description": (
            f"深链环节 {_dotted(name)}。Use when 调试 mega_pipeline 纯 code 深链;"
            "Do not use when 需要 LLM 推理。"
        ),
        "handler": f"skills100_handlers:{name}",
        "inputs": inputs,
        "outputs": outputs,
        "permissions": {"tools": [], "skills": deps},
    }


def build_entries() -> list[dict[str, Any]]:
    """构造全部 100 个技能条目(88 prompt + 12 code)。"""
    entries: list[dict[str, Any]] = []
    for cluster, count in PROMPT_CLUSTERS:
        for i in range(count):
            base = f"{cluster}_{i:02d}"
            deps = ([_dotted(f"{cluster}_{i + 1:02d}")] if i + 1 < count else []) + [
                _dotted(d) for d in EXTRA_EDGES.get(base, [])
            ]
            entries.append(_prompt_entry(base, cluster, i, deps))
    acc_in = {
        "type": "object",
        "properties": {"acc": {"type": "integer"}},
        "required": ["acc"],
    }
    total_out = {
        "type": "object",
        "properties": {"total": {"type": "integer"}},
        "required": ["total"],
    }
    entries.append(
        _code_entry(
            "mega_pipeline",
            [_dotted("chain_00")],
            {
                "type": "object",
                "properties": {"seed": {"type": "integer"}},
                "required": ["seed"],
            },
            total_out,
        )
    )
    for i in range(10):
        nxt = _dotted(f"chain_{i + 1:02d}") if i < 9 else _dotted("hub_agg")
        entries.append(_code_entry(f"chain_{i:02d}", [nxt], acc_in, total_out))
    entries.append(_code_entry("hub_agg", [], acc_in, total_out))
    return entries


def check_dag(entries: list[dict[str, Any]]) -> None:
    """拓扑校验(Kahn):引用缺失或成环即 ``SystemExit`` 报错退出。"""
    deps = {e["name"]: set(e["permissions"]["skills"]) for e in entries}
    missing = sorted({d for ds in deps.values() for d in ds if d not in deps})
    if missing:
        raise SystemExit(f"引用了不存在的技能: {missing}")
    ready = [n for n, ds in deps.items() if not ds]
    done = 0
    while ready:
        name = ready.pop()
        done += 1
        for n, ds in deps.items():
            if name in ds:
                ds.discard(name)
                if not ds:
                    ready.append(n)
    if done != len(deps):
        cycle = sorted(n for n, ds in deps.items() if ds)
        raise SystemExit(f"技能依赖存在循环: {cycle}")


def main() -> int:
    entries = build_entries()
    check_dag(entries)
    body = yaml.safe_dump({"skills": entries}, allow_unicode=True, sort_keys=False)
    OUT.write_text(_HEADER + body, encoding="utf-8")
    print(f"已生成 {OUT}:{len(entries)} 个技能,DAG 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

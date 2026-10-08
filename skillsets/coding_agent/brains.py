"""coding_agent skillset 的 scripted mock 大脑(离线演示/测试的确定性复现模式)。

消息解析约定与 travel_planner 的 ``travel_brain`` 相同(system 首行
``# skill: <name>`` 识别技能帧;首条可解析 JSON 的 USER 消息为帧输入;
TOOL 结果为 ``{"ok","value","error"}``)。核心在"真读真改真跑":

- ``coding.explore``:真调 file.list/file.read 读 fixture 源码,从读到的内容里
  定位"函数名与实现矛盾"的 return(add 做成减法),不臆造没读过的内容;
- ``coding.plan``:真调 file.write 把计划落盘(带 feedback 时出修订版);
- ``coding.implement``:先真读计划与源码,第一轮**故意改错**(只交换操作数,
  演示"改一半"),带 test_feedback 的后续轮才对症修复——old/new_string 都按
  真实文件内容计算,编辑是真编辑;
- ``coding.verify``:真调 shell.exec 跑验证命令(shell 在 workdir 真执行,
  第一轮真失败、第二轮真通过),按 exit_code 汇报;``failing_verify_brain``
  变体恒报失败,用于修复循环上限测试;
- ``coding.review``:git diff(可选,失败不碍)+ 真读改动文件,按真实内容
  判定根因是否修掉;
- ``coding.agent.chat``:多轮会话接待(P2-M3)——开场有 opening 直接起第一轮,
  否则开场即 user.ask;每轮 agent.run 子帧返回后 user.notify 汇报、user.ask
  要下一任务;答复以 退出/结束/bye/quit 开头收官;test_command 从用户指令
  原文提取(含绝对路径 python 形态),没给则按 fixture 形态给缺省;
- ``coding.agent.run``:table-driven 状态机,按已完成调用推进
  探索→计划→人审(ask_supervisor)→修复循环→collect_diff→review→结果 JSON。
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)

# ---------------------------------------------------------------------------
# 请求解析(同 travel_brain 约定)
# ---------------------------------------------------------------------------


def _skill_name(req: ChatRequest) -> str:
    """system 消息首行 ``# skill: <name>`` → 技能名。"""
    first_line = req.messages[0].content.splitlines()[0]
    return first_line.split(":", 1)[1].strip()


def _frame_input(req: ChatRequest) -> dict[str, Any]:
    """首条可解析为 JSON 对象的 USER 消息 = 帧输入(状态元消息等自动跳过)。"""
    for m in req.messages:
        if m.role is Role.USER:
            try:
                data = json.loads(m.content)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return data
    raise AssertionError("帧上下文缺少输入消息")


def _named_calls(req: ChatRequest) -> list[dict[str, Any]]:
    """按序收集已完成调用 ``{"name","args","payload"}``(payload 为 {"ok","value","error"})。"""
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    seq: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                calls[tc.id] = (tc.name, dict(tc.args or {}))
        elif m.role is Role.TOOL and m.tool_call_id in calls:
            name, args = calls[m.tool_call_id]
            seq.append({"name": name, "args": args, "payload": json.loads(m.content)})
    return seq


def _calls_named(seq: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """某调用名的全部已完成调用(保序)。"""
    return [c for c in seq if c["name"] == name]


# ---------------------------------------------------------------------------
# 应答构造
# ---------------------------------------------------------------------------


def _final(payload: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _call(tc: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _issue(skill: str, seq: list[dict[str, Any]], name: str, args: dict[str, Any]) -> ChatResponse:
    """发下一个调用;call id 由已完成结果数推导(帧内唯一且确定性)。"""
    return _call(ToolCall(id=f"call-{skill}-{len(seq)}", name=name, args=args))


# ---------------------------------------------------------------------------
# 源码分析(真读真判:函数名与实现矛盾的 return)
# ---------------------------------------------------------------------------

_FUNC_RE = re.compile(r"\s*def\s+(\w+)\s*\(([^)]*)\)")
_RETURN_RE = re.compile(r"\s*return\s+(.+?)\s*(?:#.*)?$")
_ADD_WORDS = ("add", "sum", "plus")
_MUL_WORDS = ("multiply", "mul", "product")
_PY_FILE_RE = re.compile(r"[\w./-]+\.py")

#: 探索单帧最多读的源码文件数(防大仓库烧步数)
_EXPLORE_MAX_READS = 4


def _plain_lines(numbered: str) -> list[str]:
    """fs_read 的行号前缀内容 → 源码行列表(剥离 ``"N\\t"`` 前缀)。"""
    out: list[str] = []
    for line in numbered.splitlines():
        m = re.match(r"^\d+\t(.*)$", line)
        out.append(m.group(1) if m else line)
    return out


def _find_suspicious_return(lines: list[str]) -> tuple[str, int, str, str] | None:
    """找"函数名与实现矛盾"的 return → (函数名, 行号, return 行原文, 正确运算符)。

    判定:函数名含 add/sum/plus 而首个 return 是减法(不含 +),或函数名含
    multiply/mul/product 而首个 return 是加法(不含 *)。每个函数只看第一个
    return(短路返回之外的实现行不判)。
    """
    current: str | None = None
    for lineno, text in enumerate(lines, 1):
        m = _FUNC_RE.match(text)
        if m:
            current = m.group(1)
            continue
        r = _RETURN_RE.match(text)
        if r and current:
            expr = r.group(1)
            low = current.lower()
            if any(w in low for w in _ADD_WORDS) and re.search(r"\w\s*-\s*\w", expr) and "+" not in expr:
                return current, lineno, text.strip(), "+"
            if any(w in low for w in _MUL_WORDS) and re.search(r"\w\s*\+\s*\w", expr) and "*" not in expr:
                return current, lineno, text.strip(), "*"
            current = None  # 每个函数只判第一个 return
    return None


def _compute_edit(lines: list[str], *, fix: bool) -> tuple[str, str] | None:
    """定位可疑 return,给出 (old_string, new_string);fix=True 对症修复,False 改错。

    改错(第一轮,演示"改一半"):只交换操作数,不换运算符——a - b → b - a;
    修复(带反馈的后续轮):按函数名换成正确运算符,操作数规范化排序,
    并附修复注释——注释同时让文件大小变化,确保上一轮 pytest 写下的
    __pycache__ 必然失效(CPython 以 源 mtime+大小 判 pyc 有效性,
    同大小同时间刻度的连续编辑会跑到陈旧字节码,实测坑)。
    """
    hit = _find_suspicious_return(lines)
    if hit is None:
        return None
    _func, _lineno, text, expected = hit
    expr = text.split("return", 1)[1].split("#", 1)[0].strip()
    m = re.match(r"(\w+)\s*([-+*])\s*(\w+)", expr)
    if m is None:
        return None
    if fix:
        left, right = sorted((m.group(1), m.group(3)))
        return f"return {expr}", f"return {left} {expected} {right}  # 修复:按函数名改为{expected}运算"
    new_expr = f"{m.group(3)} {m.group(2)} {m.group(1)}"
    return f"return {expr}", f"return {new_expr}"


def _slug(task: str) -> str:
    """任务文本 → 计划文件名 slug(确定性,如 "修复 calc 的 bug" → "calc-bug")。"""
    tokens = re.findall(r"[A-Za-z0-9]+", task)
    return "-".join(tokens[:3]).lower() or "plan"


# ---------------------------------------------------------------------------
# coding.explore:真读源码,从读到的内容定位矛盾
# ---------------------------------------------------------------------------


def _explore(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    lists = _calls_named(seq, "system.file.list")
    if not lists:
        focus = inp.get("focus_paths") or []
        return _issue(skill, seq, "system.file.list", {"path": focus[0] if focus else "."})
    listing = lists[-1]["payload"]
    assert listing["ok"], f"file.list 失败,demo 大脑不予恢复: {listing}"
    entries = [
        e["path"]
        for e in listing["value"]["entries"]
        if not e["is_dir"] and e["path"].endswith(".py")
    ]
    read_paths = {c["args"].get("path") for c in _calls_named(seq, "system.file.read")}
    todo = [p for p in entries if p not in read_paths][:_EXPLORE_MAX_READS]
    if todo:
        return _issue(skill, seq, "system.file.read", {"path": todo[0]})
    sources: dict[str, list[str]] = {}
    for c in _calls_named(seq, "system.file.read"):
        payload = c["payload"]
        if payload["ok"]:
            sources[str(c["args"]["path"])] = _plain_lines(str(payload["value"]))
    findings: list[str] = []
    for path, lines in sources.items():
        hit = _find_suspicious_return(lines)
        if hit is not None:
            func, lineno, text, expected = hit
            findings.append(
                f"{path}:{lineno} 的 {func}() 实现与函数名矛盾:`{text}`"
                f"(名字是 {func},应使用运算符 {expected})"
            )
    if findings:
        findings.append("test_calc.py 的 test_add 断言 add(2, 3) == 5,上述实现必失败")
    else:
        findings.append("通读相关源码后未发现函数名与实现的明显矛盾(详见 relevant_files)")
    return _final({"findings": "\n".join(findings), "relevant_files": sorted(sources)})


# ---------------------------------------------------------------------------
# coding.plan:真写计划文件(带 feedback 出修订版)
# ---------------------------------------------------------------------------


def _plan(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    plan_path = f".agent-os/plans/{_slug(inp['task'])}.md"
    if not _calls_named(seq, "system.file.write"):
        lines = [
            f"# 实施计划:{inp['task']}",
            "",
            "## 目标",
            str(inp["task"]),
            "",
            "## 根因分析(来自 coding.explore)",
            str(inp["findings"]),
            "",
            "## 改动步骤",
            "1. 按根因分析修复指出文件中的实现(最小改动);",
            "2. 运行验证命令确认测试通过;不通过则按输出继续修。",
            "",
            "## 验证方式",
            "由 coding.verify 执行调用方给的验证命令,exit_code 0 即通过。",
        ]
        if inp.get("feedback"):
            lines += [
                "",
                "## 修订记录",
                f"上一版人类意见:{inp['feedback']}(本版已据此修订)",
            ]
        return _issue(
            skill, seq, "system.file.write", {"path": plan_path, "content": "\n".join(lines) + "\n"}
        )
    written = _calls_named(seq, "system.file.write")[-1]["payload"]
    assert written["ok"], f"计划写入失败,demo 大脑不予恢复: {written}"
    summary = f"按根因分析修复并在 workdir 内验证;计划见 {plan_path}"
    if inp.get("feedback"):
        summary = f"{summary}(第 2 版,已按人类意见修订)"
    return _final({"plan_path": plan_path, "summary": summary})


# ---------------------------------------------------------------------------
# coding.implement:真读计划与源码,按真实内容落编辑(第一轮故意改错)
# ---------------------------------------------------------------------------


def _implement(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    feedback = str(inp.get("test_feedback") or "")
    plan_path = str(inp["plan_path"])
    reads = _calls_named(seq, "system.file.read")
    if not any(c["args"].get("path") == plan_path for c in reads):
        return _issue(skill, seq, "system.file.read", {"path": plan_path})
    plan_text = ""
    for c in reads:
        if c["args"].get("path") == plan_path and c["payload"]["ok"]:
            plan_text = str(c["payload"]["value"])
    candidates = [p for p in _PY_FILE_RE.findall(plan_text) if not p.startswith("test_")]
    target = candidates[0] if candidates else "calc.py"
    if not any(c["args"].get("path") == target for c in reads):
        return _issue(skill, seq, "system.file.read", {"path": target})
    edits = _calls_named(seq, "system.file.edit") + _calls_named(seq, "system.file.write")
    if not edits:
        source_text = next(
            (str(c["payload"]["value"]) for c in reads
             if c["args"].get("path") == target and c["payload"]["ok"]),
            "",
        )
        edit = _compute_edit(_plain_lines(source_text), fix=bool(feedback))
        if edit is None:
            return _final(
                {"changed_files": [], "notes": f"未在 {target} 定位到需要修改的位置"}
            )
        old, new = edit
        return _issue(
            skill, seq, "system.file.edit",
            {"path": target, "old_string": old, "new_string": new},
        )
    last = edits[-1]["payload"]
    if not last["ok"]:
        error = last.get("error") or {}
        return _final(
            {
                "changed_files": [],
                "notes": f"编辑未生效({error.get('kind', 'unknown')}: "
                f"{error.get('message', '')}),目标文件可能已是期望状态",
            }
        )
    notes = (
        f"按测试反馈对症修复 {target}"
        if feedback
        else f"按计划首次修改 {target}(交换操作数,尚未对症)"
    )
    return _final({"changed_files": [target], "notes": notes})


# ---------------------------------------------------------------------------
# coding.verify:真跑验证命令,按 exit_code 汇报
# ---------------------------------------------------------------------------


def _verify(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    shells = _calls_named(seq, "system.shell.exec")
    if not shells:
        return _issue(skill, seq, "system.shell.exec", {"command": inp["command"]})
    payload = shells[-1]["payload"]
    if not payload["ok"]:
        error = payload.get("error") or {}
        return _final(
            {
                "passed": False,
                "summary": f"验证命令未被执行({error.get('kind', 'unknown')}:被拒绝或工具错误)",
                "output_tail": "",
            }
        )
    value = payload["value"]
    passed = value.get("exit_code") == 0
    text = str(value.get("text") or f"{value.get('stdout', '')}\n{value.get('stderr', '')}")
    return _final(
        {
            "passed": passed,
            "summary": f"exit_code={value.get('exit_code')},验证{'通过' if passed else '未通过'}",
            "output_tail": text[-2000:],
        }
    )


def _verify_forced_fail(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    """verify 恒失败变体(修复循环上限测试剧本):不跑命令,直接报失败。"""
    return _final(
        {
            "passed": False,
            "summary": "(测试剧本)验证恒失败",
            "output_tail": "forced failure: test_add 恒失败(verify 剧本)",
        }
    )


# ---------------------------------------------------------------------------
# coding.review:git diff(可选)+ 真读改动文件,按真实内容判定
# ---------------------------------------------------------------------------


def _review(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    if not _calls_named(seq, "system.shell.exec"):
        return _issue(
            skill, seq, "system.shell.exec",
            {"command": "git diff --stat && git status --porcelain"},
        )
    plan_path = str(inp.get("plan_path") or "")
    reads = _calls_named(seq, "system.file.read")
    if plan_path and not any(c["args"].get("path") == plan_path for c in reads):
        return _issue(skill, seq, "system.file.read", {"path": plan_path})
    changed = [str(p) for p in inp.get("changed_files") or []]
    for path in changed:
        if not any(c["args"].get("path") == path for c in reads):
            return _issue(skill, seq, "system.file.read", {"path": path})
    issues: list[str] = []
    for c in reads:
        path = str(c["args"].get("path"))
        if path == plan_path or path not in changed:
            continue
        payload = c["payload"]
        if not payload["ok"]:
            issues.append(f"{path}:读取失败,无法确认改动内容")
            continue
        hit = _find_suspicious_return(_plain_lines(str(payload["value"])))
        if hit is not None:
            func, lineno, text, expected = hit
            issues.append(
                f"{path}:{lineno} 的 {func}() 实现仍与函数名矛盾:`{text}`(应为 {expected})"
            )
    verdict = "concerns" if issues else "pass"
    return _final({"verdict": verdict, "issues": issues})


# ---------------------------------------------------------------------------
# coding.agent.run:table-driven 编排状态机
# ---------------------------------------------------------------------------

#: 工作区引导文档(存在才读)
_BOOTSTRAP_DOCS = ("AGENTS.md", "CLAUDE.md", "README.md")


def _plan_verdict(answer: str) -> str:
    """计划审批协议(与 tool-confirm/escalation 门同款词汇):
    以 "approve" 开头(含 approve-once、approve-run)→ approved;
    以 "deny" 开头 → denied(冒号后内容为修改意见);其余 → invalid(重问)。
    """
    answer = answer.strip()
    if answer.startswith("approve"):
        return "approved"
    if answer.startswith("deny"):
        return "denied"
    return "invalid"


def _plan_feedback(answer: str) -> str:
    """deny 答复的修改意见:首个中英冒号后的内容;无冒号取全文。"""
    parts = re.split(r"[:：]", answer, maxsplit=1)
    return parts[1].strip() if len(parts) > 1 and parts[1].strip() else answer


def _plan_ask(
    skill: str, seq: list[dict[str, Any]], plan_v: dict[str, Any], *, invalid_retry: bool = False
) -> ChatResponse:
    """发计划审批提问:不限定 options(冒号附意见须为合法自由文本),词汇要求写进问题。"""
    note = "上次答复不在合法词汇内,请按要求作答。" if invalid_retry else ""
    return _issue(
        skill, seq, "ask_supervisor",
        {
            "question": f"实施计划已写入 {plan_v['plan_path']}:{plan_v['summary']}。"
            f"批准执行吗?{note}请用 approve-once / approve-run / deny 作答"
            "(deny 可附冒号与修改意见)",
            "context": {"plan_path": plan_v["plan_path"]},
        },
    )


def _failed_final(task: str, command: str, plan_path: str, summary: str) -> ChatResponse:
    return _final(
        {
            "status": "failed",
            "summary": summary,
            "changed_files": [],
            "tests": {"command": command, "ran": False, "passed": False},
            "plan_path": plan_path,
        }
    )


def _entry(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    task = str(inp["task"])
    command = str(inp["test_command"])
    plan_path = f".agent-os/plans/{_slug(task)}.md"
    # 1. 列目录
    lists = _calls_named(seq, "system.file.list")
    if not lists:
        return _issue(skill, seq, "system.file.list", {"path": "."})
    listing = lists[-1]["payload"]
    entries = listing["value"]["entries"] if listing["ok"] else []
    # 2. 读引导文档(存在才读,每份一次调用)
    docs = [
        e["path"] for e in entries
        if not e["is_dir"] and e["path"].rsplit("/", 1)[-1] in _BOOTSTRAP_DOCS
    ]
    read_paths = {c["args"].get("path") for c in _calls_named(seq, "system.file.read")}
    for doc in docs:
        if doc not in read_paths:
            return _issue(skill, seq, "system.file.read", {"path": doc})
    # 3. 任务清单
    if not _calls_named(seq, "system.task.todo_write"):
        return _issue(
            skill, seq, "system.task.todo_write",
            {
                "items": [
                    {"id": "explore", "text": "探索代码,定位问题"},
                    {"id": "plan", "text": "写实施计划并获人类批准"},
                    {"id": "fix", "text": "修复循环:改代码→跑测试"},
                    {"id": "verify", "text": "确认验证命令通过"},
                    {"id": "review", "text": "审查改动与计划一致性"},
                ]
            },
        )
    # 4. 探索
    explores = _calls_named(seq, "skill.coding.explore")
    if not explores:
        args: dict[str, Any] = {"question": f"定位并分析:{task}"}
        if inp.get("focus_paths"):
            args["focus_paths"] = list(inp["focus_paths"])
        return _issue(skill, seq, "skill.coding.explore", args)
    explore = explores[-1]["payload"]
    if not explore["ok"]:
        return _failed_final(task, command, plan_path, "coding.explore 失败,无法定位问题")
    findings = str(explore["value"]["findings"])
    # 5. 计划 + 人审(gate 同款词汇;提问至多 2 次,计划至多重出 1 版)
    plans = _calls_named(seq, "skill.coding.plan")
    asks = _calls_named(seq, "ask_supervisor")
    if asks and not asks[-1]["payload"]["ok"]:
        return _failed_final(task, command, plan_path, "计划审批未获上级应答")
    verdicts = [
        _plan_verdict(str(a["payload"]["value"].get("answer", ""))) for a in asks
    ]
    if not any(v == "approved" for v in verdicts):
        if plans and len(asks) < len(plans):
            # 最新计划尚未问 → 提问
            return _plan_ask(skill, seq, plans[-1]["payload"]["value"])
        if len(asks) >= 2:
            return _failed_final(task, command, plan_path, "实施计划两次审批均未获批准,任务终止")
        if plans and plans[-1]["payload"]["ok"] is False:
            return _failed_final(task, command, plan_path, "coding.plan 写计划失败")
        if verdicts and verdicts[-1] == "denied":
            # 拒绝:冒号后修改意见带入下一版计划
            answer = str(asks[-1]["payload"]["value"].get("answer", ""))
            return _issue(
                skill, seq, "skill.coding.plan",
                {
                    "task": task,
                    "findings": findings,
                    "feedback": f"第 {len(plans)} 版计划被拒,人类意见:{_plan_feedback(answer)}",
                },
            )
        if verdicts and verdicts[-1] == "invalid":
            # 无效答复:不重出计划,同一计划重问一次
            return _plan_ask(skill, seq, plans[-1]["payload"]["value"], invalid_retry=True)
        # 首次:出第 1 版计划
        return _issue(skill, seq, "skill.coding.plan", {"task": task, "findings": findings})
    plan_path = plans[-1]["payload"]["value"]["plan_path"]
    # 6. 修复循环
    fixes = _calls_named(seq, "skill.coding.fix_loop")
    if not fixes:
        return _issue(
            skill, seq, "skill.coding.fix_loop",
            {"task": task, "plan_path": plan_path, "test_command": command},
        )
    fix = fixes[-1]["payload"]
    if not fix["ok"]:
        return _failed_final(task, command, plan_path, "coding.fix_loop 调用失败(中途异常)")
    fix_v = fix["value"]
    # 7. 结构化 diff(非 git 仓库/被拒绝时降级为空,不致命)
    diffs = _calls_named(seq, "skill.coding.collect_diff")
    if not diffs:
        return _issue(skill, seq, "skill.coding.collect_diff", {})
    diff_payload = diffs[-1]["payload"]
    diff_text = ""
    if diff_payload["ok"] and diff_payload["value"].get("available"):
        v = diff_payload["value"]
        diff_text = f"{v['stat']}\n{v['status']}".strip()
    # 8. 审查
    reviews = _calls_named(seq, "skill.coding.review")
    if not reviews:
        return _issue(
            skill, seq, "skill.coding.review",
            {
                "plan_path": plan_path,
                "changed_files": list(fix_v["changed_files"]),
                "diff": diff_text,
            },
        )
    review = reviews[-1]["payload"]
    rev_v = (
        review["value"]
        if review["ok"]
        else {"verdict": "concerns", "issues": ["coding.review 调用失败,未能完成审查"]}
    )
    # 9. 结果 JSON
    status = "done" if fix_v["status"] == "done" and rev_v["verdict"] == "pass" else "partial"
    summary = (
        f"修复循环 {fix_v['status']}(共 {fix_v['iterations']} 轮),审查 {rev_v['verdict']}"
        f";改动 {len(fix_v['changed_files'])} 个文件"
    )
    if rev_v.get("issues"):
        summary += f";待办问题 {len(rev_v['issues'])} 条"
    return _final(
        {
            "status": status,
            "summary": summary,
            "changed_files": list(fix_v["changed_files"]),
            "tests": {
                "command": command,
                "ran": bool(str(fix_v.get("last_test_output", "")).strip()),
                "passed": fix_v["status"] == "done",
            },
            "plan_path": plan_path,
        }
    )


# ---------------------------------------------------------------------------
# coding.agent.chat:多轮会话接待(长存 run + user.ask 循环;P2-M3)
# ---------------------------------------------------------------------------

#: 用户收官词汇(答复以其开头即收官;中文精确匹配,英文小写匹配)
_CHAT_EXIT_ZH = ("退出", "结束")
_CHAT_EXIT_EN = ("bye", "quit")

#: 用户指令里的验证命令提取:python 标记取绝对路径形态(/\S*?python)或裸词
#: 边界(\bpython)——``\S`` 含 CJK 字符,贪婪 \S* 会把"验证命令:..."这类
#:  glued 中文前缀吞进命令(实测坑:shell 拿到 "bug,验证命令:/..." 报 127);
#: 尾部同理只收 ASCII shell 字符,中文尾巴(如"...-x 验证")不混入
_CHAT_CMD_RE = re.compile(r"(?:/\S*?|\b)python\S*\s+-m\s+pytest[A-Za-z0-9_\s./=-]*")


def _extract_test_command(text: str) -> str:
    """从用户指令提取验证命令原文;没给则按 fixture 仓库形态给缺省(mock 剧本约定)。"""
    m = _CHAT_CMD_RE.search(text)
    if m:
        return m.group(0).strip()
    return f"{sys.executable} -m pytest test_calc.py -x"


def _chat(inp: dict[str, Any], seq: list[dict[str, Any]], skill: str) -> ChatResponse:
    """会话循环状态机:收尾规则只看最近一次调用的类型——

    run/collect_diff 之后 → notify 汇报;notify 之后 → ask 要下一任务;
    ask 答复以收官词开头 → 收官 JSON,否则答复原文即下一轮任务;
    无任何调用 → 有 opening 直接起第一轮,否则开场即 ask。
    """
    runs = _calls_named(seq, "skill.coding.agent.run")
    opening = str(inp.get("opening") or "").strip()
    turns = len(runs)
    last = seq[-1] if seq else None

    if last is not None and last["name"] == "system.user.ask":
        payload = last["payload"]
        if not payload["ok"]:
            return _final(
                {"status": "failed", "summary": "user.ask 通道故障,无法继续会话", "turns": turns}
            )
        answer = str(payload["value"]).strip()
        if answer.startswith(_CHAT_EXIT_ZH) or answer.lower().startswith(_CHAT_EXIT_EN):
            return _final(
                {
                    "status": "done",
                    "summary": f"会话收官:共完成 {turns} 轮任务",
                    "turns": turns,
                }
            )
        task = answer
        return _issue(
            skill, seq, "skill.coding.agent.run",
            {"task": task, "test_command": _extract_test_command(task)},
        )
    if last is not None and last["name"] == "skill.coding.agent.run":
        payload = last["payload"]
        if payload["ok"]:
            v = payload["value"]
            tests = v.get("tests") or {}
            message = (
                f"任务 {v.get('status')}:{v.get('summary')};"
                f"改动 {v.get('changed_files')};tests.passed={tests.get('passed')}"
            )
        else:
            message = f"任务子帧失败:{(payload.get('error') or {}).get('message', '')}"
        return _issue(skill, seq, "system.user.notify", {"message": message})
    if last is not None and last["name"] == "system.user.notify":
        return _issue(
            skill, seq, "system.user.ask",
            {"question": "下一步做什么?(说 退出/结束 收官)"},
        )
    # 开场(无任何调用)
    if opening:
        return _issue(
            skill, seq, "skill.coding.agent.run",
            {"task": opening, "test_command": _extract_test_command(opening)},
        )
    return _issue(
        skill, seq, "system.user.ask", {"question": "要做什么?(说 退出/结束 收官)"}
    )


# ---------------------------------------------------------------------------
# 入口:按 system 首行的技能名分派
# ---------------------------------------------------------------------------


def _dispatch(req: ChatRequest, *, force_verify_fail: bool = False) -> ChatResponse:
    name = _skill_name(req)
    seq = _named_calls(req)
    if name == "coding.verify" and force_verify_fail:
        return _verify_forced_fail(_frame_input(req), seq, name)
    handlers = {
        "coding.agent.run": _entry,
        "coding.agent.chat": _chat,
        "coding.explore": _explore,
        "coding.plan": _plan,
        "coding.implement": _implement,
        "coding.verify": _verify,
        "coding.review": _review,
    }
    handler = handlers.get(name)
    if handler is None:
        raise AssertionError(f"coding_brain 未覆盖的技能: {name}")
    return handler(_frame_input(req), seq, name)


def coding_brain(req: ChatRequest) -> ChatResponse:
    """coding_agent 的 scripted mock 大脑(离线演示/e2e 测试用)。"""
    return _dispatch(req)


def failing_verify_brain(req: ChatRequest) -> ChatResponse:
    """verify 恒失败变体:修复循环 5 轮耗尽 → partial(循环上限测试用)。"""
    return _dispatch(req, force_verify_fail=True)

"""Agent OS Debugger P2:CLI 调试前端(host/cli/debug.py)锚点测试。

固定约定:

- ``agent-os debug <skill> --input ...``:装配内核时挂 DebugController,run 在
  与 REPL 同一事件循环的后台 task 上跑;入口一次性 ``step`` 断点(pdb 语义)
  使 run 启动即停在第一条 ``pre:step``;
- REPL 命令经 monkeypatch 的 ``sys.stdin``(StringIO)脚本化喂入,EOF 等价 ``q``
  (摘下会话让 run 跑完);stdout 捕获断言暂停点 / bt / i 输出;
- 退出码沿用 §3.3:0 成功 / 3 run 失败或中止(含调试器 ``stop``)。

确定性来源:``demo.fib`` + ``fib_brain``(tests/helpers),n=3 的帧/步序列固定
(根帧 depth=1 调子帧 n=2 base case,再调一次 system.python.exec 收尾)。

P5 增量(时间旅行):``agent-os debug --replay <run_id>`` 从产物目录重建回放内核
(LLM Mock 回放,工具真实重跑)后进同一 REPL,停点序列与 live 一致;
``--until-step N`` 一次性步数断点直达第 N 条 ``pre:step``。
"""

from __future__ import annotations

import io
import json

from agent_os.host.cli.main import main
from tests.helpers.config import write_config as _write_config


def _run_debug(capsys, monkeypatch, tmp_path, script: str) -> tuple[int, str, dict]:
    """脚本化 stdin 跑 ``agent-os debug demo.fib --input {"n": 3}``。

    返回 (退出码, 全部 stdout, 末行 RunRecord JSON)。
    """
    cfg = _write_config(tmp_path)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(script)
    )
    rc = main(
        [
            "debug",
            "demo.fib",
            "--input",
            '{"n": 3}',
            "--config",
            str(cfg),
            "--artifacts",
            str(tmp_path / "runs"),
        ]
    )
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    return rc, out, json.loads(lines[-1])


def _pauses(out: str) -> list[str]:
    """stdout 中的暂停点摘要行(``dbg> `` 提示符不换行,``[paused]`` 可能在行中)。"""
    return [line[line.index("[paused]") :] for line in out.splitlines() if "[paused]" in line]


# ---------------------------------------------------------------------------
# 入口暂停 / bt / i / q
# ---------------------------------------------------------------------------


def test_debug_entry_pause_backtrace_inspect_quit(tmp_path, capsys, monkeypatch):
    rc, out, record = _run_debug(capsys, monkeypatch, tmp_path, "bt\ni --messages\nq\n")
    pauses = _pauses(out)
    assert len(pauses) == 1
    assert "signal=pre:step" in pauses[0] and "reason=breakpoint" in pauses[0]
    assert "depth=1" in pauses[0]
    # bt:入口只有根帧,* 标记暂停帧
    assert "* depth=1" in out and "skill=local:demo.fib" in out
    # i --messages:帧摘要 + 用户输入消息(渲染对齐 CLI inspect)
    assert "skill: demo.fib" in out
    assert "input: {'n': 3}" in out
    assert "[user]" in out
    # q:摘下会话,run 跑完
    assert rc == 0
    assert record["status"] == "done"
    assert record["result"] == {"seq": [0, 1, 1]}


# ---------------------------------------------------------------------------
# 单步停点序列(si / so)
# ---------------------------------------------------------------------------


def test_debug_step_into_over_sequence(tmp_path, capsys, monkeypatch):
    rc, out, record = _run_debug(capsys, monkeypatch, tmp_path, "si\nso\nq\n")
    pauses = _pauses(out)
    assert len(pauses) == 3
    # 入口:根帧第一条 pre:step
    assert "signal=pre:step" in pauses[0] and "depth=1" in pauses[0]
    # si:步入子帧(n=2 base case)的第一条 pre:step
    assert "signal=pre:step" in pauses[1] and "reason=step:into" in pauses[1]
    assert "depth=2" in pauses[1]
    # so:子帧 base case 一步出结果,停在该帧 pre:frame.pop
    assert "signal=pre:frame.pop" in pauses[2] and "reason=step:over" in pauses[2]
    assert rc == 0
    assert record["result"] == {"seq": [0, 1, 1]}


# ---------------------------------------------------------------------------
# 断点:tool 命中 + info b 命中计数 + del
# ---------------------------------------------------------------------------


def test_debug_tool_breakpoint_and_info_b(tmp_path, capsys, monkeypatch):
    script = "b tool system.python.exec\nc\ninfo b\ndel bp-不存在\nq\n"
    rc, out, record = _run_debug(capsys, monkeypatch, tmp_path, script)
    pauses = _pauses(out)
    # 入口暂停 + 工具断点暂停
    assert any(
        "signal=pre:tool.call" in p and "tool=system.python.exec" in p for p in pauses
    )
    # info b:tool_call 断点已命中一次
    info = [line for line in out.splitlines() if "kind=tool_call" in line and "hits=" in line]
    assert info and "hits=1" in info[0]
    assert "*** 断点不存在: bp-不存在" in out
    assert rc == 0
    assert record["result"] == {"seq": [0, 1, 1]}


def test_debug_step_breakpoint_hits(tmp_path, capsys, monkeypatch):
    rc, out, _ = _run_debug(capsys, monkeypatch, tmp_path, "b step\nc\nc\ninfo b\nq\n")
    pauses = _pauses(out)
    # 入口 + 两次 c 各停在一条 pre:step
    assert len(pauses) == 3
    info = [line for line in out.splitlines() if "kind=step" in line and "hits=" in line]
    assert info and "hits=2" in info[0]
    assert rc == 0


# ---------------------------------------------------------------------------
# 干预:mod 改工具参数 / inj 注入消息
# ---------------------------------------------------------------------------


def test_debug_modify_tool_args(tmp_path, capsys, monkeypatch):
    script = (
        "b tool system.python.exec\nc\n"
        'mod {"code": "result = 99\\nprint(result)"}\n'
    )
    rc, out, record = _run_debug(capsys, monkeypatch, tmp_path, script)
    assert any("tool=system.python.exec" in p for p in _pauses(out))
    assert "已修改工具参数" in out
    # mod 生效即放行;其后无断点命中,run 直接跑完(EOF 不再读)
    assert rc == 0
    assert record["result"] == {"seq": [0, 1, 99]}


def test_debug_modify_tool_args_wrong_pause_point(tmp_path, capsys, monkeypatch):
    # 入口停在 pre:step(非 pre:tool.call):mod 报错并留在命令循环
    rc, out, record = _run_debug(capsys, monkeypatch, tmp_path, 'mod {"n": 1}\nq\n')
    assert "*** modify_tool_args 仅在暂停于 pre:tool.call 时有效" in out
    assert rc == 0
    assert record["result"] == {"seq": [0, 1, 1]}


def test_debug_inject_message(tmp_path, capsys, monkeypatch):
    rc, out, record = _run_debug(capsys, monkeypatch, tmp_path, "inj 请继续\n")
    assert "已注入消息到帧" in out
    assert rc == 0
    assert record["status"] == "done"
    assert record["result"] == {"seq": [0, 1, 1]}


# ---------------------------------------------------------------------------
# stop / EOF
# ---------------------------------------------------------------------------


def test_debug_stop_aborts_run(tmp_path, capsys, monkeypatch):
    rc, _out, record = _run_debug(capsys, monkeypatch, tmp_path, "stop\n")
    assert rc == 3
    assert record["status"] == "aborted"


def test_debug_stdin_eof_detaches_and_finishes(tmp_path, capsys, monkeypatch):
    # 空脚本:入口暂停后 stdin 立即 EOF,等价 q
    rc, out, record = _run_debug(capsys, monkeypatch, tmp_path, "")
    assert "stdin EOF" in out
    assert rc == 0
    assert record["status"] == "done"
    assert record["result"] == {"seq": [0, 1, 1]}


# ---------------------------------------------------------------------------
# P5 时间旅行:--replay(回放内核 + 调试会话)/ --until-step
# ---------------------------------------------------------------------------


def _record_run(tmp_path, capsys, n: int) -> str:
    """先用 ``agent-os run`` 录制一次 fib 产物(trace+checkpoint 是回放数据源)。"""
    cfg = _write_config(tmp_path)
    rc = main(
        [
            "run",
            "demo.fib",
            "--input",
            json.dumps({"n": n}),
            "--config",
            str(cfg),
            "--artifacts",
            str(tmp_path / "runs"),
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    return json.loads(out[out.index("{") :])["run_id"]


def _run_replay_debug(
    capsys, monkeypatch, tmp_path, run_id: str, script: str, *extra: str
) -> tuple[int, str, dict]:
    """脚本化 stdin 跑 ``agent-os debug --replay <run_id>``(参数同 _run_debug)。"""
    cfg = _write_config(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    rc = main(
        [
            "debug",
            "--replay",
            run_id,
            "--config",
            str(cfg),
            "--artifacts",
            str(tmp_path / "runs"),
            *extra,
        ]
    )
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    return rc, out, json.loads(lines[-1])


def test_debug_replay_same_pause_sequence(tmp_path, capsys, monkeypatch):
    """录制 fib(3) → debug --replay:停点序列与 live 调试一致(LLM Mock 回放)。"""
    run_id = _record_run(tmp_path, capsys, 3)
    rc, out, record = _run_replay_debug(capsys, monkeypatch, tmp_path, run_id, "si\nso\nq\n")
    pauses = _pauses(out)
    assert len(pauses) == 3
    # 入口:根帧第一条 pre:step(与 live 相同)
    assert "signal=pre:step" in pauses[0] and "depth=1" in pauses[0]
    # si:步入子帧(n=2 base case)的第一条 pre:step
    assert "signal=pre:step" in pauses[1] and "reason=step:into" in pauses[1]
    assert "depth=2" in pauses[1]
    # so:停在该帧 pre:frame.pop
    assert "signal=pre:frame.pop" in pauses[2] and "reason=step:over" in pauses[2]
    assert rc == 0
    assert record["status"] == "done"
    assert record["result"] == {"seq": [0, 1, 1]}
    assert record["run_id"] != run_id, "回放调试应是新 run"


def test_debug_replay_until_step(tmp_path, capsys, monkeypatch):
    """--until-step 2:run 启动后不停,直达第 2 条 pre:step(子帧)才暂停。"""
    run_id = _record_run(tmp_path, capsys, 3)
    rc, out, record = _run_replay_debug(
        capsys, monkeypatch, tmp_path, run_id, "q\n", "--until-step", "2"
    )
    pauses = _pauses(out)
    assert len(pauses) == 1
    assert "signal=pre:step" in pauses[0] and "depth=2" in pauses[0]
    assert rc == 0
    assert record["result"] == {"seq": [0, 1, 1]}


def test_debug_replay_input_validation(tmp_path, capsys, monkeypatch):
    """--replay 与 <skill>/--input 互斥;产物目录不存在 → 退出码 2。"""
    run_id = _record_run(tmp_path, capsys, 3)
    cfg = _write_config(tmp_path)
    rc = main(
        [
            "debug",
            "demo.fib",
            "--input",
            '{"n": 3}',
            "--replay",
            run_id,
            "--config",
            str(cfg),
            "--artifacts",
            str(tmp_path / "runs"),
        ]
    )
    assert rc == 2
    assert "互斥" in capsys.readouterr().err

    rc = main(
        [
            "debug",
            "--replay",
            "run-不存在",
            "--config",
            str(cfg),
            "--artifacts",
            str(tmp_path / "runs"),
        ]
    )
    assert rc == 2
    assert "找不到 run 产物目录" in capsys.readouterr().err

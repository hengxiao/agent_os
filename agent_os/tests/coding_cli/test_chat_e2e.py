"""P2-M3 多轮会话 e2e:``agent-os-chat`` × coding.agent.chat(skillsets/coding_agent)。

全真形态(同 skillsets/coding_agent/tests 先例):mock 的只是大脑
(``brains:coding_brain``,PYTHONPATH 钉 skillset 目录),file/shell/todo 全真跑,
verify 真跑 pytest,爆炸半径由 ``--workdir``(K1 覆盖旗标)圈在 fixture 副本内。
计划审批/升权/tool-confirm 门逐次真实出现(supervisor 通道),驱动一律答
``approve-once``;会话任务提问(user 通道)按剧本作答。

驱动复用 test_repl_e2e 的 ``_ChatProc``(pexpect 式游标读取);所有等待有界,
finally 必 kill,防挂死 CI。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.coding_cli.test_repl_e2e import ROOT, _ChatProc

SKILLSET_DIR = ROOT.parent / "skillsets" / "coding_agent"
FIXTURES_DIR = SKILLSET_DIR / "tests" / "fixtures"

#: fixture 目标仓库的文件清单(同 skillset conftest copy_fixture 口径,按名白名单)
_FIXTURE_FILES = ("calc.py", "test_calc.py", "README.md")

#: 提问横幅锚点(repl.py 渲染契约):══ 提问(<channel>/<kind>)══
_BANNER = "══ 提问("
#: 会话任务提问(user 通道)的横幅标志;其余(supervisor/*)都是审批门
_USER_BANNER_MARK = "(user/"

CONFIG_TEMPLATE = """
[run]
model = "mock/coding"
max_depth = 8
max_steps = 150
max_cost = 3.0
compression = "off"

[providers.mock]
brain = "brains:coding_brain"

[tools]
builtins = true
python_exec = "subprocess"
python_orchestrate = true

[skills]
path = "{skills}"

[sidecars]
budget_guard = {{ max_cost = 3.0 }}
loop_detector = {{ threshold = 3, max_strikes = 2 }}
human_approval = {{}}

[telemetry]
dir = "{telemetry}"
"""


def _copy_fixture(dst: Path) -> Path:
    """fixture 迷你仓库复制(skillset conftest.copy_fixture 同义,不跨仓 import)。"""
    import shutil

    dst.mkdir(parents=True, exist_ok=True)
    for name in _FIXTURE_FILES:
        shutil.copy2(FIXTURES_DIR / name, dst / name)
    return dst


def _write_chat_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TEMPLATE.format(
            skills=SKILLSET_DIR / "skills.yaml", telemetry=tmp_path / "traces"
        ),
        encoding="utf-8",
    )
    return cfg


def _spawn_chat(tmp_path: Path, *args: str) -> _ChatProc:
    """起 agent-os-chat 子进程;PYTHONPATH 钉 skillset 目录(brains/handlers dotted path)。"""
    return _ChatProc(
        [sys.executable, "-m", "agent_os.host.coding_cli", *args],
        cwd=tmp_path,
        pythonpath=[str(SKILLSET_DIR)],
    )


def _answer_loop(proc: _ChatProc, pos: int, chat_answers: list[str]) -> int:
    """审批门循环:逐条横幅作答直到 run 落幕;返回收尾后的游标。

    supervisor 通道的门(计划审批/升权/tool-confirm)一律 ``approve-once``;
    user 通道的会话任务提问按 ``chat_answers`` 剧本作答(弹出顺序即轮次)。
    """
    answers = list(chat_answers)
    while True:
        idx, mstart, pos = proc.wait_any([_BANNER, "[run 结束]"], start=pos, timeout=60.0)
        if idx == 1:
            return pos
        banner = proc.line_at(mstart)
        if _USER_BANNER_MARK in banner:
            assert answers, f"会话提问超出剧本: {banner}"
            proc.write(answers.pop(0))
        else:
            proc.write("approve-once")
        _, _, pos = proc.wait_any(["[已作答]", "[答案未被接受]"], start=pos)


def _answer_until_first_confirm(proc: _ChatProc, pos: int) -> int:
    """逐门 approve-once,直到首个 tool-confirm 门答完(返回其后的游标)。

    用途(P2-M3 场景 2):tool-confirm 闸门答完后 shell 真跑(pytest 秒级),
    这段窗口没有 pending 问题——注入不会被分派成闸门答案(输入分派 pending 优先)。
    """
    while True:
        _, mstart, pos = proc.wait_any([_BANNER], start=pos, timeout=60.0)
        banner = proc.line_at(mstart)
        proc.write("approve-once")
        _, _, pos = proc.wait_any(["[已作答]"], start=pos)
        if "(supervisor/tool-confirm)" in banner:
            return pos


def _store_doc(arts: Path, session_id: str) -> dict:
    return json.loads((arts / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 场景 1:多轮会话(opening 首轮 → 答第二任务 → 「退出」收官,turns=2)
# ---------------------------------------------------------------------------


def test_chat_multi_turn_session(tmp_path):
    work = _copy_fixture(tmp_path / "work1")
    cfg = _write_chat_config(tmp_path)
    arts = tmp_path / "arts1"
    opening = f"修复 calc 的 bug,验证命令:{sys.executable} -m pytest test_calc.py -x"
    proc = _spawn_chat(
        tmp_path,
        "coding.agent.chat", "--input", json.dumps({"opening": opening}),
        "--config", str(cfg), "--artifacts", str(arts),
        "--session-id", "mt1", "--workdir", str(work),
    )
    try:
        # 审批门循环(首门 = chat→agent.run 升权;计划审批/tool-confirm 逐次作答),
        # 会话任务提问按剧本:第二轮任务 → 「退出」收官
        _answer_loop(proc, 0, ["再跑一遍测试确认修复", "退出"])
        # 帧活动行(子技能进出)渲染在场
        assert "[技能开始] local:coding.agent.run" in proc.buf, proc.buf
        # notify 汇报行:每轮子帧返回后一条,摘要含 status/changed_files
        assert proc.buf.count("[通知] 任务 done:") == 2, proc.buf
        assert "calc.py" in proc.buf
        # 收官:run.end done;summary 含轮数
        proc.wait_out("'会话收官:共完成 2 轮任务'")
        proc.write("/quit")
        assert proc.wait_exit() == 0
        # 第一轮真修好了 fixture 副本
        assert "return a + b" in (work / "calc.py").read_text(encoding="utf-8")
    finally:
        proc.kill()

    # SessionStore:整段会话 = 一条 done turn(run_id 非空、summary 落盘)
    doc = _store_doc(arts, "mt1")
    assert doc["skill"] == "coding.agent.chat"
    assert len(doc["turns"]) == 1
    turn = doc["turns"][0]
    assert turn["status"] == "done" and turn["summary"] and turn["run_id"]


# ---------------------------------------------------------------------------
# 场景 2:中途插话(首轮执行中注入补充约束,run 正常收官)
# ---------------------------------------------------------------------------


def test_chat_inject_mid_run(tmp_path):
    work = _copy_fixture(tmp_path / "work2")
    cfg = _write_chat_config(tmp_path)
    arts = tmp_path / "arts2"
    opening = f"修复 calc 的 bug,验证命令:{sys.executable} -m pytest test_calc.py -x"
    proc = _spawn_chat(
        tmp_path,
        "coding.agent.chat", "--input", json.dumps({"opening": opening}),
        "--config", str(cfg), "--artifacts", str(arts),
        "--session-id", "mt2", "--workdir", str(work),
    )
    try:
        # 逐门答到首个 tool-confirm(verify 的 shell.exec 门)为止;答完 pytest 真跑,
        # 这段窗口没有 pending 问题——注入不会被分派成闸门答案(分派 pending 优先)
        pos = _answer_until_first_confirm(proc, 0)
        proc.write("补充约束:改动尽量小,不要动 README")
        proc.wait_out("[已注入]", timeout=10.0)
        # 会话继续:其余审批门照答,收官词收场
        pos = _answer_loop(proc, pos, ["退出"])
        proc.wait_out("[run 结束] done")
        proc.write("/quit")
        assert proc.wait_exit() == 0
    finally:
        proc.kill()


# ---------------------------------------------------------------------------
# 场景 3:暂停/恢复续会话(/pause → --resume 重问 → 收官;turns = paused + done)
# ---------------------------------------------------------------------------


def test_chat_pause_and_resume_session(tmp_path):
    work = _copy_fixture(tmp_path / "work3")
    cfg = _write_chat_config(tmp_path)
    arts = tmp_path / "arts3"
    opening = f"修复 calc 的 bug,验证命令:{sys.executable} -m pytest test_calc.py -x"
    proc = _spawn_chat(
        tmp_path,
        "coding.agent.chat", "--input", json.dumps({"opening": opening}),
        "--config", str(cfg), "--artifacts", str(arts),
        "--session-id", "mt3", "--workdir", str(work),
    )
    try:
        # 第一门是 chat→agent.run 升权;批准后等计划审批横幅,中途 /pause
        _, _, pos = proc.wait_any([_BANNER], start=0)
        proc.write("approve-once")
        _, _, pos = proc.wait_any(["[已作答]"], start=pos)
        _, _, pos = proc.wait_any(["批准执行吗"], start=pos)
        proc.write("/pause")
        proc.wait_out("[run 挂起] paused")
        proc.wait_out("agent-os-chat --resume mt3")  # resume 提示
        proc.write("/quit")
        assert proc.wait_exit() == 0
    finally:
        proc.kill()

    # --resume 重启(不重给 --workdir:会话级覆盖随文档存档继承,P2-M3):
    # 计划审批问题重问(内核以新 question_id 再进收件箱),答完继续到收官
    resumed = _spawn_chat(
        tmp_path, "--resume", "mt3", "--config", str(cfg), "--artifacts", str(arts)
    )
    try:
        _, _, pos = resumed.wait_any(["批准执行吗"], start=0, timeout=60.0)
        resumed.write("approve-once")  # 重问的计划审批先作答
        _, _, pos = resumed.wait_any(["[已作答]"], start=pos)
        pos = _answer_loop(resumed, pos, ["退出"])
        resumed.wait_out("[run 结束] done")
        resumed.write("/quit")
        assert resumed.wait_exit() == 0
    finally:
        resumed.kill()

    # SessionStore:paused 与 done 两条 turn,run_id 一致(resume 写回原 run)
    doc = _store_doc(arts, "mt3")
    statuses = [t["status"] for t in doc["turns"]]
    assert statuses == ["paused", "done"], statuses
    assert doc["turns"][0]["checkpoint_path"], "paused turn 带 resume 锚点"
    assert doc["turns"][0]["run_id"] == doc["turns"][1]["run_id"]
    # 恢复后任务真跑完:fixture 副本已修复
    assert "return a + b" in (work / "calc.py").read_text(encoding="utf-8")

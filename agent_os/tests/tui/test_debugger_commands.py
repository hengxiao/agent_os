"""命令解析器单测(docs/TUI-DEBUG.md §5/§9;commands.py 纯函数面)。

覆盖:spec 语法(缺省 kind / skill: 前缀 / *N 步数断点)、唯一前缀缩写、
歧义候选报错、裸 Enter 重复白名单、危险命令不重复、info/set/x 子命令、
help 从命令表生成。
"""

from __future__ import annotations

import pytest

from agent_os.host.tui.apps.debugger.commands import (
    COMMAND_SPECS,
    REPEATABLE,
    ParseError,
    parse,
    parse_bp_spec,
)

# ---------------------------------------------------------------------------
# spec 语法(§5.1:b fs_* / b skill:fib / b step / b error / b *N)
# ---------------------------------------------------------------------------


def test_spec_default_kind_tool_call():
    spec = parse_bp_spec("fs_*")
    assert (spec.kind, spec.match, spec.until) == ("tool_call", "fs_*", None)


def test_spec_skill_prefix():
    spec = parse_bp_spec("skill:fib")
    assert (spec.kind, spec.match) == ("skill_invoke", "fib")


def test_spec_step_and_error():
    assert parse_bp_spec("step").kind == "step"
    assert parse_bp_spec("error").kind == "error"
    assert parse_bp_spec("step").match == "*"


def test_spec_until_step_breakpoint():
    spec = parse_bp_spec("*3")
    assert (spec.kind, spec.until) == ("step", 3)


def test_spec_until_rejects_bad_number():
    with pytest.raises(ParseError):
        parse_bp_spec("*0")
    with pytest.raises(ParseError):
        parse_bp_spec("*abc")


def test_spec_empty_rejected():
    with pytest.raises(ParseError):
        parse_bp_spec("  ")


# ---------------------------------------------------------------------------
# 命令解析:alias 精确 / 唯一前缀 / 歧义候选 / 未知
# ---------------------------------------------------------------------------


def test_aliases_exact():
    assert parse("b fs_*").name == "break"
    assert parse("c").name == "continue"
    assert parse("s").name == "step"
    assert parse("n").name == "next"
    assert parse("bt").name == "backtrace"
    assert parse("p args").name == "print"
    assert parse("q").name == "quit"
    assert parse("h").name == "help"
    assert parse("f 1").name == "frame"  # GDB:f = frame
    assert parse("i b").name == "info"


def test_unique_prefix_abbreviation():
    assert parse("fin").name == "finish"  # GDB:fin = finish
    assert parse("del 1").name == "delete"
    assert parse("dis 1").name == "disable"
    assert parse("cont").name == "continue"
    assert parse("inf b").name == "info"


def test_ambiguous_prefix_lists_candidates():
    with pytest.raises(ParseError) as ei:
        parse("de")
    assert ei.value.kind == "ambiguous"
    assert ei.value.candidates == ["delete", "detach"]
    with pytest.raises(ParseError) as ei:
        parse("d")
    assert ei.value.kind == "ambiguous"
    assert set(ei.value.candidates) == {"delete", "disable", "detach", "down"}


def test_unknown_command():
    with pytest.raises(ParseError) as ei:
        parse("zzz")
    assert ei.value.kind == "unknown"


def test_args_pass_through():
    cmd = parse("run demo.fib --input {\"n\": 3} -b fs_*")
    assert cmd.name == "run"
    assert cmd.args == ["demo.fib", "--input", '{"n":', '3}', "-b", "fs_*"]


def test_info_subcommands():
    assert parse("info b").args == ["breakpoints"]
    assert parse("info breakpoints").args == ["breakpoints"]
    assert parse("i f").args == ["frame"]
    assert parse("i a").args == ["args"]
    assert parse("i s").args == ["sessions"]
    with pytest.raises(ParseError) as ei:  # info 无子命令 = usage
        parse("info")
    assert ei.value.kind == "usage"
    with pytest.raises(ParseError):  # 未知子命令
        parse("info zzz")


def test_set_and_x_subcommand_validation():
    assert parse("set args {\"code\": \"1\"}").name == "set"
    with pytest.raises(ParseError):
        parse("set foo")
    assert parse("x messages").name == "x"
    assert parse("x working").name == "x"
    with pytest.raises(ParseError):
        parse("x foo")


# ---------------------------------------------------------------------------
# 裸 Enter 重复白名单(§5.2;危险命令不重复)
# ---------------------------------------------------------------------------

def test_empty_line_is_repeat_marker():
    cmd = parse("")
    assert cmd.name == "" and cmd.args == []
    assert parse("   ").name == ""


def test_repeat_whitelist():
    assert REPEATABLE == frozenset(
        {"continue", "step", "next", "finish", "until", "up", "down"})


def test_dangerous_commands_not_repeatable():
    for name in ("run", "kill", "detach", "set", "inject", "quit", "break",
                 "delete", "rerun"):
        assert name not in REPEATABLE


# ---------------------------------------------------------------------------
# 命令表(help/apropos 的生成源;表内名字唯一、alias 不撞名)
# ---------------------------------------------------------------------------

def test_command_table_integrity():
    names = [s.name for s in COMMAND_SPECS]
    assert len(names) == len(set(names))
    aliases = [a for s in COMMAND_SPECS for a in s.aliases]
    assert len(aliases) == len(set(aliases))
    assert not set(aliases) & set(names)  # alias 不与全名撞车
    for spec in COMMAND_SPECS:
        assert spec.summary and spec.ms  # help 行有文案、里程碑标注


def test_help_apropos_follow_command_table(monkeypatch):
    """help/apropos 从命令表生成(D5 钉死):往表里加命令,两处输出自动出现,
    无需同步任何第二份文案;h 是 help 的别名,同一生成源。"""
    from agent_os.host.tui.apps.debugger import commands as cmds

    fake = cmds.CommandSpec("zzprobe", ("zp",), "<x>", "探针假命令wyx", "D5")
    monkeypatch.setattr(cmds, "COMMAND_SPECS", (*COMMAND_SPECS, fake))
    help_lines = cmds._help_lines([])
    assert any(ln.strip().startswith("zzprobe") and "探针假命令wyx" in ln
               for ln in help_lines)
    apropos = cmds._apropos_lines(["wyx"])
    assert apropos and "zzprobe" in apropos[0]
    assert parse("h").name == "help"  # h 与 help 同一命令,同一份生成源

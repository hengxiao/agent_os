"""tests/tui 共用夹具:注册表隔离 + demo 无头装配。

ThemeRegistry / KeymapRegistry 是进程级单例(对译 godot static 语义),
每个测试前后复位,防用例间串包。
"""

from __future__ import annotations

import pytest

from agent_os.host.tui.apps.doc_editor.app import build_app
from agent_os.host.tui.apps.doc_editor.model import DemoDocSource
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keymap import KeymapRegistry
from agent_os.host.tui.tui.theme import ThemeRegistry

#: 无头屏幕尺寸(§9:输出重定向到伪终端宽度)
HEADLESS_W = 72
HEADLESS_H = 20


@pytest.fixture(autouse=True)
def reset_registries():
    ThemeRegistry.reset()
    KeymapRegistry.reset()
    yield
    ThemeRegistry.reset()
    KeymapRegistry.reset()


@pytest.fixture
def demo_app():
    """demo 数据装配好的 (app, engine, motion, frame) 四件;frame() 渲一帧。"""
    _tree, app, engine, motion = build_app(DemoDocSource(), keymap_id="vim")

    def frame() -> CellBuffer:
        buf = CellBuffer(HEADLESS_W, HEADLESS_H)
        app.render_into(buf, Region(0, 0, HEADLESS_W, HEADLESS_H))
        return buf

    return app, engine, motion, frame


@pytest.fixture
def demo_debugger():
    """调试器 demo 装配(docs/TUI-DEBUG.md §9;DemoDebugSource 无头冒烟基座)。
    返回 (app, engine, motion, frame);frame() 渲一帧 cell buffer。"""
    from agent_os.host.tui.apps.debugger.app import build_app as build_debugger
    from agent_os.host.tui.apps.debugger.model import DemoDebugSource

    _tree, app, engine, motion = build_debugger(DemoDebugSource(), keymap_id="vim")

    def frame() -> CellBuffer:
        buf = CellBuffer(HEADLESS_W, HEADLESS_H)
        app.render_into(buf, Region(0, 0, HEADLESS_W, HEADLESS_H))
        return buf

    return app, engine, motion, frame

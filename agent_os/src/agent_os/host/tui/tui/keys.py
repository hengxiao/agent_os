"""键解码(docs/TUI-DOC.md §5.1;termios raw + 转义序列裁决)。

stdin 原始字节 → KeyEvent:可打印 / C-x / M-x / 方向键 / 功能键。
纪律:
- 进入 raw 模式关 IXON(放出 C-s/C-q;§5.1);
- Esc 与转义序列歧义用短超时(~50ms,vim ``ttimeoutlen`` 同款)裁决:
  裸 Esc 字节后 50ms 内无后续字节 → KeyEvent("esc");
- 解码器是纯状态机(feed 字节 → 吐出 KeyEvent),fd 读取与超时的壳在
  TerminalSession,无头测试直接喂字节。
"""

from __future__ import annotations

import os
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass
from typing import Self

ESC_TIMEOUT = 0.05  # Esc/转义序列歧义裁决窗口(秒;vim ttimeoutlen 同款)


@dataclass(frozen=True)
class KeyEvent:
    """解码产物。key 规范化:"enter"/"esc"/"backspace"/"tab"/"up"…/
    可打印字符本体;ctrl/meta 是修饰位(space = C-Space/C-@ 的 NUL)。"""
    key: str
    ctrl: bool = False
    meta: bool = False

    def chord(self) -> str:
        """和弦记法(keymap 绑定表的键面):"C-x" / "M-x" / "C-M-x" / 裸键。"""
        k = "space" if self.key == " " else self.key
        mods = ""
        if self.ctrl:
            mods += "C-"
        if self.meta:
            mods += "M-"
        return mods + k


#: 转义序列终字节 → 键名(SS3 与 CSI 合表;裸序列无参数面,T1 够用)
_ESC_FINALS = {
    "A": "up", "B": "down", "C": "right", "D": "left",
    "H": "home", "F": "end", "P": "f1", "Q": "f2", "R": "f3", "S": "f4",
}
_CSI_TILDE = {
    "1": "home", "2": "insert", "3": "delete", "4": "end",
    "5": "pageup", "6": "pagedown", "7": "home", "8": "end",
    "11": "f1", "12": "f2", "13": "f3", "14": "f4",
    "15": "f5", "17": "f6", "18": "f7", "19": "f8", "20": "f9", "21": "f10",
    "23": "f11", "24": "f12",
}
#: 旧式 esc-OH/OF(home/end 另一种壳)已在 _ESC_FINALS 覆盖

#: 控制字节 → (key, ctrl)。C-a..C-z 区间在 _plain_byte 里先行归 C-<字母>
#: (故 0x08 = C-h,不占 backspace;backspace 只有 0x7F)。
#: 0x00 = C-Space/C-@(NUL;emacs mark 键,§5.3)。
_CTRL_KEYS = {
    0x00: (" ", True),
    0x09: ("tab", False),
    0x0A: ("enter", False),   # LF
    0x0D: ("enter", False),   # CR
    0x7F: ("backspace", False),
}


class KeyDecoder:
    """字节流 → KeyEvent 的纯状态机。feed() 返回本次吐出的 KeyEvent 列表;
    转义序列未齐时挂起,flush_escape() 在超时后把挂起的裸 Esc 裁成 "esc"。"""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pending_esc: float | None = None  # 裸 Esc 挂起点(monotonic)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def feed(self, data: bytes) -> list[KeyEvent]:
        events: list[KeyEvent] = []
        for b in data:
            ev = self._feed_byte(b)
            if ev is not None:
                events.append(ev)
        return events

    def pending_escape(self) -> bool:
        """是否有挂起的裸 Esc(等超时裁决)。"""
        return self._pending_esc is not None

    def flush_escape(self) -> KeyEvent | None:
        """超时裁决:挂起的裸 Esc → "esc"(序列半截同理,丢弃余字节)。"""
        if self._pending_esc is None:
            return None
        self._pending_esc = None
        self._buf.clear()
        return KeyEvent("esc")

    # ------------------------------------------------------------------
    # 字节级状态机
    # ------------------------------------------------------------------

    def _feed_byte(self, b: int) -> KeyEvent | None:
        # 转义序列进行中
        if self._buf:
            self._buf.append(b)
            return self._step_escape()
        if b == 0x1B:
            self._buf.append(b)
            self._pending_esc = time.monotonic()
            return None
        self._pending_esc = None
        return self._plain_byte(b)

    def _plain_byte(self, b: int) -> KeyEvent | None:
        if b < 0x20 or b == 0x7F:
            if 0x01 <= b <= 0x1A and b not in (0x09, 0x0A, 0x0D):  # C-a..C-z(扣掉 tab/enter)
                return KeyEvent(chr(ord("a") + b - 1), ctrl=True)
            if b in _CTRL_KEYS:
                key, ctrl = _CTRL_KEYS[b]
                return KeyEvent(key, ctrl=ctrl)
            return KeyEvent(f"0x{b:02x}", ctrl=True)  # 其余控制字节:具名上抛
        if b < 0x7F:
            return KeyEvent(chr(b))
        return None  # ≥0x80 的裸字节是 UTF-8 序列的一部分;走 feed_text

    def _step_escape(self) -> KeyEvent | None:
        seq = bytes(self._buf)
        # 第二字节决定形态
        if len(seq) == 2:
            second = seq[1]
            if second in (ord("["), ord("O")):
                return None  # CSI / SS3 继续收
            # M-x:Esc 前缀 + 单键
            self._reset_escape()
            inner = self._plain_byte(second)
            if inner is None:
                return None
            return KeyEvent(inner.key, ctrl=inner.ctrl, meta=True)
        # CSI: 参数字节(数字/;)收集中,终字节 0x40-0x7E 落定
        final = seq[-1]
        if seq[1:2] == b"[" and 0x40 <= final <= 0x7E and not (0x30 <= final <= 0x3F):
            body = seq[2:-1].decode("ascii", "replace")
            self._reset_escape()
            ch = chr(final)
            if ch in _ESC_FINALS:
                return KeyEvent(_ESC_FINALS[ch])
            if ch == "~":
                num = body.split(";")[0]
                return KeyEvent(_CSI_TILDE.get(num, "unknown"))
            if ch == "u":
                # CSI u(modifyOtherKeys 形态):T1 只收 C-Enter(13;5)
                parts = body.split(";")
                if parts[0] == "13" and len(parts) > 1 and parts[1] == "5":
                    return KeyEvent("enter", ctrl=True)
                return KeyEvent("unknown")
            return KeyEvent("unknown")
        # SS3: 第三字节即终字节
        if seq[1:2] == b"O":
            ch = chr(final)
            self._reset_escape()
            return KeyEvent(_ESC_FINALS.get(ch, "unknown"))
        if len(seq) > 16:
            self._reset_escape()
            return KeyEvent("unknown")
        return None

    def _reset_escape(self) -> None:
        self._buf.clear()
        self._pending_esc = None

    # ------------------------------------------------------------------
    # UTF-8 可打印面(多字节字符;与 ASCII 面分路,超时不参与)
    # ------------------------------------------------------------------

    def feed_text(self, text: str) -> list[KeyEvent]:
        """宿主把已按 UTF-8 解码好的字符喂进来(非 ASCII 可打印字符走这里;
        每个字符一个 KeyEvent,修饰位无)。"""
        return [KeyEvent(ch) for ch in text if ch >= " " and ch != "\x7f"]


# ---------------------------------------------------------------------------
# 终端会话(raw 模式壳:进出场必须可靠恢复,atexit + signal 在 __main__ 装配)
# ---------------------------------------------------------------------------

class TerminalSession:
    """fd 读取 + raw 模式 + Esc 超时裁决。无头测试不经过本类。"""

    def __init__(self, fd: int | None = None, esc_timeout: float = ESC_TIMEOUT) -> None:
        self.fd = sys.stdin.fileno() if fd is None else fd
        self.esc_timeout = esc_timeout
        self.decoder = KeyDecoder()
        self._saved: list | None = None
        self._text_pending = ""  # UTF-8 半截缓存

    def __enter__(self) -> Self:
        self._saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        # 关 IXON(tty.setraw 已关;显式再关一次表达意图,防平台差异):
        # 放出 C-s/C-q 给键位包(docs/TUI-DOC.md §5.1)
        raw = termios.tcgetattr(self.fd)
        raw[0] &= ~termios.IXON
        termios.tcsetattr(self.fd, termios.TCSANOW, raw)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.restore()

    def restore(self) -> None:
        """恢复 termios(幂等;atexit/signal 双通道都调它)。"""
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSANOW, self._saved)
            self._saved = None

    def read_events(self, timeout: float | None = None) -> list[KeyEvent]:
        """读一轮:timeout=None 阻塞等有数据;给秒数则超时返回 [](主循环
        帧节奏口)。裸 Esc 挂起时在裁决窗内再等一次 select。"""
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return []
        events = self._read_available()
        if self.decoder.pending_escape():
            r, _, _ = select.select([self.fd], [], [], self.esc_timeout)
            if r:
                events += self._read_available()
            if self.decoder.pending_escape():
                ev = self.decoder.flush_escape()
                if ev is not None:
                    events.append(ev)
        return events

    def _read_available(self) -> list[KeyEvent]:
        try:
            data = os.read(self.fd, 4096)
        except OSError:
            return []
        if not data:
            return []
        # ASCII/控制字节走字节状态机;非 ASCII(UTF-8 多字节解出的字符)走字符面
        events: list[KeyEvent] = []
        ascii_run = bytearray()

        def flush_ascii() -> None:
            if ascii_run:
                events.extend(self.decoder.feed(bytes(ascii_run)))
                ascii_run.clear()

        text = self._text_pending + data.decode("utf-8", "surrogateescape")
        self._text_pending = ""
        for i, ch in enumerate(text):
            o = ord(ch)
            if o < 0x80:
                ascii_run.append(o)
            elif 0xDC80 <= o <= 0xDCFF:
                # UTF-8 半截(块读切断多字节):存回缓存等下一块
                self._text_pending = text[i:]
                break
            else:
                flush_ascii()
                events.append(KeyEvent(ch))
        flush_ascii()
        return events

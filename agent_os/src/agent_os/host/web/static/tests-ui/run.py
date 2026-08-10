#!/usr/bin/env python3
"""UI 测试运行器(真实 Chromium,stub 测不到的那一层)。

用法:
  ./run.sh                    # 自起静态服务器,跑全部 test_*.py
  ./run.sh test_compound      # 只跑某个(不带 .py)
  BASE=http://127.0.0.1:8391/static ./run.sh   # 打已部署的服务器

约定:每个 test_*.py 导出一个 run(t) 函数;t 是 TestCtx:
  t.open(path)          打开页面(相对 base),收集 console error / pageerror
  t.check(name, cond, detail="")
  t.no_errors(name)     断言该页无 JS 错误(每次 open 后可调)
环境准备见同目录 README.md(.venv-ui / .browsers / .syslibs 均项目内,不入库)。
"""
from __future__ import annotations

import functools
import http.server
import importlib
import os
import socketserver
import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBROOT = HERE.parent.parent  # web/(页面内链接是 /static/...,故服务 web 根)

sys.path.insert(0, str(HERE))


class TestCtx:
    def __init__(self, page, base: str):
        self.page = page
        self.base = base.rstrip("/")
        self.failures: list[str] = []
        self.passes = 0
        self.errors: list[str] = []
        self.bad_responses: list[str] = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("response", lambda r: self.bad_responses.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    def open(self, path: str):
        self.errors.clear()
        self.bad_responses.clear()
        url = path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
        self.page.goto(url, wait_until="networkidle")
        return self.page

    def check(self, name: str, cond, detail: str = ""):
        if cond:
            self.passes += 1
            print(f"  ✓ {name}")
        else:
            self.failures.append(f"{name} {detail}".strip())
            print(f"  ✗ {name} {detail}")

    def no_errors(self, name: str = "无 JS 错误"):
        detail = " | ".join((self.errors + self.bad_responses)[:4])
        self.check(name, not self.errors and not self.bad_responses, detail)


def _serve(directory: Path) -> tuple[socketserver.TCPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # 静音访问日志
        pass


def main() -> int:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(HERE / ".browsers"))
    os.environ["LD_LIBRARY_PATH"] = str(HERE / ".syslibs/usr/lib64") + ":" + os.environ.get("LD_LIBRARY_PATH", "")

    from playwright.sync_api import sync_playwright

    base = os.environ.get("BASE", "").strip()
    srv = None
    if not base:
        handler = functools.partial(_Quiet, directory=str(WEBROOT))
        srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

    only = sys.argv[1] if len(sys.argv) > 1 else None
    mods = sorted(p.stem for p in HERE.glob("test_*.py"))
    if only:
        mods = [m for m in mods if m == only or m == f"test_{only}"]

    total_fail = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for mod_name in mods:
            mod = importlib.import_module(mod_name)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            ctx = TestCtx(page, base)
            print(f"\n== {mod_name} (base={base}) ==")
            try:
                mod.run(ctx)
            except Exception:
                ctx.failures.append("用例异常:\n" + traceback.format_exc(limit=3))
                print("  ✗ 用例异常,见末尾")
            page.close()
            total_fail += len(ctx.failures)
            if ctx.failures:
                print(f"  -- {len(ctx.failures)} 项失败 --")
        browser.close()
    if srv:
        srv.shutdown()

    print(f"\n{'全部通过' if total_fail == 0 else f'共 {total_fail} 项失败'}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

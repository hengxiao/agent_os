"""Web UI 截图工具(开发用):对运行中的实例抓关键页面,供视觉评审。

用法:
    .venv/bin/python scripts/ui_shot.py --base http://127.0.0.1:8001 --out /tmp/ui-shots
    .venv/bin/python scripts/ui_shot.py --base http://127.0.0.1:8001 --out /tmp/ui-shots \
        --run-ticket T-1002   # 先造一个指定工单的 run 再拍
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

PAGES = [
    ("runs", "/#/runs"),
    ("skills", "/#/skills"),
    ("tools", "/#/tools"),
]


def api(base: str, method: str, path: str, body: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--out", default="/tmp/ui-shots")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--run-ticket", default=None, help="先 POST 一个工单 run(如 T-1001)")
    args = ap.parse_args()

    if args.run_ticket:
        api(args.base, "POST", "/api/runs",
            {"skill": "handle_ticket", "input": {"ticket_id": args.run_ticket}, "wait": True})
        time.sleep(0.5)

    runs = api(args.base, "GET", "/api/runs")
    latest = runs[0]["run_id"] if runs else None
    skills = api(args.base, "GET", "/api/skills")
    first_skill = skills[0]["name"] if skills else None

    pages = list(PAGES)
    if latest:
        pages.insert(1, ("run-detail", f"/#/runs/{latest}"))
    if first_skill:
        pages.append(("skill-detail", f"/#/skills/{first_skill}"))

    # 每页可选的点击动作(截图前执行,覆盖选中态)
    clicks = {
        "run-detail": [".ft-row", ".tl-group .tl-row"],
        "skills": [".sk-item"],
        "tools": [".tl-item, .tool-item, .tools-list li, .list-row"],
        "skill-detail": [],
    }

    import pathlib

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        for name, hash_path in pages:
            url = args.base + "/" + hash_path.lstrip("/")
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(1200)
            for sel in clicks.get(name, []):
                try:
                    loc = page.locator(sel).first
                    if loc.count():
                        loc.click()
                        page.wait_for_timeout(600)
                except Exception:
                    pass
            page.screenshot(path=str(out / f"{name}.png"))
            print(f"shot {name}: {hash_path}")
        # Launch Modal(若存在 + New Run 按钮)
        try:
            page.goto(args.base + "/", wait_until="networkidle")
            page.wait_for_timeout(800)
            btn = page.locator("text=New Run").first
            if btn.count():
                btn.click()
                page.wait_for_timeout(800)
                page.screenshot(path=str(out / "launch-modal.png"))
                print("shot launch-modal")
        except Exception as e:  # noqa: BLE001
            print("launch-modal skipped:", e, file=sys.stderr)
        browser.close()


if __name__ == "__main__":
    main()

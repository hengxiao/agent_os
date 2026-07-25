"""inline 功能截图:skills 徽标 / trace ⇥ inline 行 / 检视器内联小节 / Launch Modal 下拉。"""

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8123"
RUN_ON = "fdfbfcff21de4b5f96d7528f0cfe31f9"
OUT = "/tmp/inline-demo/shots"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 900})

    # 1. Skills 页:date_style 徽标 + 详情 Meta inline 行
    page.goto(f"{BASE}/#/skills/date_style")
    page.wait_for_selector(".brw-item", timeout=10000)
    page.wait_for_selector(".brw-detail .kv", timeout=10000)
    page.wait_for_timeout(400)
    page.screenshot(path=f"{OUT}/01-skills-badge.png")

    # 2. run 详情(on 档):trace ⇥ inline 行 + 检视器内联小节
    page.goto(f"{BASE}/#/runs/{RUN_ON}")
    page.wait_for_selector(".tl-row", timeout=10000)
    page.wait_for_selector(".insp-caps", timeout=10000)
    page.wait_for_timeout(600)
    page.screenshot(path=f"{OUT}/02-trace-inline.png")

    # 3. Launch Modal:高级区 inline 下拉
    page.goto(f"{BASE}/#/skills/report_writer")
    page.wait_for_selector(".brw-item", timeout=10000)
    page.click("text=+ New Run")
    page.wait_for_selector(".modal", timeout=10000)
    page.wait_for_timeout(600)  # 技能详情加载
    page.click(".ld-adv summary")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/03-launch-modal.png")

    browser.close()
print("shots done")

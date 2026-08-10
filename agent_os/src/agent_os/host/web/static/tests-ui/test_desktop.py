"""C4.1:desktop widget 真实浏览器测试(docs/DESKTOP-WIDGET.md §6 C4.1 / §7 验收)。

页面:/platform/desktop.html(与旧壳并存;种子 = 两个最小化演示 app + inbox badge 2)。
驱动路径:桌面图标在 → 点图标开 app(单窗)→ 输入并发一条 → 留一句草稿 →
最小化(回桌面,任务栏行激活态清)→ 任务栏行重开(内容逐字在,hidden 语义)→
拖拽重排(taskbar_order 持久)→ ✕ 两段确认关闭(remove_child)→ 模拟升权 badge +1。
真实 DOM 选择器(以 web_platform/static/desktop-page.js + widgets/w-desktop.js 为准):
.dt-icon[data-desk-open], .dt-win, .dt-titlebar, [data-desk-min], [data-desk-close],
[data-conv-draft], [data-conv-log], .dt-task[data-desk-task], [data-desk-inbox-badge],
#dt-escalate, #dt-events;调试钩 window.__desktop。
"""

APP = "conversation"
APP2 = "runs-explorer"


def run(t):
    pg = t.open("/platform/desktop.html")
    pg.wait_for_timeout(900)  # module 加载 + bootDesktop 首渲
    t.no_errors("desktop 页加载无 JS 错误")

    # ① 桌面分支:图标栅格 + 任务栏行 + inbox badge
    icons = pg.locator(".dt-icon")
    t.check("桌面图标在(两个种子 app)", icons.count() >= 2, f"icons={icons.count()}")
    inbox_badge = pg.locator("[data-desk-inbox-badge]")
    t.check("inbox 托盘 badge = 2(闸门记账)", inbox_badge.count() > 0 and inbox_badge.inner_text() == "2",
            f"text={inbox_badge.inner_text() if inbox_badge.count() else '无'}")
    rows = pg.locator(".dt-task")
    t.check("任务栏行在(两个 app)", rows.count() == 2, f"rows={rows.count()}")

    # ② 点开 app → 单窗分支
    pg.locator(f'.dt-icon[data-desk-open="{APP}"]').click()
    pg.wait_for_timeout(500)
    t.check("单窗:窗口区在", pg.locator(".dt-win").count() > 0)
    t.check("单窗:标题栏在(icon/标题/—/✕)",
            pg.locator(".dt-titlebar").count() > 0 and pg.locator("[data-desk-min]").count() > 0
            and pg.locator(".dt-titlebar [data-desk-close]").count() > 0)
    t.check("任务栏激活态", pg.locator(f'[data-desk-task="{APP}"]').get_attribute("data-active") == "1")
    t.no_errors("开 app 无 JS 错误")

    # ③ 输入:发一条 + 留一句草稿
    draft = pg.locator("[data-conv-draft]")
    draft.fill("C4.1 真实浏览器逐字验证")
    draft.press("Enter")
    pg.wait_for_timeout(400)
    t.check("消息进 log(canonical state)", "逐字验证" in pg.locator("[data-conv-log]").inner_text())
    draft = pg.locator("[data-conv-draft]")
    draft.fill("未发送的草稿")
    pg.wait_for_timeout(200)

    # ④ 最小化 → 回桌面;任务栏行在且激活态清
    pg.locator("[data-desk-min]").click()
    pg.wait_for_timeout(500)
    t.check("最小化:回桌面分支", pg.locator(".dt-desk").count() > 0 and pg.locator(".dt-win").count() == 0)
    t.check("最小化:任务栏行保留", pg.locator(f'[data-desk-task="{APP}"]').count() == 1)
    t.check("最小化:激活态清空", pg.locator(f'[data-desk-task="{APP}"]').get_attribute("data-active") == "0")

    # ⑤ 任务栏行重开 → 内容逐字在(hidden 语义,不重建 instance)
    pg.locator(f'[data-desk-task="{APP}"]').click()
    pg.wait_for_timeout(500)
    t.check("重开:单窗回来", pg.locator(".dt-win").count() > 0)
    t.check("重开:消息逐字在", "逐字验证" in pg.locator("[data-conv-log]").inner_text())
    draft_val = pg.locator("[data-conv-draft]").input_value()
    t.check("重开:草稿逐字在", draft_val == "未发送的草稿", f"draft='{draft_val}'")
    inst_same = pg.evaluate("() => __desktop.child('conversation').state.draft")
    t.check("canonical state 逐字在(不重建)", inst_same == "未发送的草稿", f"state='{inst_same}'")
    t.no_errors("最小化/重开全程无 JS 错误")

    # ⑥ 拖拽重排:conversation 拖到 runs-explorer 之后 → taskbar_order 持久
    pg.locator("[data-desk-min]").click()
    pg.wait_for_timeout(400)
    order_before = pg.evaluate("() => __desktop.state.taskbar_order.join(',')")
    a = pg.locator(f'[data-desk-task="{APP}"]').bounding_box()
    b = pg.locator(f'[data-desk-task="{APP2}"]').bounding_box()
    pg.mouse.move(a["x"] + 12, a["y"] + a["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(b["x"] + b["width"] - 6, b["y"] + b["height"] / 2, steps=10)
    pg.mouse.up()
    pg.wait_for_timeout(500)
    order_after = pg.evaluate("() => __desktop.state.taskbar_order.join(',')")
    t.check("重排:taskbar_order 持久于 state",
            order_after == f"{APP2},{APP}", f"{order_before!r} → {order_after!r}")
    first_task = pg.locator(".dt-task").first.get_attribute("data-desk-task")
    t.check("重排:任务栏 DOM 序随 state", first_task == APP2, f"first={first_task}")
    # 拖拽不触发激活(仍在桌面分支)
    t.check("重排不误激活", pg.locator(".dt-win").count() == 0)
    t.no_errors("重排无 JS 错误")

    # ⑦ 关闭:✕ 两段确认(第一次 arm,第二次 remove_child)
    pg.locator(f'[data-desk-task="{APP}"]').click()
    pg.wait_for_timeout(400)
    x = pg.locator(".dt-titlebar [data-desk-close]")
    x.click()
    pg.wait_for_timeout(250)
    t.check("关闭一段:arm 不执行", pg.locator(f'[data-desk-task="{APP}"]').count() == 1)
    pg.locator(".dt-titlebar [data-desk-close]").click()
    pg.wait_for_timeout(500)
    t.check("关闭二段:remove_child 执行", pg.locator(f'[data-desk-task="{APP}"]').count() == 0)
    t.check("关闭后回桌面分支", pg.locator(".dt-desk").count() > 0)
    gone = pg.evaluate("() => __desktop.child('conversation')")
    t.check("instance 已销毁(重开是全新)", gone is None)
    events_text = pg.locator("#dt-events").inner_text()
    t.check("close 事件上屏", "close conversation" in events_text, events_text[:60])
    t.no_errors("关闭无 JS 错误")

    # ⑧ 模拟升权 → inbox badge +1(§7 验收:升权请求到达有 badge)
    pg.locator("#dt-escalate").click()
    pg.wait_for_timeout(500)
    t.check("升权:badge 2 → 3", pg.locator("[data-desk-inbox-badge]").inner_text() == "3",
            pg.locator("[data-desk-inbox-badge]").inner_text())
    t.check("升权:任务栏行不受动(只剩运行)",
            pg.locator(".dt-task").count() == 1, f"rows={pg.locator('.dt-task').count()}")
    t.no_errors("交互全程无 JS 错误")

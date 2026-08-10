"""C4.2:真实 app 进 desktop 的真实浏览器测试(docs/DESKTOP-WIDGET.md §5/§6 C4.2)。

页面:/platform/desktop.html(与旧壳并存;种子 = boot conversation(最新会话)+ runs-explorer 占位)。
驱动路径:桌面图标 → 开 conversation(真实对话,orchestrator 照旧)→ 发「文档列表」出卡 →
卡上 doc 链接 → doc-editor 进窗口区(完整编辑器;attach_existing)→ 对话卡下 card 面活视图
(hard link 同一 instance)→ 回对话留消息+草稿 → 最小化重开逐字在(hidden 语义)→
任务栏重排 → + 新对话 → 两段 ✕ 关闭 → doc 窗口右键开泡(切割线)→ inbox 真实 pending badge。
真实 DOM 选择器(以 web_platform/static/desktop-page.js + conversation-app.js + doc-editor.js 为准):
.dt-icon[data-desk-open], .dt-task[data-desk-task], [data-cv-log], [data-cv-input], [data-cv-send],
[data-detail-kind="doc"][data-detail-ref], .cv-doc-live, .doc-toolbar, .doc-para[data-anchor],
.doc-bubble-pop, [data-desk-inbox-badge], #dt-newconv, #dt-events;调试钩 window.__desktop。
"""

DOC = "demo.test"


def run(t):
    pg = t.open("/platform/desktop.html")
    pg.wait_for_selector(".dt-icon", timeout=10000)  # 异步种子(会话装载)落图标
    pg.wait_for_timeout(500)
    t.no_errors("desktop 页加载无 JS 错误")

    # ① 桌面分支:boot conversation + 占位运行;inbox badge = 真实 pending 数
    t.check("桌面图标在(conversation/运行)",
            pg.locator('.dt-icon[data-desk-open="conversation"]').count() == 1
            and pg.locator('.dt-icon[data-desk-open="runs-explorer"]').count() == 1)
    pending = pg.evaluate("() => fetch('/platform/api/decisions').then(r => r.json()).catch(() => [])")
    badge = pg.locator("[data-desk-inbox-badge]")
    if len(pending) == 0:
        t.check("inbox 真实 pending=0 → 无徽标(读面直连)", badge.count() == 0, f"pending={len(pending)}")
    else:
        t.check("inbox badge = 真实 pending 数", badge.count() > 0 and badge.inner_text() == str(len(pending)),
                f"pending={len(pending)} badge={badge.inner_text() if badge.count() else '无'}")

    # ② 开 conversation → 真实对话 chrome(薄壳 compound)
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(600)
    t.check("conversation 单窗:log 区在", pg.locator("[data-cv-log]").count() == 1)
    t.check("conversation 单窗:composer 在", pg.locator("[data-cv-input]").count() == 1)
    t.check("任务栏激活态", pg.locator('[data-desk-task="conversation"]').get_attribute("data-active") == "1")

    # ③ 发「文档列表」→ orchestrator 出 doc_list 卡
    ta = pg.locator("[data-cv-input]")
    ta.fill("文档列表")
    ta.press("Enter")
    pg.wait_for_timeout(2500)
    log_text = pg.locator("[data-cv-log]").inner_text()
    t.check("发送:用户消息上屏", "文档列表" in log_text)
    doc_link = pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first
    t.check("orchestrator 出卡:doc 链接在", doc_link.count() > 0, f"count={doc_link.count()}")
    t.no_errors("发送出卡无 JS 错误")

    # ④ 卡上 doc 链接 → doc-editor 进窗口区(attach_existing;完整编辑器)
    doc_link.click()
    pg.wait_for_timeout(1500)
    t.check("doc 窗口:任务栏行在(题名 = 文档名)", pg.locator(f'[data-desk-task="{DOC}"]').count() == 1)
    t.check("doc 窗口:完整编辑器骨架(toolbar + 左对话)",
            pg.locator(".dt-win .doc-toolbar").count() > 0 and pg.locator(".dt-win [data-doc-chat-log]").count() > 0)
    t.check("doc 窗口:段落块渲染", pg.locator(".dt-win .doc-para[data-anchor]").count() >= 2,
            f"blocks={pg.locator('.dt-win .doc-para[data-anchor]').count()}")
    t.no_errors("doc 进窗口区无 JS 错误")

    # ⑤ hard link:回对话窗 → doc 卡下 card 面活视图(同一 instance)
    pg.locator('[data-desk-task="conversation"]').click()
    pg.wait_for_timeout(800)
    live = pg.locator(".cv-doc-live").first
    t.check("活卡:card 面活视图挂进对话流", live.count() > 0, f"count={live.count()}")
    if live.count():
        t.check("活卡:内容同源(段落块在)", live.locator(".doc-para[data-anchor]").count() >= 2,
                f"blocks={live.locator('.doc-para[data-anchor]').count()}")
    same_inst = pg.evaluate(
        "() => __desktop.child('demo.test') && __desktop.child('demo.test').state.source.includes('测试文档')"
    )
    t.check("活卡与窗口同一 instance(canonical source)", bool(same_inst))

    # ⑥ 留证:发一条消息 + 输半句草稿 → 最小化 → 任务栏重开 → 逐字在
    ta = pg.locator("[data-cv-input]")
    ta.fill("C4.2 留证消息")
    ta.press("Enter")
    pg.wait_for_timeout(2000)
    t.check("留证消息上屏", "C4.2 留证消息" in pg.locator("[data-cv-log]").inner_text())
    pg.locator("[data-cv-input]").fill("未发送的半句")
    pg.wait_for_timeout(200)
    pg.locator("[data-desk-min]").click()
    pg.wait_for_timeout(500)
    t.check("最小化:回桌面分支", pg.locator(".dt-desk").count() > 0 and pg.locator(".dt-win").count() == 0)
    pg.locator('[data-desk-task="conversation"]').click()
    pg.wait_for_timeout(600)
    t.check("重开:留证消息逐字在", "C4.2 留证消息" in pg.locator("[data-cv-log]").inner_text())
    t.check("重开:doc 卡与活视图回来(log 从 canonical 重渲)",
            live.count() > 0 or pg.locator(".cv-doc-live").count() > 0)
    draft_val = pg.locator("[data-cv-input]").input_value()
    t.check("重开:草稿逐字在(hidden 语义)", draft_val == "未发送的半句", f"draft='{draft_val}'")
    state_draft = pg.evaluate("() => __desktop.child('conversation').state.draft")
    t.check("canonical state 逐字在", state_draft == "未发送的半句", f"state='{state_draft}'")
    t.no_errors("最小化/重开全程无 JS 错误")

    # ⑦ 任务栏重排(conversation 拖到 runs-explorer 之后;持久于 state)
    pg.locator("[data-desk-min]").click()
    pg.wait_for_timeout(400)
    a = pg.locator('[data-desk-task="conversation"]').bounding_box()
    b = pg.locator('[data-desk-task="runs-explorer"]').bounding_box()
    pg.mouse.move(a["x"] + 12, a["y"] + a["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(b["x"] + b["width"] - 6, b["y"] + b["height"] / 2, steps=10)
    pg.mouse.up()
    pg.wait_for_timeout(500)
    order = pg.evaluate("() => __desktop.state.taskbar_order.join(',')")
    t.check("重排:taskbar_order 持久于 state",
            order.index("runs-explorer") < order.index("conversation"), f"order={order}")
    t.no_errors("重排无 JS 错误")

    # ⑧ + 新对话 → 新会话新窗(空态)→ 两段 ✕ 关闭
    pg.locator("#dt-newconv").click()
    pg.wait_for_timeout(1500)
    new_rows = pg.evaluate("() => __desktop.children_snapshot().map(s => s.id)")
    new_conv = next((i for i in new_rows if i.startswith("conv-")), None)
    t.check("+ 新对话:新实例进任务栏", new_conv is not None, f"rows={new_rows}")
    if new_conv:
        t.check("新对话:激活为当前窗", pg.evaluate("() => __desktop.state.active") == new_conv)
        t.check("新对话:新会话空态", "pf-empty" in (pg.locator(".dt-win").inner_html() or ""))
        x = pg.locator(".dt-titlebar [data-desk-close]")
        x.click()
        pg.wait_for_timeout(250)
        t.check("关闭一段:arm 不执行", pg.locator(f'[data-desk-task="{new_conv}"]').count() == 1)
        pg.locator(".dt-titlebar [data-desk-close]").click()
        pg.wait_for_timeout(500)
        t.check("关闭二段:remove_child 执行", pg.locator(f'[data-desk-task="{new_conv}"]').count() == 0)
    t.no_errors("新对话/关闭无 JS 错误")

    # ⑨ doc 窗口右键开泡(切割线不破:壳/批注交互在窗口面)
    pg.locator(f'[data-desk-task="{DOC}"]').click()
    pg.wait_for_timeout(600)
    t.check("回 doc 窗口", pg.locator(".dt-win .doc-para[data-anchor]").count() >= 2)
    pg.locator(".dt-win .doc-para[data-anchor]").nth(1).click(button="right")
    pg.wait_for_timeout(600)
    t.check("右键开泡:浮出壳出现(窗口面批注交互)",
            pg.locator(".dt-win .doc-bubble-pop").count() > 0,
            f"pop={pg.locator('.doc-bubble-pop').count()}")
    t.no_errors("交互全程无 JS 错误")

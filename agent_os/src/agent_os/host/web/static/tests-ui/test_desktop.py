"""C4.3:五 explorer 薄壳 + detail 路由 + 卡 DnD + 未读 badge(docs/DESKTOP-WIDGET.md §5/§6 C4.3)。

页面:/platform/desktop.html(与旧壳并存;种子 = boot conversation(最新会话)+ runs-explorer)。
驱动路径(C4.2 链不回退,C4.3 追加):
  conversation 出卡 → doc 窗口/hard link 活卡 → 留证最小化重开(C4.2);
  未读 badge(最小化 emit arrived 记 / 激活清 / 激活中不记,§7 小注);
  浏览卡 run 行链接 → runs-explorer activate + locate 高亮(detail 全 kind 路由);
  浏览卡拖进任务栏(§15 envelope)→ 同路由打开;
  skills/tools/lab/debug-console 逐个开/最小化重开(保活)/两段 ✕ 关;
  会话列表开会话(同会话去重)+ + 新对话;任务栏重排;doc 窗口右键开泡。
真实 DOM 选择器(以 web_platform/static/desktop-page.js + explorer-apps.js 为准):
  .cv-log .pf-card[data-card], [data-detail-kind][data-detail-ref], [data-run-row].doc-flash,
  .dt-tasks, [data-desk-task-badge], #dt-kind/#dt-open/#dt-sessions/#dt-openconv/#dt-newconv,
  [data-conv-draft]? 否——[data-cv-input];调试钩 window.__desktop。
"""

DOC = "demo.test"


def _task_ids(pg):
    return pg.evaluate("() => __desktop.children_snapshot().map(s => s.id)")


def run(t):
    pg = t.open("/platform/desktop.html")
    pg.wait_for_selector(".dt-icon", timeout=10000)
    pg.wait_for_timeout(500)
    t.no_errors("desktop 页加载无 JS 错误")

    # ① boot:icons + inbox 真实 pending
    t.check("桌面图标在(conversation/运行)",
            pg.locator('.dt-icon[data-desk-open="conversation"]').count() == 1
            and pg.locator('.dt-icon[data-desk-open="runs-explorer"]').count() == 1)
    pending = pg.evaluate("() => fetch('/platform/api/decisions').then(r => r.json()).catch(() => [])")
    badge = pg.locator("[data-desk-inbox-badge]")
    if len(pending) == 0:
        t.check("inbox 真实 pending=0 → 无徽标", badge.count() == 0, f"pending={len(pending)}")
    else:
        t.check("inbox badge = 真实 pending 数", badge.count() > 0 and badge.inner_text() == str(len(pending)),
                f"pending={len(pending)}")

    # ② conversation 出卡 → doc 窗口(C4.2 链)
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(600)
    t.check("conversation 单窗:chrome 在", pg.locator("[data-cv-log]").count() == 1
            and pg.locator("[data-cv-input]").count() == 1)
    ta = pg.locator("[data-cv-input]")
    ta.fill("文档列表")
    ta.press("Enter")
    # 条件等待(orchestrator LLM 路由超时可至秒级;定长会抢跑)
    pg.wait_for_selector(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]', timeout=15000)
    doc_link = pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first
    t.check("orchestrator 出卡:doc 链接在", doc_link.count() > 0, f"count={doc_link.count()}")
    doc_link.click()
    pg.wait_for_timeout(1500)
    t.check("doc 窗口:完整编辑器 + 任务栏行(题名 = 文档名)",
            pg.locator(".dt-win .doc-toolbar").count() > 0
            and pg.locator(f'[data-desk-task="{DOC}"]').count() == 1)
    t.check("doc 窗口:段落块渲染", pg.locator(".dt-win .doc-para[data-anchor]").count() >= 2)
    pg.locator('[data-desk-task="conversation"]').click()
    pg.wait_for_timeout(800)
    live = pg.locator(".cv-doc-live").first
    t.check("hard link:活卡 card 面同源", live.count() > 0
            and live.locator(".doc-para[data-anchor]").count() >= 2)
    t.no_errors("C4.2 链无 JS 错误")

    # ③ 留证最小化重开(C4.2 链)
    ta = pg.locator("[data-cv-input]")
    ta.fill("C4.3 留证消息")
    ta.press("Enter")
    pg.wait_for_selector("text=C4.3 留证消息", timeout=15000)
    pg.locator("[data-cv-input]").fill("未发送的半句")
    pg.wait_for_timeout(200)
    pg.locator("[data-desk-min]").click()
    pg.wait_for_timeout(500)
    pg.locator('[data-desk-task="conversation"]').click()
    pg.wait_for_timeout(600)
    t.check("重开:留证消息逐字在", "C4.3 留证消息" in pg.locator("[data-cv-log]").inner_text())
    t.check("重开:草稿逐字在(hidden)", pg.locator("[data-cv-input]").input_value() == "未发送的半句")

    # ④ 未读 badge(§7 小注:可见性在父——最小化记 / 激活清 / 激活中不记)
    # 差量断言:真实流量(轮询/SSE 呈现)可能同期加账,基线 +2 而非绝对 2
    pg.locator("[data-desk-min]").click()
    pg.wait_for_timeout(500)
    base_val = pg.evaluate("() => __desktop.state.badges.conversation ?? 0")
    pg.evaluate("() => __desktop.child('conversation').emit('change', { messages: 9, arrived: 2 })")
    pg.wait_for_timeout(500)
    chip = pg.locator('[data-desk-task-badge="conversation"]')
    t.check("最小化:badge 记 +2(闸门按可见性记账)",
            chip.count() > 0 and chip.inner_text() == str(base_val + 2),
            f"base={base_val} chip={chip.inner_text() if chip.count() else '无'}")
    badges_val = None
    for _ in range(10):  # 记账 → relayout 同拍但 evaluate 有调度隙;短轮询读稳
        badges_val = pg.evaluate("() => __desktop.state.badges.conversation ?? null")
        if badges_val is not None and badges_val >= base_val + 2:
            break
        pg.wait_for_timeout(200)
    t.check("badge 账进 state.badges", badges_val is not None and badges_val >= base_val + 2,
            f"base={base_val} badges={badges_val}")
    pg.locator('[data-desk-task="conversation"]').click()
    pg.wait_for_timeout(500)
    t.check("激活:badge 清账", pg.locator('[data-desk-task-badge="conversation"]').count() == 0)
    pg.evaluate("() => __desktop.child('conversation').emit('change', { messages: 10, arrived: 1 })")
    pg.wait_for_timeout(400)
    t.check("激活中:新事件不记(改写 badge:null)",
            pg.locator('[data-desk-task-badge="conversation"]').count() == 0)
    t.no_errors("badge 可见性全程无 JS 错误")

    # ⑤ detail 全 kind 路由:浏览卡 run 行链接 → runs activate + locate 高亮
    pg.locator('[data-desk-task="conversation"]').click()
    pg.wait_for_timeout(400)
    ta = pg.locator("[data-cv-input]")
    ta.fill("哪些失败")
    ta.press("Enter")
    pg.wait_for_selector('[data-cv-log] [data-detail-kind="run"]', timeout=15000)
    run_link = pg.locator('[data-cv-log] [data-detail-kind="run"]').first
    t.check("浏览卡:run 行链接在", run_link.count() > 0, f"count={run_link.count()}")
    run_ref = run_link.get_attribute("data-detail-ref") or ""
    run_link.click()
    pg.wait_for_timeout(1000)
    t.check("run 链接 → runs-explorer 激活",
            pg.evaluate("() => __desktop.state.active") == "runs-explorer")
    t.check("locate 高亮行(data-run-row.doc-flash)",
            pg.locator(".pf-ln.doc-flash").count() >= 1,
            f"flash={pg.locator('.pf-ln.doc-flash').count()} ref={run_ref[:8]}")

    # ⑥ 卡 DnD:「为什么挂」摘要卡(整卡带 run ref)拖进任务栏 → 同路由打开(§15 envelope 不变)
    # (浏览表卡只有行级 ref,整卡 ref 空——拖摘要卡才路由,与 app.js 卡面拖开详情同语义)
    pg.locator('[data-desk-task="conversation"]').click()
    pg.wait_for_timeout(400)
    ta = pg.locator("[data-cv-input]")
    ta.fill("为什么挂")
    ta.press("Enter")
    pg.wait_for_selector('.cv-log .pf-card[data-card="table"]:not([data-detail-ref=""])', timeout=15000)
    card = pg.locator('.cv-log .pf-card[data-card="table"]:not([data-detail-ref=""])').first
    t.check("摘要卡可拖(draggable + 整卡 ref)",
            card.count() > 0 and card.get_attribute("draggable") == "true"
            and bool(card.get_attribute("data-detail-ref")),
            f"count={card.count()}")
    if card.count():
        # HTML5 DnD(§15 envelope):playwright 原生 drag_and_drop 派生 drag* 事件
        pg.drag_and_drop('.cv-log .pf-card[data-card="table"]:not([data-detail-ref=""])', ".dt-tasks")
        pg.wait_for_timeout(1000)
        t.check("卡拖进任务栏 → runs-explorer 激活定位",
                pg.evaluate("() => __desktop.state.active") == "runs-explorer")
    t.no_errors("DnD 无 JS 错误")

    # ⑦ 四 legacy explorer:开 → 内容在 → 最小化 → 重开保活 → 两段 ✕ 关
    for kind, label in [("skills-explorer", "技能"), ("tools-explorer", "工具"),
                        ("lab", "Lab"), ("debug-console", "调试")]:
        pg.locator("#dt-newbtn").click()  # 「+ 新建」菜单(C4.4 发起面收拢)
        pg.wait_for_timeout(200)
        pg.select_option("#dt-kind", kind)
        pg.click("#dt-open")
        pg.wait_for_timeout(1800)
        body = pg.locator(".dt-win-body")
        text1 = body.inner_text()
        t.check(f"{label}:开窗内容在", len(text1.strip()) > 10, f"len={len(text1.strip())}")
        t.check(f"{label}:任务栏行在", pg.locator(f'[data-desk-task="{kind}"]').count() == 1)
        pg.locator("[data-desk-min]").click()
        pg.wait_for_timeout(400)
        pg.locator(f'[data-desk-task="{kind}"]').click()
        pg.wait_for_timeout(900)
        text2 = pg.locator(".dt-win-body").inner_text()
        if kind == "debug-console":
            t.check(f"{label}:重开内容在(重开面)", len(text2.strip()) > 10, f"len={len(text2.strip())}")
        else:
            t.check(f"{label}:最小化重开逐字在(保活囊)", text2 == text1,
                    f"前={text1.strip()[:24]!r} 后={text2.strip()[:24]!r}")
        x = pg.locator(".dt-titlebar [data-desk-close]")
        x.click()
        pg.wait_for_timeout(250)
        pg.locator(".dt-titlebar [data-desk-close]").click()
        pg.wait_for_timeout(500)
        t.check(f"{label}:两段 ✕ 关闭(remove_child)",
                pg.locator(f'[data-desk-task="{kind}"]').count() == 0)
        t.no_errors(f"{label}:全程无 JS 错误")

    # ⑧ 会话列表开会话(「app 即会话」:同会话去重聚焦)+ + 新对话
    rows_before = _task_ids(pg)
    opt_count = pg.locator("#dt-sessions option").count()
    t.check("会话列表已装(发起面)", opt_count >= 1, f"options={opt_count}")
    if opt_count:
        pg.locator("#dt-newbtn").click()
        pg.wait_for_timeout(200)
        pg.locator("#dt-sessions").select_option(index=0)
        pg.locator("#dt-openconv").click()
        pg.wait_for_timeout(800)
        rows_after = _task_ids(pg)
        t.check("同会话开会话 = 去重聚焦(不重复开窗)", rows_after == rows_before,
                f"{rows_before} → {rows_after}")
        t.check("去重聚焦到既有 conversation", pg.evaluate("() => __desktop.state.active") == "conversation")
    pg.locator("#dt-newbtn").click()
    pg.wait_for_timeout(200)
    pg.locator("#dt-newconv").click()
    pg.wait_for_timeout(1500)
    new_conv = next((i for i in _task_ids(pg) if i.startswith("conv-")), None)
    t.check("+ 新对话:新会话新窗", new_conv is not None)
    if new_conv:
        x = pg.locator(".dt-titlebar [data-desk-close]")
        x.click()
        pg.wait_for_timeout(250)
        pg.locator(".dt-titlebar [data-desk-close]").click()
        pg.wait_for_timeout(500)
        t.check("新对话:两段 ✕ 关闭", pg.locator(f'[data-desk-task="{new_conv}"]').count() == 0)
    t.no_errors("会话发起面无 JS 错误")

    # ⑨ 任务栏重排(C4.1 链不回退)
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

    # ⑩ doc 窗口右键开泡(C4.2 切割线不回退)
    pg.locator(f'[data-desk-task="{DOC}"]').click()
    pg.wait_for_timeout(600)
    pg.locator(".dt-win .doc-para[data-anchor]").nth(1).click(button="right")
    pg.wait_for_timeout(600)
    t.check("doc 窗口右键开泡(切割线)", pg.locator(".dt-win .doc-bubble-pop").count() > 0)
    t.no_errors("交互全程无 JS 错误")

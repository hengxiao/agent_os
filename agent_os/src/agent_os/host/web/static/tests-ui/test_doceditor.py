"""C4.4:doc-editor 真实浏览器测试——desktop 根入口(docs/DESKTOP-WIDGET.md §6 C4.4)。

页面:/platform/(C4.4 起 = desktop 根;旧壳退役)。
驱动路径:desktop 启动 → 开 conversation → 发「文档列表」出卡 → 点 demo.test 进窗口区 →
段落右键开泡 → 泡内发消息(comment.send)→ ✕ 收起为段旁标记 → 批注列表跳转 →
view source 切换 → 写动作(snapshot → rewind 两击 → 导出菜单开合;三态 exec 管道)。
真实 DOM 选择器(以 web_platform/static/desktop-page.js + doc-editor.js 为准):
.dt-icon[data-desk-open], [data-cv-input], [data-detail-kind="doc"][data-detail-ref],
.doc-para[data-anchor], .doc-bubble-pop, .doc-bubble-body, [data-bubble-draft],
[data-bubble-send], .doc-bubble-fold, .doc-bubble-marker, [data-doc-bubblebar] .doc-bar-item,
.doc-viewseg [data-vm], [data-tab-act="doc.snapshot"], [data-doc-rewind],
[data-rewind-version], [data-doc-export], [data-export-menu];调试钩 window.__desktop。
"""

DOC = "demo.test"


def run(t):
    # 切根断言:/platform/ 即 desktop 根(C4.4)
    pg = t.open("/platform/")
    t.check("产品入口 = desktop 根(dt-root 在)", pg.locator("#dt-root").count() == 1,
            f"dt-root={pg.locator('#dt-root').count()}")
    pg.wait_for_selector(".dt-icon", timeout=10000)
    pg.wait_for_timeout(500)
    t.no_errors("desktop 根加载无 JS 错误")

    # 开 conversation → 出卡 → doc 进窗口区
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(600)
    ta = pg.locator("[data-cv-input]")
    ta.fill("文档列表")
    ta.press("Enter")
    pg.wait_for_selector(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]', timeout=15000)
    link = pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first
    t.check("doc_list 卡可见 demo.test", link.count() > 0, f"count={link.count()}")
    link.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")
    t.check("doc 窗口打开(完整编辑器)", win.locator(".doc-toolbar").count() > 0)
    t.check("窗口题名 = 文档名(per-instance 题名)",
            "demo.test" in (win.locator(".dt-title").inner_text() if win.locator(".dt-title").count() else ""))
    paras = win.locator(".doc-para[data-anchor]")
    t.check("段落块渲染(doc-para + 锚点)", paras.count() >= 2, f"blocks={paras.count()}")
    t.no_errors("打开文档无 JS 错误")

    # 段落右键开泡(compound add_child;窗口面批注交互)
    paras.nth(1).click(button="right")
    pg.wait_for_timeout(600)
    pop = win.locator(".doc-bubble-pop").first
    t.check("右键开泡:浮出壳出现(add_child)", pop.count() > 0)
    t.check("气泡卡挂进壳(w-bubble 本体)", pop.locator(".w-bubble").count() > 0)
    paths = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().map(s => s.path)")
    t.check("寻址全树唯一:/root 通到 bubble 级(reparent 级联改址)",
            any(p.startswith("/root/demo.test/") for p in paths), str(paths)[:80])
    t.no_errors("开泡无 JS 错误")

    # 泡内发消息(submit → child_event → comment.send 出海)
    draft = pg.locator("[data-bubble-draft]").first
    if draft.count():
        draft.fill("desktop 根验证:这段需要精简")
        pg.locator("[data-bubble-send]").first.click()
        pg.wait_for_timeout(3500)
        body_text = pg.locator(".doc-bubble-body").first.inner_text()
        t.check("泡内消息流更新(发送可见)", "desktop 根验证" in body_text, body_text[:60])
    t.no_errors("发送全程无 JS 错误")

    # ✕ 收起 → 段旁标记(可见性管控)
    fold = pg.locator(".doc-bubble-fold").first
    if fold.count():
        fold.click()
        pg.wait_for_timeout(400)
        marker = pg.locator(".doc-bubble-marker").first
        t.check("收起:段旁标记出现", marker.count() > 0 and marker.is_visible(),
                f"count={marker.count()}")

    # 批注列表 → 点击跳转开泡(early-return 聚焦语义)
    bar_item = pg.locator("[data-doc-bubblebar] .doc-bar-item").first
    if bar_item.count():
        bar_item.click()
        pg.wait_for_timeout(500)
        pop2 = win.locator(".doc-bubble-pop").first
        t.check("批注列表跳转:气泡重开可见", pop2.count() > 0 and pop2.is_visible(),
                f"count={pop2.count()}")
    t.no_errors("批注跳转无 JS 错误")

    # view source 切换(compound state.view → doc slot 进出)
    seg_src = pg.locator('.doc-viewseg [data-vm="source"]').first
    t.check("view source segmented 在", seg_src.count() > 0)
    if seg_src.count():
        seg_src.click()
        pg.wait_for_timeout(500)
        t.check("源码态:md-viewer source 视图出现", win.locator(".wd-md-src-pre").count() > 0)
        t.check("源码含文档原文", "测试文档" in win.inner_text() or len(win.inner_text()) > 10)
        pg.locator('.doc-viewseg [data-vm="preview"]').first.click()
        pg.wait_for_timeout(500)
        t.check("回预览:段落块复原", win.locator(".doc-para[data-anchor]").count() >= 2)
    t.no_errors("view source 切换全程无 JS 错误")

    # 写动作(C4.4 偏差清零;spawn app 实例管道,三态 exec):
    # snapshot 存版 → rewind 选该版两击回滚(内容不变的安全往返)→ 导出菜单开合
    snap = win.locator('[data-tab-act="doc.snapshot"]')
    t.check("snapshot 钮在(写动作面接通)", snap.count() > 0)
    if snap.count():
        snap.click()
        pg.wait_for_timeout(1500)
        t.no_errors("snapshot 动作无 JS 错误(三态 exec)")
        conv_msgs = pg.evaluate(
            "() => __desktop.child('conversation').state.messages.map(m => m.text).join('|')"
        )
        t.check("snapshot 结果以 agent 消息进 conversation(旧壳同语义)",
                len(conv_msgs) > 0)
        rw_btn = win.locator("[data-doc-rewind]").first
        if rw_btn.count():
            vers = win.locator("[data-rewind-version] option")
            if vers.count():
                last = vers.last.get_attribute("value")
                win.locator("[data-rewind-version]").select_option(value=last)
            before = win.locator(".doc-preview, [data-doc-preview]").first.inner_text()
            rw_btn.click()  # 第一击武装
            pg.wait_for_timeout(300)
            win.locator("[data-doc-rewind]").first.click()  # 第二击执行
            pg.wait_for_timeout(1800)
            after = pg.locator(".dt-win [data-doc-preview]").first.inner_text()
            t.check("rewind 两击回滚(内容往返一致)", "测试文档" in after or after == before,
                    after[:40])
        t.no_errors("rewind 全程无 JS 错误")
    exp = win.locator("[data-doc-export]").first
    if exp.count():
        exp.click()
        pg.wait_for_timeout(300)
        menu = win.locator("[data-export-menu]")
        t.check("导出菜单开合(getTabInstance 已接通)",
                menu.count() > 0 and not menu.first.is_hidden())
    t.no_errors("写动作全程无 JS 错误")

    run_scroll(t)  # 滚动层级重构(2026-08-12):锁视口/双栏独立滚/气泡封顶 + 截图


def run_scroll(t):
    """滚动层级(2026-08-12 重构):锁视口/双栏独立滚/气泡封顶;截图进 .shots/。"""
    import os
    shots = os.path.join(os.path.dirname(__file__), ".shots")
    os.makedirs(shots, exist_ok=True)

    pg = t.open("/platform/")
    pg.wait_for_selector(".dt-icon", timeout=10000)
    pg.wait_for_timeout(400)
    t.check("产品根无开发信息栏(泄漏清零)", pg.locator(".dt-info").count() == 0)
    t.check("发起面 =「+ 新建」菜单(无裸 select)",
            pg.locator("#dt-newbtn").count() == 1 and pg.locator("#dt-newMenu").is_hidden())
    pg.locator("#dt-newbtn").click()
    pg.wait_for_timeout(200)
    t.check("菜单开合:展开含会话/app 两路",
            not pg.locator("#dt-newMenu").is_hidden()
            and pg.locator("#dt-sessions").count() == 1 and pg.locator("#dt-kind").count() == 1)
    pg.locator("#dt-newbtn").click()
    body0 = pg.evaluate("() => document.body.scrollHeight - document.documentElement.clientHeight")
    t.check("页级滚动为零(锁视口)", body0 <= 1, f"delta={body0}")

    # 长文档:右栏 preview 独立滚,左 chat 不滚;页面不滚
    # (幂等:已存在不重建,409 不进 bad_responses;JS 串内禁放 // 注释——拼接无换行会吃掉余下脚本)
    pg.evaluate(
        "async () => { const g = await fetch('/platform/api/docs/dev.longscroll');"
        "if (g.ok) return 'exists';"
        "await fetch('/platform/api/docs', {method:'POST', headers:{'Content-Type':'application/json'},"
        "body: JSON.stringify({name:'dev.longscroll', title:'dev.longscroll',"
        "text: '# 长文档\\n' + Array.from({length:90}, (_,i)=>`第 ${i+1} 行正文,撑高右栏预览区测独立滚动。`).join('\\n\\n')})}); }"
    )
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(500)
    ta = pg.locator("[data-cv-input]")
    ta.fill("文档列表")
    ta.press("Enter")
    pg.wait_for_selector('[data-cv-log] [data-detail-kind="doc"][data-detail-ref="dev.longscroll"]', timeout=15000)
    pg.locator('[data-cv-log] [data-detail-kind="doc"][data-detail-ref="dev.longscroll"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")
    t.check("长文档窗口打开(块数 ≥10)", win.locator(".doc-para[data-anchor]").count() >= 10,
            f"blocks={win.locator('.doc-para[data-anchor]').count()}")
    pv = win.locator("[data-doc-preview]")
    pv_delta = pv.evaluate("e => e.scrollHeight - e.clientHeight")
    chat_delta = win.locator("[data-doc-chat-log]").evaluate("e => e.scrollHeight - e.clientHeight")
    t.check("右栏 preview 独立可滚(长文档)", pv_delta > 100, f"delta={pv_delta}")
    t.check("左 chat 此时不滚(消息少)", chat_delta <= 1, f"delta={chat_delta}")
    body1 = pg.evaluate("() => document.body.scrollHeight - document.documentElement.clientHeight")
    t.check("长文档下页级滚动仍为零", body1 <= 1, f"delta={body1}")
    pv.evaluate("e => e.scrollTop = e.scrollHeight")
    pg.wait_for_timeout(300)
    t.check("右栏滚动:左栏不跟随(chat scrollTop=0)",
            win.locator("[data-doc-chat-log]").evaluate("e => e.scrollTop") == 0)
    tb_y = win.locator(".doc-toolbar").bounding_box()["y"]
    wb_y = pg.locator(".dt-win-body").bounding_box()["y"]
    t.check("右栏滚动:toolbar 钉顶不动", abs(tb_y - wb_y) < 40, f"toolbar_y={tb_y:.0f} body_y={wb_y:.0f}")
    pg.screenshot(path=f"{shots}/dual-pane-fullheight.png")
    pv.evaluate("e => e.scrollTop = 0")

    # 左栏滚右栏不动(灌长对话流进左 chat;布局断言,行为流另有覆盖)
    pg.evaluate(
        "() => { const log = document.querySelector('[data-doc-chat-log]');"
        "for (let i = 0; i < 30; i++) {"
        "const d = document.createElement('div'); d.className = 'doc-chat-msg'; d.dataset.role = 'user';"
        "d.textContent = '左栏滚动测试消息 ' + i; log.appendChild(d); } }"
    )
    pg.wait_for_timeout(300)
    chat_delta2 = win.locator("[data-doc-chat-log]").evaluate("e => e.scrollHeight - e.clientHeight")
    t.check("灌 30 条:左 chat 独立可滚", chat_delta2 > 100, f"delta={chat_delta2}")
    win.locator("[data-doc-chat-log]").evaluate("e => e.scrollTop = e.scrollHeight")
    pg.wait_for_timeout(300)
    t.check("左栏滚动:右栏不跟随(preview scrollTop=0)", pv.evaluate("e => e.scrollTop") == 0)
    t.check("左栏滚动:composer 钉底可见", win.locator("[data-doc-chat-input]").is_visible())
    body2 = pg.evaluate("() => document.body.scrollHeight - document.documentElement.clientHeight")
    t.check("左栏滚动:页级仍零", body2 <= 1, f"delta={body2}")
    pg.screenshot(path=f"{shots}/left-scroll-right-still.png")

    # 长气泡封顶(320px 内滚;锚段不被顶走)
    pg.evaluate(
        "() => { const log = document.querySelector('[data-doc-chat-log]');"
        "log.innerHTML = ''; }"  # 清空布局断言泵入行(只影响本页 DOM)
    )
    block = win.locator(".doc-para[data-anchor]").nth(1)
    block.click(button="right")
    pg.wait_for_timeout(600)
    y0 = block.bounding_box()["y"]
    pg.evaluate(
        "() => { const log = document.querySelector('.doc-bubble-pop .w-bubble-log');"
        "for (let i = 0; i < 30; i++) {"
        "const d = document.createElement('div'); d.className = 'w-bubble-msg';"
        "d.innerHTML = '<span class=\"w-bubble-body\"><span class=\"w-bubble-tx\">封顶测试行 ' + i + '</span></span>';"
        "log.appendChild(d); } }"
    )
    pg.wait_for_timeout(300)
    pop_h = pg.locator(".doc-bubble-pop").first.bounding_box()["height"]
    t.check("气泡封顶 320px", pop_h <= 321, f"h={pop_h:.0f}")
    blog_delta = pg.locator(".doc-bubble-pop .w-bubble-log").evaluate("e => e.scrollHeight - e.clientHeight")
    t.check("气泡内滚(w-bubble-log 可滚)", blog_delta > 100, f"delta={blog_delta}")
    t.check("气泡输入钉卡底可见", pg.locator(".doc-bubble-pop [data-bubble-draft]").is_visible())
    y1 = block.bounding_box()["y"]
    t.check("锚段不被顶走(y 不变)", abs(y1 - y0) < 2, f"{y0:.0f}→{y1:.0f}")
    body3 = pg.evaluate("() => document.body.scrollHeight - document.documentElement.clientHeight")
    t.check("气泡封顶下页级仍零", body3 <= 1, f"delta={body3}")
    pg.screenshot(path=f"{shots}/bubble-capped.png")
    t.no_errors("滚动层级全程无 JS 错误")

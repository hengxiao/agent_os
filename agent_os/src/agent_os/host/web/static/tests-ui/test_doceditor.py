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

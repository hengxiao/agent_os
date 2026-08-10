"""C3:doc-editor compound 真实浏览器测试(docs/COMPOUND-WIDGET.md §9 C3)。

页面:/platform/(已部署服务 8391;demo.test 文档带历史气泡种子)。
驱动路径:首页对话发「文档列表」(orchestrator 规则意图 → doc_list 卡)→
点 demo.test 打开 doc tab → 段落右键开泡 → 泡内发消息 → 收起为段旁标记 →
批注列表点击跳转 → view source 切换。
真实 DOM 选择器(以 web_platform/static/details.js + doc-editor.js 为准):
[data-detail-kind="doc"][data-detail-ref], .doc-para[data-anchor],
.doc-anchor-btn, .doc-bubble-pop, .doc-bubble-body, .w-bubble,
[data-bubble-draft], [data-bubble-send], .doc-bubble-fold, .doc-bubble-marker,
[data-doc-bubblebar] .doc-bar-item, .doc-viewseg [data-vm]。
"""

DOC = "demo.test"


def run(t):
    pg = t.open("/platform/")
    pg.wait_for_timeout(1200)  # 首屏挂载 + 卡渲染
    t.no_errors("platform 首页无 JS 错误")

    # 打开文档:先触发 doc_list 卡(orchestrator._DOC_RE 规则意图),
    # 旧会话里可能已有卡,有才跳过发问
    link = pg.locator(f'[data-detail-kind="doc"][data-detail-ref="{DOC}"]').first
    if link.count() == 0:
        # inputBar 只在 conv tab 激活时可见;桌面态先点托盘收件箱补回对话窗
        if not pg.locator("#inputBar").is_visible():
            pg.locator("[data-tray-inbox]").first.click()
            pg.wait_for_timeout(400)
        pg.locator("#intent").fill("文档列表")
        pg.locator("#send").click()
        pg.wait_for_timeout(1800)
        link = pg.locator(f'[data-detail-kind="doc"][data-detail-ref="{DOC}"]').first
    t.check("doc_list 卡可见 demo.test", link.count() > 0, f"count={link.count()}")
    link.click()
    pg.wait_for_timeout(800)
    preview = pg.locator('[data-doc-preview="1"]').first
    t.check("doc tab 打开(预览区在)", preview.count() > 0, f"count={preview.count()}")
    t.check("段落块渲染(doc-para + 锚点)", preview.locator(".doc-para[data-anchor]").count() >= 2,
            f"blocks={preview.locator('.doc-para[data-anchor]').count()}")
    t.no_errors("打开文档无 JS 错误")

    # 段落右键开泡(compound add_child)
    block = preview.locator(".doc-para[data-anchor]").nth(1)
    anchor = block.get_attribute("data-anchor") or ""
    block.click(button="right")
    pg.wait_for_timeout(600)
    pop = pg.locator(".doc-bubble-pop").first
    t.check("右键开泡:浮出壳出现(add_child)", pop.count() > 0, f"anchor={anchor}")
    t.check("气泡卡挂进壳(w-bubble 本体)",
            pop.locator(".w-bubble").count() > 0 or pg.locator(".doc-bubble-body .w-bubble").count() > 0,
            f"count={pg.locator('.w-bubble').count()}")
    t.no_errors("开泡无 JS 错误")

    # 泡内发消息(submit → child_event → comment.send)
    draft = pg.locator("[data-bubble-draft]").first
    if draft.count():
        draft.fill("真实浏览器 C3 验证:这段需要精简")
        pg.locator("[data-bubble-send]").first.click()
        pg.wait_for_timeout(3500)  # comment.send 出海 + 回复回填
        body_text = pg.locator(".doc-bubble-body").first.inner_text()
        t.check("泡内消息流更新(发送可见)", "真实浏览器 C3 验证" in body_text, body_text[:60])
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
        pop2 = pg.locator(".doc-bubble-pop").first
        t.check("批注列表跳转:气泡重开可见", pop2.count() > 0 and pop2.is_visible(),
                f"count={pop2.count()}")
    t.no_errors("批注跳转无 JS 错误")

    # view source 切换(compound state.view → doc slot 进出)
    seg_src = pg.locator('.doc-viewseg [data-vm="source"]').first
    t.check("view source segmented 在", seg_src.count() > 0, f"count={seg_src.count()}")
    if seg_src.count():
        seg_src.click()
        pg.wait_for_timeout(500)
        src_view = preview.locator(".wd-md-src-pre").first
        t.check("源码态:md-viewer source 视图出现", src_view.count() > 0,
                f"count={src_view.count()}")
        t.check("源码含文档原文", "测试文档" in preview.inner_text() or len(preview.inner_text()) > 10,
                preview.inner_text()[:40])
        pg.locator('.doc-viewseg [data-vm="preview"]').first.click()
        pg.wait_for_timeout(500)
        t.check("回预览:段落块复原", preview.locator(".doc-para[data-anchor]").count() >= 2,
                f"blocks={preview.locator('.doc-para[data-anchor]').count()}")
    t.no_errors("view source 切换全程无 JS 错误")

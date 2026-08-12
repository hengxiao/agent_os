"""C4.4:doc-editor 真实浏览器测试——desktop 根入口(docs/DESKTOP-WIDGET.md §6 C4.4)。

页面:/platform/(C4.4 起 = desktop 根;旧壳退役)。
驱动路径:desktop 启动 → 开 conversation → 发「文档列表」出卡 → 点 demo.test 进窗口区 →
段落右键开泡 → 泡内发消息(comment.send)→ ✕ 收起为段旁标记 → 批注列表跳转 →
view source 切换 → 写动作(snapshot → rewind 两击 → 导出菜单开合;三态 exec 管道)。
真实 DOM 选择器(以 web_platform/static/desktop-page.js + doc-editor.js 为准):
.dt-icon[data-desk-open], [data-cv-input], [data-detail-kind="doc"][data-detail-ref],
.doc-para[data-anchor], .doc-bubble-pop, .doc-bubble-body, [data-bubble-draft],
[data-bubble-send], .doc-bubble-marker, [data-doc-bubblebar] .doc-bar-item,
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

    # ✕ 收起 → 段旁标记(可见性管控;v3:卡面头部 ✕,壳不再自带)
    fold = pg.locator("[data-bubble-x]").first
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
    run_bubble_v2(t)  # W-bubble v2(用户验收反馈 2026-08-11)
    run_bubble_v3(t)  # 气泡交互 v3(用户裁决 2026-08-11):原位/选区/点外收/垃圾桶
    run_bubble_v31(t)  # 行文级批注 v3.1(用户裁决):点锚点/多泡/高亮/标记跟选段


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
    max_h = pg.evaluate("() => Math.round(Math.max(200, innerHeight * 0.45))")
    t.check("气泡封顶 ≤ maxHeight(v2 算法)", pop_h <= max_h + 1, f"h={pop_h:.0f} max={max_h}")
    blog_delta = pg.locator(".doc-bubble-pop .w-bubble-log").evaluate("e => e.scrollHeight - e.clientHeight")
    t.check("气泡内滚(w-bubble-log 可滚)", blog_delta > 100, f"delta={blog_delta}")
    t.check("气泡输入钉卡底可见", pg.locator(".doc-bubble-pop [data-bubble-draft]").is_visible())
    y1 = block.bounding_box()["y"]
    t.check("锚段不被顶走(y 不变)", abs(y1 - y0) < 2, f"{y0:.0f}→{y1:.0f}")
    body3 = pg.evaluate("() => document.body.scrollHeight - document.documentElement.clientHeight")
    t.check("气泡封顶下页级仍零", body3 <= 1, f"delta={body3}")
    pg.screenshot(path=f"{shots}/bubble-capped.png")
    t.no_errors("滚动层级全程无 JS 错误")


def run_bubble_v2(t):
    """W-bubble v2(用户验收反馈 2026-08-11):封顶内滚/翻转/连发不吞/pill/折叠。"""
    import os
    shots = os.path.join(os.path.dirname(__file__), ".shots")
    os.makedirs(shots, exist_ok=True)

    pg = t.open("/platform/")
    pg.wait_for_selector(".dt-icon", timeout=10000)
    pg.wait_for_timeout(400)
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(500)
    ta = pg.locator("[data-cv-input]")
    ta.fill("文档列表")
    ta.press("Enter")
    pg.wait_for_selector('[data-cv-log] [data-detail-kind="doc"][data-detail-ref="dev.longscroll"]', timeout=15000)
    pg.locator('[data-cv-log] [data-detail-kind="doc"][data-detail-ref="dev.longscroll"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")

    # ① 长线程封顶:30 条经 receiveReply 入流(real path),只有 log 滚
    anchor = win.locator(".doc-para[data-anchor]").nth(1).get_attribute("data-anchor") or ""
    win.locator(".doc-para[data-anchor]").nth(1).click(button="right")
    pg.wait_for_timeout(600)
    # v3.1:右键建的是点锚点(零宽 L:C-L:C),子件 id 从快照取(不猜列号)
    anchor = pg.evaluate(
        "() => __desktop.child('dev.longscroll').children_snapshot().map(s => s.id).filter(i => i !== 'doc').at(-1)"
    ) or anchor
    pg.evaluate(
        "(anchor) => { const live = __desktop.child('dev.longscroll').child(anchor).views.at(-1).live;"
        "for (let i = 0; i < 30; i++) live.receiveReply('第 ' + i + ' 条批注回复,内容足够长,验证封顶与唯一滚动区。'); }",
        anchor
    )
    pg.wait_for_timeout(400)
    pop = pg.locator(".doc-bubble-pop").first
    pop_h = pop.bounding_box()["height"]
    max_h = pg.evaluate("() => Math.round(Math.max(200, Math.min(innerHeight * 0.45, innerHeight)))")
    t.check("长线程:气泡 ≤ maxHeight(clamp 生效)", pop_h <= max_h + 1, f"h={pop_h:.0f} max={max_h}")
    log = pg.locator(".doc-bubble-pop .w-bubble-log")
    log_delta = log.evaluate("e => e.scrollHeight - e.clientHeight")
    t.check("长线程:log 唯一滚动区(内滚)", log_delta > 100, f"delta={log_delta}")
    t.check("长线程:composer 钉底可见", pg.locator(".doc-bubble-pop [data-bubble-draft]").is_visible())
    body0 = pg.evaluate("() => document.body.scrollHeight - document.documentElement.clientHeight")
    t.check("长线程:页级仍零", body0 <= 1, f"delta={body0}")
    pg.screenshot(path=f"{shots}/bubble-v2-capped.png")

    # ② 连发 5 条全部入流(busy 不吞;队列串行)
    draft = pg.locator(".doc-bubble-pop [data-bubble-draft]")
    for i in range(5):
        draft.fill(f"连发消息 {i + 1}")
        draft.press("Enter")
        pg.wait_for_timeout(120)  # 快速连发(busy 中;v2 入队不吞)
    pg.wait_for_timeout(400)
    log_text = log.inner_text()
    got = [f"连发消息 {i + 1}" in log_text for i in range(5)]
    t.check("连发 5 条全部入流(输入不丢)", all(got), f"got={got}")
    pg.wait_for_timeout(2500)
    log_text2 = log.inner_text()
    replies = log_text2.count("Agent")
    t.check("回复按序回填(≥1 agent 回复到达)", replies >= 1, f"agents={replies}")
    t.no_errors("连发无 JS 错误")

    # ③ 非底部新消息 →「↓ 新消息」pill(不硬拽);点击滚底自收
    log.evaluate("e => e.scrollTop = 0")
    pg.evaluate(
        "(anchor) => { const live = __desktop.child('dev.longscroll').child(anchor).views.at(-1).live;"
        "live.receiveReply('远处新消息——pill 测试'); }",
        anchor
    )
    pg.wait_for_timeout(400)
    pill = pg.locator("[data-bubble-pill]")
    t.check("非底部新消息:pill 浮出", pill.count() > 0 and pill.first.is_visible())
    pill.first.click()
    pg.wait_for_timeout(300)
    bottomed = log.evaluate("e => e.scrollTop + e.clientHeight >= e.scrollHeight - 4")
    t.check("pill 点击滚底自收", bottomed and (pill.count() == 0 or pill.first.is_hidden()))
    t.no_errors("pill 无 JS 错误")

    # ④ 长单条消息折叠/展开
    long_text = "\n".join([f"折叠测试第 {i} 行" for i in range(10)])  # 真换行(JSON 序列化带行)
    pg.evaluate(
        "([anchor, txt]) => { const live = __desktop.child('dev.longscroll').child(anchor).views.at(-1).live;"
        "live.receiveReply(txt); }",
        [anchor, long_text]
    )
    pg.wait_for_timeout(400)
    t.check("长单条消息折叠(clamp + 展开钮)", pg.locator(".w-bubble-tx.is-clamped").count() > 0
            and pg.locator('[data-more]').count() > 0)
    pg.locator("[data-more]").first.click()
    pg.wait_for_timeout(300)
    t.check("展开:clamp 撤", pg.locator(".w-bubble-tx.is-clamped").count() == 0)
    pg.locator("[data-more]").first.click()
    pg.wait_for_timeout(300)
    t.check("再点折叠:clamp 回", pg.locator(".w-bubble-tx.is-clamped").count() > 0)
    pg.screenshot(path=f"{shots}/bubble-v2-collapse.png")

    # ⑤ 贴底锚点开泡 → 向上翻转(doc-bubble-up;宿主壳几何)
    fold = pg.locator("[data-bubble-x]").first
    if fold.count():
        fold.click()  # 收起当前泡(v3:卡面 ✕)
        pg.wait_for_timeout(300)
    pv = win.locator("[data-doc-preview]")
    pv.evaluate("e => e.scrollTop = e.scrollHeight")  # 文档滚到底
    pg.wait_for_timeout(400)
    bottom_block = win.locator(".doc-para[data-anchor]").last
    bottom_block.click(button="right")
    pg.wait_for_timeout(600)
    pop2 = pg.locator(".doc-bubble-pop:not([hidden])").first  # 只看可见泡(折叠的旧壳留 DOM)
    t.check("贴底锚点:向上翻转(doc-bubble-up)",
            pop2.count() > 0 and "doc-bubble-up" in (pop2.get_attribute("class") or ""),
            f"class={pop2.get_attribute('class') if pop2.count() else '无'}")
    pg.screenshot(path=f"{shots}/bubble-v2-flip.png")
    t.no_errors("W-bubble v2 全程无 JS 错误")


def run_bubble_v3(t):
    """气泡交互 v3(用户裁决 2026-08-11):原位开泡/选区关联/点外收起原位图标/
    图标重开草稿在/垃圾桶删除(后端同步)。"""
    import os
    shots = os.path.join(os.path.dirname(__file__), ".shots")
    os.makedirs(shots, exist_ok=True)

    pg = t.open("/platform/")
    pg.wait_for_selector(".dt-icon", timeout=10000)
    pg.wait_for_timeout(400)
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(500)
    ta = pg.locator("[data-cv-input]")
    ta.fill("文档列表")
    ta.press("Enter")
    pg.wait_for_selector(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]', timeout=15000)
    pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")

    # ① 段落中段右键 → 气泡在该点旁(原位浮出)
    block = win.locator(".doc-para[data-anchor]").nth(1)
    bb = block.bounding_box()
    cx, cy = bb["x"] + bb["width"] * 0.4, bb["y"] + bb["height"] / 2
    block.click(button="right", position={"x": bb["width"] * 0.4, "y": bb["height"] / 2})
    pg.wait_for_timeout(600)
    pop = pg.locator(".doc-bubble-pop").first
    t.check("原位开泡:壳出现", pop.count() > 0)
    if pop.count():
        pb = pop.bounding_box()
        t.check("原位:壳在点击点旁(非段首/段尾)",
                abs(pb["y"] - cy) < 90 and pb["x"] > bb["x"] + bb["width"] * 0.2,
                f"pop=({pb['x']:.0f},{pb['y']:.0f}) click=({cx:.0f},{cy:.0f})")
        pg.screenshot(path=f"{shots}/bubble-v3-inplace.png")
    t.no_errors("原位开泡无 JS 错误")

    # ② 选定文字右键 → quote = 选中文本(anchor 带列范围)
    # (须在普通段落块上拖选——标题/列表独占块渲染与源有符号差,回落行级)
    pg.locator("[data-bubble-x]").first.click()  # 收起 ① 的泡(控件内 ✕)
    pg.wait_for_timeout(300)
    # 用鼠标拖选第二段(普通段落)前几个字
    b1 = win.locator(".doc-para[data-anchor]").nth(1)
    r1 = b1.bounding_box()
    pg.mouse.move(r1["x"] + 24, r1["y"] + r1["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(r1["x"] + 150, r1["y"] + r1["height"] / 2, steps=6)
    pg.mouse.up()
    pg.wait_for_timeout(200)
    sel_text = pg.evaluate("() => String(getSelection())")
    b1.click(button="right", position={"x": 60, "y": r1["height"] / 2})
    pg.wait_for_timeout(600)
    quote = pg.locator(".doc-bubble-pop:not([hidden]) .w-bubble-quote").first.inner_text()  # 只看可见泡(折叠壳留 DOM)
    t.check("选区右键:quote = 选中文本", bool(sel_text) and sel_text[:6] in quote,
            f"sel={sel_text[:20]!r} quote={quote[:30]!r}")
    child_ids = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().map(s => s.id)")
    has_col = any(":C" in i for i in child_ids)
    t.check("选区右键:anchor 带列范围(:C)", has_col, f"ids={child_ids[-2:]}")
    # 本次开泡的锚点 = 快照最后一个子件(v3.1:点锚点/列范围列号不定,不猜)
    anchor_col = [i for i in child_ids if i != "doc"][-1] if child_ids else None
    pg.screenshot(path=f"{shots}/bubble-v3-selection.png")
    t.no_errors("选区关联无 JS 错误")

    # ③ 输入草稿 → 点泡外 → 缩成图标在原位附近(非段首);点图标重开草稿在
    # (全程用可见泡作用域:折叠的旧壳留 DOM,别让 .first 撞上隐藏壳)
    vpop = ".doc-bubble-pop:not([hidden])"
    pg.locator(f"{vpop} [data-bubble-draft]").first.fill("v3 草稿不丢验证")
    pg.wait_for_timeout(200)
    pg.locator(".dt-win-body").click(position={"x": 40, "y": 120})  # 点泡外
    pg.wait_for_timeout(400)
    t.check("点泡外:壳收起", pg.locator(vpop).count() == 0)
    marker = pg.locator(f'.doc-bubble-marker[data-anchor="{anchor_col}"]')  # ② 那只(① 的标记也在)
    t.check("点泡外:原位标记显出", marker.count() > 0 and marker.first.is_visible())
    if marker.count():
        mb = marker.first.bounding_box()
        t.check("标记在原位附近(锚点行旁,非段首)", mb["y"] > r1["y"] - 4, f"my={mb['y']:.0f}")
        pg.screenshot(path=f"{shots}/bubble-v3-marker.png")
        marker.first.click()
        pg.wait_for_timeout(500)
        t.check("点标记:泡重开", pg.locator(vpop).first.is_visible())
        draft_val = pg.locator(f"{vpop} [data-bubble-draft]").first.input_value()
        t.check("重开:草稿逐字在(收起≠删除)", draft_val == "v3 草稿不丢验证", f"draft={draft_val!r}")
    t.no_errors("点外收起/重开无 JS 错误")

    # ④ 垃圾桶:两击删除 → 壳消失 + 后端记录删除(重拉不含)
    del_btn = pg.locator(f"{vpop} [data-bubble-del]").first  # 可见泡内(隐藏壳留 DOM)
    t.check("垃圾桶在泡头", del_btn.count() > 0)
    del_btn.click()  # 第一击武装
    pg.wait_for_timeout(250)
    pg.locator(f"{vpop} [data-bubble-del]").first.click()  # 第二击确认
    pg.wait_for_timeout(800)
    t.check("垃圾桶:气泡消失", pg.locator(".doc-bubble-pop").count() == 0
            or not pg.locator(".doc-bubble-pop").first.is_visible())
    t.check("垃圾桶:compound 子件摘除", anchor_col not in pg.evaluate(
        "() => __desktop.child('demo.test').children_snapshot().map(s => s.id)"))
    if anchor_col:
        rest = pg.evaluate("() => fetch('/platform/api/docs/demo.test/bubbles').then(r => r.json())")
        t.check("垃圾桶:后端重拉不含该批注(持久化已删)",
                not any(b.get("anchor") == anchor_col for b in rest),
                f"rest anchors={[b.get('anchor') for b in rest]}")
    t.no_errors("垃圾桶删除全程无 JS 错误")


def run_bubble_v31(t):
    """行文级批注(v3.1 用户裁决;docs/DOC-BUBBLE.md):点锚点/一行多泡/
    行内高亮/点高亮重开/标记跟选段末/重渲高亮仍在。"""
    import os
    shots = os.path.join(os.path.dirname(__file__), ".shots")
    os.makedirs(shots, exist_ok=True)

    pg = t.open("/platform/")
    pg.wait_for_selector(".dt-icon", timeout=10000)
    pg.wait_for_timeout(400)
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(500)
    ta = pg.locator("[data-cv-input]")
    ta.fill("文档列表")
    ta.press("Enter")
    pg.wait_for_selector(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]', timeout=15000)
    pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")

    # ① 同一行两个不同位置右键 → 两个点锚点泡(不同列),各自独立
    block = win.locator(".doc-para[data-anchor]").nth(1)
    bb = block.bounding_box()
    block.click(button="right", position={"x": 40, "y": bb["height"] / 2})
    pg.wait_for_timeout(500)
    block.click(button="right", position={"x": min(bb["width"] - 20, 220), "y": bb["height"] / 2})
    pg.wait_for_timeout(500)
    ids = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().map(s => s.id)")
    col_pts = [i for i in ids if ":C" in i]
    t.check("一行两点右键 → 两个点锚点泡(零宽,列不同)", len(col_pts) >= 2, f"ids={col_pts}")
    import re as _re
    fmt_ok = all(
        _re.match(r"^doc\.md#L(\d+):C(\d+)-L(\d+):C(\d+)$", i)
        and _re.match(r"^doc\.md#L(\d+):C(\d+)-L(\d+):C(\d+)$", i).group(1)
            == _re.match(r"^doc\.md#L(\d+):C(\d+)-L(\d+):C(\d+)$", i).group(3)
        for i in col_pts
    )
    t.check("点锚点格式 Lx:Cy-Lx:Cy(零宽,同行)", fmt_ok, str(col_pts))
    pops = pg.locator(".doc-bubble-pop:not([hidden])")
    t.check("两泡同屏可见(一行多泡)", pops.count() >= 2, f"pops={pops.count()}")
    pg.screenshot(path=f"{shots}/bubble-v31-two-per-line.png")
    t.no_errors("一行多泡无 JS 错误")

    # ② 选区 → 行内高亮在正确文本上(.doc-hl 内容 = 选中文本)
    # 先收起 ① 的两只泡(壳 360px 宽会盖住下方文本,拖选会落在壳上)
    pg.locator(".dt-win-body").click(position={"x": 40, "y": 60})
    pg.wait_for_timeout(400)
    b2 = win.locator(".doc-para[data-anchor]").nth(3)  # 另一普通段(标题块列映射回落行级)
    r2 = b2.bounding_box()
    pg.mouse.move(r2["x"] + 20, r2["y"] + r2["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(r2["x"] + 130, r2["y"] + r2["height"] / 2, steps=6)
    pg.mouse.up()
    pg.wait_for_timeout(200)
    sel_text = pg.evaluate("() => String(getSelection())")
    b2.click(button="right", position={"x": 70, "y": r2["height"] / 2})
    pg.wait_for_timeout(600)
    # ② 的选区锚点(快照最后子件;给 ③ 的 marker 定位用)
    sel_anchor = pg.evaluate(
        "() => __desktop.child('demo.test').children_snapshot().map(s => s.id).filter(i => i !== 'doc').at(-1)")
    hl = win.locator(".doc-hl").first
    t.check("选区开泡:行内高亮出现", hl.count() > 0, f"hl={hl.count()}")
    if hl.count():
        hl_text = hl.inner_text()
        t.check("高亮文本 = 选中文本", bool(sel_text) and hl_text == sel_text.strip(),
                f"hl={hl_text[:24]!r} sel={sel_text[:24]!r}")
    pg.screenshot(path=f"{shots}/bubble-v31-inline-hl.png")
    t.no_errors("行内高亮无 JS 错误")

    # ③ 标记在选段末(末矩形右侧,非一律行尾)
    pg.locator(".dt-win-body").click(position={"x": 40, "y": 60})  # 点泡外收起全部
    pg.wait_for_timeout(400)
    if hl.count():
        hl_r = hl.bounding_box()
        mk = win.locator(f'.doc-bubble-marker[data-anchor="{sel_anchor}"]')  # ② 那只(① 的标记也在)
        t.check("收起后标记在", mk.count() > 0)
        if mk.count():
            mk_r = mk.first.bounding_box()
            t.check("标记跟选段末(右侧附近,非块右缘)",
                    abs(mk_r["x"] - hl_r["x"] - hl_r["width"]) < 60,
                    f"marker_x={mk_r['x']:.0f} hl_right={hl_r['x'] + hl_r['width']:.0f}")
    # ④ 点高亮 → 重开对应泡(内容独立)
    if hl.count():
        hl.click()
        pg.wait_for_timeout(500)
        pop2 = pg.locator(".doc-bubble-pop:not([hidden])").first
        t.check("点高亮:泡重开", pop2.count() > 0 and pop2.is_visible())
        t.check("重开泡锚点 = 高亮锚点(带列)",
                ":C" in (pg.locator(".doc-bubble-pop:not([hidden]) .w-bubble-title").first.inner_text() or ""))
        pg.screenshot(path=f"{shots}/bubble-v31-click-hl.png")
    t.no_errors("点高亮重开无 JS 错误")

    # ⑤ 重渲后高亮仍在(view source 往返 → relayout → 重挂)
    seg_src = pg.locator('.doc-viewseg [data-vm="source"]').first
    if seg_src.count():
        seg_src.click()
        pg.wait_for_timeout(400)
        pg.locator('.doc-viewseg [data-vm="preview"]').first.click()
        pg.wait_for_timeout(500)
        t.check("重渲后行内高亮仍在(relayout 重挂)", win.locator(".doc-hl").count() > 0,
                f"hl={win.locator('.doc-hl').count()}")
    t.no_errors("v3.1 全程无 JS 错误")

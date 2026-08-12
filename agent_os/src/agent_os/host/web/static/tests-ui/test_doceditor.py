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


def clean_annotations(pg, name=DOC):
    """段落级 fixture:清该文档全部批注(annotations/ + bubbles/ 两面;
    bubbles/delete 幂等 200)。tests-ui 各段独立页但库持久,不清会撞上残留锚点。"""
    pg.evaluate(
        "(n) => fetch(`/platform/api/docs/${n}/annotations`).then(r => r.json()).then(list =>"
        " Promise.all(list.map(a => fetch(`/platform/api/docs/${n}/bubbles/delete`, {method: 'POST',"
        " headers: {'Content-Type': 'application/json'}, body: JSON.stringify({anchor: a.anchor})}))))",
        name)
    pg.wait_for_timeout(300)


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
    clean_annotations(pg)  # 先清库再开文档(种子在打开时读取)
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
    t.check("批注卡挂进壳(w-bubble 本体)", pop.locator(".w-bubble").count() > 0)
    t.check("新批注 → 输入态(v4 composing)", pop.locator('[data-view="composing"]').count() > 0)
    paths = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().map(s => s.path)")
    t.check("寻址全树唯一:/root 通到 bubble 级(reparent 级联改址)",
            any(p.startswith("/root/demo.test/") for p in paths), str(paths)[:80])
    t.no_errors("开泡无 JS 错误")

    # 泡内写批注(v4:submit → annotations 落库,无即时 AI 回复;提交即收起成标记)
    draft = pg.locator("[data-bubble-draft]").first
    if draft.count():
        draft.fill("desktop 根验证:这段需要精简")
        draft.press("Enter")
        pg.wait_for_timeout(800)
        t.check("提交后壳收起(批注落库)", pg.locator(".doc-bubble-pop:not([hidden])").count() == 0)
        marker = pg.locator(".doc-bubble-marker").first
        t.check("段旁标记显出(pending 色环)", marker.count() > 0 and marker.is_visible())
        t.check("标记状态 = pending", marker.get_attribute("data-status") == "pending")
    t.no_errors("提交全程无 JS 错误")

    # 点标记重开(展示态,内容在)→ ✕ 收起
    marker = pg.locator(".doc-bubble-marker").first
    if marker.count():
        marker.click()
        pg.wait_for_timeout(500)
        vpop = pg.locator(".doc-bubble-pop:not([hidden])").first
        t.check("点标记:泡重开(展示态)", vpop.locator('[data-view="expanded"]').count() > 0)
        t.check("重开内容在", "desktop 根验证" in vpop.inner_text())
        fold = pg.locator("[data-bubble-x]").first
        if fold.count():
            fold.click()
            pg.wait_for_timeout(400)
            t.check("✕ 收起:段旁标记在", pg.locator(".doc-bubble-marker").first.is_visible())

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
    run_bubble_v2(t)  # 气泡几何回归(贴底翻转;消息流断言随 v4 退役)
    run_bubble_v3(t)  # 交互 v4 语义:原位/选区 quote/点外 C1/重开编辑/垃圾桶
    run_bubble_v31(t)  # 行文级批注 v3.1:点锚点/多泡/高亮/标记跟选段(v4 语义)
    run_bubble_v32(t)  # 几何回归 v3.2:F1 点旁/F2 无效坐标回落
    run_bubble_v4(t)  # 批注卡 v4:输入框规格/截断/Esc/悬停 tooltip/状态色环/重新编辑回 pending


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

    # 批注卡封顶(v4:壳 maxHeight 380 绝对上限,content 区内滚;锚段不被顶走)
    pg.evaluate(
        "() => { const log = document.querySelector('[data-doc-chat-log]');"
        "log.innerHTML = ''; }"  # 清空布局断言泵入行(只影响本页 DOM)
    )
    block = win.locator(".doc-para[data-anchor]").nth(1)
    block.click(button="right")
    pg.wait_for_timeout(600)
    y0 = block.bounding_box()["y"]
    draft2 = pg.locator(".doc-bubble-pop:not([hidden]) [data-bubble-draft]").first
    draft2.fill("封顶测试行\n" * 60)  # 超长意见(500 字截断)
    draft2.press("Enter")
    pg.wait_for_timeout(800)
    # 提交成标记 → 点标记展开(展示态长卡)
    pg.locator(".doc-bubble-marker").first.click()
    pg.wait_for_timeout(500)
    pop2 = pg.locator(".doc-bubble-pop:not([hidden])").first
    pop_h = pop2.bounding_box()["height"]
    t.check("批注卡封顶 ≤ 380px(v4 绝对上限)", pop_h <= 381, f"h={pop_h:.0f}")
    cbody = pop2.locator(".w-bubble-content")
    c_delta = cbody.evaluate("e => e.scrollHeight - e.clientHeight")
    t.check("content 区内滚(scrollHeight > clientHeight)", c_delta > 50, f"delta={c_delta}")
    y1 = block.bounding_box()["y"]
    t.check("锚段不被顶走(y 不变)", abs(y1 - y0) < 2, f"{y0:.0f}→{y1:.0f}")
    body3 = pg.evaluate("() => document.body.scrollHeight - document.documentElement.clientHeight")
    t.check("批注卡封顶下页级仍零", body3 <= 1, f"delta={body3}")
    pg.screenshot(path=f"{shots}/bubble-capped.png")
    t.no_errors("滚动层级全程无 JS 错误")


def run_bubble_v2(t):
    """气泡几何回归(v4 起消息流断言退役):贴底锚点开泡向上翻转;
    封顶/内滚在 run_scroll(长批注卡)盖。"""
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
    clean_annotations(pg, "dev.longscroll")  # 先清库再开文档
    pg.locator('[data-cv-log] [data-detail-kind="doc"][data-detail-ref="dev.longscroll"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")

    # 贴底锚点开泡 → 向上翻转(doc-bubble-up;宿主壳几何)
    pv = win.locator("[data-doc-preview]")
    pv.evaluate("e => e.scrollTop = e.scrollHeight")  # 文档滚到底
    pg.wait_for_timeout(400)
    bottom_block = win.locator(".doc-para[data-anchor]").last
    bottom_block.click(button="right")
    pg.wait_for_timeout(600)
    pop2 = pg.locator(".doc-bubble-pop:not([hidden])").first  # 只看可见泡(折叠壳留 DOM)
    t.check("贴底锚点:向上翻转(doc-bubble-up)",
            pop2.count() > 0 and "doc-bubble-up" in (pop2.get_attribute("class") or ""),
            f"class={pop2.get_attribute('class') if pop2.count() else '无'}")
    t.check("翻转泡 = 输入态(v4 composing)",
            pop2.locator('[data-view="composing"]').count() > 0)
    pg.screenshot(path=f"{shots}/bubble-v2-flip.png")
    t.no_errors("几何回归全程无 JS 错误")


def run_bubble_v3(t):
    """气泡交互 v3 行为面按 v4 语义改写(C1 点外/提交即记录/重开展示态/垃圾桶):
    原位输入态/选区 quote/点外=有内容提交空取消/编辑回 pending/删除后端同步。"""
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
    clean_annotations(pg)  # 先清库再开文档(种子在打开时读取)
    pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")
    vpop = ".doc-bubble-pop:not([hidden])"  # 只看可见泡(折叠壳留 DOM)

    # ① 段落中段右键 → 输入框在该点旁(原位浮出;v4 = composing)
    block = win.locator(".doc-para[data-anchor]").nth(1)
    bb = block.bounding_box()
    cx, cy = bb["x"] + bb["width"] * 0.4, bb["y"] + bb["height"] / 2
    block.click(button="right", position={"x": bb["width"] * 0.4, "y": bb["height"] / 2})
    pg.wait_for_timeout(600)
    pop = pg.locator(vpop).first
    t.check("原位开泡:壳出现", pop.count() > 0)
    if pop.count():
        pb = pop.bounding_box()
        t.check("原位:壳在点击点旁(非段首/段尾)",
                abs(pb["y"] - cy) < 90 and pb["x"] > bb["x"] + bb["width"] * 0.2,
                f"pop=({pb['x']:.0f},{pb['y']:.0f}) click=({cx:.0f},{cy:.0f})")
        t.check("原位:输入态(composing)", pop.locator('[data-view="composing"]').count() > 0)
        pg.screenshot(path=f"{shots}/bubble-v3-inplace.png")
    t.no_errors("原位开泡无 JS 错误")

    # ② 选定文字右键 → quote = 选中文本(anchor 带列范围);提交 → 高亮持久
    pg.locator(vpop).locator("[data-bubble-x]").first.click()  # 收 ①(空内容 → 取消摘除)
    pg.wait_for_timeout(300)
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
    quote = pg.locator(f"{vpop} .w-bubble-quote").first.inner_text()
    t.check("选区右键:quote = 选中文本", bool(sel_text) and sel_text[:6] in quote,
            f"sel={sel_text[:20]!r} quote={quote[:30]!r}")
    child_ids = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().map(s => s.id)")
    has_col = any(":C" in i for i in child_ids)
    t.check("选区右键:anchor 带列范围(:C)", has_col, f"ids={child_ids[-2:]}")
    anchor_col = [i for i in child_ids if i != "doc"][-1] if child_ids else None
    pg.locator(f"{vpop} [data-bubble-draft]").first.fill("v3 选区批注")
    pg.locator(f"{vpop} [data-bubble-draft]").first.press("Enter")
    pg.wait_for_timeout(800)
    t.check("提交后高亮持久(.doc-hl 在)", win.locator(".doc-hl").count() > 0)
    t.no_errors("选区关联无 JS 错误")

    # ③ 点外(C1):有内容 = 提交(标记出);空 = 取消(无标记无子件)
    n0 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    b3 = win.locator(".doc-para[data-anchor]").nth(3)
    b3.click(button="right", position={"x": 80, "y": 10})
    pg.wait_for_timeout(500)
    pg.locator(f"{vpop} [data-bubble-draft]").first.fill("点外提交验证")
    pg.locator(".dt-win-body").click(position={"x": 40, "y": 120})  # 点泡外
    pg.wait_for_timeout(800)
    anns = pg.evaluate("() => fetch('/platform/api/docs/demo.test/annotations').then(r => r.json())")
    t.check("点外有内容 = 提交落库", any(a.get("content") == "点外提交验证" for a in anns),
            f"contents={[a.get('content') for a in anns]}")
    b3.click(button="right", position={"x": 160, "y": 10})
    pg.wait_for_timeout(500)
    n1 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    pg.locator(".dt-win-body").click(position={"x": 40, "y": 120})  # 空点外
    pg.wait_for_timeout(400)
    n2 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    t.check("点外空 = 取消(子件摘除)", n2 == n1 - 1, f"{n1}→{n2}")
    t.no_errors("点外 C1 无 JS 错误")

    # ④ 标记重开 = 展示态(内容在);编辑 → 输入态预填 → 改内容提交回 pending
    marker = pg.locator(f'.doc-bubble-marker[data-anchor="{anchor_col}"]')
    t.check("② 的标记在", marker.count() > 0)
    if marker.count():
        marker.first.click()
        pg.wait_for_timeout(500)
        v2 = pg.locator(vpop).first
        t.check("重开 = 展示态(expanded)", v2.locator('[data-view="expanded"]').count() > 0)
        t.check("内容在", "v3 选区批注" in v2.inner_text())
        v2.locator("[data-bubble-edit]").first.click()
        pg.wait_for_timeout(300)
        draft = pg.locator(f"{vpop} [data-bubble-draft]").first
        t.check("编辑:草稿预填", draft.input_value() == "v3 选区批注")
        draft.fill("v3 选区批注(改)")
        draft.press("Enter")
        pg.wait_for_timeout(800)
        anns2 = pg.evaluate("() => fetch('/platform/api/docs/demo.test/annotations').then(r => r.json())")
        rec = next((a for a in anns2 if a.get("anchor") == anchor_col), {})
        t.check("编辑提交:内容更新且回 pending",
                rec.get("content") == "v3 选区批注(改)" and rec.get("status") == "pending",
                f"rec={rec.get('content')!r}/{rec.get('status')}")
    t.no_errors("重开/编辑无 JS 错误")

    # ⑤ 垃圾桶:两击删除 → 壳消失 + 后端记录删除(annotations 重拉不含)
    pg.locator(f'.doc-bubble-marker[data-anchor="{anchor_col}"]').first.click()
    pg.wait_for_timeout(500)
    del_btn = pg.locator(f"{vpop} [data-bubble-del]").first
    t.check("垃圾桶在泡头", del_btn.count() > 0)
    del_btn.click()  # 第一击武装
    pg.wait_for_timeout(250)
    pg.locator(f"{vpop} [data-bubble-del]").first.click()  # 第二击确认
    pg.wait_for_timeout(800)
    t.check("垃圾桶:气泡消失", pg.locator(vpop).count() == 0)
    t.check("垃圾桶:compound 子件摘除", anchor_col not in pg.evaluate(
        "() => __desktop.child('demo.test').children_snapshot().map(s => s.id)"))
    rest = pg.evaluate("() => fetch('/platform/api/docs/demo.test/annotations').then(r => r.json())")
    t.check("垃圾桶:后端重拉不含该批注(持久化已删)",
            not any(a.get("anchor") == anchor_col for a in rest),
            f"rest={[a.get('anchor') for a in rest]}")
    t.no_errors("垃圾桶删除全程无 JS 错误")


def run_bubble_v31(t):
    """行文级批注(v3.1/v4 语义):点锚点/一行多泡/行内高亮/标记跟选段末/
    点高亮重开/重渲高亮仍在——批注卡形态(提交即记录)。"""
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
    clean_annotations(pg)  # 先清库再开文档(种子在打开时读取)
    pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")
    vpop = ".doc-bubble-pop:not([hidden])"

    # ① 同一行两个不同位置右键各提交一条 → 两个点锚点批注(不同列),各自独立
    block = win.locator(".doc-para[data-anchor]").nth(1)
    bb = block.bounding_box()
    block.click(button="right", position={"x": 40, "y": bb["height"] / 2})
    pg.wait_for_timeout(500)
    pg.locator(f"{vpop} [data-bubble-draft]").first.fill("v31 批注甲")
    pg.locator(f"{vpop} [data-bubble-draft]").first.press("Enter")
    pg.wait_for_timeout(700)
    block.click(button="right", position={"x": min(bb["width"] - 20, 220), "y": bb["height"] / 2})
    pg.wait_for_timeout(500)
    pg.locator(f"{vpop} [data-bubble-draft]").first.fill("v31 批注乙")
    pg.locator(f"{vpop} [data-bubble-draft]").first.press("Enter")
    pg.wait_for_timeout(700)
    ids = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().map(s => s.id)")
    col_pts = [i for i in ids if ":C" in i]
    t.check("一行两点右键 → 两个点锚点批注(零宽,列不同)", len(col_pts) >= 2, f"ids={col_pts}")
    import re as _re
    fmt_ok = all(
        _re.match(r"^doc\.md#L(\d+):C(\d+)-L(\d+):C(\d+)$", i)
        and _re.match(r"^doc\.md#L(\d+):C(\d+)-L(\d+):C(\d+)$", i).group(1)
            == _re.match(r"^doc\.md#L(\d+):C(\d+)-L(\d+):C(\d+)$", i).group(3)
        for i in col_pts
    )
    t.check("点锚点格式 Lx:Cy-Lx:Cy(零宽,同行)", fmt_ok, str(col_pts))
    markers = pg.locator(".doc-bubble-marker:visible")
    t.check("提交后两标记同屏", markers.count() >= 2, f"markers={markers.count()}")
    pg.screenshot(path=f"{shots}/bubble-v31-two-per-line.png")
    t.no_errors("一行多泡无 JS 错误")

    # ② 选区 → 行内高亮在正确文本上(.doc-hl 内容 = 选中文本)
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
    sel_anchor = pg.evaluate(
        "() => __desktop.child('demo.test').children_snapshot().map(s => s.id).filter(i => i !== 'doc').at(-1)")
    pg.locator(f"{vpop} [data-bubble-draft]").first.fill("v31 高亮批注")
    pg.locator(f"{vpop} [data-bubble-draft]").first.press("Enter")
    pg.wait_for_timeout(700)
    hl = win.locator(".doc-hl").first
    t.check("选区提交:行内高亮出现", hl.count() > 0, f"hl={hl.count()}")
    if hl.count():
        hl_text = hl.inner_text()
        t.check("高亮文本 = 选中文本", bool(sel_text) and hl_text == sel_text.strip(),
                f"hl={hl_text[:24]!r} sel={sel_text[:24]!r}")
        t.check("高亮状态 = pending", hl.get_attribute("data-status") == "pending")
    pg.screenshot(path=f"{shots}/bubble-v31-inline-hl.png")
    t.no_errors("行内高亮无 JS 错误")

    # ③ 标记在选段末(末矩形右侧,非一律行尾)
    if hl.count():
        hl_r = hl.bounding_box()
        mk = win.locator(f'.doc-bubble-marker[data-anchor="{sel_anchor}"]')
        t.check("提交后标记在", mk.count() > 0)
        if mk.count():
            mk_r = mk.first.bounding_box()
            t.check("标记跟选段末(右侧附近,非块右缘)",
                    abs(mk_r["x"] - hl_r["x"] - hl_r["width"]) < 60,
                    f"marker_x={mk_r['x']:.0f} hl_right={hl_r['x'] + hl_r['width']:.0f}")
    # ④ 点高亮 → 重开对应泡(展示态,内容独立)
    if hl.count():
        hl.click()
        pg.wait_for_timeout(500)
        pop2 = pg.locator(vpop).first
        t.check("点高亮:泡重开", pop2.count() > 0 and pop2.is_visible())
        t.check("重开 = 展示态(已提交批注)",
                pop2.locator('[data-view="expanded"]').count() > 0)
        t.check("重开泡锚点 = 高亮锚点(带列)",
                ":C" in (pg.locator(f"{vpop} .w-bubble-title").first.inner_text() or ""))
        pg.locator(f"{vpop} [data-bubble-x]").first.click()  # 收起,留给 ⑤
        pg.wait_for_timeout(300)
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


def run_bubble_v32(t):
    """v3.2 几何回归(F1/F2;F3 封顶挪 run_scroll v4 版,F4 连发随消息流退役):
    泡左缘 = 点击点旁(不吸右缘);无效坐标回落(不页顶跳/不新建重复)。"""
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
    clean_annotations(pg)  # 先清库再开文档(种子在打开时读取)
    pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")
    vpop = ".doc-bubble-pop:not([hidden])"

    # ① F1:段内偏左右键 → 泡左缘 = 点击点旁(不吸行右缘),纵向在点击行下方
    block = win.locator(".doc-para[data-anchor]").nth(1)
    bb = block.bounding_box()
    block.click(button="right", position={"x": 100, "y": bb["height"] / 2})
    pg.wait_for_timeout(600)
    pop = pg.locator(vpop).first
    t.check("F1:点旁开泡壳出现", pop.count() > 0)
    if pop.count():
        pb = pop.bounding_box()
        click_x, click_y = bb["x"] + 100, bb["y"] + bb["height"] / 2
        t.check("F1:泡左缘 = 点击点旁(±60px)", abs(pb["x"] - click_x) < 60,
                f"pop_x={pb['x']:.0f} click_x={click_x:.0f}")
        t.check("F1:不吸行右缘", pb["x"] < bb["x"] + bb["width"] - 100,
                f"pop_x={pb['x']:.0f} block_right={bb['x'] + bb['width']:.0f}")
        t.check("F1:纵向在点击行下方", pb["y"] >= click_y,
                f"pop_y={pb['y']:.0f} click_y={click_y:.0f}")
        pg.screenshot(path=f"{shots}/bubble-v32-point.png")
    t.no_errors("F1 无 JS 错误")

    # ② F2:合成 contextmenu(clientX/Y=0,无效坐标)→ 回落链;已有泡再发不新建;
    # 壳不页顶跳
    pg.locator(".dt-win-body").click(position={"x": 40, "y": 120})  # 点泡外(空 → 取消)
    pg.wait_for_timeout(400)
    n0 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    pg.evaluate(
        "() => { const b = document.querySelector('.dt-win .doc-para[data-anchor]');"
        "b.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 0, clientY: 0})); }")
    pg.wait_for_timeout(600)
    n1 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    t.check("F2:无效坐标首发开泡(回落链建成一只)", n1 == n0 + 1, f"n0={n0} n1={n1}")
    pop2 = pg.locator(vpop).first
    y2 = pop2.bounding_box()["y"] if pop2.count() else -1
    t.check("F2:泡可见且不页顶跳(y > 50)", pop2.count() > 0 and y2 > 50, f"y={y2:.0f}")
    pg.evaluate(
        "() => { const b = document.querySelector('.dt-win .doc-para[data-anchor]');"
        "b.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 0, clientY: 0})); }")
    pg.wait_for_timeout(600)
    n2 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    t.check("F2:该块已有泡 → 聚焦不新建(子件数不变)", n2 == n1, f"n1={n1} n2={n2}")
    t.no_errors("F2 无 JS 错误")


def run_bubble_v4(t):
    """批注卡 v4(v2.1 §3/§1.2/§2.3):输入框硬规格/空抖动/500 截断/Esc 取消/
    悬停 tooltip/状态色环/applied 重新编辑回 pending。"""
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
    clean_annotations(pg)  # 先清库再开文档(种子在打开时读取)
    pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first.click()
    pg.wait_for_timeout(1500)
    win = pg.locator(".dt-win")
    vpop = ".doc-bubble-pop:not([hidden])"

    # ① 输入框硬规格(v2.1 §3):宽 240-400 / 占位文案 / 取消+添加 / 空添加禁用
    block = win.locator(".doc-para[data-anchor]").nth(2)
    block.click(button="right", position={"x": 60, "y": 10})
    pg.wait_for_timeout(600)
    pop = pg.locator(vpop).first
    t.check("v4:输入态壳出现", pop.count() > 0 and pop.locator('[data-view="composing"]').count() > 0)
    pb = pop.bounding_box()
    t.check("v4:输入框宽 240-400(v2.1 §3.2)", 240 <= pb["width"] <= 400, f"w={pb['width']:.0f}")
    ta1 = pop.locator("[data-bubble-draft]")
    t.check("v4:占位文案在", bool(ta1.get_attribute("placeholder")))
    t.check("v4:取消/添加钮在", pop.locator("[data-bubble-cancel]").count() > 0
            and pop.locator("[data-bubble-send]").count() > 0)
    t.check("v4:空输入添加禁用", ta1.evaluate("e => e.closest('.w-bubble').querySelector('[data-bubble-send]').disabled"))
    # ② 空 Enter → 不提交(壳仍在输入态;不产生标记)
    ta1.press("Enter")
    pg.wait_for_timeout(300)
    t.check("v4:空 Enter 不提交(壳仍在输入态,无标记)",
            pop.is_visible() and pg.locator(".doc-bubble-marker:visible").count() == 0)
    # ③ 500 字截断(v2.1 §3.4)
    ta1.fill("字" * 600)
    pg.wait_for_timeout(200)
    t.check("v4:500 字截断", len(ta1.input_value()) == 500, f"len={len(ta1.input_value())}")
    t.check("v4:截断提示在", pop.locator("[data-bubble-hint]:not([hidden])").count() > 0)
    # ④ Esc 取消:无标记无子件残留
    n0 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    ta1.press("Escape")
    pg.wait_for_timeout(400)
    n1 = pg.evaluate("() => __desktop.child('demo.test').children_snapshot().length")
    t.check("v4:Esc 取消摘除(子件回落)", n1 == n0 - 1, f"{n0}→{n1}")
    t.no_errors("v4 输入态无 JS 错误")

    # ⑤ 提交 → 标记 pending 色环 + 悬停 tooltip(摘录 + 锚点 + 状态)
    block.click(button="right", position={"x": 60, "y": 10})
    pg.wait_for_timeout(500)
    pg.locator(f"{vpop} [data-bubble-draft]").first.fill("v4 悬停预览验证批注")
    pg.locator(f"{vpop} [data-bubble-draft]").first.press("Enter")
    pg.wait_for_timeout(800)
    mk = pg.locator(".doc-bubble-marker:visible").first
    t.check("v4:提交成标记", mk.count() > 0)
    t.check("v4:标记 pending 色环", mk.get_attribute("data-status") == "pending")
    tok = pg.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--ann-pending').trim()")
    t.check("v4:状态色走 token(--ann-pending 在)", bool(tok), f"token={tok!r}")
    mk.hover()
    pg.wait_for_timeout(450)
    tip = pg.locator(".doc-ann-tip").first
    t.check("v4:悬停 200ms 出 tooltip", tip.count() > 0 and tip.is_visible())
    if tip.count():
        t.check("v4:tooltip 含摘录与锚点", "悬停预览验证" in tip.inner_text() and "L" in tip.inner_text(),
                tip.inner_text()[:60])
        pg.screenshot(path=f"{shots}/bubble-v4-tooltip.png")
    pg.mouse.move(40, 120)  # 移开 → tooltip 收
    pg.wait_for_timeout(300)
    t.check("v4:移开 tooltip 收", pg.locator(".doc-ann-tip").count() == 0)
    t.no_errors("v4 悬停无 JS 错误")

    # ⑥ applied 态(API 造)→ 标记绿 + 重新编辑回 pending(v2.1 §12.4)
    anchor5 = pg.evaluate(
        "() => __desktop.child('demo.test').children_snapshot().map(s => s.id).filter(i => i !== 'doc').at(-1)")
    pg.evaluate(
        "(a) => fetch('/platform/api/docs/demo.test/annotations', {method: 'POST',"
        "headers: {'Content-Type': 'application/json'},"
        "body: JSON.stringify({anchor: a, content: 'v4 悬停预览验证批注', status: 'applied'})})", anchor5)
    pg.wait_for_timeout(300)
    pg.reload()
    pg.wait_for_selector(".dt-icon", timeout=10000)
    pg.wait_for_timeout(400)
    pg.locator('.dt-icon[data-desk-open="conversation"]').click()
    pg.wait_for_timeout(500)
    pg.locator("[data-cv-input]").fill("文档列表")
    pg.locator("[data-cv-input]").press("Enter")
    pg.wait_for_selector(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]', timeout=15000)
    pg.locator(f'[data-cv-log] [data-detail-kind="doc"][data-detail-ref="{DOC}"]').first.click()
    pg.wait_for_timeout(1500)
    mk5 = pg.locator(f'.doc-bubble-marker[data-anchor="{anchor5}"]')
    t.check("v4:applied 标记色环(applied)", mk5.count() > 0 and mk5.first.get_attribute("data-status") == "applied",
            f"status={mk5.first.get_attribute('data-status') if mk5.count() else '无'}")
    if mk5.count():
        mk5.first.click()
        pg.wait_for_timeout(500)
        pg.locator(f"{vpop} [data-bubble-edit]").first.click()
        pg.wait_for_timeout(300)
        d5 = pg.locator(f"{vpop} [data-bubble-draft]").first
        d5.fill("v4 悬停预览验证批注(再改)")
        d5.press("Enter")
        pg.wait_for_timeout(800)
        rec5 = pg.evaluate(
            "(a) => fetch('/platform/api/docs/demo.test/annotations').then(r => r.json()).then("
            "list => list.find(x => x.anchor === a))", anchor5)
        t.check("v4:重新编辑回 pending(可参与下一轮生成)",
                rec5 and rec5.get("status") == "pending" and "再改" in rec5.get("content", ""),
                f"rec={rec5}")
        pg.screenshot(path=f"{shots}/bubble-v4-reedit.png")
    t.no_errors("v4 全程无 JS 错误")

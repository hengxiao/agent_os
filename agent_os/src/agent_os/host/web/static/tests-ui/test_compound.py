"""Compound playground 四能力真实浏览器测试(docs/COMPOUND-WIDGET.md §9 C2)。

页面:/compound.html。真实 DOM 选择器(以 js/compound-playground.js 为准):
- 两栏 = .pg-cols > .pg-col(第 1 左/第 2 右);卡 = .pg-item;操作钮 = [data-pg-act]
  (link/move-r/move-l/x);选件 = #pg-kind;加件 = #pg-add;事件流 = #pg-events;
  hard link 区 = #pg-linkzone > .pg-link-item。
四能力:① 预定义 slots ② 动态生灭 ③ 跨父转移 ④ hard link 多视图。
"""


def run(t):
    pg = t.open("/static/compound.html")
    pg.wait_for_timeout(800)  # module 加载 + 首渲
    t.no_errors("页面加载无 JS 错误")

    left = pg.locator(".pg-cols > .pg-col").nth(0)
    right = pg.locator(".pg-cols > .pg-col").nth(1)

    # ① 预定义:左栏初始有卡
    t.check("① 预定义 slots:左栏初始 ≥1 卡", left.locator(".pg-item").count() >= 1,
            f"实际 {left.locator('.pg-item').count()}")

    # ② 动态生灭:加一件 W-log,再 ✕ 移除
    before = left.locator(".pg-item").count()
    pg.select_option("#pg-kind", "log-viewer")
    pg.click("#pg-add")
    pg.wait_for_timeout(400)
    t.check("② 动态添加:左栏多一卡", left.locator(".pg-item").count() == before + 1,
            f"{before} → {left.locator('.pg-item').count()}")
    left.locator(".pg-item").last.locator('[data-pg-act="x"]').click()
    pg.wait_for_timeout(400)
    t.check("② 动态移除:✕ 后卡数回落", left.locator(".pg-item").count() == before,
            f"{left.locator('.pg-item').count()} ≠ {before}")

    # ③ 跨父转移:第一张卡 → 右栏,state 不动,有 reparent 广播
    card = left.locator(".pg-item").first
    text_before = card.inner_text()
    card.locator('[data-pg-act="move-r"]').click()
    pg.wait_for_timeout(500)
    t.check("③ 转移:右栏出现该卡", right.locator(".pg-item").count() >= 1,
            f"右栏 {right.locator('.pg-item').count()}")
    if right.locator(".pg-item").count():
        text_after = right.locator(".pg-item").first.inner_text()
        t.check("③ 转移 state 不动(内容一致)", text_before[:12] in text_after,
                f"左='{text_before[:30]}' 右='{text_after[:30]}'")
    events_text = pg.locator("#pg-events").inner_text()
    t.check("③ reparent 广播可见", "reparent" in events_text, events_text[:80])
    # 移回
    if right.locator(".pg-item").count():
        right.locator(".pg-item").first.locator('[data-pg-act="move-l"]').click()
        pg.wait_for_timeout(500)

    # ④ hard link:链接 → 双 view;右 tab 输入 → 左卡同步
    pg.select_option("#pg-kind", "text-editor")
    pg.click("#pg-add")
    pg.wait_for_timeout(400)
    card = left.locator(".pg-item").last
    card.locator('[data-pg-act="link"]').click()
    pg.wait_for_timeout(500)
    linkzone = pg.locator("#pg-linkzone")
    t.check("④ hard link:链接区出现第二 view", linkzone.locator("textarea").count() >= 1,
            f"textarea {linkzone.locator('textarea').count()}")
    if linkzone.locator("textarea").count():
        ta = linkzone.locator("textarea").first
        ta.click()
        ta.press("End")
        ta.type("XYZ")
        pg.wait_for_timeout(400)
        card_text = left.locator(".pg-item").last.inner_text()
        t.check("④ 扇出:右 tab 输入左卡同步", "XYZ" in card_text, card_text[-40:])
    t.no_errors("交互全程无 JS 错误")

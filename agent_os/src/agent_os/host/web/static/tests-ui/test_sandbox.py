"""widget.html 沙盒回归:13 控件 × 2 形态 × 全部样例,真实浏览器逐个挂载。

覆盖的正是 stub 测不出的那层:模块加载、主题 init、真实 DOM 挂载、
重渲不崩。选择器以 widget.html 的 sb-* id 为准。
"""

KINDS = [
    "text-editor", "json-editor", "table-editor", "kv-editor", "schema-form",
    "select-list", "ns-tree", "date-picker", "chart", "log-viewer",
    "diff-viewer", "md-viewer", "chat-bubble",
]


def run(t):
    pg = t.open("/static/widget.html")
    pg.wait_for_timeout(800)
    t.no_errors("沙盒加载无 JS 错误")

    for kind in KINDS:
        for surface in ("tab", "card"):
            pg.select_option("#sb-kind", kind)
            pg.select_option("#sb-surface", surface)
            pg.wait_for_timeout(250)
            n_samples = pg.locator("#sb-sample option").count()
            ok_all = True
            detail = ""
            for i in range(n_samples):
                pg.select_option("#sb-sample", str(i))
                pg.wait_for_timeout(250)
                html = pg.locator("#sb-stage").inner_html()
                if len(html) < 40:
                    ok_all = False
                    detail = f"样例 {i} 舞台近乎空({len(html)}B)"
                    break
            t.check(f"{kind} · {surface}:全部 {n_samples} 样例渲染非空", ok_all, detail)
            t.check(f"{kind} · {surface}:无 JS 错误", not (t.errors or t.bad_responses),
                    " | ".join((t.errors + t.bad_responses)[:2]))

    # 交互抽查:W-text 输入 → Events 面板有 change;State 面板同步
    pg.select_option("#sb-kind", "text-editor")
    pg.select_option("#sb-surface", "tab")
    pg.select_option("#sb-sample", "0")
    pg.wait_for_timeout(300)
    ta = pg.locator("#sb-stage textarea").first
    ta.click()
    ta.type("探针")
    pg.wait_for_timeout(800)  # State 面板 500ms 轮询,等它刷一轮
    events = pg.locator("#sb-events").inner_text()
    t.check("交互:text 输入 → Events 面板收到 change", "change" in events, events[:60])
    pg.click("#sb-tab-state")
    pg.wait_for_timeout(600)
    state_text = pg.locator("#sb-state").input_value()  # textarea:内容在 value,不在 inner_text
    t.check("交互:State 面板同步输入内容", "探针" in state_text, state_text[-40:])
    t.no_errors("交互全程无 JS 错误")

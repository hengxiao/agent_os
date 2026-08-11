"""widget-libs 试点 3:markdown-it 对照件(md-viewer-mi)真实浏览器断言。

页面:/static/widget.html(沙盒;?kind=md-viewer-mi 直达)。
断言:与 md-viewer 同 source 并排——关键结构两版都在(标题/列表/代码块+复制钮/
表格/引用/行内 code/链接 target+rel);XSS 样例剥壳实测(script 不进 DOM、
javascript: 链接整块移除);view source 切换;并排截图 2 张。
"""

SRC_TYPICAL = 0
SRC_XSS = 1


def run(t):
    import os
    shots = os.path.join(os.path.dirname(__file__), ".shots")
    os.makedirs(shots, exist_ok=True)

    # ① 两版并排渲染对照(同 source,关键结构都在)
    structs = {}
    for kind in ["md-viewer", "md-viewer-mi"]:
        pg = t.open(f"/static/widget.html?kind={kind}&sample={SRC_TYPICAL}")
        pg.wait_for_timeout(700)
        body = pg.locator("#sb-stage")
        structs[kind] = {
            "h": body.locator("h4").count(),
            "li": body.locator("li").count(),
            "pre": body.locator("pre").count(),
            "copybtn": body.locator("[data-md-copy]").count(),
            "table": body.locator("table.wd-md-table").count(),
            "quote": body.locator(".wd-md-quote, blockquote").count(),
            "inline_code": body.locator("code.mono").count(),
            "link": body.locator('a[target="_blank"][rel="noopener noreferrer"]').count(),
        }
        t.no_errors(f"{kind} 渲染无 JS 错误")
    for key in ["h", "li", "pre", "copybtn", "table", "quote", "inline_code", "link"]:
        t.check(f"结构对照:{key} 两版都在且等量",
                structs["md-viewer"][key] > 0 and structs["md-viewer"][key] == structs["md-viewer-mi"][key],
                f"hand={structs['md-viewer'][key]} mi={structs['md-viewer-mi'][key]}")

    # 截图:并排对照(同 source 两版)
    pg = t.open(f"/static/widget.html?kind=md-viewer&sample={SRC_TYPICAL}")
    pg.wait_for_timeout(700)
    pg.screenshot(path=f"{shots}/mi-vs-hand-md.png")
    pg = t.open(f"/static/widget.html?kind=md-viewer-mi&sample={SRC_TYPICAL}")
    pg.wait_for_timeout(700)
    pg.screenshot(path=f"{shots}/mi-vs-hand-mi.png")

    # ② XSS 剥壳实测(markdown-it 版:script 不进 DOM + javascript: 整块移除)
    pg = t.open(f"/static/widget.html?kind=md-viewer-mi&sample={SRC_XSS}")
    pg.wait_for_timeout(700)
    t.check("XSS:script 标签不进 DOM", pg.locator("#sb-stage script").count() == 0)
    t.check("XSS:无 alert/onerror 属性面",
            pg.locator("#sb-stage [onerror], #sb-stage [onload], #sb-stage [onclick]").count() == 0)
    stage_text = pg.locator("#sb-stage").inner_text()
    t.check("XSS:javascript: 链接整块移除(不留残迹)",
            "javascript:" not in stage_text and "点我" not in pg.locator("#sb-stage").inner_html(),
            stage_text[:60])
    t.check("XSS:无链接残留", pg.locator("#sb-stage a").count() == 0)
    t.no_errors("XSS 样例无 JS 错误")

    # ③ view source 切换(复用 W-md 同构面)
    pg = t.open(f"/static/widget.html?kind=md-viewer-mi&sample={SRC_TYPICAL}")
    pg.wait_for_timeout(700)
    pg.locator('[data-md-view="source"]').click()
    pg.wait_for_timeout(400)
    t.check("view source:源码态出现", pg.locator(".wd-md-src-pre").count() == 1)
    t.check("view source:内容 = 原始 markdown", "# 标题" in pg.locator(".wd-md-src-pre").inner_text())
    pg.locator('[data-md-view="preview"]').click()
    pg.wait_for_timeout(400)
    t.check("回 preview:结构复原", pg.locator("#sb-stage h4").count() > 0)
    t.no_errors("view source 全程无 JS 错误")

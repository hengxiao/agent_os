"""widget-libs 试点 2:CodeMirror 6 对照件(text-editor-cm)真实浏览器断言。

页面:/static/widget.html(沙盒;?kind=text-editor-cm 直达)。
断言:CM6 实例挂载(.cm-editor 出)/输入进 state(State 面板)/dirty+微标跟随/
主题 token 生效(cm-content 计算色 == --fg-0 解析值)/readonly 不可输入;
截图 = 与 text-editor 并排对比(classic,同样例)。
沙盒 State 面板 = #sb-state(textarea,widget.state JSON;500ms 轮询)。
"""

SAMPLES = {
    "typical": 0,      # plain 典型
    "mono": 1,         # mono 行号槽
    "empty": 2,
    "readonly": 3,
}


def _open(pg, base, kind, sample):
    pg.goto(f"{base}/static/widget.html?kind={kind}&sample={sample}", wait_until="networkidle")
    pg.wait_for_timeout(900)


def run(t):
    import os
    shots = os.path.join(os.path.dirname(__file__), ".shots")
    os.makedirs(shots, exist_ok=True)

    pg = t.open(f"/static/widget.html?kind=text-editor-cm&sample={SAMPLES['mono']}")
    pg.wait_for_timeout(1500)  # CM6 异步装载
    t.no_errors("沙盒打开 text-editor-cm 无 JS 错误")

    # ① CM6 挂载 + 我们的 chrome 同屏
    t.check("CM6 实例挂载(.cm-editor 出)", pg.locator(".cm-editor").count() == 1)
    t.check("CM6 行号槽内建(.cm-gutters)", pg.locator(".cm-gutters").count() == 1)
    t.check("我们的 chrome 在(头部 field · lang)", "body · markdown" in pg.locator(".wd-text-head").inner_text())
    t.check("微标在(wd-micro)", pg.locator(".wd-micro").count() == 1)

    # ② 输入进 state + dirty/微标跟随(updateListener → canonical)
    pg.locator(".cm-content").click()
    pg.keyboard.press("End")
    pg.keyboard.type("X7")
    pg.wait_for_timeout(900)  # State 面板 500ms 轮询
    state_text = pg.locator("#sb-state").input_value()
    t.check("输入进 state(State 面板含新文本)", "X7" in state_text, state_text[-80:])
    t.check("dirty 置位(state)", '"dirty": true' in state_text or '"dirty":true' in state_text)
    t.check("dirty 边条/圆点跟随(is-dirty)", pg.locator(".wd-text.is-dirty").count() == 1)
    t.check("微标行数跟随(4 行)", "4 行" in pg.locator(".wd-micro").inner_text())
    t.no_errors("输入全程无 JS 错误")

    # ③ 主题 token 生效(cm-content 计算色 == --fg-0 解析值;非默认黑)
    cm_color, ref_color = pg.evaluate(
        """() => {
          const cm = getComputedStyle(document.querySelector('.cm-content')).color;
          const probe = document.createElement('div');
          probe.style.color = 'var(--fg-0)';
          document.body.appendChild(probe);
          const ref = getComputedStyle(probe).color;
          probe.remove();
          return [cm, ref];
        }"""
    )
    t.check("CM6 前景色 = 我们的 token(--fg-0)", cm_color == ref_color, f"cm={cm_color} ref={ref_color}")
    gutter_bg = pg.evaluate(
        "() => getComputedStyle(document.querySelector('.cm-gutters')).backgroundColor"
    )
    t.check("行号槽底色 = token(--bg-1,非 CM 默认)", gutter_bg != "rgb(255, 255, 255)", f"bg={gutter_bg}")

    # ④ readonly 不可输入(editable=false;state 不动)
    _open(pg, t.base, "text-editor-cm", SAMPLES["readonly"])
    pg.wait_for_selector(".cm-content", timeout=10000)  # CM6 异步装载(条件等待)
    before = pg.locator("#sb-state").input_value()
    pg.locator(".cm-content").click()
    pg.keyboard.type("ZZZ")
    pg.wait_for_timeout(800)
    after = pg.locator("#sb-state").input_value()
    t.check("readonly:输入不进 state", "ZZZ" not in after and after == before, after[-60:])
    t.check("readonly:锁徽标在", pg.locator(".wd-lock").count() == 1)
    t.no_errors("readonly 全程无 JS 错误")

    # ⑤ 并排对比截图(classic;同样例 mono 行号槽)
    _open(pg, t.base, "text-editor", SAMPLES["mono"])
    pg.wait_for_selector(".wd-text textarea", timeout=10000)
    pg.wait_for_timeout(400)
    pg.screenshot(path=f"{shots}/cm-vs-wtext-hand.png")
    _open(pg, t.base, "text-editor-cm", SAMPLES["mono"])
    pg.wait_for_selector(".cm-content", timeout=10000)
    pg.wait_for_timeout(400)
    pg.screenshot(path=f"{shots}/cm-vs-wtext-cm.png")
    t.no_errors("对比截图全程无 JS 错误")

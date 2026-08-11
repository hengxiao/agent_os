"""widget-libs 试点:vendor 库存在性 + 关键导出真实浏览器断言(docs/WIDGET-ARCH.md
「vendor 库集成原则」——每个 vendor 库必须有存在性 + 关键导出的真实浏览器断言)。

页面:/platform/(desktop 根)。被验库:floating-ui(1.8.0,MIT,
web/static/vendor/floating-ui/floating-ui.dom.mjs,core import 已改写相对路径)。
"""


def run(t):
    pg = t.open("/platform/")
    pg.wait_for_timeout(1200)
    t.no_errors("页面加载无 JS 错误")

    result = pg.evaluate(
        """async () => {
          const F = await import('/static/vendor/floating-ui/floating-ui.dom.mjs?v=2026-08-12.1');
          const need = ["computePosition", "flip", "shift", "offset", "size", "arrow", "autoUpdate"];
          const missing = need.filter((k) => typeof F[k] !== "function");
          if (missing.length) return { missing };
          const ref = document.createElement("div");
          ref.style.cssText = "position:absolute;left:200px;top:200px;width:40px;height:20px";
          const fl = document.createElement("div");
          fl.style.cssText = "position:absolute;width:100px;height:60px";
          document.body.appendChild(ref);
          document.body.appendChild(fl);
          const pos = await F.computePosition(ref, fl, {
            placement: "bottom-start",
            middleware: [F.offset(8), F.flip(), F.shift({ padding: 16 })],
          });
          ref.remove(); fl.remove();
          return { missing: [], x: pos.x, y: pos.y, placement: pos.placement };
        }"""
    )
    t.check("floating-ui 可导入(关键导出齐)", not result.get("missing"),
            f"missing={result.get('missing')}")
    t.check("computePosition 实测返回坐标",
            isinstance(result.get("x"), (int, float)) and isinstance(result.get("y"), (int, float)),
            str(result)[:80])
    t.check("placement = bottom-start(无翻转需求时不乱翻)",
            result.get("placement") == "bottom-start", str(result.get("placement")))
    t.check("offset(8) 生效(y 有 8px 间隙)", result.get("y", 0) >= 228, f"y={result.get('y')}")
    t.no_errors("vendor 断言全程无 JS 错误")

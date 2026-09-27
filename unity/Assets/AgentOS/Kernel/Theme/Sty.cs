using UnityEngine;
using UnityEngine.UIElements;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// 样式助手:组件只消费当前主题的 token(对齐"全部样式只消费契约 token,
    /// 不写散值"的纪律;WEB-UI.md §3 / DEBUG-UI-THEMES.md §4-6 组件无分支)。
    /// v1 用 inline style 应用 token;USS 资产化是后续优化(见 unity/README.md)。
    /// </summary>
    public static class Sty
    {
        public static Color C(string token)
        {
            var t = ThemeRegistry.Current;
            if (t == null) return Color.magenta;
            Color c;
            return t.Colors.TryGetValue(token, out c) ? c : Color.magenta;
        }

        public static int S(string token)
        {
            var t = ThemeRegistry.Current;
            if (t == null) return 12;
            int v;
            return t.Sizes.TryGetValue(token, out v) ? v : 12;
        }

        public static string Copy(string key)
        {
            var t = ThemeRegistry.Current;
            string v;
            return t != null && t.Copy.TryGetValue(key, out v) ? v : key; // 缺 key 上屏 key 本体,契约测试的肉眼版
        }

        // ---------------------------------------------------------------- 结构件

        public static void Border(VisualElement el, float width, string colorToken)
        {
            var c = C(colorToken);
            el.style.borderTopWidth = width; el.style.borderTopColor = c;
            el.style.borderBottomWidth = width; el.style.borderBottomColor = c;
            el.style.borderLeftWidth = width; el.style.borderLeftColor = c;
            el.style.borderRightWidth = width; el.style.borderRightColor = c;
        }

        public static void Radius(VisualElement el, string token)
        {
            var r = S(token);
            el.style.borderTopLeftRadius = r; el.style.borderTopRightRadius = r;
            el.style.borderBottomLeftRadius = r; el.style.borderBottomRightRadius = r;
        }

        public static void Pad(VisualElement el, int all)
        {
            el.style.paddingTop = all; el.style.paddingBottom = all;
            el.style.paddingLeft = all; el.style.paddingRight = all;
        }

        /// <summary>面板(bg-1 + line 描边 + r-md)。</summary>
        public static VisualElement Panel(string bgToken = "bg-1")
        {
            var el = new VisualElement();
            el.style.backgroundColor = C(bgToken);
            Border(el, 1, "line");
            Radius(el, "r-md");
            Pad(el, S("s3"));
            return el;
        }

        /// <summary>按钮(四态由主题档 + 交互回调承担;主按钮 = live 色面)。</summary>
        public static Button Btn(string text, bool primary = false)
        {
            var b = new Button { text = text };
            b.style.backgroundColor = C(primary ? "live" : "bg-2");
            b.style.color = C(primary ? "bg-0" : "fg-0");
            Border(b, 1, primary ? "line-strong" : "line");
            Radius(b, "r-sm");
            b.style.paddingTop = S("s1"); b.style.paddingBottom = S("s1");
            b.style.paddingLeft = S("s3"); b.style.paddingRight = S("s3");
            b.style.marginTop = 2; b.style.marginBottom = 2;
            b.style.marginLeft = 2; b.style.marginRight = 2;
            return b;
        }

        public static Label Text(string content, string sizeToken = "text-md", string colorToken = "fg-0", bool bold = false)
        {
            var l = new Label(content);
            l.style.fontSize = S(sizeToken);
            l.style.color = C(colorToken);
            l.style.whiteSpace = WhiteSpace.Normal;
            if (bold) l.style.unityFontStyleAndWeight = FontStyle.Bold;
            return l;
        }

        public static TextField Input(string placeholder = "", bool multiline = false)
        {
            var f = new TextField { multiline = multiline };
            f.style.backgroundColor = C("bg-1");
            f.style.color = C("fg-0");
            Border(f, 1, "line");
            Radius(f, "r-sm");
            f.style.fontSize = S("text-sm");
            if (placeholder.Length > 0) f.tooltip = placeholder;
            return f;
        }

        /// <summary>状态点(双编码的一半:颜色;文字/图标通道由调用方另给,a11y 纪律)。</summary>
        public static VisualElement Dot(string colorToken, int size = 10)
        {
            var d = new VisualElement();
            d.style.backgroundColor = C(colorToken);
            d.style.width = size; d.style.height = size;
            d.style.borderTopLeftRadius = size / 2f; d.style.borderTopRightRadius = size / 2f;
            d.style.borderBottomLeftRadius = size / 2f; d.style.borderBottomRightRadius = size / 2f;
            d.style.marginTop = 3; d.style.marginRight = 4;
            return d;
        }

        /// <summary>细分隔线。</summary>
        public static VisualElement HLine()
        {
            var l = new VisualElement();
            l.style.height = 1;
            l.style.backgroundColor = C("line");
            l.style.marginTop = S("s1"); l.style.marginBottom = S("s1");
            return l;
        }

        /// <summary>行容器。</summary>
        public static VisualElement Row()
        {
            var r = new VisualElement();
            r.style.flexDirection = FlexDirection.Row;
            r.style.alignItems = Align.Center;
            return r;
        }

        /// <summary>卡片(悬浮面 bg-2 + 强调描边)。</summary>
        public static VisualElement Card()
        {
            var el = Panel("bg-2");
            Border(el, 1, "line-strong");
            return el;
        }
    }
}

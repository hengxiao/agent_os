using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace AgentOS.UI.Apps.DocEditor
{
    /// <summary>md-viewer 定义面(W-md 的 Unity 版:白名单渲染,安全转义先行)。</summary>
    public sealed class MdViewerDef : WidgetDef
    {
        public override string Kind { get { return "md-viewer"; } }
        public override System.Collections.Generic.IReadOnlyList<string> Events
        {
            get { return new[] { "open-bubble" }; }
        }
        public override Widget CreateInstance(JObject state) { return new MdViewerWidget(this, state); }
    }

    /// <summary>
    /// Markdown 预览(W-md):按 MdBlocks 分块渲染;每块 💬 锚点钮 + 右键开泡。
    /// 白名单纪律:先整体转义再加工有限标记(标题/粗斜体/行内码/代码块/列表/表格行),
    /// 不做 HTML 注入。块内联气泡壳有意不做——气泡聚在 app 的批注栏,viewer 重渲
    /// 不摘气泡宿主(web 侧 D2 弯腰点 ② 的结构性规避)。
    /// </summary>
    public sealed class MdViewerWidget : Widget
    {
        public MdViewerWidget(WidgetDef def, JObject state) : base(def, state) { }

        public override void Render()
        {
            Root.Clear();
            Root.style.flexGrow = 1;
            var source = State.Value<string>("source") ?? "";
            var counts = State["bubbleCounts"] as JObject ?? new JObject();
            var changed = State["changed"] as JArray ?? new JArray();

            if (source.Trim().Length == 0)
            {
                Root.Add(Sty.Text("(空文档 —— 在下方对话里说出你要什么)", "text-sm", "fg-2"));
                return;
            }

            foreach (var block in MdBlocks.Split(source))
            {
                var row = Sty.Row();
                row.style.alignItems = Align.FlexStart;
                row.style.paddingTop = 2; row.style.paddingBottom = 2;

                var anchor = block.Anchor;
                var content = new Label(ToRichText(block)) { enableRichText = true, selectionEnabled = true };
                content.style.flexGrow = 1;
                content.style.whiteSpace = WhiteSpace.Normal;
                content.style.fontSize = Sty.S("text-sm");
                content.style.color = Sty.C("fg-0");
                row.Add(content);

                var count = counts.Value<int?>(anchor) ?? 0;
                var marker = count > 0 ? "💬" + count : "💬";
                var open = Sty.Btn(marker);
                open.style.fontSize = Sty.S("text-xs");
                open.style.minWidth = 30;
                var captured = anchor;
                open.clicked += () => Emit("open-bubble", new JObject { ["anchor"] = captured });
                row.Add(open);

                // 右键开泡(D5:contextmenu 任意块)
                row.RegisterCallback<ContextClickEvent>(evt =>
                {
                    Emit("open-bubble", new JObject { ["anchor"] = captured });
                    evt.StopPropagation();
                });

                if (Contains(changed, anchor))
                {
                    row.style.backgroundColor = WithAlpha(Sty.C("ok"), 0.18f);
                    MotionPlayer.Play("change-flash", row);
                }
                Root.Add(row);
                Root.Add(Sty.HLine());
            }
        }

        static bool Contains(JArray arr, string s)
        {
            foreach (var t in arr) if (t.Type == JTokenType.String && t.Value<string>() == s) return true;
            return false;
        }

        static Color WithAlpha(Color c, float a) { return new Color(c.r, c.g, c.b, a); }

        /// <summary>块 → 富文本(白名单加工;转义在先)。</summary>
        static string ToRichText(MdBlock block)
        {
            var text = block.Text;
            if (block.Kind == "code")
                return "<color=#" + ColorUtility.ToHtmlStringRGB(Sty.C("sig-llm")) + ">" + Escape(text) + "</color>";

            if (block.Kind == "heading")
            {
                var m = Regex.Match(text, @"^\s*(#{1,6})\s+(.*)$");
                if (m.Success)
                {
                    var level = m.Groups[1].Length;
                    var size = Mathf.Max(Sty.S("text-xl") - (level - 1) * 2, Sty.S("text-md"));
                    return "<size=" + size + "><b>" + Inline(m.Groups[2].Value.Trim()) + "</b></size>";
                }
            }
            if (block.Kind == "list-item")
            {
                var m = Regex.Match(text, @"^(\s*)([-*+]|\d+\.)\s+(.*)$");
                if (m.Success)
                    return Escape(m.Groups[1].Value) + "• " + Inline(m.Groups[3].Value);
            }
            return Inline(text);
        }

        /// <summary>行内白名单:转义后仅加工 **粗** / *斜* / `码` 三种。</summary>
        static string Inline(string text)
        {
            var s = Escape(text);
            s = Regex.Replace(s, @"\*\*(.+?)\*\*", "<b>$1</b>");
            s = Regex.Replace(s, @"(?<!\w)\*([^*\n]+?)\*(?!\w)", "<i>$1</i>");
            s = Regex.Replace(s, "`([^`\n]+?)`",
                "<color=#" + ColorUtility.ToHtmlStringRGB(Sty.C("sig-llm")) + ">$1</color>");
            return s.Replace("\n", "\n");
        }

        static string Escape(string s)
        {
            var sb = new StringBuilder(s.Length);
            foreach (var ch in s)
            {
                if (ch == '<') sb.Append("&lt;");
                else if (ch == '>') sb.Append("&gt;");
                else if (ch == '&') sb.Append("&amp;");
                else sb.Append(ch);
            }
            return sb.ToString();
        }

        public override string Summary()
        {
            var source = State.Value<string>("source") ?? "";
            return "文档预览(" + source.Length + " 字)";
        }
    }
}

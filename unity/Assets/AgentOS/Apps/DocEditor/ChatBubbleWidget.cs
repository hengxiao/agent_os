using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace AgentOS.UI.Apps.DocEditor
{
    /// <summary>chat-bubble 定义面(W-bubble 的 Unity 版)。</summary>
    public sealed class ChatBubbleDef : WidgetDef
    {
        public override string Kind { get { return "chat-bubble"; } }
        public override System.Collections.Generic.IReadOnlyList<string> Events
        {
            get { return new[] { "submit", "apply", "close" }; }
        }
        public override Widget CreateInstance(JObject state) { return new ChatBubbleWidget(this, state); }
    }

    /// <summary>
    /// 锚点批注气泡(docs/WIDGETS.md W-bubble):锚点引用行 + 消息流 + 输入框。
    /// 铁律:widget 不出海——submit/apply 只发事件,父(DocEditorApp)经闸门接管,
    /// 出海走 comment 专属端点与 comment.apply 管道(D2 先例)。
    /// state:{anchor, quote, messages:[{role,text,edits?}], busy, draft}
    /// </summary>
    public sealed class ChatBubbleWidget : Widget
    {
        public string Anchor { get { return State.Value<string>("anchor") ?? ""; } }

        public ChatBubbleWidget(WidgetDef def, JObject state) : base(def, state) { }

        public override void Render()
        {
            Root.Clear();
            var card = Sty.Card();
            card.style.marginBottom = Sty.S("s2");

            // 头:锚点 + ✕(收起;消息流在服务端,重开不丢)
            var head = Sty.Row();
            head.Add(Sty.Text(Anchor, "text-xs", "fg-2"));
            var spacer = new VisualElement(); spacer.style.flexGrow = 1; head.Add(spacer);
            var close = Sty.Btn("✕");
            close.style.fontSize = Sty.S("text-xs");
            close.tooltip = "收起气泡(消息保留在服务端)";
            close.clicked += () => Emit("close", new JObject { ["anchor"] = Anchor });
            head.Add(close);
            card.Add(head);

            // 锚点引用行(摘录)
            var quote = State.Value<string>("quote") ?? "";
            if (quote.Length > 120) quote = quote.Substring(0, 120) + "…";
            var quoteLabel = Sty.Text(quote, "text-xs", "fg-1");
            quoteLabel.style.unityFontStyleAndWeight = FontStyle.Italic;
            quoteLabel.style.marginBottom = Sty.S("s1");
            card.Add(quoteLabel);

            // 消息流(role=log 语义)
            var messages = State["messages"] as JArray ?? new JArray();
            foreach (var m in messages)
            {
                var msg = m as JObject;
                if (msg == null) continue;
                var isUser = msg.Value<string>("role") == "user";
                var line = Sty.Row();
                line.style.alignItems = Align.FlexStart;
                line.style.marginTop = 2;
                var who = Sty.Text(isUser ? "你" : "agent", "text-xs", isUser ? "live" : "ok", true);
                who.style.minWidth = 34;
                line.Add(who);
                var body = Sty.Text(msg.Value<string>("text") ?? "", "text-sm", "fg-0");
                body.selectionEnabled = true;
                body.style.flexGrow = 1;
                line.Add(body);
                card.Add(line);

                // agent 建议 → apply 按钮(人按才落,agent 永不直改——升权哲学)
                var edits = msg["edits"] as JArray;
                if (!isUser && edits != null)
                {
                    foreach (var e in edits)
                    {
                        var edit = e as JObject;
                        if (edit == null) continue;
                        var replace = edit.Value<string>("replace_text");
                        if (string.IsNullOrEmpty(replace)) continue;
                        var suggestion = edit.Value<string>("suggestion") ?? "";
                        var row = Sty.Row();
                        if (suggestion.Length > 0)
                        {
                            var sg = Sty.Text(suggestion, "text-xs", "fg-1");
                            sg.style.flexGrow = 1;
                            row.Add(sg);
                        }
                        var apply = Sty.Btn(Sty.Copy("doc.comment.apply"), true);
                        var capturedAnchor = Anchor;
                        var capturedEdit = edit;
                        apply.clicked += () => Emit("apply", new JObject
                        {
                            ["anchor"] = capturedAnchor,
                            ["replace_text"] = capturedEdit.Value<string>("replace_text") ?? "",
                            ["suggestion"] = capturedEdit.Value<string>("suggestion") ?? "",
                        });
                        row.Add(apply);
                        card.Add(row);
                    }
                }
            }

            // busy 骨架(思考中;reduced-motion 时这是纯文案,无动画)
            if (State.Value<bool?>("busy") == true)
                card.Add(Sty.Text(Sty.Copy("doc.comment.thinking"), "text-xs", "fg-2"));

            // 输入行
            var inputRow = Sty.Row();
            var draft = Sty.Input(Sty.Copy("doc.comment.placeholder"));
            draft.style.flexGrow = 1;
            draft.SetValueWithoutNotify(State.Value<string>("draft") ?? "");
            var busy = State.Value<bool?>("busy") == true;
            draft.RegisterValueChangedCallback<string>(evt => MutateState(s => s["draft"] = evt.newValue));
            inputRow.Add(draft);
            var send = Sty.Btn(Sty.Copy("doc.comment.send"), true);
            send.SetEnabled(!busy);
            send.clicked += () =>
            {
                var text = State.Value<string>("draft") ?? "";
                if (text.Trim().Length == 0) return;
                MutateState(s => s["draft"] = "");
                Emit("submit", new JObject { ["anchor"] = Anchor, ["text"] = text });
            };
            inputRow.Add(send);
            card.Add(inputRow);

            Root.Add(card);
        }

        /// <summary>widget 级 cascade fragment;段落/全文由父(app)经 ChildContext 注入(§7-2)。</summary>
        public override JObject ContextFragment()
        {
            return new JObject
            {
                ["kind"] = Kind,
                ["anchor"] = Anchor,
                ["quote"] = State.Value<string>("quote") ?? "",
            };
        }

        public override string Summary()
        {
            var n = (State["messages"] as JArray)?.Count ?? 0;
            return "「" + Anchor + "」批注," + n + " 条消息";
        }
    }
}

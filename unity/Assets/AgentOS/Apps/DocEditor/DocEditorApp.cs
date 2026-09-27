using System;
using System.Collections.Generic;
using AgentOS.UI.Kernel;
using Newtonsoft.Json.Linq;
using UnityEngine.UIElements;

namespace AgentOS.UI.Apps.DocEditor
{
    /// <summary>doc-editor 定义面(docs/COMPOUND-WIDGET.md C3:第一个产品级 compound)。</summary>
    public sealed class DocEditorDef : WidgetDef
    {
        readonly DocServices _services;
        public DocEditorDef(DocServices services) { _services = services; }
        public override string Kind { get { return "doc-editor"; } }
        public override Widget CreateInstance(JObject state) { return new DocEditorApp(this, state, _services); }
    }

    /// <summary>
    /// Doc Editor(docs/DOC-EDITOR.md):文档 = 预定义 md-viewer 子件,段落批注 =
    /// 动态 chat-bubble 子件;管控三通道全用(事件闸门接管 submit/apply/close,
    /// ChildContext 注入锚段/全文,气泡显隐由父管理)。
    /// 写动作(save/snapshot/rewind/apply)全走 action 管道;comment 走 D2 专属端点先例。
    /// state:{name,text,dirty,savedAt,view,versions,chat,bubbles}
    /// </summary>
    public sealed class DocEditorApp : CompoundWidget
    {
        readonly DocServices _svc;
        AppInstance _serverApp;   // 服务端 kind=doc 的 instance(action 管道的锚)
        MdViewerWidget _viewer;
        TextField _editor;
        int _editSeq;             // 预览防抖序号(避免依赖调度器取消 API)

        public string DocName { get { return State.Value<string>("name") ?? ""; } }

        public DocEditorApp(WidgetDef def, JObject state, DocServices services) : base(def, state)
        {
            _svc = services;
            if (!State.ContainsKey("view")) State["view"] = "split";
            if (!State.ContainsKey("dirty")) State["dirty"] = false;
            CreateSlots(services.Registry);
            _viewer = FindChild<MdViewerWidget>(null);
        }

        // ---------------------------------------------------------------- compound 协议面

        protected override IReadOnlyList<SlotSpec> Slots
        {
            get { return new[] { new SlotSpec { Id = "viewer", Kind = "md-viewer", State = new JObject() } }; }
        }

        protected override IReadOnlyList<string> DynamicAllow
        {
            get { return new[] { "chat-bubble" }; }
        }

        /// <summary>app 级 cascade provider(与 web doc-editor 的 _appProvider 同构)。</summary>
        public override JObject ContextFragment()
        {
            return new JObject
            {
                ["name"] = DocName,
                ["versions"] = State["versions"] != null ? State["versions"].DeepClone() : new JArray(),
                ["dirty"] = State.Value<bool?>("dirty") ?? false,
            };
        }

        /// <summary>信息管控(§7-2):气泡的 cascade fragment 注入锚段原文与全文。</summary>
        protected internal override JObject ChildContext(Widget child, JObject fragment)
        {
            var bubble = child as ChatBubbleWidget;
            if (bubble != null)
            {
                var text = State.Value<string>("text") ?? "";
                fragment["anchor"] = bubble.Anchor;
                fragment["paragraph"] = MdBlocks.BlockText(text, bubble.Anchor);
                fragment["full_text"] = text;
            }
            return fragment;
        }

        /// <summary>事件闸门:接管子件事件(放行上行,同时在此落地为动作)。</summary>
        protected override bool OnChildEvent(Widget child, string evtName, JObject payload)
        {
            if (child == _viewer && evtName == "open-bubble") { OpenBubble(payload.Value<string>("anchor") ?? ""); return true; }
            var bubble = child as ChatBubbleWidget;
            if (bubble != null && evtName == "submit") { HandleSubmit(bubble, payload.Value<string>("text") ?? ""); return true; }
            if (bubble != null && evtName == "apply") { HandleApply(bubble, payload); return true; }
            if (bubble != null && evtName == "close") { RemoveChild(child.Id, true); Render(); return true; }
            return true;
        }

        /// <summary>装配服务端 instance(开窗时 spawn kind=doc 的结果)。</summary>
        public void AttachServerApp(AppInstance serverApp) { _serverApp = serverApp; }

        // ---------------------------------------------------------------- 数据装载

        /// <summary>服务端文档 JSON → 本地 state(读面直给;写动作永远走管道)。</summary>
        public void ApplyDocJson(JObject doc, JArray bubbles)
        {
            MutateState(s =>
            {
                s["name"] = doc.Value<string>("name") ?? DocName;
                s["text"] = doc.Value<string>("text") ?? "";
                s["title"] = doc["meta"] != null ? doc["meta"].Value<string>("title") ?? "" : "";
                s["savedAt"] = doc["meta"] != null ? doc["meta"].Value<double?>("savedAt") ?? 0 : 0;
                s["versions"] = doc["versions"] as JArray ?? new JArray();
                s["chat"] = doc["chat"] as JArray ?? new JArray();
                s["bubbles"] = bubbles ?? new JArray();
                s["dirty"] = false;
            });
        }

        public async void ReloadDoc()
        {
            try
            {
                var doc = await _svc.Client.ReadDoc(DocName);
                var bubbles = await _svc.Client.ReadBubbles(DocName);
                ApplyDocJson(doc, bubbles);
                MergeOpenBubbleFlows();
                Render();
            }
            catch (Exception e) { Report("重载失败:" + e.Message, "danger"); }
        }

        // ---------------------------------------------------------------- 渲染

        public override void Render()
        {
            Root.Clear();
            Root.style.flexGrow = 1;
            SyncViewer();

            // ── 工具条 ──
            var bar = Sty.Row();
            bar.style.marginBottom = Sty.S("s1");
            var title = State.Value<string>("title");
            bar.Add(Sty.Text(string.IsNullOrEmpty(title) ? DocName : title, "text-lg", "fg-0", true));
            var gap0 = new VisualElement(); gap0.style.flexGrow = 1; bar.Add(gap0);

            var versions = State["versions"] as JArray ?? new JArray();
            var choices = new List<string>();
            foreach (var v in versions) choices.Add(v.Value<string>());
            DropdownField versionPick;
            if (choices.Count > 0) versionPick = new DropdownField(choices, 0);
            else { versionPick = new DropdownField(); versionPick.SetEnabled(false); }
            versionPick.style.minWidth = 90;
            bar.Add(versionPick);

            var save = Sty.Btn(Sty.Copy("doc.save"), true);
            save.clicked += DoSave;
            bar.Add(save);
            var snapshot = Sty.Btn(Sty.Copy("doc.snapshot"));
            snapshot.clicked += DoSnapshot;
            bar.Add(snapshot);
            var rewind = Sty.Btn(Sty.Copy("doc.rewind"));
            rewind.clicked += () => DoRewind(rewind, versionPick);
            bar.Add(rewind);
            var review = Sty.Btn(Sty.Copy("doc.review"));
            review.clicked += DoReview;
            bar.Add(review);
            var export = Sty.Btn(Sty.Copy("doc.export"));
            export.clicked += DoExport;
            bar.Add(export);

            foreach (var mode in new[] { "edit", "split", "preview" })
            {
                var m = mode;
                var b = Sty.Btn(Sty.Copy("doc.view." + m));
                if (State.Value<string>("view") == m) b.style.backgroundColor = Sty.C("bg-3");
                b.clicked += () => PatchState(s => s["view"] = m); // 视图切换 = 纯 state(本地)
                bar.Add(b);
            }
            Root.Add(bar);

            // ── 本体:编辑 | 预览 | 批注栏 ──
            var body = Sty.Row();
            body.style.alignItems = Align.Stretch;
            body.style.flexGrow = 1;
            var view = State.Value<string>("view") ?? "split";

            if (view != "preview")
            {
                _editor = new TextField { multiline = true };
                _editor.style.flexGrow = 1;
                _editor.style.fontSize = Sty.S("text-sm");
                _editor.style.backgroundColor = Sty.C("bg-0");
                _editor.style.color = Sty.C("fg-0");
                _editor.SetValueWithoutNotify(State.Value<string>("text") ?? "");
                _editor.RegisterValueChangedCallback<string>(evt =>
                {
                    MutateState(s => { s["text"] = evt.newValue; s["dirty"] = true; });
                    var seq = ++_editSeq;
                    _editor.schedule.Execute(() =>
                    {
                        if (seq == _editSeq) SyncViewer(); // 400ms 防抖:预览同源重渲(滚动各自,不同步)
                    }).StartingIn(400);
                });
                body.Add(_editor);
            }

            if (view != "edit")
            {
                var viewerScroll = new ScrollView();
                viewerScroll.style.flexGrow = 1;
                viewerScroll.Add(_viewer.Root);
                body.Add(viewerScroll);
            }

            var bubbleCol = new ScrollView();
            bubbleCol.style.minWidth = 260;
            bubbleCol.style.maxWidth = 320;
            foreach (var child in Children)
            {
                if (child is ChatBubbleWidget) { child.Render(); bubbleCol.Add(child.Root); }
            }
            body.Add(bubbleCol);
            Root.Add(body);

            // ── 主对话条(D5:doc 作用域对话;changed 时重拉重渲)──
            Root.Add(Sty.HLine());
            var chatLog = new ScrollView();
            chatLog.style.maxHeight = 120;
            var chat = State["chat"] as JArray ?? new JArray();
            foreach (var m in chat)
            {
                var msg = m as JObject;
                if (msg == null) continue;
                var isUser = msg.Value<string>("role") == "user";
                var line = Sty.Row();
                var who = Sty.Text(isUser ? "你" : "agent", "text-xs", isUser ? "live" : "ok", true);
                who.style.minWidth = 34;
                line.Add(who);
                var txt = Sty.Text(msg.Value<string>("text") ?? "", "text-sm", "fg-0");
                txt.selectionEnabled = true;
                txt.style.flexGrow = 1;
                line.Add(txt);
                chatLog.Add(line);
            }
            Root.Add(chatLog);

            var chatRow = Sty.Row();
            var chatInput = Sty.Input(Sty.Copy("doc.chat.placeholder"));
            chatInput.style.flexGrow = 1;
            chatRow.Add(chatInput);
            var chatSend = Sty.Btn(Sty.Copy("doc.chat.send"), true);
            chatSend.clicked += () => DoChat(chatInput.value, chatInput);
            chatRow.Add(chatSend);
            Root.Add(chatRow);

            // ── 状态栏 ──
            Root.Add(Sty.HLine());
            var status = Sty.Row();
            var text = State.Value<string>("text") ?? "";
            var dirty = State.Value<bool?>("dirty") ?? false;
            status.Add(Sty.Dot(dirty ? "warn" : "ok"));
            status.Add(Sty.Text(
                text.Length + " 字 · " + (dirty ? Sty.Copy("doc.status.dirty") : Sty.Copy("doc.status.saved"))
                + " · " + versions.Count + " 个版本",
                "text-xs", "fg-2"));
            Root.Add(status);
        }

        void SyncViewer()
        {
            if (_viewer == null) return;
            var counts = new JObject();
            var bubbles = State["bubbles"] as JArray ?? new JArray();
            foreach (var b in bubbles)
            {
                var rec = b as JObject;
                if (rec == null) continue;
                var anchor = rec.Value<string>("anchor") ?? "";
                var msgs = rec["messages"] as JArray;
                if (anchor.Length > 0) counts[anchor] = msgs != null && msgs.Count > 0 ? msgs.Count : 1;
            }
            _viewer.SetState(new JObject
            {
                ["source"] = State.Value<string>("text") ?? "",
                ["bubbleCounts"] = counts,
            });
        }

        /// <summary>已开气泡的消息流与服务端事实源合并(重载后调)。</summary>
        void MergeOpenBubbleFlows()
        {
            foreach (var child in Children)
            {
                var bubble = child as ChatBubbleWidget;
                if (bubble == null) continue;
                var rec = FindBubbleRecord(bubble.Anchor);
                if (rec != null)
                    bubble.MutateState(s => s["messages"] = rec["messages"] != null ? rec["messages"].DeepClone() : new JArray());
                bubble.Render();
            }
        }

        JObject FindBubbleRecord(string anchor)
        {
            var bubbles = State["bubbles"] as JArray ?? new JArray();
            foreach (var b in bubbles)
            {
                var rec = b as JObject;
                if (rec != null && rec.Value<string>("anchor") == anchor) return rec;
            }
            return null;
        }

        // ---------------------------------------------------------------- 气泡流

        void OpenBubble(string anchor)
        {
            if (anchor.Length == 0) return;
            var existing = FindChild<ChatBubbleWidget>(b => b.Anchor == anchor);
            if (existing != null) { FocusWidget(existing); return; } // 防重复(同锚点 early-return)
            var rec = FindBubbleRecord(anchor);
            var quote = MdBlocks.BlockText(State.Value<string>("text") ?? "", anchor);
            var bubble = (ChatBubbleWidget)_svc.Registry.Create("chat-bubble", new JObject
            {
                ["anchor"] = anchor,
                ["quote"] = quote,
                ["messages"] = rec != null && rec["messages"] != null ? rec["messages"].DeepClone() : new JArray(),
                ["busy"] = false,
                ["draft"] = "",
            });
            AddChild(bubble, null);
            Render();
            FocusWidget(bubble);
        }

        void FocusWidget(Widget w)
        {
            w.Root.ScrollIntoView();
            MotionPlayer.Play("focus-pulse", w.Root);
        }

        async void HandleSubmit(ChatBubbleWidget bubble, string text)
        {
            bubble.MutateState(s => s["busy"] = true);
            bubble.Render();
            try
            {
                var cascade = _svc.Tree.Cascade.Build(bubble); // §16 级联信封(widget→app→shell)
                var resp = await _svc.Client.SendComment(DocName, bubble.Anchor, text, cascade);
                var msgs = bubble.State["messages"] as JArray ?? new JArray();
                msgs.Add(new JObject { ["role"] = "user", ["text"] = text });
                var assistant = new JObject
                {
                    ["role"] = "assistant",
                    ["text"] = resp.Value<string>("reply") ?? "",
                };
                if (resp["edits"] is JArray edits && edits.Count > 0) assistant["edits"] = edits.DeepClone();
                msgs.Add(assistant);
                bubble.MutateState(s => { s["messages"] = msgs; s["busy"] = false; });
                MutateState(s =>
                {
                    var bubbles = s["bubbles"] as JArray;
                    if (bubbles == null) { bubbles = new JArray(); s["bubbles"] = bubbles; }
                    var rec = FindBubbleRecord(bubble.Anchor);
                    if (rec != null) rec["messages"] = msgs.DeepClone();
                    else bubbles.Add(new JObject // 新锚点首条落账,计数即时可见(服务端仍是事实源)
                    {
                        ["anchor"] = bubble.Anchor,
                        ["messages"] = msgs.DeepClone(),
                    });
                });
                SyncViewer();
            }
            catch (Exception e)
            {
                bubble.MutateState(s => s["busy"] = false);
                Report("评论助手暂不可用:" + e.Message, "danger"); // 与既有 503 归类同语义
            }
            bubble.Render();
        }

        async void HandleApply(ChatBubbleWidget bubble, JObject payload)
        {
            if (_serverApp == null) { Report("app instance 未装配,无法 apply", "danger"); return; }
            var anchor = payload.Value<string>("anchor") ?? "";
            var expected = MdBlocks.BlockText(State.Value<string>("text") ?? "", anchor); // 越界/已变校验面
            try
            {
                await _svc.Pipeline.InvokeAsync(_serverApp, "comment.apply", new JObject
                {
                    ["anchor"] = anchor,
                    ["replace_text"] = payload.Value<string>("replace_text") ?? "",
                    ["expected"] = expected,
                }, "tab", null, bubble);
                Report("已应用修改(" + anchor + ")", "ok");
                ReloadDoc();
            }
            catch (ApiException e)
            {
                Report(e.Status == 400 || e.Status == 409
                    ? "文档已变化,请重新评审(" + anchor + ")"
                    : "apply 失败:" + e.Message, "danger");
            }
            catch (Exception e) { Report("apply 失败:" + e.Message, "danger"); }
        }

        // ---------------------------------------------------------------- 工具条动作(全走管道)

        async void DoSave()
        {
            if (_serverApp == null) { Report("app instance 未装配,无法保存", "danger"); return; }
            try
            {
                await _svc.Pipeline.InvokeAsync(_serverApp, "doc.save", new JObject
                {
                    ["text"] = State.Value<string>("text") ?? "",
                }, "tab", null, this);
                MutateState(s => { s["dirty"] = false; s["savedAt"] = NowUnix(); });
                Report(Sty.Copy("doc.status.saved") + " · " + DocName, "ok");
                Render();
            }
            catch (Exception e) { Report("保存失败:" + e.Message, "danger"); }
        }

        async void DoSnapshot()
        {
            if (!CheckApp()) return;
            try
            {
                await _svc.Pipeline.InvokeAsync(_serverApp, "doc.snapshot", new JObject(), "tab", null, this);
                Report("快照完成", "ok");
                ReloadDoc();
            }
            catch (Exception e) { Report("快照失败:" + e.Message, "danger"); }
        }

        bool _rewindArmed;
        async void DoRewind(UnityEngine.UIElements.Button button, DropdownField pick)
        {
            if (!CheckApp()) return;
            var version = pick != null ? pick.value : null;
            if (string.IsNullOrEmpty(version)) { Report("先选版本", "danger"); return; }
            if (!_rewindArmed) // 两击确认(lab-iterate 同款 armed 范式;rewind 改工作副本)
            {
                _rewindArmed = true;
                button.text = Sty.Copy("doc.rewind.confirm");
                button.schedule.Execute(() => { _rewindArmed = false; button.text = Sty.Copy("doc.rewind"); }).StartingIn(2500);
                return;
            }
            _rewindArmed = false;
            button.text = Sty.Copy("doc.rewind");
            try
            {
                await _svc.Pipeline.InvokeAsync(_serverApp, "doc.rewind", new JObject
                {
                    ["version"] = version,
                }, "tab", null, this);
                Report("已回滚到 " + version + "(历史不动)", "ok");
                ReloadDoc();
            }
            catch (Exception e) { Report("回滚失败:" + e.Message, "danger"); }
        }

        async void DoReview()
        {
            try
            {
                Report("评审中…", "info");
                await _svc.Client.ReviewDoc(DocName);
                var bubbles = await _svc.Client.ReadBubbles(DocName);
                MutateState(s => s["bubbles"] = bubbles);
                MergeOpenBubbleFlows();
                Render();
                Report("评审完成:批注已挂到各段", "ok");
            }
            catch (Exception e) { Report("评审助手暂不可用:" + e.Message, "danger"); }
        }

        void DoExport()
        {
            var text = State.Value<string>("text") ?? "";
            _svc.Sink.CopyText(text);
            Report("已复制全文(" + text.Length + " 字)", "ok");
        }

        async void DoChat(string text, TextField input)
        {
            if (string.IsNullOrWhiteSpace(text)) return;
            input.SetValueWithoutNotify("");
            try
            {
                var resp = await _svc.Client.SendChat(DocName, text);
                var chat = State["chat"] as JArray ?? new JArray();
                chat.Add(new JObject { ["role"] = "user", ["text"] = text });
                chat.Add(new JObject { ["role"] = "assistant", ["text"] = resp.Value<string>("reply") ?? "" });
                MutateState(s => s["chat"] = chat);
                if (resp.Value<bool?>("changed") == true) // changed = 服务端全文对比,不信自报
                {
                    Report("文档已更新", "ok");
                    ReloadDoc();
                    return;
                }
                Render();
            }
            catch (Exception e) { Report("编辑助手暂不可用:" + e.Message, "danger"); }
        }

        bool CheckApp()
        {
            if (_serverApp != null) return true;
            Report("app instance 未装配(spawn 失败?)", "danger");
            return false;
        }

        void Report(string msg, string tone) { _svc.Report(msg, tone); }

        static double NowUnix()
        {
            return (DateTimeOffset.UtcNow - DateTimeOffset.UnixEpoch).TotalSeconds;
        }

        public override string Summary()
        {
            return "文档 " + DocName + "(" + (State.Value<string>("text") ?? "").Length + " 字)";
        }
    }
}

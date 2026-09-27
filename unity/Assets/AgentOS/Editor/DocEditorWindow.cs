using System;
using System.Collections.Generic;
using AgentOS.UI.Apps.DocEditor;
using AgentOS.UI.Kernel;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace AgentOS.UI.EditorHost
{
    /// <summary>Editor 宿主的导出面:系统剪贴板 + 保存对话框。</summary>
    public sealed class EditorExportSink : IExportSink
    {
        public void CopyText(string text) { GUIUtility.systemCopyBuffer = text ?? ""; }

        public void SaveFile(string suggestedName, string text)
        {
            var path = EditorUtility.SaveFilePanel("导出文档", "", suggestedName, "md");
            if (path.Length > 0) System.IO.File.WriteAllText(path, text ?? "");
        }
    }

    /// <summary>
    /// Doc Editor 的 Editor 宿主窗口(菜单:Agent OS ▸ Doc Editor)。
    /// 装配职责(docs/APP-MODEL.md §17.10 框架侧):建内核(注册表/树/管道/主题)、
    /// 挂 app 子件、事件 → 动作映射之外的宿主义务(连接、状态栏、主题切换)。
    /// </summary>
    public sealed class DocEditorWindow : EditorWindow
    {
        const string ServerPrefKey = "agent-os.unity.server";

        AgentOsClient _client;
        ActionPipeline _pipeline;
        WidgetRegistry _registry;
        WidgetTree _tree;
        DocServices _services;

        DocListWidget _docList;
        DocEditorApp _docApp;
        TextField _serverField;
        Label _statusLabel;
        VisualElement _statusDot;
        VisualElement _mainHost;
        Button _themeButton;

        [MenuItem("Agent OS/Doc Editor")]
        public static void Open()
        {
            var w = GetWindow<DocEditorWindow>();
            w.titleContent = new GUIContent("Agent OS · Doc Editor");
            w.minSize = new Vector2(760, 420);
            w.Show();
        }

        public void CreateGUI()
        {
            BuildKernel();
            BuildChrome();
            ThemeRegistry.ThemeChanged -= OnThemeChanged; // 防域重载后重复订阅
            ThemeRegistry.ThemeChanged += OnThemeChanged;
            LoadDocList();
        }

        void BuildKernel()
        {
            // 主题:契约校验不过不注册(防半成品);恢复上次选择
            ThemeRegistry.Register(BuiltInThemes.Classic(), out _);
            ThemeRegistry.Register(BuiltInThemes.Pixel(), out _);
            ThemeRegistry.RestoreSaved();

            _registry = new WidgetRegistry();
            _tree = new WidgetTree();
            _client = new AgentOsClient
            {
                BaseUrl = PlayerPrefs.GetString(ServerPrefKey, "http://127.0.0.1:8391"),
            };
            _pipeline = new ActionPipeline(_client, _tree);
            _services = new DocServices
            {
                Client = _client,
                Pipeline = _pipeline,
                Registry = _registry,
                Tree = _tree,
                Sink = new EditorExportSink(),
                Report = SetStatus,
            };

            _registry.Register(new DocListDef());
            _registry.Register(new MdViewerDef());
            _registry.Register(new ChatBubbleDef());
            _registry.Register(new DocEditorDef(_services));

            _docList = (DocListWidget)_registry.Create("doc-list", new JObject
            {
                ["items"] = new JArray(), ["filter"] = "", ["selected"] = "",
            });
            _docList.EventEmitted += OnDocListEvent;
        }

        void BuildChrome()
        {
            rootVisualElement.Clear();
            rootVisualElement.style.backgroundColor = Sty.C("bg-0");
            Sty.Pad(rootVisualElement, Sty.S("s2"));

            // 顶栏:server · 连接状态 · 主题切换(托盘语义)
            var top = Sty.Row();
            top.style.marginBottom = Sty.S("s2");
            top.Add(Sty.Text("Agent OS", "text-lg", "fg-0", true));
            _serverField = Sty.Input("http://127.0.0.1:8391");
            _serverField.style.width = 230;
            _serverField.SetValueWithoutNotify(_client.BaseUrl);
            _serverField.RegisterValueChangedCallback<string>(evt =>
            {
                _client.BaseUrl = evt.newValue.TrimEnd('/');
                PlayerPrefs.SetString(ServerPrefKey, _client.BaseUrl);
            });
            top.Add(_serverField);
            var refresh = Sty.Btn("刷新列表");
            refresh.clicked += LoadDocList;
            top.Add(refresh);
            _statusDot = Sty.Dot("fg-2", 10);
            _statusDot.tooltip = "后端连接状态";
            top.Add(_statusDot);
            var spacer = new VisualElement(); spacer.style.flexGrow = 1; top.Add(spacer);
            _themeButton = Sty.Btn("主题:" + ThemeRegistry.Current.Name);
            _themeButton.clicked += () =>
            {
                ThemeRegistry.Cycle(); // 顺序循环,与 web 托盘同语义
            };
            top.Add(_themeButton);
            rootVisualElement.Add(top);

            // 主体:左列表 + 右 app 面
            var body = Sty.Row();
            body.style.alignItems = Align.Stretch;
            body.style.flexGrow = 1;
            var left = new VisualElement();
            left.style.width = 240;
            left.style.marginRight = Sty.S("s2");
            left.Add(_docList.Root);
            _docList.Render();
            body.Add(left);
            _mainHost = new VisualElement();
            _mainHost.style.flexGrow = 1;
            _mainHost.Add(Sty.Text("← 选一篇文档,或新建", "text-sm", "fg-2"));
            body.Add(_mainHost);
            rootVisualElement.Add(body);

            // 状态栏
            _statusLabel = Sty.Text("", "text-xs", "fg-2");
            _statusLabel.style.marginTop = Sty.S("s1");
            rootVisualElement.Add(_statusLabel);
        }

        void OnThemeChanged()
        {
            _themeButton.text = "主题:" + ThemeRegistry.Current.Name;
            // 主题 = 数据包,组件零分支:全量重渲即换肤(state 在 widget,不丢)
            BuildChrome();
            LoadDocList();
            if (_docApp != null)
            {
                _mainHost.Clear();
                _docApp.Render();
                _mainHost.Add(_docApp.Root);
            }
        }

        void OnDisable()
        {
            ThemeRegistry.ThemeChanged -= OnThemeChanged;
        }

        // ---------------------------------------------------------------- 文档流

        async void LoadDocList()
        {
            try
            {
                var items = await _client.ListDocs();
                _docList.MutateState(s => s["items"] = items);
                _docList.Render();
                SetConn(true);
            }
            catch (Exception e)
            {
                SetConn(false);
                SetStatus("连不上后端(" + _client.BaseUrl + "):" + e.Message + " —— 先用 instance/run-web.sh 起服务", "danger");
            }
        }

        void OnDocListEvent(Widget w, string evtName, JObject payload)
        {
            if (evtName == "select") OpenDoc(payload.Value<string>("name") ?? "");
            else if (evtName == "create") CreateDoc(payload.Value<string>("name") ?? "");
        }

        async void CreateDoc(string name)
        {
            try
            {
                await _client.CreateDoc(name, "", "");
                SetStatus("已创建 " + name, "ok");
                LoadDocList();
                OpenDoc(name);
            }
            catch (ApiException e)
            {
                SetStatus(e.Status == 409 ? "重名:" + name : "创建失败:" + e.Message, "danger");
            }
            catch (Exception e) { SetStatus("创建失败:" + e.Message, "danger"); }
        }

        async void OpenDoc(string name)
        {
            if (name.Length == 0) return;
            try
            {
                SetStatus("打开 " + name + " …", "info");
                var doc = await _client.ReadDoc(name);
                var bubbles = await _client.ReadBubbles(name);
                // spawn = app 管道锚(kind+ref 去重;state 过服务端 schema 闸)
                var serverApp = await _client.Spawn("doc", name, name,
                    new JObject { ["name"] = name, ["dirty"] = false, ["view"] = "split" }, "unity-editor");

                // 旧 app 离树(关闭 = remove_child;重开是全新 instance,DESKTOP §7 语义)
                if (_docApp != null) _tree.Root.RemoveChild(_docApp.Id, true);

                _docApp = (DocEditorApp)_registry.Create("doc-editor", new JObject { ["name"] = name });
                _docApp.AttachServerApp(serverApp);
                _docApp.ApplyDocJson(doc, bubbles);
                _tree.Root.AddChild(_docApp, "doc-" + name);
                _docApp.Render();

                _mainHost.Clear();
                _mainHost.Add(_docApp.Root);
                SetStatus("已打开 " + name + "(instance " + serverApp.Id + ")", "ok");
            }
            catch (Exception e) { SetStatus("打开失败:" + e.Message, "danger"); }
        }

        // ---------------------------------------------------------------- 状态面

        void SetConn(bool ok)
        {
            if (_statusDot == null) return;
            _statusDot.style.backgroundColor = Sty.C(ok ? "live" : "warn"); // 色点 + tooltip 文字 = 双编码
            _statusDot.tooltip = ok ? "已连接" : "连接失败";
        }

        void SetStatus(string msg, string tone)
        {
            if (_statusLabel == null) return;
            _statusLabel.text = msg;
            _statusLabel.style.color = Sty.C(tone == "danger" ? "danger" : tone == "ok" ? "ok" : "fg-2");
        }
    }
}

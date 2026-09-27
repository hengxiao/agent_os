using AgentOS.UI.Kernel;
using Newtonsoft.Json.Linq;
using UnityEngine.UIElements;

namespace AgentOS.UI.Apps.DocEditor
{
    /// <summary>doc-list 定义面(W-list 语义:过滤/选择/激活)。</summary>
    public sealed class DocListDef : WidgetDef
    {
        public override string Kind { get { return "doc-list"; } }
        public override System.Collections.Generic.IReadOnlyList<string> Events
        {
            get { return new[] { "select", "create" }; }
        }
        public override Widget CreateInstance(JObject state) { return new DocListWidget(this, state); }
    }

    /// <summary>
    /// 文档列表(Card Surface 的数据源面):搜索过滤 + 选择 + 新建入口。
    /// state:{items:[{name,title,first_line,chars,savedAt}], filter, selected, creating, draft}
    /// 渲染分两档:Render() 重建 chrome;RebuildItems() 只重建列表项(过滤输入不丢焦点)。
    /// </summary>
    public sealed class DocListWidget : Widget
    {
        ScrollView _listHost;

        public DocListWidget(WidgetDef def, JObject state) : base(def, state) { }

        public override void Render()
        {
            Root.Clear();
            Root.style.flexGrow = 1;

            var filter = Sty.Input("过滤…");
            filter.SetValueWithoutNotify(State.Value<string>("filter") ?? "");
            filter.RegisterValueChangedCallback<string>(evt =>
            {
                MutateState(s => s["filter"] = evt.newValue);
                RebuildItems(); // 只重渲列表区,过滤框焦点不动
            });
            Root.Add(filter);

            var creating = State.Value<bool?>("creating") == true;
            if (creating)
            {
                var row = Sty.Row();
                var nameInput = Sty.Input("文档名(点分,如 design.new-ui)");
                nameInput.style.flexGrow = 1;
                nameInput.SetValueWithoutNotify(State.Value<string>("draft") ?? "");
                nameInput.RegisterValueChangedCallback<string>(evt => MutateState(s => s["draft"] = evt.newValue));
                row.Add(nameInput);
                var create = Sty.Btn(Sty.Copy("doc.create"), true);
                create.clicked += () =>
                {
                    var name = (State.Value<string>("draft") ?? "").Trim();
                    if (name.Length == 0) return;
                    MutateState(s => { s["creating"] = false; s["draft"] = ""; });
                    Emit("create", new JObject { ["name"] = name });
                };
                row.Add(create);
                Root.Add(row);
            }
            else
            {
                var add = Sty.Btn(Sty.Copy("doc.new"));
                add.clicked += () => PatchState(s => s["creating"] = true);
                Root.Add(add);
            }
            Root.Add(Sty.HLine());

            _listHost = new ScrollView();
            _listHost.style.flexGrow = 1;
            Root.Add(_listHost);
            RebuildItems();
        }

        void RebuildItems()
        {
            if (_listHost == null) return;
            _listHost.Clear();
            var items = State["items"] as JArray ?? new JArray();
            var filterText = (State.Value<string>("filter") ?? "").ToLowerInvariant();
            var shown = 0;
            foreach (var it in items)
            {
                var item = it as JObject;
                if (item == null) continue;
                var name = item.Value<string>("name") ?? "";
                if (filterText.Length > 0 && !name.ToLowerInvariant().Contains(filterText)) continue;
                shown++;
                var row = Sty.Panel(State.Value<string>("selected") == name ? "bg-3" : "bg-1");
                row.style.marginBottom = Sty.S("s1");
                row.RegisterCallback<ClickEvent>(_ =>
                {
                    MutateState(s => s["selected"] = name);
                    RebuildItems();
                    Emit("select", new JObject { ["name"] = name });
                });
                row.Add(Sty.Text(item.Value<string>("title") ?? name, "text-sm", "fg-0", true));
                var firstLine = item.Value<string>("first_line") ?? "";
                if (firstLine.Length > 0) row.Add(Sty.Text(firstLine, "text-xs", "fg-2"));
                var meta = Sty.Row();
                meta.Add(Sty.Text((item.Value<int?>("chars") ?? 0) + " 字", "text-xs", "fg-2"));
                var savedAt = item.Value<double?>("savedAt") ?? 0;
                if (savedAt > 0) meta.Add(Sty.Text(" · " + RelTime(savedAt), "text-xs", "fg-2"));
                row.Add(meta);
                _listHost.Add(row);
            }
            if (shown == 0)
                _listHost.Add(Sty.Text(Sty.Copy("doc.list.empty"), "text-sm", "fg-2"));
        }

        static string RelTime(double epochSec)
        {
            var span = System.DateTimeOffset.Now - System.DateTimeOffset.FromUnixTimeSeconds((long)epochSec);
            if (span.TotalMinutes < 1) return "刚刚";
            if (span.TotalHours < 1) return (int)span.TotalMinutes + " 分钟前";
            if (span.TotalDays < 1) return (int)span.TotalHours + " 小时前";
            return (int)span.TotalDays + " 天前";
        }

        public override string Summary()
        {
            var items = State["items"] as JArray;
            return "文档列表(" + (items != null ? items.Count : 0) + " 篇)";
        }
    }
}

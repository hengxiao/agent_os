using Newtonsoft.Json.Linq;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// Context Cascade(docs/APP-MODEL.md §16):动作触发时从触发 widget 沿 ownership 树
    /// 向上逐级收集 fragment,组成级联信封。纪律:
    /// 每级只贡献自己的 fragment;级联单向向上(祖先链),不横向打听——上下文边界 = 树边界;
    /// 父可经 ChildContext 改写子的 fragment(信息管控,COMPOUND §7 通道 2)。
    /// scope 规则:root = "shell";root 的直接子 = "app";中间层 = "section";触发者 = "widget"。
    /// </summary>
    public sealed class ContextCascade
    {
        readonly WidgetTree _tree;

        public ContextCascade(WidgetTree tree) { _tree = tree; }

        public JArray Build(Widget trigger)
        {
            var entries = new JArray();
            if (trigger == null) return entries;
            entries.Add(Entry("widget", trigger));
            var owner = trigger.Owner;
            while (owner != null)
            {
                entries.Add(Entry(ScopeOf(owner), owner));
                owner = owner.Owner;
            }
            return entries;
        }

        string ScopeOf(Widget w)
        {
            if (ReferenceEquals(w, _tree.Root)) return "shell";
            if (w.Owner != null && ReferenceEquals(w.Owner, _tree.Root)) return "app";
            return "section";
        }

        static JObject Entry(string scope, Widget w)
        {
            var frag = w.ContextFragment();
            if (w.Owner != null) frag = w.Owner.ChildContext(w, frag); // 信息管控:父改写子对外提供的信息
            return new JObject
            {
                ["scope"] = scope,
                ["path"] = w.Path,
                ["data"] = frag,
            };
        }
    }
}

using System;
using Newtonsoft.Json.Linq;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// 根 compound(docs/COMPOUND-WIDGET.md §8 / DESKTOP-WIDGET.md §2):
    /// path = "/root",全树寻址从这里开始。v1 的 root 只做寻址/级联,不渲染 chrome
    /// (窗口 chrome 是宿主窗口的职责;desktop 的壁纸/图标/任务栏属后续 3D 化的桌面 app)。
    /// </summary>
    public sealed class DesktopRootDef : WidgetDef
    {
        public override string Kind { get { return "desktop"; } }
        public override Widget CreateInstance(JObject state) { return new DesktopRoot(this, state); }
    }

    public sealed class DesktopRoot : CompoundWidget
    {
        public DesktopRoot(WidgetDef def, JObject state) : base(def, state)
        {
            PathSegment = "root";
            Path = "/root";
        }

        protected override IReadOnlyList<string> DynamicAllow
        {
            get { return null; } // 根的子件由宿主显式挂(窗口期不做白名单闸)
        }

        /// <summary>shell 级 fragment(C4.4 child_context 同构:user/theme/at)。</summary>
        public override JObject ContextFragment()
        {
            return new JObject
            {
                ["user"] = "unity-editor",
                ["theme"] = ThemeRegistry.Current != null ? ThemeRegistry.Current.Id : "",
                ["at"] = DateTimeOffset.Now.ToString("o"),
            };
        }

        public override string Summary() { return "Agent OS 桌面根(Unity)"; }

        public override void Render() { /* root 不渲染 chrome;子件由宿主窗口布局 */ }
    }
}

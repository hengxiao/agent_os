using System.Text;
using Newtonsoft.Json.Linq;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// Widget 树 + 寻址(docs/APP-MODEL.md §14):路径 = ownership 链,与视图无关;
    /// agent 三动词里 read/focus 全开放,act 的代理收口留待后续(§14.4,本期不对 agent 开放)。
    /// </summary>
    public sealed class WidgetTree
    {
        public DesktopRoot Root { get; private set; }
        public ContextCascade Cascade { get; private set; }

        public WidgetTree()
        {
            Root = (DesktopRoot)new DesktopRootDef().CreateInstance(new JObject());
            Cascade = new ContextCascade(this);
        }

        /// <summary>路径解析(查树不查视图):/root/seg/seg…;不存在返回 null。</summary>
        public Widget Resolve(string path)
        {
            if (string.IsNullOrEmpty(path)) return null;
            var segs = path.Split('/');
            var i = 0;
            if (segs.Length > 0 && segs[0].Length == 0) i = 1; // 前导斜杠
            if (i >= segs.Length || segs[i] != "root") return null;
            Widget cur = Root;
            i++;
            for (; i < segs.Length; i++)
            {
                var c = cur as CompoundWidget;
                if (c == null) return null;
                cur = c.FindChild(segs[i]);
                if (cur == null) return null;
            }
            return cur;
        }

        /// <summary>read 动词(§14.3):人话摘要(禁忌词纪律由 widget 摘要层保证)。</summary>
        public string Read(string path)
        {
            var w = Resolve(path);
            return w == null ? null : w.Summary();
        }

        /// <summary>focus 动词(§14.3):滚动到视图 + 高亮脉冲(reduced-motion 时即时切换)。</summary>
        public bool Focus(string path)
        {
            var w = Resolve(path);
            if (w == null) return false;
            w.Root.ScrollIntoView();
            MotionPlayer.Play("focus-pulse", w.Root);
            return true;
        }

        /// <summary>调试用:整树路径清单。</summary>
        public string DumpPaths()
        {
            var sb = new StringBuilder();
            DumpRec(Root, sb);
            return sb.ToString();
        }

        static void DumpRec(Widget w, StringBuilder sb)
        {
            sb.AppendLine(w.Path + "  [" + w.Kind + "]");
            var c = w as CompoundWidget;
            if (c != null) foreach (var ch in c.Children) DumpRec(ch, sb);
        }
    }
}

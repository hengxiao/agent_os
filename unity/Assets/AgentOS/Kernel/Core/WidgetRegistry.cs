using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// Widget 注册表(docs/WIDGETS.md §1.2 / widgets/registry.js):注册即校验,
    /// 不合规拒注册(抛异常,与"拒注册"同语义——注册面是代码评审面)。
    /// </summary>
    public sealed class WidgetRegistry
    {
        static readonly HashSet<string> KnownSurfaces = new HashSet<string> { "card", "tab" };

        readonly Dictionary<string, WidgetDef> _defs = new Dictionary<string, WidgetDef>();

        public void Register(WidgetDef def)
        {
            if (def == null) throw new ArgumentNullException("def");
            if (string.IsNullOrEmpty(def.Kind)) throw new InvalidOperationException("widget def 缺 kind,拒注册");
            if (def.V < 1) throw new InvalidOperationException(def.Kind + ": v 必须 >= 1");
            var actionIds = new HashSet<string>();
            if (def.Actions != null)
            {
                foreach (var a in def.Actions)
                {
                    if (a == null || string.IsNullOrEmpty(a.Id))
                        throw new InvalidOperationException(def.Kind + ": action 缺 id,拒注册");
                    if (!actionIds.Add(a.Id))
                        throw new InvalidOperationException(def.Kind + ": action id 重复 " + a.Id);
                    CheckSurfaces(def.Kind, a.Surfaces);
                }
            }
            if (def.Events != null)
                foreach (var e in def.Events)
                    if (string.IsNullOrEmpty(e))
                        throw new InvalidOperationException(def.Kind + ": events 含空项,拒注册");
            CheckSurfaces(def.Kind, def.Surfaces);
            _defs[def.Kind] = def;
        }

        static void CheckSurfaces(string kind, IEnumerable<string> surfaces)
        {
            if (surfaces == null) throw new InvalidOperationException(kind + ": surfaces 为 null");
            foreach (var s in surfaces)
                if (!KnownSurfaces.Contains(s))
                    throw new InvalidOperationException(kind + ": surface " + s + " 越界(只允许 card/tab)");
        }

        public WidgetDef Get(string kind)
        {
            WidgetDef def;
            return kind != null && _defs.TryGetValue(kind, out def) ? def : null;
        }

        /// <summary>无 def 的 kind 拒绝创建(不炸调用方的方式 = 显式异常,与"拒绝渲染"对齐)。</summary>
        public Widget Create(string kind, JObject state)
        {
            var def = Get(kind);
            if (def == null) throw new InvalidOperationException("未注册的 widget kind: " + kind);
            return def.CreateInstance(state);
        }

        public IEnumerable<string> Kinds { get { return _defs.Keys; } }
    }
}

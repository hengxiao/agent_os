using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentOS.UI.Kernel
{
    /// <summary>预定义子件槽位(COMPOUND-WIDGET.md §2 compound.slots)。</summary>
    public sealed class SlotSpec
    {
        public string Id;
        public string Kind;
        public JObject State;
        public string Surface = "tab";
    }

    /// <summary>
    /// 复合 widget 基座(docs/COMPOUND-WIDGET.md):拥有子 widget,定义组合、事件闸门、
    /// context 改写、动态生灭。ownership 唯一;寻址路径随挂载重算(reparent 安全)。
    /// v1 留口:多 view(hard link)与跨父转移(move_child)未实现,见 unity/README.md。
    /// </summary>
    public abstract class CompoundWidget : Widget
    {
        readonly List<Widget> _children = new List<Widget>();
        public IReadOnlyList<Widget> Children { get { return _children; } }

        protected CompoundWidget(WidgetDef def, JObject state) : base(def, state) { }

        /// <summary>预定义子件清单(静态,随实例创建)。</summary>
        protected virtual IReadOnlyList<SlotSpec> Slots { get { return null; } }
        /// <summary>动态子件 kind 白名单(null = 不许动态)。</summary>
        protected virtual IReadOnlyList<string> DynamicAllow { get { return null; } }
        protected virtual int DynamicMax { get { return 50; } }

        /// <summary>事件闸门(§7 通道 1):false 吞掉;true 放行(实现内可改写 payload)。</summary>
        protected virtual bool OnChildEvent(Widget child, string evtName, JObject payload) { return true; }
        /// <summary>信息管控(§7 通道 2):cascade 收集子 fragment 时经父改写(过滤/补充)。</summary>
        protected internal virtual JObject ChildContext(Widget child, JObject fragment) { return fragment; }

        /// <summary>闸门放行后的上行事件(父/app 层订阅,做事件 → action 的映射)。</summary>
        public event Action<Widget, string, JObject> ChildEvent;

        /// <summary>按 Slots 创建预定义子件(mount 期,kind 惰性校验在 registry)。</summary>
        public void CreateSlots(WidgetRegistry registry)
        {
            if (Slots == null) return;
            foreach (var slot in Slots)
            {
                var child = registry.Create(slot.Kind, slot.State);
                child.Surface = slot.Surface;
                AddChild(child, slot.Id);
            }
        }

        /// <summary>挂载子件(ownership 唯一 + 祖先链防环,§10)。slotId 即路径段。</summary>
        public T AddChild<T>(T child, string slotId) where T : Widget
        {
            if (child == null) throw new ArgumentNullException("child");
            if (child.Owner != null) throw new InvalidOperationException("ownership 唯一:子件已有 owner(" + child.Owner.Path + ")");
            for (var p = this; p != null; p = p.Owner)
                if (ReferenceEquals(p, child)) throw new InvalidOperationException("ownership 是树,不允许成环");
            if (slotId == null && DynamicAllow != null)
            {
                var allowed = false;
                foreach (var k in DynamicAllow) if (k == child.Kind) { allowed = true; break; }
                if (!allowed) throw new InvalidOperationException("kind " + child.Kind + " 不在 " + Kind + " 的 dynamic.allow 白名单");
                if (CountDynamic() >= DynamicMax) throw new InvalidOperationException(Kind + " 动态子件超上限 " + DynamicMax);
            }
            child.Owner = this;
            child.PathSegment = slotId ?? child.Id;
            _children.Add(child);
            Repath(child);
            return child;
        }

        int CountDynamic()
        {
            var slotIds = new HashSet<string>();
            if (Slots != null) foreach (var s in Slots) slotIds.Add(s.Id);
            var n = 0;
            foreach (var c in _children) if (!slotIds.Contains(c.PathSegment)) n++; // 预定义不占动态额
            return n;
        }

        /// <summary>移除子件。destroy=false = detach(instance 活着,可被别家 attach,§4)。</summary>
        public Widget RemoveChild(string id, bool destroy)
        {
            for (var i = 0; i < _children.Count; i++)
            {
                if (_children[i].Id == id || _children[i].PathSegment == id)
                {
                    var child = _children[i];
                    _children.RemoveAt(i);
                    ClearBadge(child);
                    child.Owner = null;
                    if (destroy) DestroyRec(child);
                    return child;
                }
            }
            return null;
        }

        public Widget FindChild(string segment)
        {
            foreach (var c in _children) if (c.PathSegment == segment || c.Id == segment) return c;
            return null;
        }

        public T FindChild<T>(Func<T, bool> pred) where T : Widget
        {
            foreach (var c in _children) { var t = c as T; if (t != null && (pred == null || pred(t))) return t; }
            return null;
        }

        static void DestroyRec(Widget w)
        {
            var c = w as CompoundWidget;
            if (c != null) foreach (var ch in new List<Widget>(c._children)) { DestroyRec(ch); }
            w.Destroy();
        }

        /// <summary>路径随 ownership 链重算,后代级联(C4.4 _repathSubtree 同语义)。</summary>
        internal static void Repath(Widget w)
        {
            w.Path = w.Owner == null ? w.PathSegment : w.Owner.Path + "/" + w.PathSegment;
            var c = w as CompoundWidget;
            if (c != null) foreach (var ch in c.Children) Repath(ch);
        }

        /// <summary>
        /// 事件闸门入口(子 Emit 的必经路):闸门 → badge 记账 → 订阅者 → 继续上行。
        /// badge 记账(COMPOUND §7 补丁):放行负载带 badge 字段(number 记 / 0·null 摘),
        /// 是父对子事件的记账,不是父偷读子 state。
        /// </summary>
        internal void DispatchChildEvent(Widget child, string evtName, JObject payload)
        {
            if (!OnChildEvent(child, evtName, payload)) return; // 吞掉
            var badge = payload != null ? payload["badge"] : null;
            if (badge != null)
            {
                var badges = State["badges"] as JObject;
                if (badges == null) { badges = new JObject(); State["badges"] = badges; }
                if (badge.Type == JTokenType.Null) badges.Remove(child.Id);
                else if (badge.Type == JTokenType.Integer && badge.Value<int>() == 0) badges.Remove(child.Id);
                else badges[child.Id] = badge.DeepClone();
            }
            child.RaiseDirect(evtName, payload);
            var h = ChildEvent;
            if (h != null) h(child, evtName, payload);
            if (Owner != null) Owner.DispatchChildEvent(child, evtName, payload); // 沿树继续上行
        }

        void ClearBadge(Widget child)
        {
            var badges = State["badges"] as JObject;
            if (badges != null && badges.Remove(child.Id)) { /* 离树清讫 */ }
        }
    }
}

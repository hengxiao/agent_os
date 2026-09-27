using System;
using Newtonsoft.Json.Linq;
using UnityEngine.UIElements;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// Widget 实例基类(docs/WIDGETS.md §1.2/§1.3 三条铁律):
    /// 有状态(State 可序列化)、发事件(Emit 上行,永不直接调后端)、四态齐备由主题层保证。
    /// Render() 是 state → 视图的纯渲染:先清后建,可从任意 state 重建。
    /// </summary>
    public abstract class Widget
    {
        static int _seq;

        public WidgetDef Def { get; private set; }
        public string Kind { get { return Def.Kind; } }
        public string Id { get; private set; }
        public JObject State { get; private set; }
        public string Path { get; internal set; }        // /root/... 全树唯一(docs/APP-MODEL.md §14)
        public string PathSegment { get; internal set; } // owner 路径下的段(slot id 或实例 id)
        public string Surface { get; internal set; }     // "card" | "tab"(当前面孔)
        public CompoundWidget Owner { get; internal set; }
        public VisualElement Root { get; private set; }

        /// <summary>事件上行口(经 owner 闸门;未声明事件不发)。</summary>
        public event Action<Widget, string, JObject> EventEmitted;

        protected Widget(WidgetDef def, JObject state)
        {
            Def = def;
            Id = def.Kind + "-" + (++_seq).ToString("x4");
            PathSegment = Id;
            State = state != null ? (JObject)state.DeepClone() : new JObject();
            Surface = "tab";
            Root = new VisualElement { name = def.Kind + "#" + Id };
        }

        /// <summary>整态替换 + 重渲。</summary>
        public void SetState(JObject next)
        {
            State = next != null ? (JObject)next.DeepClone() : new JObject();
            Render();
        }

        /// <summary>就地改 state + 重渲。</summary>
        public void PatchState(Action<JObject> mutate)
        {
            if (mutate != null) mutate(State);
            Render();
        }

        /// <summary>静默改 state 不重渲(文本输入等高频路径用,防重建丢焦点)。</summary>
        public void MutateState(Action<JObject> mutate)
        {
            if (mutate != null) mutate(State);
        }

        /// <summary>
        /// 事件上行:校验已声明(未声明事件不发,对齐 widgets/registry.js 拒注册语义),
        /// 经 owner 事件闸门(COMPOUND-WIDGET.md §7 通道 1),放行后才到订阅者。
        /// </summary>
        protected void Emit(string evtName, JObject payload)
        {
            var declared = false;
            foreach (var e in Def.Events) if (e == evtName) { declared = true; break; }
            if (!declared) throw new InvalidOperationException(Kind + " 未声明事件: " + evtName);
            var body = payload ?? new JObject();
            if (Owner != null) Owner.DispatchChildEvent(this, evtName, body);
            else EventEmitted?.Invoke(this, evtName, body);
        }

        internal void RaiseDirect(string evtName, JObject payload)
        {
            var h = EventEmitted;
            if (h != null) h(this, evtName, payload);
        }

        /// <summary>state → Root 的渲染。约定:先 Clear 再重建,幂等可重入。</summary>
        public abstract void Render();

        /// <summary>
        /// context_provider(docs/APP-MODEL.md §16;WIDGETS.md 协议面):本 widget 贡献给
        /// cascade 的 fragment。缺省 = kind + 人话摘要;声明者覆盖。父可经 ChildContext 改写。
        /// </summary>
        public virtual JObject ContextFragment()
        {
            return new JObject { ["kind"] = Kind, ["summary"] = Summary() };
        }

        /// <summary>read 动词的人话摘要(卡面禁忌词纪律由具体 widget 自律)。</summary>
        public virtual string Summary() { return Kind; }

        public virtual void Destroy() { }
    }
}

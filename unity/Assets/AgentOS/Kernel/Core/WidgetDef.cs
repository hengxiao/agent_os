using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// action 的执行通道(docs/APP-MODEL.md §4 三态 exec;§17 起 exec.mode 降级为元信息,
    /// 执行一律经服务端 kernel.run——客户端只需要知道"要不要出海")。
    /// </summary>
    public enum ExecMode
    {
        Local,      // 纯 UI / state 动作(不出海;或经管道走服务端 local skill)
        Endpoint,   // 确定性写,走 action 管道
        Run,        // agentic 长任务,走 action 管道(spawn run)
    }

    /// <summary>WidgetDef 里的 action 声明面(对应 manifest 的 actions[] 项)。</summary>
    public sealed class ActionSpec
    {
        public string Id;
        public ExecMode Exec = ExecMode.Endpoint;
        public string Ref = "";                          // 服务端 skill ref(元信息;客户端永不直调)
        public string[] ArgsInput = Array.Empty<string>(); // 客户端可控参数键(硬校验永远在服务端)
        public string[] Surfaces = { "card", "tab" };      // 在哪些面孔上提供

        public ActionSpec(string id) { Id = id; }
    }

    /// <summary>
    /// Widget 协议定义面(docs/WIDGETS.md §1.2 的 WidgetDef;APP-MODEL.md §2 的 AppManifest 同哲学):
    /// kind / v / actions / events / surfaces + 实例工厂。widget 是代码资产,注册进 WidgetRegistry。
    /// </summary>
    public abstract class WidgetDef
    {
        public abstract string Kind { get; }
        public virtual int V { get { return 1; } }
        public virtual IReadOnlyList<ActionSpec> Actions { get { return Array.Empty<ActionSpec>(); } }
        public virtual IReadOnlyList<string> Events { get { return Array.Empty<string>(); } }
        public virtual IReadOnlyList<string> Surfaces { get { return new[] { "card", "tab" }; } }

        /// <summary>实例工厂。state 必须可 JSON 序列化(刷新/重渲染可恢复)。</summary>
        public abstract Widget CreateInstance(JObject state);
    }
}

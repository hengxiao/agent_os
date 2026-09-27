using System;
using AgentOS.UI.Kernel;

namespace AgentOS.UI.Apps.DocEditor
{
    /// <summary>Doc Editor app 的宿主服务束(窗口装配时注入;widget 层永远拿不到)。</summary>
    public sealed class DocServices
    {
        public AgentOsClient Client;
        public ActionPipeline Pipeline;
        public WidgetRegistry Registry;
        public WidgetTree Tree;
        public IExportSink Sink;
        /// <summary>状态汇报(msg, tone: "info"|"ok"|"danger")——窗口状态栏的投递口。</summary>
        public Action<string, string> Report = (m, t) => { };
    }
}

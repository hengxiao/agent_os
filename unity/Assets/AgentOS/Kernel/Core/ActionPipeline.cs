using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// app instance(docs/APP-MODEL.md §2 AppInstance):state 服务端权威,
    /// 客户端只镜像、只发事件不发状态(§1.2-3)。
    /// </summary>
    public sealed class AppInstance
    {
        public string Id = "";
        public string Kind = "";
        public string Ref = "";
        public string Title = "";
        public JObject State = new JObject();

        public static AppInstance FromJson(JObject j)
        {
            if (j == null) return null;
            return new AppInstance
            {
                Id = j.Value<string>("id") ?? "",
                Kind = j.Value<string>("kind") ?? "",
                Ref = j.Value<string>("ref") ?? "",
                Title = j.Value<string>("title") ?? "",
                State = j["state"] as JObject ?? new JObject(),
            };
        }

        /// <summary>从服务端响应镜像最新 state(管道响应里的 instance 面)。</summary>
        public void MirrorFrom(JObject j)
        {
            if (j == null) return;
            if (j.Value<string>("id") != null) Id = j.Value<string>("id");
            if (j.Value<string>("kind") != null) Kind = j.Value<string>("kind");
            if (j.Value<string>("ref") != null) Ref = j.Value<string>("ref");
            if (j.Value<string>("title") != null) Title = j.Value<string>("title");
            var s = j["state"] as JObject;
            if (s != null) State = s;
        }
    }

    /// <summary>
    /// Action 管道客户端(docs/APP-MODEL.md §4):用户点击(任何面孔)→ 只发事件
    /// (action_id + args_input 载荷,不发 state)→ POST /apps/{id}/actions/{action}。
    /// manifest 裁决/args_from 服务端绑定/schema 校验全部在服务端;trigger 非空时
    /// 自动携带 §16 级联信封(action 声明 context: [] 弃权是服务端语义,客户端不裁剪)。
    /// </summary>
    public sealed class ActionPipeline
    {
        readonly AgentOsClient _client;
        readonly WidgetTree _tree;

        public ActionPipeline(AgentOsClient client, WidgetTree tree)
        {
            _client = client;
            _tree = tree;
        }

        public async Task<JObject> InvokeAsync(
            AppInstance app, string actionId, JObject argsInput,
            string surface, string sessionId = null, Widget trigger = null)
        {
            var body = new JObject
            {
                ["surface"] = string.IsNullOrEmpty(surface) ? "tab" : surface,
                ["args"] = argsInput ?? new JObject(),
            };
            if (!string.IsNullOrEmpty(sessionId)) body["session_id"] = sessionId;
            if (trigger != null) body["cascade"] = _tree.Cascade.Build(trigger);
            var resp = await _client.InvokeAction(app.Id, actionId, body);
            var inst = resp["instance"] as JObject;
            if (inst != null) app.MirrorFrom(inst); // state 回写镜像(服务端权威)
            return resp;
        }
    }
}

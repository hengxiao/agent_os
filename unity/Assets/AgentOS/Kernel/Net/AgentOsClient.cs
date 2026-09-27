using System;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using UnityEngine.Networking;

namespace AgentOS.UI.Kernel
{
    /// <summary>FastAPI 错误面:{detail: string|[…]} 拆包成一条异常。</summary>
    public sealed class ApiException : Exception
    {
        public int Status;
        public ApiException(int status, string detail) : base(status + " " + detail) { Status = status; }
    }

    /// <summary>
    /// Agent OS Web Platform 的 REST 客户端(web_platform/app.py 的端点契约)。
    /// 只负责出海;动作语义(白名单/闸门/promote)全部在服务端,客户端零裁决。
    /// 默认 BaseUrl = 本地实例(run-web.sh 缺省 8391),平台挂载前缀 /platform。
    /// </summary>
    public sealed class AgentOsClient
    {
        public string BaseUrl = "http://127.0.0.1:8391";

        const int DefaultTimeoutSec = 30;
        const int LlmTimeoutSec = 180; // comment/chat/review 起 LLM run,给一个宽松上限

        // ---------------------------------------------------------------- 低层

        async Task<string> Send(string method, string path, JObject body, int timeoutSec)
        {
            using (var req = new UnityWebRequest(BaseUrl + path, method))
            {
                req.downloadHandler = new DownloadHandlerBuffer();
                req.timeout = timeoutSec;
                if (body != null)
                {
                    var bytes = Encoding.UTF8.GetBytes(body.ToString(Newtonsoft.Json.Formatting.None));
                    req.uploadHandler = new UploadHandlerRaw(bytes);
                    req.SetRequestHeader("Content-Type", "application/json");
                }
                await req.SendWebRequest().ToTask();
                if (req.result != UnityWebRequest.Result.Success)
                    throw new ApiException((int)req.responseCode, ExtractDetail(req));
                return req.downloadHandler != null ? req.downloadHandler.text : "";
            }
        }

        static string ExtractDetail(UnityWebRequest req)
        {
            try
            {
                var text = req.downloadHandler != null ? req.downloadHandler.text : null;
                if (!string.IsNullOrEmpty(text))
                {
                    var j = JToken.Parse(text);
                    var d = j["detail"];
                    if (d != null) return d.Type == JTokenType.String ? d.Value<string>() : d.ToString();
                }
            }
            catch (Exception) { /* 非 JSON 错误体,回落 req.error */ }
            return req.error;
        }

        public async Task<JObject> GetJson(string path, int timeoutSec = DefaultTimeoutSec)
        {
            var text = await Send("GET", path, null, timeoutSec);
            return string.IsNullOrEmpty(text) ? new JObject() : JObject.Parse(text);
        }

        public async Task<JArray> GetArray(string path, int timeoutSec = DefaultTimeoutSec)
        {
            var text = await Send("GET", path, null, timeoutSec);
            return string.IsNullOrEmpty(text) ? new JArray() : JArray.Parse(text);
        }

        public async Task<JObject> PostJson(string path, JObject body, int timeoutSec = DefaultTimeoutSec)
        {
            var text = await Send("POST", path, body ?? new JObject(), timeoutSec);
            return string.IsNullOrEmpty(text) ? new JObject() : JObject.Parse(text);
        }

        // ---------------------------------------------------------------- docs 读面(web_platform/app.py)

        /// <summary>GET /platform/api/docs:文档索引(标题/首行/字数/最近编辑)。</summary>
        public Task<JArray> ListDocs() { return GetArray("/platform/api/docs"); }

        /// <summary>POST /platform/api/docs {name,title?,text?}:新建(点分名校验在 store;重名 409)。</summary>
        public Task<JObject> CreateDoc(string name, string title, string text)
        {
            return PostJson("/platform/api/docs", new JObject
            {
                ["name"] = name,
                ["title"] = title ?? "",
                ["text"] = text ?? "",
            });
        }

        /// <summary>GET /platform/api/docs/{name}:全文 + meta + versions + chat 种子。</summary>
        public Task<JObject> ReadDoc(string name)
        {
            return GetJson("/platform/api/docs/" + Uri.EscapeDataString(name));
        }

        /// <summary>GET /platform/api/docs/{name}/bubbles:全文档气泡流(服务端事实源)。</summary>
        public Task<JArray> ReadBubbles(string name)
        {
            return GetArray("/platform/api/docs/" + Uri.EscapeDataString(name) + "/bubbles");
        }

        /// <summary>POST …/comment {anchor,text,cascade} → {reply,edits}(D2 专属端点先例)。</summary>
        public Task<JObject> SendComment(string name, string anchor, string text, JArray cascade)
        {
            return PostJson("/platform/api/docs/" + Uri.EscapeDataString(name) + "/comment", new JObject
            {
                ["anchor"] = anchor ?? "",
                ["text"] = text ?? "",
                ["cascade"] = cascade ?? new JArray(),
            }, LlmTimeoutSec);
        }

        /// <summary>POST …/chat {text} → {reply,changed};changed=服务端全文对比(不信技能自报)。</summary>
        public Task<JObject> SendChat(string name, string text)
        {
            return PostJson("/platform/api/docs/" + Uri.EscapeDataString(name) + "/chat", new JObject
            {
                ["text"] = text ?? "",
            }, LlmTimeoutSec);
        }

        /// <summary>POST …/review:全文评审 → 锚点批注集自动挂段(带 severity)。</summary>
        public Task<JObject> ReviewDoc(string name)
        {
            return PostJson("/platform/api/docs/" + Uri.EscapeDataString(name) + "/review", new JObject(), LlmTimeoutSec);
        }

        // ---------------------------------------------------------------- app 管道(APP-MODEL §4)

        /// <summary>POST /platform/api/apps/spawn {kind,ref,title,state,created_by} → instance(kind+ref 去重)。</summary>
        public async Task<AppInstance> Spawn(string kind, string refName, string title, JObject state, string createdBy)
        {
            var resp = await PostJson("/platform/api/apps/spawn", new JObject
            {
                ["kind"] = kind,
                ["ref"] = refName ?? "",
                ["title"] = title ?? refName ?? "",
                ["state"] = state ?? new JObject(),
                ["created_by"] = createdBy ?? "",
            });
            return AppInstance.FromJson(resp["instance"] as JObject);
        }

        /// <summary>GET /platform/api/apps/{id}:instance 读取面。</summary>
        public async Task<AppInstance> GetInstance(string instanceId)
        {
            var resp = await GetJson("/platform/api/apps/" + Uri.EscapeDataString(instanceId));
            return AppInstance.FromJson(resp);
        }

        /// <summary>POST /platform/api/apps/{id}/actions/{action}(surface/args/session_id/cascade)。</summary>
        public Task<JObject> InvokeAction(string instanceId, string actionId, JObject body, int timeoutSec = DefaultTimeoutSec)
        {
            return PostJson(
                "/platform/api/apps/" + Uri.EscapeDataString(instanceId) + "/actions/" + Uri.EscapeDataString(actionId),
                body, timeoutSec);
        }

        /// <summary>连通性探针(sessions 是数组读面;兼作状态点数据源)。</summary>
        public async Task<bool> Ping()
        {
            try { await GetArray("/platform/api/sessions", 8); return true; }
            catch (Exception) { return false; }
        }
    }
}

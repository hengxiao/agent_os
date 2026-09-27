using System;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEngine.Networking;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// SSE 客户端:GET /platform/api/stream(M4b;decision.new/run.finished/keepalive 帧)。
    /// UnityWebRequest + 流式 DownloadHandler 增量解析 "event:/data:" 帧;断线由宿主决定重连
    /// 策略(回落 5s 轮询是 web 前端的既有语义,此处不替它做决定)。
    /// Doc Editor v1 未消费本通道(平台流暂无 doc 事件);这是内核给后续 app(run 态势等)
    /// 备下的 live 面。
    /// </summary>
    public sealed class SseClient : IDisposable
    {
        public event Action<string, JObject> EventReceived;  // (event, data)
        public event Action<bool> ConnectionChanged;

        readonly string _url;
        UnityWebRequest _req;
        StreamHandler _handler;

        public SseClient(string baseUrl)
        {
            _url = (baseUrl ?? "").TrimEnd('/') + "/platform/api/stream";
        }

        public void Start()
        {
            Stop();
            _handler = new StreamHandler(this);
            _req = new UnityWebRequest(_url, "GET") { downloadHandler = _handler, timeout = 0 };
            _req.SendWebRequest().completed += _ => OnDone();
            ConnectionChanged?.Invoke(true);
        }

        public void Stop()
        {
            if (_req != null)
            {
                try { _req.Abort(); } catch (Exception) { /* 已结束 */ }
                _req.Dispose();
                _req = null;
            }
        }

        void OnDone()
        {
            ConnectionChanged?.Invoke(false); // 连接关闭(含错误);重连策略在宿主
        }

        void OnFrame(string raw)
        {
            string evt = "message";
            var data = new StringBuilder();
            foreach (var line in raw.Split('\n'))
            {
                if (line.StartsWith("event:")) evt = line.Substring(6).Trim();
                else if (line.StartsWith("data:")) data.Append(line.Substring(5).TrimStart(' '));
                // ":" 开头 = keepalive 注释帧,忽略
            }
            if (data.Length == 0) return;
            JObject parsed = null;
            try { parsed = JObject.Parse(data.ToString()); }
            catch (Exception) { return; } // 坏帧丢弃(流是只读聚合,容错优先)
            EventReceived?.Invoke(evt, parsed);
        }

        public void Dispose() { Stop(); }

        sealed class StreamHandler : DownloadHandlerScript
        {
            readonly SseClient _owner;
            string _buf = "";

            public StreamHandler(SseClient owner) { _owner = owner; }

            protected override bool ReceiveData(byte[] data, int dataLength)
            {
                if (dataLength <= 0) return true;
                _buf += Encoding.UTF8.GetString(data, 0, dataLength);
                int idx;
                while ((idx = _buf.IndexOf("\n\n", StringComparison.Ordinal)) >= 0)
                {
                    var frame = _buf.Substring(0, idx);
                    _buf = _buf.Substring(idx + 2);
                    _owner.OnFrame(frame);
                }
                return true;
            }
        }
    }
}

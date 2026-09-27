using System;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;

namespace AgentOS.UI.Kernel
{
    /// <summary>UnityWebRequestAsyncOperation → Task 桥(全版本可用,不依赖 Unity 6 Awaitable)。</summary>
    public static class UnityWebTasks
    {
        public static Task ToTask(this UnityWebRequestAsyncOperation op)
        {
            var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            op.completed += _ => tcs.TrySetResult(true);
            return tcs.Task;
        }
    }
}

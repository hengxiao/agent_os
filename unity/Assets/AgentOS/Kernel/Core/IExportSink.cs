namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// 导出宿主页:拷贝全文 / 另存文件。内核不假设平台能力——Editor 宿主用
    /// 系统剪贴板 + SaveFilePanel 实现;运行时宿主以后再给(留口)。
    /// </summary>
    public interface IExportSink
    {
        void CopyText(string text);
        void SaveFile(string suggestedName, string text);
    }
}

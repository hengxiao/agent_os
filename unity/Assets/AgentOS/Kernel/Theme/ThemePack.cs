using System.Collections.Generic;
using UnityEngine;

namespace AgentOS.UI.Kernel
{
    /// <summary>动效档位(主题契约 §2.3):主题给具名动效一个实现档;reduced-motion 强制 Instant。</summary>
    public enum MotionLevel { Full, Subtle, Instant }

    /// <summary>
    /// 主题包(docs/DEBUG-UI-THEMES.md §2 三层契约):可插拔数据包,组件代码永远只有一份。
    /// v1 主题 = 代码贡献(§6 政策);token 值逐字移植自 host/web/static/css/themes/*.css。
    /// </summary>
    public sealed class ThemePack
    {
        public string Id;
        public string Name;
        public string FontUi = "";   // 字体名回退链(UI Toolkit 侧尽力而为;mono 不换是纪律)
        public string FontMono = "";
        public string Mascot;        // "mochi" | "sprite8" | null(可整体关闭的可选层)

        public readonly Dictionary<string, Color> Colors = new Dictionary<string, Color>();
        public readonly Dictionary<string, int> Sizes = new Dictionary<string, int>();      // text-* / s* / r-*
        public readonly Dictionary<string, string> Copy = new Dictionary<string, string>(); // 文案键(翻译层)
        public readonly Dictionary<string, MotionLevel> Motion = new Dictionary<string, MotionLevel>();

        public MotionLevel MotionOf(string name)
        {
            MotionLevel level;
            return Motion.TryGetValue(name, out level) ? level : MotionLevel.Subtle;
        }
    }

    /// <summary>
    /// 主题契约(§2.1/§4):token 全集 + copy 键清单;加载时校验完整性,
    /// 缺一项 → 主题不注册(防半成品主题上线)。对比度/双编码的机检在 web 契约测试侧
    /// 已是闸门;Unity 侧的颜色值与 web 同源(逐字移植),不重复造对比度计算器。
    /// </summary>
    public static class ThemeContract
    {
        public static readonly string[] RequiredColors =
        {
            "bg-0", "bg-1", "bg-2", "bg-3", "line", "line-strong",
            "fg-0", "fg-1", "fg-2",
            "ok", "warn", "danger", "aborted", "live",
            "sig-llm", "sig-tool", "sig-sidecar", "sig-compress", "sig-budget", "sig-frame",
            "perm-read", "perm-write", "perm-net", "perm-exec",
            "focus-ring",
        };

        public static readonly string[] RequiredSizes =
        {
            "text-xs", "text-sm", "text-md", "text-lg", "text-xl",
            "s1", "s2", "s3", "s4", "s6", "s8",
            "r-sm", "r-md", "r-lg",
        };

        /// <summary>Doc Editor 用到的 copy 键(doc.* 主题;技术原文豁免直读)。</summary>
        public static readonly string[] RequiredCopy =
        {
            "doc.list.empty", "doc.new", "doc.create",
            "doc.save", "doc.snapshot", "doc.rewind", "doc.rewind.confirm", "doc.review", "doc.export",
            "doc.status.dirty", "doc.status.saved",
            "doc.comment.placeholder", "doc.comment.send", "doc.comment.apply", "doc.comment.thinking",
            "doc.chat.placeholder", "doc.chat.send",
            "doc.view.edit", "doc.view.preview", "doc.view.split",
        };

        /// <summary>返回缺失项清单;空 = 契约通过。</summary>
        public static List<string> Validate(ThemePack pack)
        {
            var missing = new List<string>();
            foreach (var c in RequiredColors) if (!pack.Colors.ContainsKey(c)) missing.Add("color:" + c);
            foreach (var s in RequiredSizes) if (!pack.Sizes.ContainsKey(s)) missing.Add("size:" + s);
            foreach (var k in RequiredCopy) if (!pack.Copy.ContainsKey(k)) missing.Add("copy:" + k);
            return missing;
        }
    }
}

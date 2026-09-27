using System;
using System.Collections.Generic;
using UnityEngine;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// 主题注册表(docs/DEBUG-UI-THEMES.md §2.5):声明式清单 + 契约校验
    /// (缺变量 → 不注册)+ 切换持久化 + 顺序循环。组件零分支:组件只消费
    /// ThemeRegistry.Current 的 token/copy/motion,不出现主题 id 字符串。
    /// </summary>
    public static class ThemeRegistry
    {
        const string PrefKey = "agent-os.unity.theme";

        static readonly List<ThemePack> _order = new List<ThemePack>();
        static readonly Dictionary<string, ThemePack> _packs = new Dictionary<string, ThemePack>();

        public static ThemePack Current { get; private set; }
        /// <summary>对齐 prefers-reduced-motion:所有具名动效解析为 Instant,对所有主题生效。</summary>
        public static bool ReducedMotion;
        public static event Action ThemeChanged;

        /// <summary>契约不过不注册(返回 false,missing 带缺失清单)。</summary>
        public static bool Register(ThemePack pack, out List<string> missing)
        {
            missing = ThemeContract.Validate(pack);
            if (missing.Count > 0) return false;
            if (!_packs.ContainsKey(pack.Id)) _order.Add(pack);
            _packs[pack.Id] = pack;
            if (Current == null) Current = pack;
            return true;
        }

        public static void Apply(string id)
        {
            ThemePack pack;
            if (!_packs.TryGetValue(id, out pack)) return;
            Current = pack;
            PlayerPrefs.SetString(PrefKey, id);
            PlayerPrefs.Save();
            var h = ThemeChanged;
            if (h != null) h();
        }

        /// <summary>注册表顺序循环(与 web 托盘主题切换同一通道语义)。</summary>
        public static string Cycle()
        {
            if (_order.Count == 0) return "";
            var i = _order.IndexOf(Current);
            var next = _order[(i + 1) % _order.Count];
            Apply(next.Id);
            return next.Id;
        }

        /// <summary>启动恢复(持久化偏好;缺省第一个注册主题)。</summary>
        public static void RestoreSaved()
        {
            var saved = PlayerPrefs.GetString(PrefKey, "");
            if (!string.IsNullOrEmpty(saved) && _packs.ContainsKey(saved)) Current = _packs[saved];
            if (Current == null && _order.Count > 0) Current = _order[0];
        }

        public static IReadOnlyList<ThemePack> All { get { return _order; } }
    }
}

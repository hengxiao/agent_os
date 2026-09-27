using UnityEngine;
using UnityEngine.UIElements;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// 具名动效播放层(主题契约 §2.3):组件只调用具名动效,档位由主题档案给;
    /// ReducedMotion 强制 Instant。性能纪律:只动 opacity/背景色,单帧 &lt;16ms。
    /// </summary>
    public static class MotionPlayer
    {
        public static void Play(string name, VisualElement el)
        {
            if (el == null) return;
            var theme = ThemeRegistry.Current;
            var level = theme != null ? theme.MotionOf(name) : MotionLevel.Subtle;
            if (ThemeRegistry.ReducedMotion) level = MotionLevel.Instant;
            if (level == MotionLevel.Instant) return;

            if (name == "focus-pulse") Pulse(el, level == MotionLevel.Full ? 3 : 2);
            else if (name == "change-flash") Flash(el);
        }

        /// <summary>焦点脉冲:focus-ring 色呼吸 N 次后复位。</summary>
        static void Pulse(VisualElement el, int times)
        {
            var ring = Sty.C("focus-ring");
            var original = el.style.backgroundColor;
            for (var i = 0; i < times; i++)
            {
                var at = i;
                el.schedule.Execute(() =>
                {
                    el.style.backgroundColor = new Color(ring.r, ring.g, ring.b, 0.35f);
                    el.schedule.Execute(() => { el.style.backgroundColor = original; }).StartingIn(140);
                }).StartingIn(at * 280);
            }
        }

        /// <summary>变化高亮:ok 色淡入后淡出一次(不循环)。</summary>
        static void Flash(VisualElement el)
        {
            var ok = Sty.C("ok");
            var original = el.style.backgroundColor;
            el.style.backgroundColor = new Color(ok.r, ok.g, ok.b, 0.25f);
            el.schedule.Execute(() => { el.style.backgroundColor = original; }).StartingIn(1500);
        }
    }
}

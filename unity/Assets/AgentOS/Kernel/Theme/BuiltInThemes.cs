using UnityEngine;

namespace AgentOS.UI.Kernel
{
    /// <summary>
    /// 内置主题:值逐字移植自 host/web/static/css/themes/{classic,pixel}.css(行为同源)。
    /// classic = 契约参考实现(严肃工程,dark-first);pixel = 8-bit 复古游戏(证明可插拔,
    /// 游戏腔 copy 是"翻译层",技术原文豁免直读)。
    /// </summary>
    public static class BuiltInThemes
    {
        static Color C(string hex)
        {
            Color c;
            return ColorUtility.TryParseHtmlString(hex, out c) ? c : Color.magenta;
        }

        public static ThemePack Classic()
        {
            var t = new ThemePack { Id = "classic", Name = "严肃工程", Mascot = null };
            // 基底(classic.css §3.1)
            t.Colors["bg-0"] = C("#0b0e14"); t.Colors["bg-1"] = C("#11151d");
            t.Colors["bg-2"] = C("#171d29"); t.Colors["bg-3"] = C("#1f2837");
            t.Colors["line"] = C("#263043"); t.Colors["line-strong"] = C("#33405a");
            t.Colors["fg-0"] = C("#e6ebf2"); t.Colors["fg-1"] = C("#9aa7ba"); t.Colors["fg-2"] = C("#5d6b82");
            // 语义:状态
            t.Colors["ok"] = C("#3fb68b"); t.Colors["warn"] = C("#d9a03f");
            t.Colors["danger"] = C("#e5534b"); t.Colors["aborted"] = C("#9e6bde"); t.Colors["live"] = C("#3b9eff");
            // 语义:信号
            t.Colors["sig-llm"] = C("#6f9fff"); t.Colors["sig-tool"] = C("#3fb68b");
            t.Colors["sig-sidecar"] = C("#d9a03f"); t.Colors["sig-compress"] = C("#8b7cf6");
            t.Colors["sig-budget"] = C("#e5534b"); t.Colors["sig-frame"] = C("#5d6b82");
            // 语义:权限
            t.Colors["perm-read"] = C("#5d6b82"); t.Colors["perm-write"] = C("#d9a03f");
            t.Colors["perm-net"] = C("#6f9fff"); t.Colors["perm-exec"] = C("#e5534b");
            t.Colors["focus-ring"] = C("#3b9eff");
            // 排版(§3.2;Unity 侧 float point,12.5px → 12)
            t.Sizes["text-xs"] = 11; t.Sizes["text-sm"] = 12; t.Sizes["text-md"] = 14;
            t.Sizes["text-lg"] = 16; t.Sizes["text-xl"] = 20;
            t.Sizes["s1"] = 4; t.Sizes["s2"] = 8; t.Sizes["s3"] = 12;
            t.Sizes["s4"] = 16; t.Sizes["s6"] = 24; t.Sizes["s8"] = 32;
            t.Sizes["r-sm"] = 4; t.Sizes["r-md"] = 8; t.Sizes["r-lg"] = 12;
            // copy:classic 的文案表 = 现状文案的抽离(简 technical)
            t.Copy["doc.list.empty"] = "还没有文档";
            t.Copy["doc.new"] = "+ 新建文档";
            t.Copy["doc.create"] = "创建";
            t.Copy["doc.save"] = "保存";
            t.Copy["doc.snapshot"] = "快照";
            t.Copy["doc.rewind"] = "回滚";
            t.Copy["doc.rewind.confirm"] = "确认回滚?";
            t.Copy["doc.review"] = "评审";
            t.Copy["doc.export"] = "导出";
            t.Copy["doc.status.dirty"] = "未保存";
            t.Copy["doc.status.saved"] = "已保存";
            t.Copy["doc.comment.placeholder"] = "就这一段提问…";
            t.Copy["doc.comment.send"] = "发送";
            t.Copy["doc.comment.apply"] = "应用此修改";
            t.Copy["doc.comment.thinking"] = "思考中…";
            t.Copy["doc.chat.placeholder"] = "告诉 agent 要怎么改这篇文档…";
            t.Copy["doc.chat.send"] = "发送";
            t.Copy["doc.view.edit"] = "编辑";
            t.Copy["doc.view.preview"] = "预览";
            t.Copy["doc.view.split"] = "分屏";
            t.Motion["focus-pulse"] = MotionLevel.Subtle;
            t.Motion["change-flash"] = MotionLevel.Subtle;
            return t;
        }

        public static ThemePack Pixel()
        {
            var t = new ThemePack { Id = "pixel", Name = "像素复古", Mascot = "sprite8" };
            // 基底:深靛夜(pixel.css)
            t.Colors["bg-0"] = C("#1a1c2c"); t.Colors["bg-1"] = C("#23253a");
            t.Colors["bg-2"] = C("#2c2f4a"); t.Colors["bg-3"] = C("#363a58");
            t.Colors["line"] = C("#3d4166"); t.Colors["line-strong"] = C("#5a5f8c");
            t.Colors["fg-0"] = C("#f2f3fa"); t.Colors["fg-1"] = C("#b4b9dc"); t.Colors["fg-2"] = C("#8287ad");
            // 语义色:饱和 8-bit(HP 绿/金币黄/MP 蓝)
            t.Colors["ok"] = C("#5dd65d"); t.Colors["warn"] = C("#f8d838");
            t.Colors["danger"] = C("#ff6b5e"); t.Colors["aborted"] = C("#c08cf8"); t.Colors["live"] = C("#4ecdf8");
            // 信号
            t.Colors["sig-llm"] = C("#7ec4ff"); t.Colors["sig-tool"] = C("#5dd65d");
            t.Colors["sig-sidecar"] = C("#f8d838"); t.Colors["sig-compress"] = C("#c08cf8");
            t.Colors["sig-budget"] = C("#ff6b5e"); t.Colors["sig-frame"] = C("#8287ad");
            // 权限
            t.Colors["perm-read"] = C("#8287ad"); t.Colors["perm-write"] = C("#f8d838");
            t.Colors["perm-net"] = C("#7ec4ff"); t.Colors["perm-exec"] = C("#ff6b5e");
            t.Colors["focus-ring"] = C("#4ecdf8");
            t.Sizes["text-xs"] = 11; t.Sizes["text-sm"] = 12; t.Sizes["text-md"] = 14;
            t.Sizes["text-lg"] = 16; t.Sizes["text-xl"] = 20;
            t.Sizes["s1"] = 4; t.Sizes["s2"] = 8; t.Sizes["s3"] = 12;
            t.Sizes["s4"] = 16; t.Sizes["s6"] = 24; t.Sizes["s8"] = 32;
            t.Sizes["r-sm"] = 0; t.Sizes["r-md"] = 2; t.Sizes["r-lg"] = 2; // 圆角归零(§3.6)
            // copy:游戏腔(技术原文照常并列,翻译层纪律)
            t.Copy["doc.list.empty"] = "no docs yet.";
            t.Copy["doc.new"] = "+ NEW DOC";
            t.Copy["doc.create"] = "START";
            t.Copy["doc.save"] = "SAVE";
            t.Copy["doc.snapshot"] = "存档";
            t.Copy["doc.rewind"] = "读档";
            t.Copy["doc.rewind.confirm"] = "SURE?";
            t.Copy["doc.review"] = "SCAN!";
            t.Copy["doc.export"] = "导出";
            t.Copy["doc.status.dirty"] = "● modified";
            t.Copy["doc.status.saved"] = "SAVED";
            t.Copy["doc.comment.placeholder"] = "这一段怎么了?";
            t.Copy["doc.comment.send"] = "SEND";
            t.Copy["doc.comment.apply"] = "APPLY";
            t.Copy["doc.comment.thinking"] = "…";
            t.Copy["doc.chat.placeholder"] = "要改成什么样?";
            t.Copy["doc.chat.send"] = "GO";
            t.Copy["doc.view.edit"] = "EDIT";
            t.Copy["doc.view.preview"] = "VIEW";
            t.Copy["doc.view.split"] = "SPLIT";
            t.Motion["focus-pulse"] = MotionLevel.Full;
            t.Motion["change-flash"] = MotionLevel.Full;
            return t;
        }
    }
}

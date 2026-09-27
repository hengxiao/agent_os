using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;

namespace AgentOS.UI.Apps.DocEditor
{
    /// <summary>一个 markdown 块:类型 + 1-based 行号闭区间 + 锚点串。</summary>
    public sealed class MdBlock
    {
        public string Kind;      // heading | paragraph | list-item | table-row | code
        public int StartLine;    // 1-based,含
        public int EndLine;      // 1-based,含
        public string Text;
        public string Anchor;    // doc.md#L<start>-L<end>(服务端 _ANCHOR_RE 兼容面)
    }

    /// <summary>
    /// md 块切分(镜像 web doc-editor.js 的 mdBlocks:D2 实现注"空行分块,
    /// 标题/列表项/表格行独占,1-based 行号区间")。锚点格式与服务端
    /// _ANCHOR_RE(^doc\.md#L(\d+)-L(\d+)$,列跨度 :C 可选)兼容。
    /// </summary>
    public static class MdBlocks
    {
        static readonly Regex HeadingRe = new Regex(@"^\s*#{1,6}\s", RegexOptions.Compiled);
        static readonly Regex ListRe = new Regex(@"^\s*([-*+]|\d+\.)\s", RegexOptions.Compiled);
        static readonly Regex TableRe = new Regex(@"^\s*\|.*\|\s*$", RegexOptions.Compiled);
        static readonly Regex AnchorRe = new Regex(@"^doc\.md#L(\d+)(?::C(\d+))?-L(\d+)(?::C(\d+))?$", RegexOptions.Compiled);

        public static List<MdBlock> Split(string text)
        {
            var blocks = new List<MdBlock>();
            if (string.IsNullOrEmpty(text)) return blocks;
            var lines = text.Replace("\r\n", "\n").Split('\n');
            var i = 0;
            while (i < lines.Length)
            {
                var ln = lines[i];
                if (string.IsNullOrWhiteSpace(ln)) { i++; continue; }
                var start = i + 1; // 1-based

                if (ln.TrimStart().StartsWith("```"))
                {
                    var sb = new StringBuilder(ln);
                    i++;
                    while (i < lines.Length && !lines[i].TrimStart().StartsWith("```"))
                    {
                        sb.Append('\n').Append(lines[i]);
                        i++;
                    }
                    if (i < lines.Length) { sb.Append('\n').Append(lines[i]); i++; } // 收尾围栏
                    blocks.Add(Mk("code", start, i, sb.ToString()));
                    continue;
                }
                if (HeadingRe.IsMatch(ln)) { blocks.Add(Mk("heading", start, start, ln)); i++; continue; }
                if (TableRe.IsMatch(ln)) { blocks.Add(Mk("table-row", start, start, ln)); i++; continue; }
                if (ListRe.IsMatch(ln)) { blocks.Add(Mk("list-item", start, start, ln)); i++; continue; }

                // 段落:累积到空行或特殊起始行
                var psb = new StringBuilder(ln);
                i++;
                while (i < lines.Length && !string.IsNullOrWhiteSpace(lines[i])
                       && !HeadingRe.IsMatch(lines[i]) && !ListRe.IsMatch(lines[i])
                       && !TableRe.IsMatch(lines[i]) && !lines[i].TrimStart().StartsWith("```"))
                {
                    psb.Append('\n').Append(lines[i]);
                    i++;
                }
                blocks.Add(Mk("paragraph", start, i, psb.ToString()));
            }
            return blocks;
        }

        static MdBlock Mk(string kind, int start, int end, string text)
        {
            return new MdBlock
            {
                Kind = kind,
                StartLine = start,
                EndLine = end,
                Text = text,
                Anchor = "doc.md#L" + start + "-L" + end,
            };
        }

        /// <summary>锚点 → 行区间(越界/非法返回 false)。</summary>
        public static bool ParseAnchor(string anchor, out int start, out int end)
        {
            start = 0; end = 0;
            if (string.IsNullOrEmpty(anchor)) return false;
            var m = AnchorRe.Match(anchor);
            if (!m.Success) return false;
            start = int.Parse(m.Groups[1].Value);
            end = int.Parse(m.Groups[3].Value);
            return end >= start;
        }

        /// <summary>按锚点取当前全文里的块原文(apply 的 expected 校验面)。</summary>
        public static string BlockText(string fullText, string anchor)
        {
            int start, end;
            if (!ParseAnchor(anchor, out start, out end) || fullText == null) return "";
            var lines = fullText.Replace("\r\n", "\n").Split('\n');
            if (start < 1 || end > lines.Length) return "";
            var sb = new StringBuilder();
            for (var i = start; i <= end; i++)
            {
                if (i > start) sb.Append('\n');
                sb.Append(lines[i - 1]);
            }
            return sb.ToString();
        }
    }
}

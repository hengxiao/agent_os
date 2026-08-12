/* doc 编辑器挂载(D5,docs/DOC-EDITOR.md §2;两栏重构:左对话 35% / 右展示 65%):
   左 = doc 作用域主对话(chatbot;说"写一篇 X/加一节/按批注改一遍" → chat 端点,
   agent 经 doc.read/doc.edit 直接改文档,changed=true 时右侧重拉重渲);
   右 = 文档展示(段落块渲染 + 每块 💬 + 右键 contextmenu 开 local 气泡);
   版本下拉/快照/rewind 两击/导出/评审收进右侧顶部极细工具条;大纲/分屏/手写
   编辑面废弃(导航靠滚动+气泡跳转,改文档走对话)。
   段落气泡(D2 §2.1)与气泡栏(D3)/未读增量(D4)管道全部保留:
   提交父级组 §16 cascade 信封出海,回复/应用全经管道与专属端点。
   写动作(snapshot/rewind/export/apply)不在此——全部走 tabAction 管道(§3)。

   C3(docs/COMPOUND-WIDGET.md §9):右侧编辑器本体 = 第一个产品级 compound
   (kind "doc-editor")——文档主体 = 预定义 md-viewer 子件(view source 形态面),
   段落批注 = 动态 chat-bubble 子件;管控三通道(§7)全用上:child_context
   注入锚段/全文(cascade widget 级),on_child_event 放行 + child_event
   监听接管 submit/apply/close,壳显隐走可见性管控。
   W5.4 切割线不动:浮出定位壳/未读游标仍宿主职责,bubble view 经 §5
   link_view 挂进壳内(视图不限 slot 内)。

   C4.2(docs/DESKTOP-WIDGET.md §5-2):直进 desktop——
   - createDocEditor(doc) 工厂与挂载分离:实例只建一次(含 def/compound/
     气泡账/主对话流),desktop 拿实例 attach_existing 进窗口区;
   - mount_view 自包含且可重挂:宿主无骨架先落 docTabHtml 骨架,全部监听
     按 view 绑定(wireView),隐藏语义(最小化摘 view)后重开 = 同一实例
     重挂——文档/气泡/主对话/seen 全在 canonical 与实例闭包,逐字不丢;
   - 多 view(§5 hard link):窗口区 tab 面 = 交互面(cur,壳/工具条归属),
     对话流内 card 面 = 内容面(只渲染,不绑批注交互;批注交互在窗口面)。

   UX 批(2026-08-04):可发现性三件套(💬 hover 显形[纯 CSS]/一次性引导浮层/
   建议 chips)+ 气泡浮出化(✕ 收起为段旁标记,带未读)+ 批注列表实体化
   (位置/摘录/条数/未读)+ 改稿可视化(变化块 1.5s 高亮淡出 + "第 N 行"链接)。 */

import { copy } from "/static/js/themes.js";
import { createCompound, mdToHtml, registerWidgetDef } from "/static/js/widgets/index.js";
import { registerContextProvider } from "/static/js/widgets/cascade.js";
import { relTime } from "/static/js/widgets/w-text.render.js";
// widget-libs 试点:vendored Floating UI(docs/WIDGET-ARCH.md vendor 集成原则)
import { computePosition, offset, flip, shift, size, arrow, autoUpdate } from "/static/vendor/floating-ui/floating-ui.dom.mjs";
import { docTabHtml } from "./details.js";

// 长文档阈值(§2/§7 边界):>200KB 预览截断提示,不炸(展示面只读,无编辑器)
const PREVIEW_LIMIT = 200 * 1024;

/* severity 单源(D4 打磨;与后端 app.py 的 DOC_SEVERITIES 字面一致——
   后端校验集在 app.py,前端类名/copy 在此,两端各一份单一事实源) */
export const DOC_SEVERITIES = ["must", "should", "nit"];

/* 改稿可视化:changed=true 重拉前的旧全文暂存(按文档名;消费即删。
   跨挂载存活——reload 重挂载后新实例据此 diff 出变化块) */
const _prevTexts = new Map();

/* 导出(D4,§3):生成下载锚(Blob;无 URL 对象时退化 data: URL——
   返回 {href, filename} 供测试断言,真实浏览器由调用方 appendChild + click) */
export function exportDoc({ text, filename, doc = null }) {
  const d = doc ?? globalThis.document;
  const a = d.createElement("a");
  try {
    a.href = globalThis.URL?.createObjectURL?.(new Blob([text], { type: "text/markdown" })) ?? "";
  } catch {
    a.href = "";
  }
  if (!a.href) a.href = `data:text/markdown;charset=utf-8,${encodeURIComponent(text)}`;
  a.download = filename;
  a.textContent = filename;
  return a;
}

/* 段落块切分(D2 §2.1;1-based 行号区间):
   空行分块;标题/列表项/表格行独占一块;连续普通行并成一块 */
export function mdBlocks(text) {
  const lines = String(text ?? "").split("\n");
  const blocks = [];
  let cur = null;
  const isSpecial = (ln) => /^#{1,6}\s/.test(ln) || /^\s*[-*]\s/.test(ln) || /^\s*\|/.test(ln);
  const flush = () => {
    if (cur) blocks.push({ start: cur.start, end: cur.end, text: cur.lines.join("\n") });
    cur = null;
  };
  lines.forEach((ln, i) => {
    const no = i + 1;
    if (!ln.trim()) {
      flush();
      return;
    }
    if (isSpecial(ln)) {
      flush();
      blocks.push({ start: no, end: no, text: ln });
      return;
    }
    if (!cur) cur = { start: no, end: no, lines: [ln] };
    else {
      cur.end = no;
      cur.lines.push(ln);
    }
  });
  flush();
  return blocks;
}

/* 锚点解析(v3 用户裁决 2026-08-11):doc.md#L<start>[:C<col>]-L<end>[:C<col>]
   → {start, end, sc, ec}(列可空;旧行级 anchor 向后兼容,sc/ec = null) */
export function parseAnchor(anchor) {
  const m = /^doc\.md#L(\d+)(?::C(\d+))?-L(\d+)(?::C(\d+))?$/.exec(String(anchor ?? ""));
  if (!m) return null;
  return { start: Number(m[1]), end: Number(m[3]), sc: m[2] != null ? Number(m[2]) : null, ec: m[4] != null ? Number(m[4]) : null };
}

/* 列范围锚点 → 块内文本偏移(纯,导出;高亮包 mark/标记定位共用):
   行级/零宽/越界 → null(行级锚点不出行内高亮) */
export function anchorColOffsetsOf(text, anchor) {
  const r = parseAnchor(anchor);
  if (!r || r.sc == null || r.ec == null) return null;
  if (r.start === r.end && r.sc === r.ec) return null; // 零宽点锚点:无文本不高亮
  const lines = String(text ?? "").split("\n").slice(r.start - 1, r.end);
  if (!lines.length) return null;
  const head = lines.slice(0, -1).reduce((a, l) => a + l.length + 1, 0);
  return { offS: r.sc - 1, offE: head + r.ec - 1 };
}

/* C4.2:工厂与挂载分离——只建实例(def/compound/闭包事实源),不碰 DOM;
   视图经 inst.mount_view(host) 挂(自包含骨架;可重挂,见文件头 C4.2 注)。 */
export function createDocEditor(doc, { seedFlows = [], getTabInstance = null, reload = null } = {}) {
  let currentText = doc?.text ?? ""; // 展示面事实源(改文档走对话;changed 后 reload 重拉)
  let dirty = false; // 展示面只读:保留 dirty 状态面(cascade/apply 归态兼容),无手写入口
  // D5 主对话:种子 = chat.json(服务端事实源,开关不丢)
  const messages = (doc?.chat ?? []).map((m) => ({ role: m.role, text: m.text }));
  // UX 批改稿可视化:本次挂载若有暂存的旧全文(changed 重拉),diff 出变化块
  const _prev = doc?.name ? _prevTexts.get(doc.name) : undefined;
  if (doc?.name) _prevTexts.delete(doc.name); // 消费即删
  const changedAnchors = new Set();
  if (_prev != null && _prev !== currentText) {
    const oldTexts = new Set(mdBlocks(_prev).map((b) => b.text));
    for (const b of mdBlocks(currentText)) {
      if (!oldTexts.has(b.text)) changedAnchors.add(`doc.md#L${b.start}-L${b.end}`);
    }
  }
  /* ── C3:doc-editor = 第一个产品级 compound(docs/COMPOUND-WIDGET.md §9)──
     结构:预定义 slot "doc"(md-viewer = 文档主体,view source 形态面)+
     动态 chat-bubble 子件(段落批注,生灭随右键/锚点钮/评审)。
     管控三通道全部用上(§7):
     - child_context:给每个 bubble 注入锚段/全文(widget 级 fragment,
       取代手工 registerContextProvider 的两级注册);
     - on_child_event:submit/apply/close 放行 → child_event 监听接管;
     - surface/可见性:壳显隐在 close 事件后由宿主做(§7-3)。
     浮出定位壳/未读游标仍是宿主职责(W5.4 切割线不动)——bubble 的 view 经
     §5 link_view 挂进壳内(视图不限 slot 内),不进 layout 占位;
     出海( comment.send/apply/review)全部留在父级本组件,widget 不出海。 */
  const def = registerWidgetDef({
    kind: "doc-editor",
    v: 1,
    state_schema: { type: "object" },
    state_defaults: { source: "", view: "preview", seen: {}, changed: [] },
    actions: [],
    events: ["change"],
    aria: { role: "document" },
    surfaces: ["card", "tab"],
    compound: {
      slots: [
        { id: "doc", kind: "md-viewer", surface: "tab",
          state: { source: currentText, view: "source", title: doc?.name ?? "" },
          options: { source: currentText, view: "source", title: doc?.name ?? "", bar: false } },
      ],
      dynamic: { allow: ["chat-bubble"], max: 50 },
      layout: (state) => _layoutDoc(state),
      on_child_event: (child, event) => {
        if (event === "open") return false; // 打开类内部事件不上行(宿主已知)
        return true; // submit/apply/close 放行 → child_event 监听接管(§7-1)
      },
      child_context: (child, frag) => {
        const anchor = child.state?.anchor?.path;
        if (!anchor) return frag;
        return { ...frag, anchor, paragraph: blockTextOf(anchor), full_text: currentText };
      },
    },
  });
  const inst = createCompound(def, {
    path: `/doc/${doc?.name ?? "untitled"}`,
    state: { source: currentText, view: "preview", seen: {}, changed: [...changedAnchors] },
  });
  // app 级 cascade provider(文档名/版本/脏;widget 级由 child_context 注入,§7-2)
  // C4.4:reparent 后 path 前缀变了(如挂进 desktop /root/<name>)——
  // 本 provider 在 compound 基座之外,驱动经 _rebindAppProvider 改址重注
  const _appProvider = () => ({ name: doc.name, versions: doc.versions ?? [], dirty });
  let unregApp = registerContextProvider(`/doc/${doc?.name ?? "untitled"}`, "app", _appProvider);

  // D2→v4(P2,批注批处理工作流):批注种子 = annotations 读取面(新记录 +
  // 旧流压缩迁移);消息流/未读游标/回复存证全部退役(批注卡没有对话)
  const seedByAnchor = Object.fromEntries((seedFlows ?? []).map((r) => [r.anchor, r]));
  const bubbles = new Map(); // anchor → {inst, view, el, body, marker, anchor, blockAnchor, offTop, offLeft, ...}

  /* 交互面(cur,C4.2):最近非 card 挂接的宿主元素集——bubble 壳/工具条/
     批注栏/主对话的归属。多 view 时 card 面只渲染(不绑批注交互),
     交互面唯一;窗口摘 view(最小化)期间渲染写空树无害,重挂即更新。 */
  let cur = null; // {host, preview, chatLog, chatInput, chars, dirtyEl}

  /* 段落原文(cascade widget 级 fragment:锚点段 + 全文;v3:列范围时取选中跨度) */
  function blockTextOf(anchor) {
    const range = parseAnchor(anchor);
    if (!range) return "";
    const lines = currentText.split("\n");
    if (range.sc == null) return lines.slice(range.start - 1, range.end).join("\n");
    const seg = lines.slice(range.start - 1, range.end);
    if (!seg.length) return "";
    seg[seg.length - 1] = seg[seg.length - 1].slice(0, range.ec != null ? range.ec - 1 : undefined);
    seg[0] = seg[0].slice(range.sc - 1);
    return seg.join("\n");
  }

  /* layout(纯;state 驱动):preview = 段落块 chrome(与 D5 同构),
     source = doc slot(md-viewer 源码态)——布局切换不动子 instance(§3-3 占位进出) */
  function _layoutDoc(state) {
    const text = String(state.source ?? "");
    if (state.view === "source") return `<div data-slot="doc"></div>`;
    if (!text.trim()) {
      return `<div class="doc-guide" data-doc-guide="1">${esc(copy("platform.doc.guide"))}</div>`;
    }
    const limited = text.length > PREVIEW_LIMIT;
    const blocks = mdBlocks(limited ? text.slice(0, PREVIEW_LIMIT) : text);
    return (
      (limited
        ? `<div class="pf-warnline">${esc(copy("platform.doc.truncate"))}</div>`
        : "") +
      blocks
        .map((b) => {
          const anchor = `doc.md#L${b.start}-L${b.end}`;
          return (
            `<div class="doc-para${(state.changed ?? []).includes(anchor) ? " doc-changed" : ""}" data-anchor="${anchor}">` +
            `<button class="doc-anchor-btn" data-anchor-btn="1" aria-label="${esc(copy("w.bubble.ph"))}">💬</button>` +
            mdToHtml(b.text) +
            `</div>`
          );
        })
        .join("")
    );
  }

  /* 壳挂回(layout 重渲会把壳摘出 DOM——按引用挂回对应块;切割线:壳由宿主建)。
     源码态挂起(W6.7 原语义:源码 = 全文面,无锚点块,壳保持摘下;
     回预览由同一挂回逻辑复原,bubbles 映射不动)。挂在交互面(cur)。 */
  function _rehangShells() {
    if (!cur || inst.state.view === "source") return;
    for (const entry of bubbles.values()) {
      // v3:壳按块级锚点归位(列范围锚点不对应块;块内偏移在 entry.offTop)
      const block = [...cur.preview.children].find(
        (c) => c !== entry.el && c !== entry.marker && c.dataset?.anchor === entry.blockAnchor
      );
      (block ?? cur.preview).appendChild(entry.el);
      if (entry.marker) (block ?? cur.preview).appendChild(entry.marker);
    }
  }
  const _baseRelayout = inst.relayout; // 基座 relayout(layout 重渲 + 子 view 重挂)
  const _relayout = () => {
    _baseRelayout();
    _rehangShells();
    _paintHighlights(); // v3.1:行内高亮随重挂(幂等;双区同步)
    for (const entry of bubbles.values()) _placeMarker(entry); // 标记跟选段末/点位
  };
  inst.relayout = _relayout; // C4.2:外部扇出(desktop 经 live.update)也带壳挂回

  /* 气泡壳定位(widget-libs 试点:vendored Floating UI 替换手写几何)——
     computePosition(虚拟参考点(锚点块内偏移),壳,{placement:"bottom-start",
     middleware:[offset(8), flip(), shift({padding:16}), size(上限), arrow]});
     v3 行为契约不变:原位开泡/翻转/maxHeight clamp(200px, min(380,45vh), 可用−16)/
     标记原位。stub/无布局环境静默(切割线:壳几何归宿主,卡面 render 不动)。 */
  function _fitBubble(entry) {
    const block = [...(cur?.preview.children ?? [])].find((c) => c.dataset?.anchor === entry.blockAnchor);
    if (!block?.getBoundingClientRect) return;
    // F2b(2026-08-13 验收):块零 rect(detached 旧块/过渡态)会把泡甩到页顶——
    // 跳本拍,下一帧重试(重开路径已先重挂;stub/无 rAF 环境不守卫,照旧跑)
    const br0 = block.getBoundingClientRect();
    if (br0 && br0.width === 0 && br0.height === 0 && typeof requestAnimationFrame !== "undefined") {
      requestAnimationFrame(() => { if (!entry.el.hidden) _fitBubble(entry); });
      return;
    }
    if (!entry._refEl) {
      // 虚拟参考点:块内偏移(点击点/选区);rect 每次现取——滚动随行(v3 原位语义)。
      // 块引用**每次现找不捕获**:首开 add_child 会触发基座内层 relayout 重渲
      // preview,捕获的块引用随后过期(detached → rect 全零 → 重开页顶跳,P2 抓出)
      entry._refEl = {
        getBoundingClientRect: () => {
          const live = [...(cur?.preview.children ?? [])].find(
            (c) => c.dataset?.anchor === entry.blockAnchor
          ) ?? block;
          const r = live.getBoundingClientRect();
          const x = r.left + (entry.offLeft ?? r.width - 8);
          const y = r.top + (entry.offTop ?? 24);
          return { x, y, top: y, left: x, right: x, bottom: y, width: 0, height: 0 };
        },
      };
    }
    computePosition(entry._refEl, entry.el, {
      placement: "bottom-start",
      strategy: "absolute", // 壳挂在锚点块内(offsetParent = 块)
      middleware: [
        offset(8),
        flip({ padding: 16 }), // 空间不足 → 翻转向上(与 shift 同 padding,
        // 抗过渡/布局沉降期的瞬时误翻——实测 flake 抓出;v3 翻转语义不变)
        shift({ padding: 16 }),
        size({
          padding: 16,
          apply({ availableHeight }) {
            const vh = globalThis.innerHeight ?? 900;
            // F3(2026-08-13 验收):380px 绝对上限,超出内滚(原 45vh 在高大屏上无顶)
            const maxH = Math.round(Math.max(200, Math.min(Math.min(380, vh * 0.45), availableHeight - 16)));
            entry.el.style.maxHeight = `${maxH}px`; // v3.2 契约:clamp(200, min(380,45vh), 可用−16)
          },
        }),
        ...(entry.arrowEl ? [arrow({ element: entry.arrowEl, padding: 6 })] : []),
      ],
    }).then(({ x, y, placement, middlewareData }) => {
      Object.assign(entry.el.style, {
        left: `${Math.round(x)}px`, top: `${Math.round(y)}px`, right: "auto", bottom: "auto",
      });
      entry.el.classList.toggle("doc-bubble-up", placement.startsWith("top")); // 语义类(测试面)
      if (entry.arrowEl) {
        const side = placement.split("-")[0];
        const staticSide = { top: "bottom", right: "left", bottom: "top", left: "right" }[side];
        const ax = middlewareData.arrow?.x;
        const ay = middlewareData.arrow?.y;
        entry.arrowEl.dataset.side = side; // 方向样式(CSS 按边旋转)
        Object.assign(entry.arrowEl.style, {
          left: ax != null ? `${Math.round(ax)}px` : "",
          top: ay != null ? `${Math.round(ay)}px` : "",
          right: "", bottom: "",
          [staticSide]: "-5px",
        });
      }
    });
  }
  const _refitBubbles = () => { // 手工重算面(重挂/调试用;滚动/resize 由 autoUpdate 接管)
    for (const entry of bubbles.values()) {
      if (!entry.el.hidden) _fitBubble(entry);
    }
  };

  /* 点锚点(v3.1 行文级):无选区右键时,caretRangeFromPoint 把点击点映射为
     行列 → 零宽点锚点 `L3:C8-L3:C8`;标记语法块回落行级(同选区规则);
     无 caret 面/映射失败 → null(调用方回落行级)。 */
  function _pointAnchor(block, x, y) {
    const base = parseAnchor(block?.dataset?.anchor ?? "");
    if (!base) return null;
    const srcFirst = currentText.split("\n")[base.start - 1] ?? "";
    if (/^(#{1,6}\s|\s*[-*]\s|\s*\|)/.test(srcFirst)) return null;
    const docu = block.ownerDocument ?? globalThis.document;
    let node = null;
    let offset = 0;
    if (docu.caretRangeFromPoint) {
      const r = docu.caretRangeFromPoint(x, y);
      if (!r) return null;
      node = r.startContainer;
      offset = r.startOffset;
    } else if (docu.caretPositionFromPoint) {
      const p = docu.caretPositionFromPoint(x, y);
      if (!p) return null;
      node = p.offsetNode;
      offset = p.offset;
    } else {
      return null;
    }
    const off = _domTextOffset(block, node, offset);
    if (off == null) return null;
    const upto = (block.textContent ?? "").slice(0, off);
    const line = base.start + upto.split("\n").length - 1;
    if (line > base.end) return null;
    const col = off - (upto.lastIndexOf("\n") + 1) + 1;
    return `doc.md#L${line}:C${col}-L${line}:C${col}`; // 零宽点锚点
  }

  /* 列范围锚点 → 块内文本偏移(纯函数 anchorColOffsetsOf 的闭包面;
     行级/零宽/越界 → null(行级锚点不出行内高亮) */
  function anchorColOffsets(anchor) {
    return anchorColOffsetsOf(currentText, anchor);
  }

  /* 行内高亮(v3.1):列范围锚点的文本包 .doc-hl(--live 浅底 + 底部细线,
     Notion 式);按文本偏移 split text nodes 包 span;预览重渲后重挂
     (与 _rehangShells 同周期,refresh 调);行级锚点维持段落左条,不出行内高亮 */
  function _paintHighlights() {
    if (!cur || inst.state.view === "source") return;
    const docu = cur.host.ownerDocument ?? globalThis.document;
    for (const entry of bubbles.values()) {
      const offs = anchorColOffsets(entry.anchor);
      if (!offs) continue;
      const block = [...cur.preview.children].find((c) => c.dataset?.anchor === entry.blockAnchor);
      if (!block?.textContent) continue;
      const existingHl = block.querySelector?.(`.doc-hl[data-anchor="${entry.anchor}"]`);
      if (existingHl) { // 幂等(重挂不叠包);状态随刷(generate 后高亮变色)
        existingHl.dataset.status = entry.inst.state.status ?? "pending";
        continue;
      }
      const walker = docu.createTreeWalker?.(block, 4, _TEXT_FILTER); // 跳过泡壳/标记子树
      if (!walker) continue;
      const cuts = [];
      let acc = 0;
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (typeof n.splitText !== "function") break; // stub 无文本节点操作面:静默
        const len = (n.nodeValue ?? "").length;
        const a = Math.max(offs.offS - acc, 0);
        const b = Math.min(offs.offE - acc, len);
        if (a < b) cuts.push([n, a, b]);
        acc += len;
      }
      for (let i = cuts.length - 1; i >= 0; i--) { // 从后往前包,偏移不失效
        const [n, a, b] = cuts[i];
        const mid = n.splitText(a);
        mid.splitText(b - a);
        const mark = docu.createElement("span");
        mark.className = "doc-hl";
        mark.dataset.anchor = entry.anchor;
        mark.dataset.status = entry.inst.state.status ?? "pending"; // v4:高亮状态样式(淡出/删除线/橙)
        mid.parentNode.replaceChild(mark, mid);
        mark.appendChild(mid);
      }
    }
  }

  /* 标记跟随(v3.1):列范围锚点的 marker 定位在锚点范围**末尾**(末矩形右侧);
     零宽点锚点 → 点位旁;行级锚点 → 行尾(旧语义)。块内坐标,滚动随行。 */
  function _placeMarker(entry) {
    if (!entry.marker || !cur) return;
    const block = [...cur.preview.children].find((c) => c.dataset?.anchor === entry.blockAnchor);
    const bRect = block?.getBoundingClientRect?.();
    if (!bRect) return;
    const hl = [...(block.querySelectorAll?.(".doc-hl") ?? [])].find((e) => e.dataset?.anchor === entry.anchor);
    if (hl) {
      const r = hl.getBoundingClientRect();
      entry.marker.style.right = "auto";
      entry.marker.style.left = `${Math.round(r.right - bRect.left + 4)}px`;
      entry.marker.style.top = `${Math.round(r.bottom - bRect.top - 6)}px`;
      return;
    }
    if (entry.offTop != null) {
      // 点锚点/无高亮:点位旁(offLeft/offTop)
      entry.marker.style.right = "auto";
      entry.marker.style.left = `${Math.round((entry.offLeft ?? 8) + 4)}px`;
      entry.marker.style.top = `${Math.round(Math.max(0, entry.offTop - 14))}px`;
    }
    // 行级锚点:不动(CSS 默认 right:0 行尾)
  }
  /* 块内文本偏移(TreeWalker 累加;跳过泡壳/标记子树——它们挂在块内,
     不剔除会把批注卡文本算进锚点偏移,v3.1 高亮/点锚点实测抓出) */
  const _TEXT_FILTER = (n) =>
    n.parentElement?.closest?.(".doc-bubble-pop,.doc-bubble-marker") ? 2 : 1; // REJECT 子树

  function _domTextOffset(root, node, offset) {
    const docu = root.ownerDocument ?? globalThis.document;
    const walker = docu.createTreeWalker?.(root, 4 /* NodeFilter.SHOW_TEXT */, _TEXT_FILTER);
    if (!walker) return null;
    let acc = 0;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n === node) return acc + offset;
      acc += (n.nodeValue ?? "").length;
    }
    return null;
  }

  function _selectionAnchor(block, sel) {
    const base = parseAnchor(block?.dataset?.anchor ?? "");
    if (!base) return null;
    // 标记语法块(#/-/* /| 前缀)渲染文与源有符号差,列映射不可靠 → 回落行级
    // (普通段落单行/多行 textContent≈源文,可做列级映射)
    const srcFirst = currentText.split("\n")[base.start - 1] ?? "";
    if (/^(#{1,6}\s|\s*[-*]\s|\s*\|)/.test(srcFirst)) return null;
    const range = sel.getRangeAt?.(0);
    if (!range) return null;
    const text = block.textContent ?? "";
    const offS = _domTextOffset(block, range.startContainer, range.startOffset);
    const offE = _domTextOffset(block, range.endContainer, range.endOffset);
    if (offS == null || offE == null || offE <= offS) return null;
    const upS = text.slice(0, offS);
    const upE = text.slice(0, offE);
    const ls = base.start + upS.split("\n").length - 1;
    const le = base.start + upE.split("\n").length - 1;
    if (le > base.end) return null;
    const cs = offS - (upS.lastIndexOf("\n") + 1) + 1;
    const ce = offE - (upE.lastIndexOf("\n") + 1) + 1;
    return `doc.md#L${ls}:C${cs}-L${le}:C${ce}`;
  }

  /* 开气泡(多条并存,各锚点独立;种子 = 持久化消息流,开关不丢;
     同锚点重开 = 聚焦,不重复建。C3:add_child + link_view 进壳;
     view 被控件内 ✕ 摘过时重开先重挂。壳挂交互面 cur.preview。
     v3:point = 右键点(原位浮出,该点旁);quote = 选区原文(列范围锚点)) */
  function openBubble(anchor, blockEl, { point = null, quote = null } = {}) {
    if (!cur) return null;
    const existing = bubbles.get(anchor);
    if (existing) {
      if (!existing.inst.views.length) {
        existing.view = existing.inst.link_view(existing.body, { surface: "tab" });
      }
      existing.el.hidden = false; // 收起着的话先展开(重开 = 聚焦)
      if (existing.marker) existing.marker.hidden = true;
      if (!existing.el.isConnected) _rehangShells(); // F2b:壳被布局重渲摘出时先挂回再定位
      _fitBubble(existing); // v2:重开重算几何
      existing.view?.live?.focus?.();
      renderBubbleBar();
      return existing;
    }
    // compound add_child(段落批注 = 动态子件;canonical 持 state,view 由 link_view 挂)
    // v4:种子 = 单条批注记录;有内容 = 展示态(expanded),无 = 输入态(composing)
    const seed = seedByAnchor[anchor];
    const quoteText = String(seed?.quote ?? quote ?? blockTextOf(anchor) ?? "");
    const anchorState = { member: doc.name, path: anchor, quote: quoteText };
    const bubbleState = {
      anchor: anchorState,
      quote: quoteText,
      content: String(seed?.content ?? ""),
      status: seed?.status ?? "pending",
      createdAt: seed?.createdAt ?? "",
      severity: seed?.severity ?? "",
      generation: seed?.generation ?? null,
      view: seed?.content ? "expanded" : "composing",
      draft: "",
    };
    const bubbleInst = inst.add_child("chat-bubble", {
      slot: anchor,
      state: bubbleState,
      options: { ...bubbleState, triggerPath: `${inst.path}/${anchor}` },
    });
    // 浮出定位壳(宿主职责,W5.4 切割线;v3:壳内不再自带 ✕——与卡面头部
    // ✕/🗑 叠位冲突;收起 = 卡面 ✕(close 事件)/点泡外(v3)/Esc,语义同一)
    const wrap = (cur.host.ownerDocument ?? globalThis.document).createElement("div");
    wrap.className = "doc-bubble-pop";
    wrap.dataset.anchor = anchor;
    const body = (cur.host.ownerDocument ?? globalThis.document).createElement("div");
    body.className = "doc-bubble-body";
    wrap.appendChild(body);
    // v3:壳归位到**块**级锚点(列范围锚点不对应任何块;块内偏移随 point)
    const blockAnchor = blockEl?.dataset?.anchor ?? anchor;
    const parent = [...cur.preview.children].find((c) => c.dataset?.anchor === blockAnchor) ?? blockEl ?? cur.preview;
    parent.appendChild(wrap);
    // Floating UI arrow:真元素(顶替手写 ::before;定位由 arrow middleware 给)
    const arrowEl = (cur.host.ownerDocument ?? globalThis.document).createElement("div");
    arrowEl.className = "doc-bubble-arrow";
    wrap.appendChild(arrowEl);
    const marker = (cur.host.ownerDocument ?? globalThis.document).createElement("button");
    marker.className = "doc-bubble-marker";
    marker.dataset.bubbleMarker = "1";
    marker.dataset.anchor = anchor;
    marker.hidden = true;
    parent.appendChild(marker);
    // v3 原位:point(右键点)给虚拟参考点的块内偏移(间隙由 offset(8) 中间件给);
    // 无 point = 旧语义(参考点落块右上);
    // F1(2026-08-13 验收):左缘 = 点击点块内偏移,只钳 8px 边距——
    // 不再钳"块宽−48"(那会把泡吸到右缘呈固定面板);视口边界交给 shift(padding:16)
    let offTop = null;
    let offLeft = null;
    const bRect = parent.getBoundingClientRect?.();
    if (point && bRect) {
      offTop = Math.max(4, point.y - bRect.top);
      offLeft = Math.max(8, point.x - bRect.left);
    }
    // §5:view 挂进壳(link_view;hard link 视图,挂载点不限 slot 内)
    const view = bubbleInst.link_view(body, { surface: "tab" });
    const entry = { inst: bubbleInst, view, el: wrap, body, marker, anchor, blockAnchor, offTop, offLeft, arrowEl, unfit: null, severity: "" };
    bubbles.set(anchor, entry);
    _fitBubble(entry); // 开泡即定位(Floating UI;widget-libs 试点)
    _paintHighlights(); // v3.1:行内高亮(列范围锚点)
    _rehangShells(); // add_child 触发的是基座内层 relayout(不经我们的 _relayout 包装),
    // 先开的壳会被摘出 DOM——补挂回(一行多泡 v3.1 抓出;幂等)
    _placeMarker(entry); // v3.1:标记跟选段末/点位
    // autoUpdate(开泡期间:文档滚动/窗口 resize/布局位移自动重算——
    // 顶替手工 scroll/resize 监听;隐藏不重算,删除/销毁时摘。
    // stub/无 window 环境静默(vendored 库触摸 window))
    if (typeof window !== "undefined" && parent.getBoundingClientRect) {
      entry.unfit = autoUpdate(entry._refEl ?? parent, entry.el, () => {
        if (!entry.el.hidden) _fitBubble(entry);
      });
    }
    // D4 未读游标退役(v4:批注卡无消息流)——打开即展示,无已读语义
    view.live.focus?.();
    renderBubbleBar();
    return entry;
  }

  /* child_event 监听(§7-1 放行后在此接管):submit → annotations 端点落库
     (P2:save_annotation 路径,无即时 AI 回复);close → 可见性管控(§7-3)。
     注意负载形态:{child: 子件 id(=锚点串), event, payload}——
     id 是字符串不是实例(基座闸门按 rec.id 打包)。 */
  inst.on("child_event", ({ child, event, payload }) => {
    const anchor = typeof child === "string" ? child : (child?.state?.anchor?.path ?? "");
    const entry = bubbles.get(anchor);
    if (!entry) return; // doc 子件(md-viewer)或未知锚点,忽略
    if (event === "submit") {
      _saveAnnotation(entry, payload).catch((err) => {
        console.warn("annotations 保存失败:", err?.message ?? err);
        entry.view?.live?.notifyError?.(err?.message ?? String(err));
      });
      return;
    }
    if (event === "close") {
      if (!entry.inst.state.content && !seedByAnchor[anchor]) {
        _discardBubble(entry, anchor); // 新建取消(裁决 C1):未落库,直接摘除
        return;
      }
      entry.el.hidden = true; // 控件内 ✕ = 收起为段旁标记(live 已自毁)
      if (entry.marker) entry.marker.hidden = false;
      renderBubbleBar();
      return;
    }
    if (event === "delete") {
      _deleteBubble(entry, anchor); // v3:垃圾桶上行 → 摘除 + remove_child + 后端删持久化
    }
  });

  /* 新建取消(C1):未落库的批注直接摘除(不出标记,不碰后端) */
  function _discardBubble(entry, anchor) {
    entry.unfit?.();
    entry.el.remove?.();
    entry.marker?.remove?.();
    bubbles.delete(anchor);
    try {
      inst.remove_child(anchor, { destroy: true });
    } catch {
      /* 已不在子表(防御) */
    }
    renderBubbleBar();
  }

  /* v3:批注删除(控件 delete action → 闸门放行 → 此面):壳/标记摘除,
     compound remove_child(destroy),后端端点删持久化(失败不挡 UI,读面是事实源) */
  async function _deleteBubble(entry, anchor) {
    entry.unfit?.(); // autoUpdate 摘除(widget-libs)
    entry.el.remove?.();
    entry.marker?.remove?.();
    bubbles.delete(anchor);
    renderBubbleBar();
    try {
      inst.remove_child(anchor, { destroy: true });
    } catch {
      /* 已不在子表(防御) */
    }
    try {
      await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/bubbles/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ anchor }),
      });
    } catch {
      /* 删除失败不挡 UI(下一轮读面仍是事实源) */
    }
  }

  /* 批注提交(P2,save_annotation 路径):出海在父级(本组件)——annotations
     端点 upsert;**无即时 AI 回复**(comment 对话链退役,批注攒着等 generate
     批处理)。成功 → 收起成段旁标记(v2.1 §2.1 帧 3);失败 → 控件回输入态 */
  async function _saveAnnotation(entry, { anchor: a, content, quote: q }) {
    const anchorStr = typeof a === "string" ? a : (a?.path ?? entry.anchor);
    try {
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/annotations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          anchor: anchorStr,
          quote: String(q ?? entry.inst.state.quote ?? ""),
          content: String(content ?? ""),
          version: _currentVersionNo(),
          status: "pending",
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const rec = await res.json();
      seedByAnchor[anchorStr] = rec; // 种子同步(重开/迁移面一致)
      entry.inst.state.content = rec.content;
      entry.inst.state.status = rec.status;
      entry.inst.state.createdAt = rec.createdAt ?? "";
      entry.el.hidden = true; // 提交即收起成标记(高亮/标记状态随刷)
      if (entry.marker) entry.marker.hidden = false;
      _paintHighlights();
      _placeMarker(entry);
      renderBubbleBar();
    } catch (err) {
      entry.view?.live?.notifyError?.(err.message ?? String(err)); // 回输入态(草稿恢复)
    }
  }

  /* 当前版本号(annotations.version;doc.versions = vid 列表新→旧) */
  function _currentVersionNo() {
    const n = Number.parseInt(String(doc.versions?.[0] ?? "").slice(1), 10);
    return Number.isFinite(n) ? n : null;
  }

  /* D5 主对话:发送一轮(chat 端点;agent 直接改文档,changed=true → 右侧重拉) */
  async function sendChat() {
    const text = String(cur?.chatInput?.value ?? "").trim();
    if (!text) return;
    messages.push({ role: "user", text });
    if (cur?.chatInput) cur.chatInput.value = "";
    renderChat();
    try {
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const body = await res.json();
      messages.push({ role: "assistant", text: body.reply ?? "" });
      renderChat();
      if (body.changed) {
        _prevTexts.set(doc.name, currentText); // UX 批:暂存旧全文,重挂载后 diff 变化块
        reload?.(); // 文档被改 → 重拉全文重渲右侧(chat.json 已落,对话不丢)
      }
    } catch (err) {
      messages.push({ role: "assistant", text: `(${copy("platform.doc.chat.fail")}: ${err.message ?? err})` });
      renderChat();
    }
  }

  /* "第 N 行"链接化(UX 批):可解析且在文档行数内 → 链接;否则原样(不编造) */
  function linkifyLines(text) {
    const total = currentText.split("\n").length;
    return esc(text ?? "").replace(/第\s*(\d+)\s*行/g, (m, n) => {
      const line = Number(n);
      if (!Number.isInteger(line) || line < 1 || line > total) return m;
      return `<button class="doc-linelink" data-goto-line="${line}">${m}</button>`;
    });
  }

  /* 左栏:主对话渲染(空态 = 系统提示"告诉我你要什么文档",只展示不占流) */
  function renderChat() {
    if (!cur?.chatLog) return;
    const msgs = messages.length
      ? messages
      : [{ role: "system", text: copy("platform.doc.chat.hint") }];
    cur.chatLog.innerHTML = msgs
      .map(
        (m) =>
          `<div class="doc-chat-msg" data-role="${esc(m.role ?? "user")}">${linkifyLines(m.text)}</div>`
      )
      .join("");
  }

  /* 行号链接跳转:滚动到该行所在块并高亮脉冲(1.5s 后摘除) */
  function gotoLine(line) {
    if (!cur) return;
    const block = mdBlocks(currentText).find((b) => b.start <= line && line <= b.end);
    if (!block) return;
    const anchor = `doc.md#L${block.start}-L${block.end}`;
    const el = [...cur.preview.children].find((c) => c.dataset?.anchor === anchor);
    if (!el) return;
    el.scrollIntoView?.();
    el.classList.add("doc-flash");
    setTimeout(() => el.classList.remove("doc-flash"), 1500);
  }

  function renderStatus() {
    if (!cur) return;
    cur.chars.textContent = copy("platform.doc.chars").replace("{n}", String(currentText.length));
    cur.dirtyEl.textContent = dirty ? "●" : "";
    cur.dirtyEl.dataset.on = dirty ? "1" : "0";
  }

  /* 批注列表(UX 批实体化;v4:消息流/未读退役):每条 = 状态点 + 位置(Lx-Ly)
     + 批注内容摘录(前 20 字);点击跳转定位。同步刷新收起标记的状态色环 */
  function renderBubbleBar() {
    const bar = cur?.host.querySelector("[data-doc-bubblebar]");
    if (!bar) return;
    const entries = [...bubbles.values()];
    bar.innerHTML = entries.length
      ? `<div class="doc-bar-title">${esc(copy("platform.doc.bubblebar"))}</div>` +
        entries
          .map((entry) => {
            const st = entry.inst.state;
            const excerpt = String(st.content ?? "").replace(/\s+/g, " ").trim().slice(0, 20);
            return (
              `<button class="doc-bar-item" data-bar-anchor="${esc(entry.anchor)}">` +
              `<span class="doc-sev doc-bar-status" data-status="${esc(st.status ?? "pending")}">●</span>` +
              `<span class="mono">${esc(entry.anchor.replace("doc.md#", ""))}</span>` +
              (excerpt ? `<span class="doc-bar-excerpt">${esc(excerpt)}</span>` : "") +
              `</button>`
            );
          })
          .join("")
      : "";
    // 收起标记的状态色环随渲染同步(v4:未读数退役)
    for (const entry of entries) {
      if (!entry.marker) continue;
      entry.marker.textContent = "💬";
      entry.marker.dataset.status = entry.inst.state.status ?? "pending";
    }
  }

  /* D3 评审流(v4 改写):[评审] → review 端点(后端仍落旧流,兼容)→ 逐条
     upsert 进 annotations(severity 随)→ 重拉读取面同步(新锚点出标记,不弹壳) */
  async function runReview() {
    const btn = cur?.host.querySelector("[data-doc-review]");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/review`, {
        method: "POST",
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const body = await res.json();
      let hung = 0;
      for (const note of body.notes ?? []) {
        if (!DOC_SEVERITIES.includes(note.severity)) continue; // D4:severity 单源校验
        const r = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/annotations`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            anchor: note.anchor,
            quote: blockTextOf(note.anchor),
            content: note.text,
            version: _currentVersionNo(),
            status: "pending",
            severity: note.severity,
          }),
        }).catch(() => null);
        if (r?.ok) hung += 1;
      }
      await _syncAnnotations();
      return hung;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* 种子出标(v4:批注标记持久可见):种子记录逐条建 entry——壳藏 + 标记显
     (不弹壳)。首挂 refresh 与 _syncAnnotations 共用;已建跳过(幂等) */
  function _ensureEntry(rec) {
    if (!rec?.anchor || bubbles.has(rec.anchor)) return null;
    const m = /^doc\.md#L(\d+)/.exec(rec.anchor);
    const blk = m ? mdBlocks(currentText).find((b) => b.start <= Number(m[1]) && Number(m[1]) <= b.end) : null;
    const blockAnchor = blk ? `doc.md#L${blk.start}-L${blk.end}` : null;
    const block = blockAnchor && cur
      ? [...cur.preview.children].find((c) => c.dataset?.anchor === blockAnchor)
      : null;
    const created = openBubble(rec.anchor, block ?? cur?.preview);
    if (created) {
      created.el.hidden = true;
      if (created.marker) created.marker.hidden = false;
    }
    return created ?? null;
  }

  /* 批注读取面同步(P2):重拉 annotations → 种子更新;新锚点建 entry(壳藏 +
     标记显,不弹壳),已有 entry 状态刷新(开着的壳不拽——用户正在看) */
  async function _syncAnnotations() {
    let list;
    try {
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/annotations`);
      if (!res.ok) return;
      list = await res.json();
    } catch {
      return; // 同步失败不挡 UI(下一轮读面仍是事实源)
    }
    for (const rec of list ?? []) seedByAnchor[rec.anchor] = rec;
    for (const rec of list ?? []) {
      const entry = bubbles.get(rec.anchor);
      if (entry) {
        Object.assign(entry.inst.state, {
          quote: rec.quote ?? entry.inst.state.quote,
          content: rec.content ?? entry.inst.state.content,
          status: rec.status ?? entry.inst.state.status,
          severity: rec.severity ?? entry.inst.state.severity,
          generation: rec.generation ?? entry.inst.state.generation,
        });
        continue;
      }
      _ensureEntry(rec);
    }
    _paintHighlights();
    renderBubbleBar();
  }

  function refresh() {
    _relayout(); // compound relayout + 壳挂回 + 行内高亮/标记(均在 _relayout 内)
    renderChat();
    renderStatus();
    for (const rec of Object.values(seedByAnchor)) _ensureEntry(rec); // v4:种子批注出标(幂等)
    renderBubbleBar();
  }

  /* view source(W6.7 用户裁决;C3 化:模式进 compound state.view,layout
     按 state 出块 chrome 或 doc slot——预览 = 块渲染 + 批注锚点(切割线不动),
     源码 = md-viewer 子件的 source 形态;切换不重取数据)。 */
  let _viewMode = "preview";
  let _vmSeg = null; // 实例级 seg(跨挂接复用;挂到交互面工具条)
  const setViewMode = (mode) => {
    _viewMode = mode === "source" ? "source" : "preview";
    for (const b of _vmSeg?.querySelectorAll?.("[data-vm]") ?? []) {
      b.dataset.on = b.dataset.vm === _viewMode ? "1" : "0";
    }
    inst.state.view = _viewMode;
    // doc 子件的 canonical 形态字段同步(md-viewer 的 view 渲染面)
    const docInst = inst.child("doc");
    if (docInst) {
      docInst.state.view = _viewMode;
      docInst.state.source = currentText;
    }
    _relayout(); // preview:块 chrome + 壳挂回;source:doc slot 挂载进占位
  };

  /* host 委托(C4.2:实例级一份,按 viewHost 的 __docEditorBound 幂等绑;
     旧壳 #detailHost 跨 renderDetail 存活语义同前——永远转给最新实例) */
  async function onHostClick(e) {
    if (!cur) return;
    // v4(裁决 C1,覆盖 v3.2 保草稿):点泡外 = 输入态有内容提交/空取消,
    // 展示态收起。注意:泡内控件点击(添加/删除/编辑等)会先重渲卡面把事件
    // 目标摘出 DOM,冒泡到这时 closest(".doc-bubble-pop") 已空——须按控件
    // data 面判内,否则添加键一按就被误收(真实浏览器抓出)
    const _BUBBLE_CTL = ["[data-bubble-send]", "[data-bubble-del]", "[data-bubble-cancel]",
      "[data-bubble-edit]", "[data-bubble-x]", "[data-bubble-draft]"];
    const _inPop = e.target.closest?.(".doc-bubble-pop") ||
      _BUBBLE_CTL.some((sel) => e.target.closest?.(sel)) ||
      e.target.closest?.(".doc-hl"); // v3.1:高亮区 = 重开入口(开泡语义,非"泡外")
    if (!_inPop && !e.target.closest?.("[data-bubble-marker]")) {
      // C1:输入态有内容提交/空取消;展示态收起(submitOrCancel 内部判)
      for (const entry of bubbles.values()) {
        if (!entry.el.hidden) entry.view?.live?.submitOrCancel?.();
      }
    }
    // D5:[发送] → 主对话一轮
    if (e.target.closest("[data-doc-chat-send]")) return sendChat();
    // UX 批:建议 chips(回填并发送)
    const chip = e.target.closest("[data-doc-chip]")?.dataset.docChip;
    if (chip) {
      if (cur.chatInput) cur.chatInput.value = copy(`platform.doc.chip.${chip}`);
      return sendChat();
    }
    // UX 批:引导浮层关闭(一次性,localStorage 记忆)
    if (e.target.closest("[data-doc-intro-close]")) {
      const el = cur.host.querySelector("[data-doc-intro]");
      if (el) el.hidden = true;
      globalThis.localStorage?.setItem?.("doc.introSeen", "1");
      return;
    }
    // UX 批:气泡浮出——壳内 ✕ 已并入卡面头部(v3;close 事件路径),此处无 fold 分支
    // UX 批:段旁标记 → 重新展开(openBubble early-return 记 seen;view 缺时重挂)
    const markerBtn = e.target.closest("[data-bubble-marker]");
    if (markerBtn) {
      const entry = bubbles.get(markerBtn.dataset.anchor);
      if (entry) {
        openBubble(entry.anchor, cur.preview);
        entry.view?.live?.focus?.();
      }
      return;
    }
    // UX 批:"第 N 行"链接 → 滚动定位 + 高亮脉冲
    const goto = e.target.closest("[data-goto-line]")?.dataset.gotoLine;
    if (goto) {
      gotoLine(Number(goto));
      return;
    }
    // D4:导出菜单(开/合;下载走 exportDoc,复制走 clipboard 降级)
    if (e.target.closest("[data-doc-export]")) {
      const menu = cur.host.querySelector("[data-export-menu]");
      if (menu) menu.hidden = !menu.hidden;
      return;
    }
    const exportMode = e.target.closest("[data-export-mode]")?.dataset.exportMode;
    if (exportMode) {
      const tab = getTabInstance?.();
      if (!tab?.instance) return;
      try {
        const res = await fetch(
          `/platform/api/apps/${encodeURIComponent(tab.instance)}/actions/doc.export`,
          { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ surface: "tab", args: {} }) }
        );
        if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
        const body = await res.json();
        if (exportMode === "download") {
          const a = exportDoc({ text: body.text ?? "", filename: body.filename ?? `${doc.name}.md`, doc: cur.host.ownerDocument });
          cur.host.ownerDocument.body?.appendChild?.(a);
          a.click?.(); // 真实浏览器触发下载(stub 里仅生成锚,测试断 href/filename)
          a.remove?.();
        } else {
          const ok = await globalThis.navigator?.clipboard?.writeText?.(body.text ?? "")
            .then(() => true, () => false);
          const toastFn = globalThis.__docToast ?? (() => {});
          toastFn(ok ? copy("platform.doc.copied") : copy("platform.doc.copy.fail"));
        }
      } catch (err) {
        (globalThis.__docToast ?? (() => {}))(err.message ?? String(err));
      }
      const menu = cur.host.querySelector("[data-export-menu]");
      if (menu) menu.hidden = true;
      return;
    }
    // D3:[评审] → review 流(批注集自动挂段)
    if (e.target.closest("[data-doc-review]")) return runReview();
    // D3:批注列表点击 → 跳转开泡(UX 批:+ 高亮脉冲定位;源码态先回预览)
    const barItem = e.target.closest("[data-bar-anchor]");
    if (barItem) {
      const entry = bubbles.get(barItem.dataset.barAnchor);
      if (entry) {
        if (_viewMode === "source") setViewMode("preview");
        const block = [...cur.preview.children].find((c) => c.dataset?.anchor === entry.anchor);
        if (block) {
          block.scrollIntoView?.();
          block.classList.add("doc-flash");
          setTimeout(() => block.classList.remove("doc-flash"), 1500);
        }
        openBubble(entry.anchor, block ?? cur.preview); // D4:跳转 = 重开(early-return 也记 seen)
      }
      return;
    }
    // rewind 两击确认(lab-iterate 同款:第一击武装,第二击才走管道)
    const rw = e.target.closest("[data-doc-rewind]");
    if (rw && rw.dataset.armed !== "1") {
      rw.dataset.armed = "1";
      rw.textContent = copy("platform.doc.rewind.confirm");
      e.stopPropagation?.();
      e.preventDefault?.();
    }
  }

  /* 按 view 绑定(C4.2):交互面(surface≠card)全量——viewseg/contextmenu/
     锚点钮/主对话 Enter/host 委托 + 引导浮层;card 面只渲染不绑(批注
     交互在窗口面,§5 内容面)。 */
  function wireView(viewHost, els, mopts = {}) {
    if (mopts.surface === "card") return;
    // viewseg(实例级元素,随挂接搬进工具条;监听只建一次)
    if (!_vmSeg) {
      _vmSeg = (viewHost.ownerDocument ?? globalThis.document).createElement("span");
      _vmSeg.className = "wd-seg doc-viewseg";
      _vmSeg.innerHTML =
        `<button class="wd-seg-btn" data-vm="preview" data-on="1">${esc(copy("w.md.preview"))}</button>` +
        `<button class="wd-seg-btn" data-vm="source" data-on="0">${esc(copy("w.md.source"))}</button>`;
      _vmSeg.addEventListener("click", (e) => {
        const btn = e.target.closest?.("[data-vm]");
        if (btn) setViewMode(btn.dataset.vm);
      });
    }
    viewHost.querySelector(".doc-toolbar")?.appendChild?.(_vmSeg);
    // UX 批:一次性引导浮层(localStorage 记忆只显一次;关闭在 host 委托)
    const intro = viewHost.querySelector("[data-doc-intro]");
    if (intro) intro.hidden = globalThis.localStorage?.getItem?.("doc.introSeen") === "1";
    // D5/v3/v3.1:右键(contextmenu)——选区在块内 → 行列范围锚点(quote = 选中);
    // 无选区 → caretRangeFromPoint 点锚点(零宽,该点行列);映射不成回落行级;
    // 壳浮在该点旁(块限定 .doc-para,防 .doc-hl 的 data-anchor 抢 closest)
    els.preview.addEventListener("contextmenu", (e) => {
      e.preventDefault?.();
      const block = e.target.closest?.(".doc-para[data-anchor]") ??
        e.target.closest?.("[data-anchor]") ??
        (e.target.dataset?.anchor ? e.target : null);
      if (!block) return;
      const lineAnchor = block.dataset.anchor;
      const sel = (cur.host.ownerDocument ?? globalThis.document).getSelection?.();
      let anchor = lineAnchor;
      let quote = null;
      // F2a(2026-08-13 验收):无效坐标(0,0/非有限——合成事件/过渡态)不当点位;
      // 先按块矩形中心再试一次 caret 映射,仍不成才回落行级(该行已有行级泡则聚焦不新建)
      const px = e.clientX, py = e.clientY;
      const validPt = Number.isFinite(px) && Number.isFinite(py) && (px !== 0 || py !== 0);
      if (sel && !sel.isCollapsed && String(sel).trim()) {
        quote = String(sel);
        anchor = _selectionAnchor(block, sel) ?? lineAnchor; // v3:选区关联(列可选)
      } else {
        if (validPt) anchor = _pointAnchor(block, px, py) ?? lineAnchor; // v3.1:点锚点(零宽)
        if (anchor === lineAnchor) {
          const br = block.getBoundingClientRect?.();
          if (br && br.width > 0 && br.height > 0) {
            anchor = _pointAnchor(block, br.left + br.width / 2, br.top + br.height / 2) ?? lineAnchor;
          }
        }
      }
      openBubble(anchor, block, { point: validPt ? { x: px, y: py } : null, quote });
    });
    // v4 悬停预览(v2.1 §2.3):marker hover 200ms 出 tooltip(内容摘录 + 锚点 +
    // 相对时间);移出即收;开着的泡不出(stub 无 rect 面静默)
    let _tipTimer = null;
    let _tipEl = null;
    const _hideTip = () => {
      if (_tipTimer) {
        clearTimeout(_tipTimer);
        _tipTimer = null;
      }
      _tipEl?.remove?.();
      _tipEl = null;
    };
    viewHost.addEventListener("mouseover", (e) => {
      const m = e.target.closest?.("[data-bubble-marker]");
      if (!m || m.hidden) return;
      const entry = bubbles.get(m.dataset.anchor);
      if (!entry || !entry.el.hidden) return; // 开着的泡不出 tooltip
      if (_tipTimer) clearTimeout(_tipTimer);
      _tipTimer = setTimeout(() => {
        const r = m.getBoundingClientRect?.();
        if (!r || (r.width === 0 && r.height === 0)) return;
        _tipEl?.remove?.();
        const st = entry.inst.state;
        const createdTs = Date.parse(st.createdAt ?? "") / 1000;
        const tip = (viewHost.ownerDocument ?? globalThis.document).createElement("div");
        tip.className = "doc-ann-tip";
        tip.innerHTML =
          `<span class="doc-ann-tip-tx">${esc(String(st.content ?? "").slice(0, 80))}</span>` +
          `<span class="doc-ann-tip-meta"><span class="mono">${esc(entry.anchor.replace("doc.md#", ""))}</span>` +
          `<span>${esc(copy(`w.bubble.status.${st.status ?? "pending"}`))}</span>` +
          (createdTs ? `<span>${esc(relTime(createdTs))}</span>` : "") +
          `</span>`;
        tip.style.left = `${Math.round(r.left)}px`;
        tip.style.top = `${Math.round(r.bottom + 6)}px`;
        (viewHost.ownerDocument ?? globalThis.document).body?.appendChild?.(tip); // fixed 定位,挂 body 防祖先 transform
        _tipEl = tip;
      }, 200);
    });
    viewHost.addEventListener("mouseout", (e) => {
      if (e.target.closest?.("[data-bubble-marker]")) _hideTip();
    });
    // D2:段落锚点钮 → 开/聚焦对应气泡;v3.1:点高亮区 = 重开对应泡
    els.preview.addEventListener("click", (e) => {
      const hl = e.target.closest?.(".doc-hl");
      if (hl?.dataset?.anchor) {
        const block = hl.closest?.(".doc-para[data-anchor]");
        openBubble(hl.dataset.anchor, block);
        return;
      }
      const btn = e.target.closest("[data-anchor-btn]");
      if (!btn) return;
      const block = btn.closest(".doc-para[data-anchor]") ?? btn.closest("[data-anchor]") ?? btn.parentNode;
      const anchor = block?.dataset?.anchor;
      if (anchor) openBubble(anchor, block);
    });
    // D5:主对话输入(Enter 发送;[发送] 钮在 host 委托里)
    els.chatInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault?.();
        sendChat();
      }
    });
    /* host 委托(重挂载幂等):委托只挂一次,永远转给最新实例(否则 N 次挂载 =
       N 个委托,导出菜单被切 N 次:偶数次 = 没切的幽灵 bug,D1-D4 旧码已潜伏) */
    viewHost.__docEditorClick = onHostClick; // 最新实例(重挂载覆盖)
    if (!viewHost.__docEditorBound) {
      viewHost.__docEditorBound = true;
      viewHost.addEventListener("click", (e) => viewHost.__docEditorClick?.(e));
    }
  }

  /* mount_view 重包(C4.2):自包含(无骨架先落 docTabHtml 骨架)+ 按 view
     绑定 + compound layout 进 preview 区。基座 _linkView 经此挂窗口/card 面;
     返回 view 句柄的 detach 只摘本 view(instance/state/闭包不动,§5 hidden)。 */
  const _baseMountView = inst.mount_view.bind(inst);
  inst.mount_view = (viewHost, mopts = {}) => {
    if (!viewHost.querySelector?.("[data-doc-preview]")) {
      viewHost.innerHTML = docTabHtml(doc); // 自包含骨架(旧调用方总带骨架,幂等不动)
    }
    const els = {
      host: viewHost,
      preview: viewHost.querySelector("[data-doc-preview]"),
      chatLog: viewHost.querySelector("[data-doc-chat-log]"),
      chatInput: viewHost.querySelector("[data-doc-chat-input]"),
      chars: viewHost.querySelector("[data-doc-chars]"),
      dirtyEl: viewHost.querySelector("[data-doc-dirty]"),
    };
    wireView(viewHost, els, mopts);
    const view = _baseMountView(els.preview, mopts);
    if (mopts.surface !== "card") cur = els; // 交互面 = 最近非 card 挂接
    refresh();
    return { host: viewHost, detach: () => view.detach() };
  };

  /* v2:窗口 resize → 气泡壳几何重算;实例销毁即摘除(防监听泄漏)——
     widget-libs 起:autoUpdate 接管滚动/resize(_refitBubbles 留作重挂面);
     destroy 时逐泡摘 autoUpdate */
  const _baseDestroy = inst.destroy.bind(inst);
  inst.destroy = () => {
    for (const entry of bubbles.values()) entry.unfit?.();
    _baseDestroy();
  };

  const api = {
    get dirty() {
      return dirty;
    },
    setDirty(v) {
      dirty = Boolean(v);
      renderStatus();
    },
    refresh,
    runReview,
    sendChat, // D5:主对话一轮(测试面;UI 走 [发送]/Enter)
    gotoLine, // UX 批:行号链接跳转(测试面)
    chat: messages, // D5:主对话消息流(测试面)
    bubbles,
    compound: inst, // C3:compound 实例(测试面;children_snapshot/child_event 经此取)
    changedAnchors, // UX 批:本次挂载的变化块锚点集(测试面)
    setText(text) {
      currentText = text ?? "";
      dirty = false;
      inst.state.source = currentText;
      const docInst = inst.child("doc");
      if (docInst) docInst.state.source = currentText; // doc 子件 canonical 同步
      refresh();
    },
    _unregister: unregApp, // app 级 provider 注销面(实例销毁路径留口)
    _rebindAppProvider(newPath) {
      unregApp?.(); // reparent 改址(C4.4):旧 path 注销,新 path 重注
      unregApp = registerContextProvider(newPath, "app", _appProvider);
    },
  };
  return { compound: inst, api };
}

/* 兼容壳(C3/旧壳路径不变):host 带骨架 → 建实例 + 首挂;
   host 无骨架时 mount_view 自包含注入(C4.2 起)。 */
export function mountDocEditor(host, doc, { seedFlows = [], getTabInstance = null, reload = null } = {}) {
  const ed = createDocEditor(doc, { seedFlows, getTabInstance, reload });
  ed.compound.mount_view(host);
  return ed.api;
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

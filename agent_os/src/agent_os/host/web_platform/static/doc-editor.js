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

/* 锚点解析(doc.md#L<start>-L<end> → {start, end};非法 → null) */
export function parseAnchor(anchor) {
  const m = /^doc\.md#L(\d+)-L(\d+)$/.exec(String(anchor ?? ""));
  return m ? { start: Number(m[1]), end: Number(m[2]) } : null;
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

  // D4 未读增量:seen 游标进 compound state(可序列化);localStorage 备份照原
  const _seenKey = (anchor) => `doc.seen.${doc.name}.${anchor}`;
  const _seenGet = (anchor) => {
    if (anchor in (inst.state.seen ?? {})) return inst.state.seen[anchor];
    const raw = globalThis.localStorage?.getItem?.(_seenKey(anchor));
    return raw ? Number(raw) : 0;
  };
  const _seenSet = (anchor, n) => {
    inst.state.seen[anchor] = n;
    globalThis.localStorage?.setItem?.(_seenKey(anchor), String(n));
  };
  // D2:气泡种子(seedFlows = DocStore bubbles/ 事实源;editsMap 存回复的替换建议)
  const seedByAnchor = Object.fromEntries((seedFlows ?? []).map((f) => [f.anchor, f.messages ?? []]));
  const bubbles = new Map(); // anchor → {inst, view, el, body, marker, anchor, severity}
  const editsMap = new Map(); // anchor → edits(回复时的替换建议存证,apply 用)

  /* 交互面(cur,C4.2):最近非 card 挂接的宿主元素集——bubble 壳/工具条/
     批注栏/主对话的归属。多 view 时 card 面只渲染(不绑批注交互),
     交互面唯一;窗口摘 view(最小化)期间渲染写空树无害,重挂即更新。 */
  let cur = null; // {host, preview, chatLog, chatInput, chars, dirtyEl}

  /* 段落原文(cascade widget 级 fragment:锚点段 + 全文) */
  function blockTextOf(anchor) {
    const range = parseAnchor(anchor);
    if (!range) return "";
    const lines = currentText.split("\n");
    return lines.slice(range.start - 1, range.end).join("\n");
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
      const block = [...cur.preview.children].find(
        (c) => c !== entry.el && c !== entry.marker && c.dataset?.anchor === entry.anchor
      );
      (block ?? cur.preview).appendChild(entry.el);
      if (entry.marker) (block ?? cur.preview).appendChild(entry.marker);
    }
  }
  const _baseRelayout = inst.relayout; // 基座 relayout(layout 重渲 + 子 view 重挂)
  const _relayout = () => {
    _baseRelayout();
    _rehangShells();
  };
  inst.relayout = _relayout; // C4.2:外部扇出(desktop 经 live.update)也带壳挂回

  /* 锚点未读(assistant 数 − seen 游标;D4 增量语义) */
  function _unreadOf(entry) {
    const assistant = entry.inst.state.messages.filter((m) => m.role === "assistant").length;
    return Math.max(0, assistant - Math.min(_seenGet(entry.anchor), assistant));
  }

  /* 开气泡(多条并存,各锚点独立;种子 = 持久化消息流,开关不丢;
     同锚点重开 = 聚焦,不重复建。C3:add_child + link_view 进壳;
     view 被控件内 ✕ 摘过时重开先重挂。壳挂交互面 cur.preview) */
  function openBubble(anchor, blockEl) {
    if (!cur) return null;
    const existing = bubbles.get(anchor);
    if (existing) {
      if (!existing.inst.views.length) {
        existing.view = existing.inst.link_view(existing.body, { surface: "tab" });
      }
      existing.el.hidden = false; // 收起着的话先展开(重开 = 聚焦)
      if (existing.marker) existing.marker.hidden = true;
      // D4:重开也记"已读"(seen 游标随聚焦前进)
      _seenSet(anchor, existing.inst.state.messages.filter((m) => m.role === "assistant").length);
      existing.view?.live?.focus?.();
      renderBubbleBar();
      return existing;
    }
    // compound add_child(段落批注 = 动态子件;canonical 持 state,view 由 link_view 挂)
    const anchorState = { member: doc.name, path: anchor, quote: blockTextOf(anchor) };
    const seed = (seedByAnchor[anchor] ?? []).map((m) => ({ role: m.role, text: m.text, ts: m.ts ?? m.at }));
    const bubbleInst = inst.add_child("chat-bubble", {
      slot: anchor,
      state: { anchor: anchorState, messages: seed, open: true, busy: false, draft: "", unread: 0 },
      options: { anchor: anchorState, triggerPath: `${inst.path}/${anchor}`, seedMessages: seed },
    });
    // 浮出定位壳(宿主职责,W5.4 切割线;结构同 D5:fold 首子 + body)
    const wrap = (cur.host.ownerDocument ?? globalThis.document).createElement("div");
    wrap.className = "doc-bubble-pop";
    wrap.dataset.anchor = anchor;
    const fold = (cur.host.ownerDocument ?? globalThis.document).createElement("button");
    fold.className = "doc-bubble-fold";
    fold.dataset.bubbleFold = "1";
    fold.title = copy("platform.doc.fold");
    fold.textContent = "✕";
    wrap.appendChild(fold);
    const body = (cur.host.ownerDocument ?? globalThis.document).createElement("div");
    body.className = "doc-bubble-body";
    wrap.appendChild(body);
    const parent = [...cur.preview.children].find((c) => c.dataset?.anchor === anchor) ?? blockEl ?? cur.preview;
    parent.appendChild(wrap);
    const marker = (cur.host.ownerDocument ?? globalThis.document).createElement("button");
    marker.className = "doc-bubble-marker";
    marker.dataset.bubbleMarker = "1";
    marker.dataset.anchor = anchor;
    marker.hidden = true;
    parent.appendChild(marker);
    // §5:view 挂进壳(link_view;hard link 视图,挂载点不限 slot 内)
    const view = bubbleInst.link_view(body, { surface: "tab" });
    const entry = { inst: bubbleInst, view, el: wrap, body, marker, anchor, severity: "" };
    bubbles.set(anchor, entry);
    // D4:打开即记"已读"(seen 游标 = 当前 assistant 数)
    _seenSet(anchor, bubbleInst.state.messages.filter((m) => m.role === "assistant").length);
    view.live.focus?.();
    renderBubbleBar();
    return entry;
  }

  /* child_event 监听(§7-1 放行后在此接管):submit → comment.send 出海;
     apply → comment.apply 经 action 管道;close → 可见性管控(§7-3)。
     注意负载形态:{child: 子件 id(=锚点串), event, payload}——
     id 是字符串不是实例(基座闸门按 rec.id 打包)。 */
  inst.on("child_event", ({ child, event, payload }) => {
    const anchor = typeof child === "string" ? child : (child?.state?.anchor?.path ?? "");
    const entry = bubbles.get(anchor);
    if (!entry) return; // doc 子件(md-viewer)或未知锚点,忽略
    if (event === "submit") {
      _submitComment(entry, payload).catch((err) => {
        console.warn("comment.send 失败:", err?.message ?? err);
        entry.view?.live?.notifyError?.(err?.message ?? String(err));
      });
      return;
    }
    if (event === "apply") {
      _applyComment(entry, payload).catch(() => {});
      return;
    }
    if (event === "close") {
      entry.el.hidden = true; // 控件内 ✕ = 收起为段旁标记(live 已自毁)
      if (entry.marker) entry.marker.hidden = false;
      _seenSet(anchor, entry.inst.state.messages.filter((m) => m.role === "assistant").length);
      renderBubbleBar();
    }
  });

  /* comment.send(§3 run+cascade):出海在父级(本组件)——专属端点 */
  async function _submitComment(entry, { anchor: a, text, cascade }) {
    const anchorStr = typeof a === "string" ? a : (a?.path ?? entry.anchor);
    try {
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ anchor: anchorStr, text, cascade: cascade.cascade }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const body = await res.json();
      editsMap.set(anchorStr, body.edits ?? []);
      entry.view?.live?.receiveReply?.(body.reply ?? "");
      renderBubbleBar(); // D4:新回复 → 未读增量刷新
    } catch (err) {
      entry.view?.live?.notifyError?.(err.message ?? String(err)); // 失败态(行内红条 + 重试)
    }
  }

  /* comment.apply(§3 endpoint):**人按才落**——replace_text 由回复时存证,
     经 action 管道应用;应用后服务端已 save(.bak),重载 tab 拿新全文 */
  async function _applyComment(entry, { anchor: a }) {
    const anchorStr = typeof a === "string" ? a : (a?.path ?? entry.anchor);
    const replace_text = (editsMap.get(anchorStr) ?? [])[0]?.replace_text;
    const tab = getTabInstance?.();
    if (!replace_text || !tab?.instance) return;
    try {
      const res = await fetch(
        `/platform/api/apps/${encodeURIComponent(tab.instance)}/actions/comment.apply`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ surface: "tab", args: { anchor: anchorStr, replace_text } }),
        }
      );
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      reload?.();
    } catch (err) {
      entry.view?.live?.receiveReply?.(`(${err.message ?? err})`);
    }
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

  /* 批注列表(UX 批实体化;D3 气泡栏演进):每条 = 位置(Lx-Ly)+ 锚段摘录
     (前 20 字)+ 对话条数 + 未读;点击跳转定位。同步刷新收起标记的未读数 */
  function renderBubbleBar() {
    const bar = cur?.host.querySelector("[data-doc-bubblebar]");
    if (!bar) return;
    const entries = [...bubbles.values()];
    bar.innerHTML = entries.length
      ? `<div class="doc-bar-title">${esc(copy("platform.doc.bubblebar"))}</div>` +
        entries
          .map((entry) => {
            const msgs = entry.inst.state.messages;
            const unread = _unreadOf(entry);
            const sev = entry.severity ?? "";
            const excerpt = blockTextOf(entry.anchor).replace(/\s+/g, " ").trim().slice(0, 20);
            return (
              `<button class="doc-bar-item" data-bar-anchor="${esc(entry.anchor)}">` +
              (sev ? `<span class="doc-sev" data-sev="${esc(sev)}">●</span>` : "") +
              `<span class="mono">${esc(entry.anchor.replace("doc.md#", ""))}</span>` +
              (excerpt ? `<span class="doc-bar-excerpt">${esc(excerpt)}</span>` : "") +
              `<span class="pf-dim">${esc(copy("platform.doc.bubblebar.count").replace("{n}", String(msgs.length)))}</span>` +
              (unread ? `<span class="doc-bar-n">${unread}</span>` : "") +
              `</button>`
            );
          })
          .join("")
      : "";
    // 收起标记的未读数随渲染同步
    for (const entry of entries) {
      if (!entry.marker) continue;
      const unread = _unreadOf(entry);
      entry.marker.textContent = unread ? `💬 ${unread}` : "💬";
    }
  }

  /* D3 评审流:[评审] → review 端点 → 批注集自动挂段(severity 着色) */
  async function runReview() {
    const btn = cur?.host.querySelector("[data-doc-review]");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/review`, {
        method: "POST",
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const body = await res.json();
      // 气泡雨:逐条挂段(锚点块在就开泡并注入批注;块不在记挂空)
      let hung = 0;
      for (const note of body.notes ?? []) {
        if (!DOC_SEVERITIES.includes(note.severity)) continue; // D4:severity 单源校验
        const block = cur ? [...cur.preview.children].find((c) => c.dataset?.anchor === note.anchor) : null;
        const entry = openBubble(note.anchor, block ?? cur?.preview);
        if (!entry) continue;
        entry.severity = note.severity;
        entry.el.classList.add(`doc-sev-${note.severity}`);
        entry.view?.live?.receiveReply?.(note.text);
        hung += 1;
      }
      renderBubbleBar();
      return hung;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function refresh() {
    _relayout(); // compound relayout(preview 块 chrome / source slot 进出)+ 壳挂回
    renderChat();
    renderStatus();
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
    // UX 批:气泡浮出——✕ 收起为段旁标记
    const foldBtn = e.target.closest("[data-bubble-fold]");
    if (foldBtn) {
      const wrapEl = foldBtn.closest(".doc-bubble-pop") ?? foldBtn.parentNode;
      const entry = bubbles.get(wrapEl?.dataset?.anchor);
      if (entry) {
        entry.el.hidden = true;
        if (entry.marker) entry.marker.hidden = false;
        renderBubbleBar(); // 标记未读数刷新
      }
      return;
    }
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
    // D5:右键(contextmenu)任意块 → 开 local 气泡(问问题/表达需求,不是主对话)
    els.preview.addEventListener("contextmenu", (e) => {
      e.preventDefault?.();
      const block = e.target.closest?.("[data-anchor]") ??
        (e.target.dataset?.anchor ? e.target : null);
      const anchor = block?.dataset?.anchor;
      if (anchor) openBubble(anchor, block);
    });
    // D2:段落锚点钮 → 开/聚焦对应气泡
    els.preview.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-anchor-btn]");
      if (!btn) return;
      const block = btn.closest("[data-anchor]") ?? btn.parentNode;
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
    seenGet: _seenGet, // D4:seen 游标(测试面)
    seenSet: _seenSet,
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

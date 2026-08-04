/* doc 编辑器挂载(D5,docs/DOC-EDITOR.md §2;两栏重构:左对话 35% / 右展示 65%):
   左 = doc 作用域主对话(chatbot;说"写一篇 X/加一节/按批注改一遍" → chat 端点,
   agent 经 doc.read/doc.edit 直接改文档,changed=true 时右侧重拉重渲);
   右 = 文档展示(mdBlocks 段落块渲染 + 每块 💬 + 右键 contextmenu 开 local 气泡);
   版本下拉/快照/rewind 两击/导出/评审收进右侧顶部极细工具条;大纲/分屏/手写
   编辑面废弃(导航靠滚动+气泡跳转,改文档走对话)。
   段落气泡(D2 §2.1)与气泡栏(D3)/未读增量(D4)管道全部保留:
   提交父级组 §16 cascade 信封出海,回复/应用全经管道与专属端点。
   写动作(snapshot/rewind/export/apply)不在此——全部走 tabAction 管道(§3)。 */

import { copy } from "/static/js/themes.js";
import { mdToHtml, mountBubble } from "/static/js/widgets/index.js";

// 长文档阈值(§2/§7 边界):>200KB 预览截断提示,不炸(展示面只读,无编辑器)
const PREVIEW_LIMIT = 200 * 1024;

/* severity 单源(D4 打磨;与后端 app.py 的 DOC_SEVERITIES 字面一致——
   后端校验集在 app.py,前端类名/copy 在此,两端各一份单一事实源) */
export const DOC_SEVERITIES = ["must", "should", "nit"];

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

export function mountDocEditor(host, doc, { seedFlows = [], getTabInstance = null, reload = null } = {}) {
  const preview = host.querySelector("[data-doc-preview]");
  const chatLog = host.querySelector("[data-doc-chat-log]");
  const chatInput = host.querySelector("[data-doc-chat-input]");
  const chars = host.querySelector("[data-doc-chars]");
  const dirtyEl = host.querySelector("[data-doc-dirty]");
  if (!preview) return null;

  let currentText = doc?.text ?? ""; // 展示面事实源(改文档走对话;changed 后 reload 重拉)
  let dirty = false; // 展示面只读:保留 dirty 状态面(cascade/apply 归态兼容),无手写入口
  // D5 主对话:种子 = chat.json(服务端事实源,开关不丢)
  const messages = (doc?.chat ?? []).map((m) => ({ role: m.role, text: m.text }));
  // D4 未读增量:seen 游标(module Map + localStorage 备份;打开气泡即记"已读")
  const _seen = new Map();
  const _seenKey = (anchor) => `doc.seen.${doc.name}.${anchor}`;
  const _seenGet = (anchor) => {
    if (_seen.has(anchor)) return _seen.get(anchor);
    const raw = globalThis.localStorage?.getItem?.(_seenKey(anchor));
    return raw ? Number(raw) : 0;
  };
  const _seenSet = (anchor, n) => {
    _seen.set(anchor, n);
    globalThis.localStorage?.setItem?.(_seenKey(anchor), String(n));
  };
  // D2:气泡状态(seedFlows = DocStore bubbles/ 事实源;editsMap 存回复的替换建议)
  const seedByAnchor = Object.fromEntries((seedFlows ?? []).map((f) => [f.anchor, f.messages ?? []]));
  const bubbles = new Map(); // anchor → bubble widget
  const editsMap = new Map(); // anchor → edits(回复时的替换建议存证,apply 用)

  /* 段落原文(cascade widget 级 fragment:锚点段 + 全文) */
  function blockTextOf(anchor) {
    const range = parseAnchor(anchor);
    if (!range) return "";
    const lines = currentText.split("\n");
    return lines.slice(range.start - 1, range.end).join("\n");
  }

  /* 开气泡(多条并存,各锚点独立;种子 = 持久化消息流,开关不丢;
     同锚点重开 = 聚焦,不重复建——右键开泡的防重复也走这个 early-return) */
  function openBubble(anchor, blockEl) {
    const existing = bubbles.get(anchor);
    if (existing) {
      // D4:重开也记"已读"(seen 游标随聚焦前进)
      _seenSet(anchor, existing.bubble.state.messages.filter((m) => m.role === "assistant").length);
      renderBubbleBar();
      return existing;
    }
    const bubbleHost = document.createElement("div");
    bubbleHost.dataset.anchor = anchor; // 重渲后按引用挂回(见 renderPreview)
    blockEl.appendChild(bubbleHost);
    const bubble = mountBubble(bubbleHost, {
      anchor: { member: doc.name, path: anchor }, // 引用行展示(成员 · 锚点)
      triggerPath: `/doc/${doc.name}/${anchor}`,
      seedMessages: (seedByAnchor[anchor] ?? []).map((m) => ({ role: m.role, text: m.text })),
      cascadeProviders: [
        {
          prefix: `/doc/${doc.name}`,
          scope: "widget",
          fn: () => ({ anchor, paragraph: blockTextOf(anchor), full_text: currentText }),
        },
        {
          prefix: "/doc",
          scope: "app",
          fn: () => ({ name: doc.name, versions: doc.versions ?? [], dirty }),
        },
      ],
    });
    bubble.on("submit", async ({ anchor: a, text, cascade }) => {      // comment.send(§3 run+cascade):出海在父级(本组件)——专属端点
      const anchorStr = typeof a === "string" ? a : (a?.path ?? "");
      try {
        const res = await fetch(`/platform/api/docs/${encodeURIComponent(doc.name)}/comment`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ anchor: anchorStr, text, cascade: cascade.cascade }),
        });
        if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
        const body = await res.json();
        editsMap.set(anchorStr, body.edits ?? []);
        bubble.receiveReply(body.reply ?? "");
        renderBubbleBar(); // D4:新回复 → 未读增量刷新
      } catch (err) {
        bubble.receiveReply(`(评论助手暂不可用: ${err.message ?? err})`);
      }
    });
    bubble.on("apply", async ({ anchor: a }) => {
      // comment.apply(§3 endpoint):**人按才落**——replace_text 由回复时存证,
      // 经 action 管道应用;应用后服务端已 save(.bak),重载 tab 拿新全文
      const anchorStr = typeof a === "string" ? a : (a?.path ?? "");
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
        bubble.receiveReply(`(${err.message ?? err})`);
      }
    });
    const entry = { bubble, el: bubbleHost, anchor };
    bubbles.set(anchor, entry);
    // D4:打开即记"已读"(seen 游标 = 当前 assistant 数)
    _seenSet(anchor, bubble.state.messages.filter((m) => m.role === "assistant").length);
    bubble.focus();
    renderBubbleBar();
    return entry;
  }

  /* D5 主对话:发送一轮(chat 端点;agent 直接改文档,changed=true → 右侧重拉) */
  async function sendChat() {
    const text = String(chatInput?.value ?? "").trim();
    if (!text) return;
    messages.push({ role: "user", text });
    if (chatInput) chatInput.value = "";
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
      if (body.changed) reload?.(); // 文档被改 → 重拉全文重渲右侧(chat.json 已落,对话不丢)
    } catch (err) {
      messages.push({ role: "assistant", text: `(${copy("platform.doc.chat.fail")}: ${err.message ?? err})` });
      renderChat();
    }
  }

  /* 左栏:主对话渲染(空态 = 系统提示"告诉我你要什么文档",只展示不占流) */
  function renderChat() {
    if (!chatLog) return;
    const msgs = messages.length
      ? messages
      : [{ role: "system", text: copy("platform.doc.chat.hint") }];
    chatLog.innerHTML = msgs
      .map(
        (m) =>
          `<div class="doc-chat-msg" data-role="${esc(m.role ?? "user")}">${esc(m.text ?? "")}</div>`
      )
      .join("");
  }

  function renderPreview() {
    const text = currentText;
    // D5 空态(新建文档):引导文案——"试着在左边输入你的需求。我会为你创建一个文档。"
    if (!text.trim()) {
      preview.innerHTML = `<div class="doc-guide" data-doc-guide="1">${esc(copy("platform.doc.guide"))}</div>`;
      return;
    }
    const limited = text.length > PREVIEW_LIMIT;
    const blocks = mdBlocks(limited ? text.slice(0, PREVIEW_LIMIT) : text);
    preview.innerHTML =
      (limited
        ? `<div class="pf-warnline">${esc(copy("platform.doc.truncate"))}</div>`
        : "") +
      blocks
        .map(
          (b) =>
            `<div class="doc-para" data-anchor="doc.md#L${b.start}-L${b.end}">` +
            `<button class="doc-anchor-btn" data-anchor-btn="1" aria-label="${esc(copy("w.bubble.ph"))}">💬</button>` +
            mdToHtml(b.text) +
            `</div>`
        )
        .join("");
    // innerHTML 重渲会把气泡宿主摘出 DOM——按引用挂回对应块(气泡不重建,
    // 消息流/未读都在;§2.1 多条并存 + 开关不丢的双保险)
    for (const entry of bubbles.values()) {
      const block = [...preview.children].find(
        (c) => c !== entry.el && c.dataset?.anchor === entry.anchor
      );
      (block ?? preview).appendChild(entry.el);
    }
  }

  function renderStatus() {
    chars.textContent = copy("platform.doc.chars").replace("{n}", String(currentText.length));
    dirtyEl.textContent = dirty ? "●" : "";
    dirtyEl.dataset.on = dirty ? "1" : "0";
  }

  /* D3 气泡栏:聚合视图(锚点/severity/未读增量,点击跳转开泡;D5 起是右栏底部横条) */
  function renderBubbleBar() {
    const bar = host.querySelector("[data-doc-bubblebar]");
    if (!bar) return;
    const entries = [...bubbles.values()];
    bar.innerHTML = entries.length
      ? `<div class="doc-bar-title">${esc(copy("platform.doc.bubblebar"))}</div>` +
        entries
          .map((entry) => {
            const assistant = entry.bubble.state.messages.filter((m) => m.role === "assistant").length;
            const unread = Math.max(0, assistant - Math.min(_seenGet(entry.anchor), assistant)); // D4:上次已读增量
            const sev = entry.severity ?? "";
            return (
              `<button class="doc-bar-item" data-bar-anchor="${esc(entry.anchor)}">` +
              (sev ? `<span class="doc-sev" data-sev="${esc(sev)}">●</span>` : "") +
              `<span class="mono">${esc(entry.anchor.replace("doc.md#", ""))}</span>` +
              (unread ? `<span class="doc-bar-n">${unread}</span>` : "") +
              `</button>`
            );
          })
          .join("")
      : "";
  }

  /* D3 评审流:[评审] → review 端点 → 批注集自动挂段(severity 着色) */
  async function runReview() {
    const btn = host.querySelector("[data-doc-review]");
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
        const block = [...preview.children].find((c) => c.dataset?.anchor === note.anchor);
        const entry = openBubble(note.anchor, block ?? preview);
        entry.severity = note.severity;
        entry.el.classList.add(`doc-sev-${note.severity}`);
        entry.bubble.receiveReply(note.text);
        hung += 1;
      }
      renderBubbleBar();
      return hung;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function refresh() {
    renderPreview();
    renderChat();
    renderStatus();
    renderBubbleBar();
  }

  // D5:右键(contextmenu)任意块 → 开 local 气泡(问问题/表达需求,不是主对话)
  preview.addEventListener("contextmenu", (e) => {
    e.preventDefault?.();
    const block = e.target.closest?.("[data-anchor]") ??
      (e.target.dataset?.anchor ? e.target : null);
    const anchor = block?.dataset?.anchor;
    if (anchor) openBubble(anchor, block);
  });
  // D2:段落锚点钮 → 开/聚焦对应气泡
  preview.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-anchor-btn]");
    if (!btn) return;
    const block = btn.closest("[data-anchor]") ?? btn.parentNode;
    const anchor = block?.dataset?.anchor;
    if (anchor) openBubble(anchor, block);
  });
  // D5:主对话输入(Enter 发送;[发送] 钮在 host 委托里)
  chatInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault?.();
      sendChat();
    }
  });
  /* host 委托(重挂载幂等):#detailHost 跨 renderDetail 存活,innerHTML 重渲
     不摘旧监听——委托只挂一次,永远转给最新实例(否则 N 次挂载 = N 个委托,
     导出菜单被切 N 次:偶数次 = 没切的幽灵 bug,D1-D4 旧码已潜伏) */
  async function onHostClick(e) {
    // D5:[发送] → 主对话一轮
    if (e.target.closest("[data-doc-chat-send]")) return sendChat();
    // D4:导出菜单(开/合;下载走 exportDoc,复制走 clipboard 降级)
    if (e.target.closest("[data-doc-export]")) {
      const menu = host.querySelector("[data-export-menu]");
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
          const a = exportDoc({ text: body.text ?? "", filename: body.filename ?? `${doc.name}.md`, doc: host.ownerDocument });
          host.ownerDocument.body?.appendChild?.(a);
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
      const menu = host.querySelector("[data-export-menu]");
      if (menu) menu.hidden = true;
      return;
    }
    // D3:[评审] → review 流(批注集自动挂段)
    if (e.target.closest("[data-doc-review]")) return runReview();
    // D3:气泡栏点击 → 跳转开泡
    const barItem = e.target.closest("[data-bar-anchor]");
    if (barItem) {
      const entry = bubbles.get(barItem.dataset.barAnchor);
      if (entry) {
        const block = [...preview.children].find((c) => c.dataset?.anchor === entry.anchor);
        if (block) block.scrollIntoView?.();
        openBubble(entry.anchor, block ?? preview); // D4:跳转 = 重开(early-return 也记 seen)
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
  host.__docEditorClick = onHostClick; // 最新实例(重挂载覆盖)
  if (!host.__docEditorBound) {
    host.__docEditorBound = true;
    host.addEventListener("click", (e) => host.__docEditorClick?.(e));
  }

  refresh();
  return {
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
    chat: messages, // D5:主对话消息流(测试面)
    bubbles,
    seenGet: _seenGet, // D4:seen 游标(测试面)
    seenSet: _seenSet,
    setText(text) {
      currentText = text ?? "";
      dirty = false;
      refresh();
    },
  };
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* doc 编辑器挂载(D1,docs/DOC-EDITOR.md §2;只组合既有控件,不新造基础件):
   左 W-text(mono,选区保留)/ 右 W-md 实时预览、大纲树(点击滚动定位)、
   dirty 追踪 + 状态栏(字数/dirty ●/视图 toggle/版本 rewind 两击确认);
   D2(§2.1):预览按**段落块**渲染(块 = 空行分块,标题/列表项/表格行独占),
   每块带 💬 锚点钮(anchor = doc.md#L<start>-L<end>),点开 W-bubble——
   提交父级组 §16 cascade 信封出海,回复/应用全经管道与专属端点。
   写动作(save/snapshot/rewind/export)不在此——全部走 tabAction 管道(§3)。 */

import { copy } from "/static/js/themes.js";
import { mdToHtml, mountBubble, mountTextEditor } from "/static/js/widgets/index.js";
import { parseOutline } from "./details.js";

// 长文档阈值(§2/§7 边界):>200KB 预览截断提示,不炸(编辑器本体不受影响)
const PREVIEW_LIMIT = 200 * 1024;

/* 段落块切分(D2 §2.1;与大纲解析同源,1-based 行号区间):
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

export function mountDocEditor(host, doc, { seedFlows = [], getTabInstance = null, reload = null, onViewChange = null } = {}) {
  const textarea = host.querySelector("[data-doc-text]");
  const preview = host.querySelector("[data-doc-preview]");
  const outline = host.querySelector("[data-doc-outline]");
  const chars = host.querySelector("[data-doc-chars]");
  const dirtyEl = host.querySelector("[data-doc-dirty]");
  const cols = host.querySelector(".doc-cols");
  if (!textarea) return null;

  textarea.value = doc?.text ?? "";
  // aria 兜底 + host 适配(虚拟 DOM 的 region 按选择器字符串缓存——"textarea" 与
  // "[data-doc-text]" 是两个对象;W-text 的 host 只消费这四个面,包一层即真实)
  if (!textarea.getAttribute?.("aria-label")) {
    textarea.setAttribute?.("aria-label", copy("platform.doc.text"));
  }
  const textHost = {
    querySelector: (sel) => (sel === "textarea" ? textarea : null),
    addEventListener: (t, fn) => textarea.addEventListener?.(t, fn),
    appendChild: (c) => host.appendChild?.(c),
    ownerDocument: host.ownerDocument,
    dataset: { variant: "mono" },
  };
  const editor = mountTextEditor(textHost, {});
  let dirty = false;
  // D2:气泡状态(seedFlows = DocStore bubbles/ 事实源;editsMap 存回复的替换建议)
  const seedByAnchor = Object.fromEntries((seedFlows ?? []).map((f) => [f.anchor, f.messages ?? []]));
  const bubbles = new Map(); // anchor → bubble widget
  const editsMap = new Map(); // anchor → edits(回复时的替换建议存证,apply 用)

  /* 段落原文(cascade widget 级 fragment:锚点段 + 全文) */
  function blockTextOf(anchor) {
    const range = parseAnchor(anchor);
    if (!range) return "";
    const lines = textarea.value.split("\n");
    return lines.slice(range.start - 1, range.end).join("\n");
  }

  /* 开气泡(多条并存,各锚点独立;种子 = 持久化消息流,开关不丢) */
  function openBubble(anchor, blockEl) {
    const existing = bubbles.get(anchor);
    if (existing) return existing.bubble;
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
          fn: () => ({ anchor, paragraph: blockTextOf(anchor), full_text: textarea.value }),
        },
        {
          prefix: "/doc",
          scope: "app",
          fn: () => ({ name: doc.name, versions: doc.versions ?? [], dirty }),
        },
      ],
    });
    bubble.on("submit", async ({ anchor: a, text, cascade }) => {
      // comment.send(§3 run+cascade):出海在父级(本组件)——专属端点
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
    bubbles.set(anchor, { bubble, el: bubbleHost, anchor });
    bubble.focus();
    return bubble;
  }

  function renderPreview() {
    const text = textarea.value;
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

  function renderOutline() {
    const items = parseOutline(textarea.value);
    outline.innerHTML = items.length
      ? items
          .map(
            (h) =>
              `<button class="doc-outline-item" data-offset="${h.offset}" style="--ns-depth:${h.level - 1}">` +
              `${esc(h.text)}</button>`
          )
          .join("")
      : `<div class="pf-dim">—</div>`;
  }

  function renderStatus() {
    chars.textContent = copy("platform.doc.chars").replace("{n}", String(textarea.value.length));
    dirtyEl.textContent = dirty ? "●" : "";
    dirtyEl.dataset.on = dirty ? "1" : "0";
  }

  function refresh() {
    renderPreview();
    renderOutline();
    renderStatus();
  }

  textarea.addEventListener("input", () => {
    dirty = true;
    refresh();
  });
  textarea.addEventListener("keydown", (e) => {
    // Ctrl/Cmd+S = 保存(与状态栏 [保存] 同一管道按钮)
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      host.querySelector('[data-tab-act="doc.save"]')?.click?.();
    }
  });
  outline.addEventListener("click", (e) => {
    const item = e.target.closest("[data-offset]");
    if (!item) return;
    // 滚动定位:选区跳到标题偏移(W-text 选区语义;滚动由浏览器 reveal)
    const offset = Number(item.dataset.offset);
    textarea.focus();
    textarea.selectionStart = offset;
    textarea.selectionEnd = offset;
  });
  // D2:段落锚点钮 → 开/聚焦对应气泡
  preview.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-anchor-btn]");
    if (!btn) return;
    const block = btn.closest("[data-anchor]") ?? btn.parentNode;
    const anchor = block?.dataset?.anchor;
    if (anchor) openBubble(anchor, block);
  });
  host.addEventListener("click", (e) => {
    const mode = e.target.closest("[data-view-mode]")?.dataset.viewMode;
    if (mode) {
      cols.dataset.view = mode; // 分屏 toggle(edit/preview/split;CSS 驱动)
      host.querySelectorAll("[data-view-mode]").forEach((b) => {
        b.dataset.on = b.dataset.viewMode === mode ? "1" : "0";
      });
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
  });

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
    editor,
    setText(text) {
      textarea.value = text ?? "";
      dirty = false;
      refresh();
    },
  };
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

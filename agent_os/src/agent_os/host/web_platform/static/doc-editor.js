/* doc 编辑器挂载(D1,docs/DOC-EDITOR.md §2;只组合既有控件,不新造基础件):
   左 W-text(mono,选区保留)/ 右 W-md 实时预览、大纲树(点击滚动定位)、
   dirty 追踪 + 状态栏(字数/dirty ●/视图 toggle/版本 rewind 两击确认)。
   写动作(save/snapshot/rewind/export)不在此——全部走 tabAction 管道(§3)。 */

import { copy } from "/static/js/themes.js";
import { mdToHtml, mountTextEditor } from "/static/js/widgets/index.js";
import { parseOutline } from "./details.js";

// 长文档阈值(§2/§7 边界):>200KB 预览截断提示,不炸(编辑器本体不受影响)
const PREVIEW_LIMIT = 200 * 1024;

export function mountDocEditor(host, doc, { initialDirty = false } = {}) {
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
  let dirty = initialDirty;

  function renderPreview() {
    const text = textarea.value;
    if (text.length > PREVIEW_LIMIT) {
      preview.innerHTML =
        `<div class="pf-warnline">${esc(copy("platform.doc.truncate"))}</div>` +
        mdToHtml(text.slice(0, PREVIEW_LIMIT));
    } else {
      preview.innerHTML = mdToHtml(text);
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

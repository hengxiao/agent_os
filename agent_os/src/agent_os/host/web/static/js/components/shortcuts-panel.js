/* `?` 快捷键面板(docs/WEB-UI.md §5):居中 Modal,列出全部快捷键(分组:全局 / Workbench)。
   Esc / 遮罩点击关闭;打开关闭均做焦点管理。SHORTCUTS 为纯数据(node 单测可载)。 */

import { esc } from "../util.js";

/* ── 快捷键注册表(纯数据;§5 键盘规范)────────────────────── */
export const SHORTCUTS = [
  { scope: "全局", keys: "/", desc: "聚焦当前页搜索框" },
  { scope: "全局", keys: "?", desc: "打开本快捷键面板" },
  { scope: "全局", keys: "Esc", desc: "关闭一切浮层" },
  { scope: "全局", keys: "⌘K / Ctrl+K", desc: "命令条(输入框聚焦时也可用)" },
  { scope: "Workbench", keys: "j / k", desc: "下 / 上一条信号" },
  { scope: "Workbench", keys: "gg / G", desc: "首条 / 末条信号" },
  { scope: "Workbench", keys: "⌘J / Ctrl+J", desc: "定位首个错误(异常 run)" },
  { scope: "命令条", keys: "↑ / ↓", desc: "移动高亮" },
  { scope: "命令条", keys: "Enter", desc: "执行高亮命令" },
];

export function openShortcutsPanel({ doc = document } = {}) {
  const prevFocus = doc.activeElement;
  const overlay = doc.createElement("div");
  overlay.className = "modal-overlay sk-overlay";
  const groups = [...new Set(SHORTCUTS.map((s) => s.scope))];
  overlay.innerHTML =
    `<div class="modal sk-modal" role="dialog" aria-modal="true" aria-label="快捷键">` +
    `<div class="modal-head">` +
    `<span class="modal-title">快捷键</span>` +
    `<button class="icon-btn sk-close" aria-label="关闭">✕</button>` +
    `</div>` +
    `<div class="modal-body sk-body">` +
    groups
      .map(
        (g) =>
          `<div class="sk-group">` +
          `<div class="sk-group-title">${esc(g)}</div>` +
          SHORTCUTS.filter((s) => s.scope === g)
            .map(
              (s) =>
                `<div class="sk-row"><kbd class="sk-keys">${esc(s.keys)}</kbd>` +
                `<span class="sk-desc">${esc(s.desc)}</span></div>`)
            .join("") +
          `</div>`)
      .join("") +
    `</div></div>`;

  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    doc.removeEventListener("keydown", onKeydown);
    overlay.remove();
    prevFocus?.focus?.(); // 焦点管理:还原打开前焦点
  }
  function onKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault?.();
      close();
    }
  }
  overlay.querySelector(".sk-close").addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close(); // 遮罩点击关闭
  });
  doc.addEventListener("keydown", onKeydown);
  doc.body.appendChild(overlay);
  overlay.querySelector(".sk-close").focus();
  return { close };
}

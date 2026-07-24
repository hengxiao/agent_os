/* ⌘K 命令条(WEB-UI.md §5 键盘):居中 Modal,顶部输入 + fuzzy 过滤列表,
   ↑/↓ 移动高亮,Enter 执行,Esc / 遮罩点击关闭;打开聚焦输入,关闭还原焦点。
   命令可见性(Stop 仅 running / Resume·定位首个错误 仅异常 run)由调用方
   在打开前按上下文过滤,本组件只渲染收到的命令集。

   纯数据 + 纯函数(不碰 DOM,node 单测可载):
     COMMANDS                      命令注册表(§5:New Run / Stop / Resume / Reload /
                                   跳 Runs / 跳 Skills / 跳 Tools / 定位首个错误)
     fuzzyScore(text, query)       子序列匹配打分(null = 不匹配;连续/词首/靠前命中加权)
     fuzzyFilter(commands, query)  命令集 fuzzy 过滤 + 按分排序(空 query 原序返回) */

import { esc } from "../util.js";

/* ── 命令注册表(§5;glyph = 列表字母章,keys = 展示用快捷键)── */
export const COMMANDS = [
  { id: "new-run", title: "New Run", hint: "发起新运行", glyph: "+" },
  { id: "stop", title: "Stop", hint: "中止当前 run(不可逆)", glyph: "■" },
  { id: "resume", title: "Resume", hint: "从 checkpoint 恢复当前 run", glyph: "▶" },
  { id: "reload-skills", title: "Reload Skills", hint: "热重载技能文件", glyph: "↻" },
  { id: "goto-runs", title: "跳 Runs", hint: "运行列表", glyph: "R" },
  { id: "goto-skills", title: "跳 Skills", hint: "技能浏览器", glyph: "S" },
  { id: "goto-tools", title: "跳 Tools", hint: "工具浏览器", glyph: "T" },
  { id: "jump-error", title: "定位首个错误", hint: "RCA 一键定位首个错误", glyph: "✖", keys: "⌘J" },
];

/* ── fuzzy 过滤 ─────────────────────────────────────────────
   子序列(fzf 风格)匹配:query 每个字符按序在 text 中找到即匹配;
   打分 = 基础 1/字符 + 连续命中 streak×2 + 词首(串首或分隔符后)+ 3,
   首个命中位置每靠后一位 -0.1(短前缀优先)。 */
export function fuzzyScore(text, query) {
  const t = String(text ?? "").toLowerCase();
  const q = String(query ?? "").toLowerCase().trim();
  if (!q) return 0;
  let score = 0;
  let ti = 0;
  let streak = 0;
  let firstHit = -1;
  for (let qi = 0; qi < q.length; qi += 1) {
    const ch = q[qi];
    let found = -1;
    for (let i = ti; i < t.length; i += 1) {
      if (t[i] === ch) {
        found = i;
        break;
      }
    }
    if (found < 0) return null;
    if (firstHit < 0) firstHit = found;
    streak = found === ti ? streak + 1 : 0; // ti = 上一命中 +1:相等即连续命中
    const wordHead = found === 0 || /[\s\-_/.:]/.test(t[found - 1]);
    score += 1 + streak * 2 + (wordHead ? 3 : 0);
    ti = found + 1;
  }
  return score - firstHit * 0.1;
}

export function fuzzyFilter(commands, query) {
  const list = Array.isArray(commands) ? commands : [];
  const q = String(query ?? "").trim();
  if (!q) return [...list];
  return list
    .map((cmd) => ({
      cmd,
      score: Math.max(
        ...[cmd.title, cmd.hint, cmd.id].map((field) => fuzzyScore(field, q) ?? Number.NEGATIVE_INFINITY)),
    }))
    .filter((x) => x.score > Number.NEGATIVE_INFINITY)
    .sort((a, b) => b.score - a.score || String(a.cmd.title).localeCompare(String(b.cmd.title)))
    .map((x) => x.cmd);
}

/* ── DOM(唯一碰 DOM 的部分)──────────────────────────────────
   openCommandPalette({ commands, onPick, doc? }) → { close }。 */
export function openCommandPalette({ commands = [], onPick, doc = document } = {}) {
  const prevFocus = doc.activeElement;
  const overlay = doc.createElement("div");
  overlay.className = "modal-overlay cp-overlay";
  overlay.innerHTML =
    `<div class="modal cp-modal" role="dialog" aria-modal="true" aria-label="命令条">` +
    `<div class="cp-input-row">` +
    `<span class="cp-glyph" aria-hidden="true">⌘K</span>` +
    `<input class="input cp-input" type="text" placeholder="输入命令…" aria-label="过滤命令">` +
    `</div>` +
    `<div class="cp-list" role="listbox" aria-label="命令列表"></div>` +
    `</div>`;
  const input = overlay.querySelector(".cp-input");
  const list = overlay.querySelector(".cp-list");
  const state = { query: "", active: 0, closed: false };

  const visible = () => fuzzyFilter(commands, state.query);

  function render() {
    const rows = visible();
    if (state.active >= rows.length) state.active = Math.max(0, rows.length - 1);
    list.innerHTML = rows.length
      ? rows
          .map(
            (c, i) =>
              `<div class="cp-item${i === state.active ? " is-active" : ""}" data-cmd="${esc(c.id)}"` +
              ` role="option" aria-selected="${i === state.active}">` +
              `<span class="cp-item-glyph" aria-hidden="true">${esc(c.glyph ?? "?")}</span>` +
              `<span class="cp-item-title">${esc(c.title)}</span>` +
              `<span class="cp-item-hint">${esc(c.hint ?? "")}</span>` +
              (c.keys ? `<kbd class="cp-keys">${esc(c.keys)}</kbd>` : "") +
              `</div>`)
          .join("")
      : `<div class="cp-empty">无匹配命令</div>`;
  }

  function pick(index) {
    const cmd = visible()[index];
    if (!cmd) return;
    close();
    onPick?.(cmd.id);
  }

  function close() {
    if (state.closed) return;
    state.closed = true;
    doc.removeEventListener("keydown", onKeydown);
    overlay.remove();
    prevFocus?.focus?.(); // 焦点管理:还原打开前焦点
  }

  function onKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault?.();
      close();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault?.();
      const n = visible().length;
      if (n) {
        state.active = (state.active + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
        render();
      }
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault?.();
      pick(state.active);
    }
  }

  input.addEventListener("input", () => {
    state.query = input.value;
    state.active = 0;
    render();
  });
  list.addEventListener("click", (e) => {
    const item = e.target.closest?.("[data-cmd]");
    if (item && overlay.contains(item)) {
      const idx = visible().findIndex((c) => c.id === item.dataset.cmd);
      pick(idx < 0 ? 0 : idx);
    }
  });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close(); // 遮罩点击关闭
  });
  doc.addEventListener("keydown", onKeydown);

  doc.body.appendChild(overlay);
  render();
  input.focus(); // 焦点管理:打开聚焦输入
  return { close, isClosed: () => state.closed };
}

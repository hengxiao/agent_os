/* W-diff — diff 查看器(docs/WIDGETS.md §2;iterate diff、版本对比)。

   state{left, right, mode: "split"|"unified"};actions 全 local:set_mode;
   细节:字段两列 + prompt 红绿行(**split 与既有 diff 呈现逐字节一致**——
   提取不改语义,cards.js 的 diffCard 已换用本模块的 diffBodyHtml);
   unified = 单列新旧堆叠 + same 行折叠上下文([+n] 展开钮)。 */

import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const DIFF_VIEWER_DEF = registerWidgetDef({
  kind: "diff-viewer",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { mode: "split" },
  actions: [{ id: "set_mode", exec: "local", args_input: { mode: { type: "string" } } }],
  events: ["change"],
  aria: { role: "group", keys: [] },
  surfaces: ["card", "tab"],
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* split 两列(与 cards.js diffCard 原输出同构:pf-dmember/pf-twocol/pf-dline) */
function _splitMemberHtml(m) {
  const fields = (m.fields ?? [])
    .map(
      (f) =>
        `<div class="pf-twocol" data-kind="${esc(f.kind)}">` +
        `<div>${esc(JSON.stringify(f.old) ?? "—")}</div><div>${esc(JSON.stringify(f.new) ?? "—")}</div></div>`
    )
    .join("");
  const lines = (m.prompt_diff ?? [])
    .filter((l) => l.kind !== "same")
    .map(
      (l) =>
        `<div class="pf-dline" data-kind="${esc(l.kind)}">${l.kind === "add" ? "+" : "-"} ${esc(l.text)}</div>`
    )
    .join("");
  return (
    `<div class="pf-dmember"><span class="mono">${esc(m.member)}</span>` +
    `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
    fields + lines + `</div>`
  );
}

/* unified 单列(旧上新下;same 行折叠上下文,默认收起,[+n] 展开) */
function _unifiedMemberHtml(m, expanded) {
  const fields = (m.fields ?? [])
    .map(
      (f) =>
        `<div class="wd-diff-field" data-kind="${esc(f.kind)}">` +
        `<div class="wd-diff-old">${esc(JSON.stringify(f.old) ?? "—")}</div>` +
        `<div class="wd-diff-new">${esc(JSON.stringify(f.new) ?? "—")}</div></div>`
    )
    .join("");
  const key = m.member ?? "";
  const open = expanded?.has?.(key);
  const parts = [];
  let sameRun = 0;
  const flush = () => {
    if (!sameRun) return;
    parts.push(
      open
        ? `<div class="wd-diff-same" data-kind="same">${sameRun} 行未变(上下文中)</div>`
        : `<button class="wd-diff-fold" data-fold="${esc(key)}">+${sameRun} 行未变</button>`
    );
    sameRun = 0;
  };
  for (const l of m.prompt_diff ?? []) {
    if (l.kind === "same") {
      if (open) {
        flush();
        parts.push(`<div class="pf-dline" data-kind="same">  ${esc(l.text)}</div>`);
      } else {
        sameRun += 1;
      }
      continue;
    }
    flush();
    parts.push(
      `<div class="pf-dline" data-kind="${esc(l.kind)}">${l.kind === "add" ? "+" : "-"} ${esc(l.text)}</div>`
    );
  }
  flush();
  return (
    `<div class="pf-dmember"><span class="mono">${esc(key)}</span>` +
    `<span class="lab-pkg-status" data-status="${esc(m.status)}">${esc(m.status)}</span>` +
    fields + parts.join("") + `</div>`
  );
}

/* diff 本体渲染(mode 分发;split = 既有呈现,unified = 折叠上下文单列) */
export function diffBodyHtml(diff, { mode = "split", expanded = null } = {}) {
  const members = (diff?.members ?? [])
    .map((m) => (mode === "unified" ? _unifiedMemberHtml(m, expanded) : _splitMemberHtml(m)))
    .join("");
  return members || "";
}

/* 挂进宿主:diff(与 cards/lab-iterate 同构的 diff 对象);set_mode 切换重渲 */
export function mountDiffViewer(host, { diff, mode = "split", path = "", onRegister = null, onUnregister = null } = {}) {
  const widget = createWidget(DIFF_VIEWER_DEF, {
    path,
    state: { left: diff, right: null, mode },
    onRegister,
    onUnregister,
  });
  const expanded = new Set();

  function render() {
    host.innerHTML =
      `<div class="wd-diff" data-mode="${esc(widget.state.mode)}">` +
      `<div class="wd-diff-modes">` +
      ["split", "unified"]
        .map(
          (m) =>
            `<button class="wd-mode" data-mode="${m}"${widget.state.mode === m ? ' data-on="1"' : ""}>${m}</button>`
        )
        .join("") +
      `</div>` +
      diffBodyHtml(widget.state.left, { mode: widget.state.mode, expanded }) +
      `</div>`;
  }

  widget.set_mode = (mode) => {
    if (mode !== "split" && mode !== "unified") return;
    widget.state.mode = mode;
    render();
    widget.emit("change", { mode });
  };

  host.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (btn) return widget.set_mode(btn.dataset.mode);
    const fold = e.target.closest("[data-fold]");
    if (fold) {
      expanded.add(fold.dataset.fold);
      render(); // 折叠上下文展开(§2 长文本折叠)
    }
  });

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

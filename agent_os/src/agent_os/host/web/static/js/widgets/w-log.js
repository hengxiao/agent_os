/* W-log — 日志/终端查看器(docs/WIDGETS.md §2;run 输出、trace、调试控制台)。

   state{lines: [{kind, text}], follow, filter};
   actions 全 local:append/toggle_follow/filter/copy_all;
   细节:**跟随模式**(follow 时新行自动滚底;用户上滚即暂停跟随并显示
   "回到底部"钮,点了恢复)、长窗口截断(保留尾部 N 行)、kind 着色
   (信号色 token,data-kind 驱动)、复制全部。 */

import { copy } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { createWidget } from "./widget.js";

export const LOG_VIEWER_DEF = registerWidgetDef({
  kind: "log-viewer",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { lines: [], follow: true, filter: "" },
  actions: [
    { id: "append", exec: "local" },
    { id: "toggle_follow", exec: "local" },
    { id: "filter", exec: "local", args_input: { text: { type: "string" } } },
    { id: "copy_all", exec: "local" },
  ],
  events: ["change", "copy"],
  aria: { role: "log", keys: [] },
  surfaces: ["card", "tab"],
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* 挂进宿主:lines 初始行 + maxLines(截断上限,缺省 500 保尾部) */
export function mountLogViewer(host, { lines = [], maxLines = 500, path = "", onRegister = null, onUnregister = null } = {}) {
  const widget = createWidget(LOG_VIEWER_DEF, {
    path,
    state: { lines: lines.map((l) => ({ kind: l.kind ?? "info", text: String(l.text ?? "") })), follow: true, filter: "" },
    onRegister,
    onUnregister,
  });
  let box = null;

  const visible = () =>
    widget.state.lines.filter(
      (l) => !widget.state.filter || l.text.toLowerCase().includes(widget.state.filter.toLowerCase()) || l.kind.includes(widget.state.filter)
    );

  function render() {
    const vis = visible();
    host.innerHTML =
      `<div class="wd-log">` +
      `<div class="wd-log-bar">` +
      `<input class="input wd-log-filter" data-wlog-filter="1" placeholder="${esc(copy("w.log.filter"))}"` +
      ` aria-label="${esc(copy("w.log.filter"))}" value="${esc(widget.state.filter)}">` +
      `<button class="wd-mini" data-wlog-copy="1">${esc(copy("w.log.copy"))}</button>` +
      `</div>` +
      `<div class="wd-log-box" role="log" data-wlog-box="1">` +
      vis
        .map(
          (l) =>
            `<div class="wd-log-line" data-kind="${esc(l.kind)}">` +
            `<span class="wd-log-kind mono">${esc(l.kind)}</span> ${esc(l.text)}</div>`
        )
        .join("") +
      (vis.length ? "" : `<div class="wd-empty">${esc(copy("w.log.empty"))}</div>`) +
      `</div>` +
      (widget.state.follow ? "" : `<button class="wd-log-bottom" data-wlog-bottom="1">${esc(copy("w.log.bottom"))}</button>`) +
      `</div>`;
    box = host.querySelector("[data-wlog-box]");
    if (widget.state.follow && box) box.scrollTop = box.scrollHeight; // 跟随:自动滚底
  }

  widget.append = (items) => {
    const next = [...widget.state.lines, ...items.map((l) => ({ kind: l.kind ?? "info", text: String(l.text ?? "") }))];
    widget.state.lines = next.length > maxLines ? next.slice(next.length - maxLines) : next; // 截断保尾部
    render();
    widget.emit("change", { lines: widget.state.lines });
  };
  widget.toggle_follow = () => {
    widget.state.follow = !widget.state.follow;
    render();
  };
  widget.filter = (text) => {
    widget.state.filter = text ?? "";
    render();
  };
  widget.copy_all = () => {
    const text = widget.state.lines.map((l) => `${l.kind} ${l.text}`).join("\n");
    globalThis.navigator?.clipboard?.writeText?.(text); // 剪贴板(缺席环境降级为事件)
    widget.emit("copy", { text });
    return text;
  };

  host.addEventListener("input", (e) => {
    if (e.target.closest("[data-wlog-filter]")) widget.filter(e.target.value);
  });
  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-wlog-copy]")) return widget.copy_all();
    if (e.target.closest("[data-wlog-bottom]")) {
      widget.state.follow = true;
      return render();
    }
  });
  host.addEventListener("scroll", (e) => {
    const el = e.target.closest?.("[data-wlog-box]") ?? box;
    if (!el) return;
    // 上滚即暂停跟随(§2);滚回底部自动恢复
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    if (!atBottom && widget.state.follow) {
      widget.state.follow = false;
      render();
    } else if (atBottom && !widget.state.follow) {
      widget.state.follow = true;
      render();
    }
  });

  render();
  widget.register(host.dataset.summary ?? "");
  return widget;
}

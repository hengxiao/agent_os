/* text-editor-cm(widget-libs 试点 2:CodeMirror 6 对照实验件)——
   与 W-text 同 state 面({value, baseline, dirty, readonly, mono, rows, field,
   label, lang, updated_at})、同 events(change/commit/revert/open)、同双形态。
   **不动 W-text/W-json 一行**;card 面复用 w-text.render 的 _textCardHtml;
   tab 面 = vendored CM6 实例 + 我们的 chrome(头部/dirty 边条/微标)。

   CM6 天然面(对照意义):行号槽/当前行高亮/选区/焦点/IME/滚动同步全内建,
   手写 W-text 的 syncGutter/syncCursor/syncScroll/preserveSelection 一套全省。
   主题映射:EditorView.theme 全走契约 token(六主题自动生效);dark 旗标
   按主题表(classic/terminal/blueprint/pixel 深,moe/ink 浅)。

   stub 降级(§4.5 vendor 集成原则第 4 条):无 window/无真实测量面时
   mount 只产 chrome(不建 CM6),update 走纯渲染;真实行为断言在 tests-ui。
   铁律:零 fetch;事件上行;vendor 只在逻辑面调用。 */

import { copy, currentThemeId } from "../themes.js";
import { registerWidgetDef } from "./registry.js";
import { bindCardOpen, createWidget } from "./widget.js";
import { renderTextEditor, textEditorMicro, _textCardHtml } from "./w-text.render.js";

/* 主题 → CM6 dark 旗标映射表(classic/terminal/blueprint/pixel 深;moe/ink 浅) */
const _DARK_BY_THEME = { classic: true, terminal: true, blueprint: true, pixel: true, moe: false, ink: false };

/* tab chrome(纯;CM6 宿主槽 + 我们的头部/dirty 边条/微标——结构与 W-text 同族) */
export function renderTextEditorCm(state, { surface = "tab" } = {}) {
  if (surface === "card") return _textCardHtml(state); // card 面复用(不建 CM6,省资源)
  const readonly = Boolean(state.readonly);
  const value = String(state.value ?? "");
  return (
    `<div class="wd-text wd-cm${state.dirty ? " is-dirty" : ""}${readonly ? " is-readonly" : ""}" data-variant="mono">` +
    (state.field
      ? `<div class="wd-text-head"><span class="wd-text-name">${esc(state.field)}${state.lang ? ` · ${esc(state.lang)}` : ""}</span>` +
        (readonly ? `<span class="wd-head-side"><span class="wd-lock" aria-hidden="true">🔒</span></span>` : "") +
        `</div>`
      : "") +
    `<div class="wd-editor wd-cm-editor"><div data-cm-host="1"></div>` +
    `<span class="wd-micro-box">` +
    `<span class="wd-micro-dot" aria-hidden="true"${state.dirty ? "" : " hidden"}></span>` +
    `<span class="wd-micro">${esc(textEditorMicro(value))}</span>` +
    `<span class="wd-micro-tip" aria-hidden="true"></span>` +
    `</span></div></div>`
  );
}

/* CM6 主题(全契约 token;六主题自动生效) */
function _cmTheme(EditorView, dark) {
  return EditorView.theme(
    {
      "&": {
        backgroundColor: "var(--bg-1)", color: "var(--fg-0)",
        fontSize: "var(--text-sm)", height: "100%",
      },
      ".cm-content": { fontFamily: "var(--font-mono)", caretColor: "var(--fg-0)", padding: "var(--s2) 0" },
      ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.5", overflow: "auto" },
      ".cm-gutters": {
        backgroundColor: "var(--bg-1)", color: "var(--fg-2)",
        border: "none", borderRight: "var(--stroke) solid var(--line)",
        fontFamily: "var(--font-mono)",
      },
      ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--live) 8%, transparent)" },
      ".cm-activeLineGutter": {
        backgroundColor: "color-mix(in srgb, var(--live) 12%, transparent)", color: "var(--fg-0)",
      },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--fg-0)" },
      "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
        backgroundColor: "color-mix(in srgb, var(--live) 22%, transparent)",
      },
      ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--live) 14%, transparent)" },
    },
    { dark }
  );
}

/* stub 探测(§4.5):无 window/无计算样式面 = 降级(纯 chrome,不建 CM6) */
const _degraded = (host) =>
  typeof window === "undefined" ||
  !(host.ownerDocument?.defaultView ?? globalThis).getComputedStyle;

export const TEXT_EDITOR_CM_DEF = registerWidgetDef({
  kind: "text-editor-cm",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: {
    value: "", baseline: "", dirty: false, readonly: false,
    mono: true, rows: 6, lang: "", wrap: true,
  },
  actions: [
    { id: "set_value", exec: "local", args_input: { value: { type: "string" } } },
    { id: "commit", exec: "local" },
    { id: "revert", exec: "local" },
  ],
  events: ["change", "commit", "revert", "open"], // open = card 形态整卡点击(§1.4)
  aria: { role: "textbox-multiline", keys: ["Escape"] },
  surfaces: ["card", "tab"],
  render: renderTextEditorCm,
  mount: mountTextEditorCm,
});

export function mountTextEditorCm(host, { value = "", field = "", mono = true, rows = null, readonly = false, label = "", lang = "", path = "", onRegister = null, onUnregister = null, surface = "tab" } = {}) {
  const fieldName = field || host.dataset?.field || "";
  const ariaLabel = label || fieldName;
  if (!ariaLabel) throw new Error("text-editor-cm: aria-label 必填(或给 data-field 推导)");
  const widget = createWidget(TEXT_EDITOR_CM_DEF, {
    path,
    state: {
      value: String(value ?? ""),
      baseline: String(value ?? ""),
      dirty: false,
      readonly: Boolean(readonly),
      mono: Boolean(mono),
      rows: rows ?? (Number(host.dataset?.rows) || 6),
      field: fieldName,
      label: ariaLabel,
      lang,
    },
    onRegister,
    onUnregister,
  });

  let view = null; // CM6 EditorView(降级面恒 null)
  const render = () => {
    host.innerHTML = renderTextEditorCm(widget.state, { surface });
  };
  const syncDirty = () => {
    host.querySelector(".wd-text")?.classList?.toggle("is-dirty", widget.state.dirty);
    const micro = host.querySelector(".wd-micro");
    if (micro) micro.textContent = textEditorMicro(widget.state.value);
    for (const sel of [".wd-micro-dot", ".wd-card-dot"]) {
      const dot = host.querySelector(sel);
      if (dot) dot.hidden = !widget.state.dirty;
    }
  };
  /* 行列 tip(CM6 内建当前行;我们的微标行只做 pos 展示) */
  const syncTip = () => {
    const tip = host.querySelector(".wd-micro-tip");
    if (!tip || !view) return;
    const head = view.state.selection.main.head;
    const line = view.state.doc.lineAt(head);
    tip.textContent = copy("w.text.pos").replace("{line}", String(line.number)).replace("{col}", String(head - line.from + 1));
  };

  /* state.value → CM6(doc 不一致才 dispatch;输入回环防护) */
  const _pushToView = () => {
    if (!view) return;
    const cur = view.state.doc.toString();
    if (cur !== widget.state.value) {
      view.dispatch({ changes: { from: 0, to: cur.length, insert: widget.state.value } });
    }
  };

  widget.update = (patch) => {
    Object.assign(widget.state, patch);
    if (view) {
      _pushToView();
      syncDirty();
      syncTip();
    } else {
      render(); // 降级面:纯渲染
    }
  };
  widget.commit = () => {
    widget.state.baseline = widget.state.value;
    widget.state.dirty = false;
    widget.state.updated_at = Date.now() / 1000;
    syncDirty();
    widget.emit("commit", { value: widget.state.value });
  };
  widget.revert = () => {
    widget.state.value = widget.state.baseline;
    widget.state.dirty = false;
    _pushToView();
    syncDirty();
    widget.emit("revert", { value: widget.state.baseline });
  };

  render();
  syncDirty();

  if (surface === "card") {
    bindCardOpen(host, widget); // card:宿主委托只挂 open(§1.4;不建 CM6)
  } else if (!_degraded(host)) {
    // 真 DOM:异步建 CM6(vendor 按需装载;widget 立即可用,就绪后接管编辑面)
    (async () => {
      const { EditorView, basicSetup } = await import("/static/vendor/codemirror/codemirror.mjs");
      if (widget._destroyed) return; // 装载期间被拆
      const dark = _DARK_BY_THEME[currentThemeId()] ?? true;
      const cmHost = host.querySelector("[data-cm-host]");
      if (!cmHost) return;
      // 行数语义(rows=视高,同 W-text rows 契约):CM6 默认吃内容高,补 min-height
      cmHost.style.minHeight = `${Math.round(widget.state.rows * 22)}px`;
      view = new EditorView({
        parent: cmHost,
        doc: widget.state.value,
        extensions: [
          basicSetup,
          _cmTheme(EditorView, dark),
          EditorView.editable.of(!widget.state.readonly),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) {
              const v = view.state.doc.toString();
              widget.state.value = v;
              widget.state.dirty = v !== widget.state.baseline;
              syncDirty();
              widget.emit("change", { value: v, dirty: widget.state.dirty });
            }
            if (u.selectionSet || u.docChanged) syncTip();
          }),
        ],
      });
      syncTip();
    })().catch(() => {}); // vendor 装载失败 = 留 chrome(降级同语义)
    host.addEventListener("keydown", (e) => {
      if (e.key === "Escape") host.querySelector(".cm-content")?.blur?.(); // Esc=blur(§2 a11y,同 W-text)
    });
  }

  const _destroy = widget.destroy.bind(widget);
  widget.destroy = () => {
    widget._destroyed = true;
    view?.destroy?.();
    view = null;
    host.innerHTML = "";
    _destroy();
  };
  widget.register(host.dataset?.summary ?? "");
  return widget;
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

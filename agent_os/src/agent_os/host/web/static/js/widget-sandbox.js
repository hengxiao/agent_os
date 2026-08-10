/* 控件沙盒逻辑(docs/WIDGET-ARCH.md 沙盒节;开发工具,非产品 UI)。

   URL 协议:``?kind=<kind>&theme=<id>&sample=<N>&surface=<tab|card>``,另支持
   ``#options=<urlencoded json>`` 覆盖样例 mount options(surface 与样例正交,
   由顶栏形态切换器驱动,不进 options 覆盖)。
   挂载流:destroy 旧实例 → 清空舞台 → 按样例 options mount →
   订阅 def.events 全部事件写 Events 面板;State 面板事件触发 + 500ms
   轮询刷新,「应用」走 widget.update()(无 update 的控件禁用并提示)。
   纯函数(parseSandboxUrl/buildSandboxUrl)供单测;页面入口 = bootSandbox()。
   构建号(报告 §6 流程项):BUILD 常量是缓存破坏与验收对版的单一来源——
   widget.html 的 css/js 链接 ?v= 与本常量同步(改 widget 代码时一起改)。 */

import { applyTheme, initTheme, listThemes } from "./themes.js";
import * as widgets from "./widgets/index.js";
import { SAMPLES } from "./widgets/samples.js";

/* 构建号(缓存破坏 ?v= 与页角显示;改 widget 代码时与 widget.html 链接同步) */
export const BUILD = "2026-08-11.3";

/* URL 解析(纯函数):未知 kind → null(调用方回落);坏 options JSON →
   optionsError 标记(不崩页面);sample 非法 → 0。 */
export function parseSandboxUrl(search, hash) {
  const q = new URLSearchParams(search ?? "");
  const kind = q.get("kind");
  const sampleN = Number(q.get("sample") ?? "0");
  let options = null;
  let optionsError = "";
  const raw = String(hash ?? "").replace(/^#/, "");
  if (raw.startsWith("options=")) {
    try {
      options = JSON.parse(decodeURIComponent(raw.slice("options=".length)));
    } catch {
      options = null;
      optionsError = "options JSON 解析失败";
    }
  }
  return {
    kind: kind && SAMPLES[kind] ? kind : null,
    theme: q.get("theme") ?? "",
    sample: Number.isInteger(sampleN) && sampleN >= 0 ? sampleN : 0,
    surface: q.get("surface") === "card" ? "card" : "tab", // 非法值回落 tab(完整形态)
    options,
    optionsError,
  };
}

/* URL 序列化(纯函数;options 为 null 不带 hash;surface=tab 是缺省不进 URL) */
export function buildSandboxUrl({ kind, theme, sample, surface = "tab", options = null }) {
  const q = new URLSearchParams();
  if (kind) q.set("kind", kind);
  if (theme) q.set("theme", theme);
  if (sample !== null && sample !== undefined) q.set("sample", String(sample));
  if (surface === "card") q.set("surface", "card");
  const hash = options ? `#options=${encodeURIComponent(JSON.stringify(options))}` : "";
  return `?${q.toString()}${hash}`;
}

const _EVENTS_CAP = 100;

/* 页面入口(widget.html 的 module script 调用) */
export function bootSandbox() {
  const $ = (sel) => document.querySelector(sel);
  const stage = $("#sb-stage");
  const eventsEl = $("#sb-events");
  const stateEl = $("#sb-state");
  const applyBtn = $("#sb-apply");
  const hintEl = $("#sb-state-hint");
  initTheme(); // ?theme= 搜索参数兼容(themes.js 原生受理)

  const url = parseSandboxUrl(location.search, location.hash);
  const kinds = Object.keys(SAMPLES);
  $("#sb-build").textContent = `build ${BUILD}`; // 页角构建号(报告 §6)
  const current = {
    kind: url.kind ?? kinds[0],
    sample: Math.min(url.sample, (SAMPLES[url.kind ?? kinds[0]].samples.length - 1)),
    surface: url.surface,
    optionsOverride: url.options,
    widget: null,
  };

  // 顶栏:控件/主题/样例下拉
  const kindSel = $("#sb-kind");
  kindSel.innerHTML = kinds
    .map((k) => `<option value="${k}"${k === current.kind ? " selected" : ""}>${k}</option>`)
    .join("");
  const themeSel = $("#sb-theme");
  themeSel.innerHTML = listThemes()
    .map((t) => `<option value="${t.id}"${t.id === (url.theme || "classic") ? " selected" : ""}>${t.name}</option>`)
    .join("");
  if (url.theme) applyTheme(url.theme, { persist: false, syncUrl: false });
  const sampleSel = $("#sb-sample");
  const _fillSamples = () => {
    sampleSel.innerHTML = SAMPLES[current.kind].samples
      .map((s, i) => `<option value="${i}"${i === current.sample ? " selected" : ""}>${s.name}</option>`)
      .join("");
  };
  _fillSamples();
  // 形态切换(完整/卡片;W5.6 双形态,与样例正交)
  const surfaceSel = $("#sb-surface");
  surfaceSel.value = current.surface;

  /* Events 面板(新的在上,上限 100 条) */
  const _events = [];
  const renderEvents = () => {
    eventsEl.textContent = _events
      .map((e) => `${e.ts}  ${e.name}  ${e.payload}`)
      .join("\n");
  };
  const pushEvent = (name, payload) => {
    const ts = new Date().toISOString().slice(11, 23);
    let json = "";
    try {
      json = JSON.stringify(payload ?? {});
    } catch {
      json = String(payload);
    }
    _events.unshift({ ts, name, payload: json });
    if (_events.length > _EVENTS_CAP) _events.length = _EVENTS_CAP;
    renderEvents();
    refreshState(); // 事件触发即刷新 State 面板
  };

  /* State 面板(实时 JSON;「应用」= update(patch)) */
  const refreshState = () => {
    if (!stateEl) return;
    try {
      stateEl.value = JSON.stringify(current.widget?.state ?? {}, null, 2);
    } catch {
      stateEl.value = "(state 不可序列化)";
    }
  };
  const refreshApply = () => {
    const has = typeof current.widget?.update === "function";
    applyBtn.disabled = !has;
    hintEl.textContent = has ? "" : "该控件暂无 update()";
  };

  function mountCurrent() {
    current.widget?.destroy?.();
    stage.innerHTML = "";
    stage.dataset.surface = current.surface; // card 形态舞台收窄(widget.html 样式面)
    const entry = SAMPLES[current.kind];
    const sample = entry.samples[current.sample] ?? entry.samples[0];
    const options = current.optionsOverride ?? sample.options;
    const w = widgets[entry.mount](stage, { ...options, surface: current.surface });
    const def = widgets.getWidgetDef(current.kind);
    for (const ev of def?.events ?? []) w.on(ev, (payload) => pushEvent(ev, payload));
    current.widget = w;
    _events.length = 0;
    renderEvents();
    refreshState();
    refreshApply();
  }

  kindSel.addEventListener("change", () => {
    current.kind = kindSel.value;
    current.sample = 0;
    current.optionsOverride = null;
    _fillSamples();
    mountCurrent();
  });
  sampleSel.addEventListener("change", () => {
    current.sample = Number(sampleSel.value) || 0;
    current.optionsOverride = null;
    mountCurrent();
  });
  themeSel.addEventListener("change", () => applyTheme(themeSel.value, { persist: false, syncUrl: false }));
  surfaceSel.addEventListener("change", () => {
    current.surface = surfaceSel.value === "card" ? "card" : "tab";
    mountCurrent();
  });
  $("#sb-remount").addEventListener("click", mountCurrent);
  $("#sb-share").addEventListener("click", async () => {
    const url = buildSandboxUrl({
      kind: current.kind,
      theme: themeSel.value,
      sample: current.sample,
      surface: current.surface, // 分享链接带上形态(W5.6)
      options: current.optionsOverride,
    });
    const full = `${location.origin}${location.pathname}${url}`;
    const ok = await navigator.clipboard?.writeText?.(full).then(() => true, () => false);
    hintEl.textContent = ok ? "链接已复制" : `复制失败,手动复制:${url}`;
  });
  applyBtn.addEventListener("click", () => {
    if (typeof current.widget?.update !== "function") {
      hintEl.textContent = "该控件暂无 update()";
      return;
    }
    let parsed = null;
    try {
      parsed = JSON.parse(stateEl.value);
    } catch {
      hintEl.textContent = "JSON 解析失败,未应用";
      return;
    }
    current.widget.update(parsed);
    hintEl.textContent = "已应用";
    refreshState();
  });

  setInterval(refreshState, 500); // State 面板轮询(500ms)
  mountCurrent();
  if (url.optionsError) hintEl.textContent = url.optionsError;
}

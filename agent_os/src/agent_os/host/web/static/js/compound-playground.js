/* Compound playground 逻辑(docs/COMPOUND-WIDGET.md §9 C2;开发工具,非产品 UI)。

   双栏复合演示(吃自己狗粮,全程 createCompound):
   - 左栏 pg-stack = 卡片栈(子件 card 面),右栏 pg-stage = 完整区(tab 面),
     两栏本身是一个 pg-root compound 的两个预定义子件(compound 套 compound);
   - 四能力演示:① 预定义 slots(根的两栏)/ ② 动态生灭(从 kind 列表加件 +
     ✕ 移除)/ ③ 左右互移(move_child reparent:state 不动,path 重算,广播)/
     ④ hard link(「链接到右侧」:同一 instance 在右区开 tab 第二 view,
     改右 tab 左卡同步变——update 扇出)。
   入口 = bootPlayground()(compound.html 的 module script 调用)。 */

import { initTheme } from "./themes.js";
import { registerWidgetDef, createCompound, getWidgetDef } from "./widgets/index.js";
import { BUILD } from "./widget-sandbox.js";

/* ── 演示子件选件(kind → 初始 state / mount options)──────────────────── */
const KINDS = [
  { kind: "text-editor", label: "W-text 文本",
    state: () => ({ value: "可编辑文本\n改我试试", field: "demo", label: "demo" }),
    options: () => ({ label: "demo", field: "demo" }) },
  { kind: "json-editor", label: "W-json JSON",
    state: () => ({ value: '{\n  "demo": true,\n  "n": 1\n}', field: "demo", label: "demo" }),
    options: () => ({ label: "demo", field: "demo" }) },
  { kind: "log-viewer", label: "W-log 日志",
    state: () => ({ lines: [{ kind: "run.start", text: "run 开始" }, { kind: "warn", text: "告警行" }, { kind: "error", text: "错误行" }] }),
    options: () => ({}) },
  { kind: "chart", label: "W-chart 图表",
    state: () => ({ series: [{ name: "cost", points: [{ x: 1, y: 2 }, { x: 2, y: 5 }, { x: 3, y: 3 }] }], type: "line" }),
    options: () => ({ label: "cost 演示" }) },
  { kind: "select-list", label: "W-list 列表",
    state: () => ({ items: [{ id: "a", label: "Alpha", hint: "第一项" }, { id: "b", label: "Beta", hint: "第二项" }, { id: "g", label: "Gamma" }] }),
    options: () => ({}) },
  { kind: "ns-tree", label: "W-tree 树",
    state: () => ({ nodes: [{ name: "system.net.http_fetch" }, { name: "system.fs.read" }, { name: "weather.query" }] }),
    options: () => ({}) },
  { kind: "kv-editor", label: "W-kv 键值",
    state: () => ({ entries: [{ key: "host", value: "127.0.0.1" }, { key: "port", value: "8391" }] }),
    options: () => ({}) },
];
const KIND_MAP = Object.fromEntries(KINDS.map((k) => [k.kind, k]));
const ALLOW = KINDS.map((k) => k.kind);

/* 栈/台通用 def 工厂(layout 带每卡操作条;操作经 playground 宿主委托) */
function _stackDef(kind, side) {
  const acts =
    side === "left"
      ? `<button class="btn" data-pg-act="link" title="hard link:右侧开同 instance 的 tab 第二 view">⧉ 链接</button>` +
        `<button class="btn" data-pg-act="move-r" title="reparent:移到右栏">→</button>` +
        `<button class="btn" data-pg-act="x" title="remove(destroy)">✕</button>`
      : `<button class="btn" data-pg-act="move-l" title="reparent:移回左栏">←</button>` +
        `<button class="btn" data-pg-act="x" title="remove(destroy)">✕</button>`;
  return registerWidgetDef({
    kind,
    v: 1,
    state_schema: { type: "object" },
    state_defaults: { side },
    actions: [],
    events: ["change"],
    aria: { role: "group" },
    surfaces: ["card", "tab"],
    compound: {
      slots: [],
      dynamic: { allow: ALLOW, max: 20 },
      layout: (state, slotRefs) =>
        `<div class="pg-stack">` +
        (Object.keys(slotRefs).length
          ? Object.entries(slotRefs)
              .map(
                ([id, r]) =>
                  `<div class="pg-item">` +
                  `<div class="pg-item-bar"><span class="pg-kind mono">${r.kind}</span>` +
                  `<span class="pg-acts" data-id="${id}">${acts}</span></div>` +
                  `<div data-slot="${id}"></div></div>`
              )
              .join("")
          : `<div class="pg-empty">空 — 从上方选 kind 加件</div>`) +
        `</div>`,
    },
  });
}

export function bootPlayground() {
  const $ = (sel) => document.querySelector(sel);
  initTheme();
  $("#pg-build").textContent = `build ${BUILD}`;

  // 根 compound:两栏是 stack/stage 两个 compound 子件(compound 套 compound)。
  // 预定义 slots 直接生成真 compound 实例(C1 修复后 _spawn 递归 createCompound),
  // 不再需要手工 createCompound + attach_existing(那会产生僵尸/重复子件)。
  const stackDef = _stackDef("pg-stack", "left");
  const stageDef = _stackDef("pg-stage", "right");
  const rootDef = registerWidgetDef({
    kind: "pg-root",
    v: 1,
    state_schema: { type: "object" },
    state_defaults: {},
    actions: [],
    events: [],
    aria: { role: "group" },
    surfaces: ["card", "tab"],
    compound: {
      slots: [
        { id: "stack", kind: "pg-stack", surface: "card" },
        { id: "stage", kind: "pg-stage", surface: "tab" },
      ],
      layout: (state, slotRefs) =>
        `<div class="pg-cols">` +
        `<section class="pg-col"><h2 class="pg-h">左栏 · 卡片栈 <span class="pg-dim">card 面</span></h2><div data-slot="stack"></div></section>` +
        `<section class="pg-col"><h2 class="pg-h">右栏 · 完整区 <span class="pg-dim">tab 面</span></h2><div data-slot="stage"></div>` +
        `<div class="pg-links"><h3 class="pg-h">hard link 区 <span class="pg-dim">同 instance 的第二 view</span></h3><div id="pg-linkzone"></div></div></section>` +
        `</div>`,
    },
  });

  const root = createCompound(rootDef, { path: "/pg" });
  const _stackInst = root.child("stack");
  const _stageInst = root.child("stage");

  // 选件下拉
  const kindSel = $("#pg-kind");
  kindSel.innerHTML = KINDS.map((k) => `<option value="${k.kind}">${k.label}</option>`).join("");
  const host = $("#pg-root");
  root.mount_view(host);

  /* hard link 登记(id → {view, wrap})(链接跟 instance 走,与 owner 无关) */
  const links = new Map();
  const _dropLink = (id) => {
    const rec = links.get(id);
    if (rec) {
      rec.view.detach();
      links.delete(id);
    }
  };
  const _mkLink = (id) => {
    const child = _stackInst.child(id) ?? _stageInst.child(id);
    if (!child || links.has(id)) return;
    const wrap = document.createElement("div");
    wrap.className = "pg-link-item";
    const cap = document.createElement("div");
    cap.className = "pg-link-cap";
    cap.textContent = `hard link · ${id}(与左栏同 instance — 改右侧,左卡同步)`;
    const vhost = document.createElement("div");
    wrap.appendChild(cap);
    wrap.appendChild(vhost);
    $("#pg-linkzone").appendChild(wrap);
    links.set(id, { view: child.link_view(vhost, { surface: "tab" }), wrap });
  };

  /* 加件 / 互移 / 移除 / 链接(事件委托在 #pg-root,layout 重渲不伤) */
  $("#pg-add").addEventListener("click", () => {
    const k = KIND_MAP[kindSel.value];
    _stackInst.add_child(k.kind, { state: k.state(), surface: "card", options: k.options() });
  });
  host.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-pg-act]");
    if (!btn) return;
    const id = btn.parentNode?.dataset?.id;
    const act = btn.dataset.pgAct;
    if (act === "x") {
      _dropLink(id);
      (_stackInst.child(id) ? _stackInst : _stageInst).remove_child(id, { destroy: true });
      return;
    }
    if (act === "move-r") {
      const keep = links.has(id);
      _dropLink(id);
      const inst = _stackInst.move_child(id, _stageInst, { surface: "tab" });
      if (keep) _mkLink(inst._compoundId);
      return;
    }
    if (act === "move-l") {
      const keep = links.has(id);
      _dropLink(id);
      const inst = _stageInst.move_child(id, _stackInst, { surface: "card" });
      if (keep) _mkLink(inst._compoundId);
      return;
    }
    if (act === "link") {
      if (links.has(id)) {
        _dropLink(id); // 再点断开(切换演示)
        return;
      }
      _mkLink(id);
    }
  });

  /* reparent 广播上屏(§6-3 事件可视化) */
  const eventsEl = $("#pg-events");
  const log = [];
  for (const c of [root, _stackInst, _stageInst]) {
    c.on("reparent", (p) => {
      log.unshift(`${new Date().toISOString().slice(11, 19)}  reparent  ${p.child}: ${p.from} → ${p.to}`);
      eventsEl.textContent = log.slice(0, 8).join("\n");
    });
    c.on("child_event", (p) => {
      if (p.event !== "change") return;
      const el = $("#pg-gate");
      if (el) el.textContent = `child_event(闸门上行):${p.child} · ${p.event}`;
    });
  }
  // 演示闸门类：stack 的闸门把 warn 级 change 也放行(默认全放行,这里只是可视化)
  stackDef.compound.on_child_event = (child, event, payload) => true;
  stageDef.compound.on_child_event = (child, event, payload) => true;
  stackDef.compound.child_context = (child, frag) => ({ ...frag, owner: "pg-stack(经 child_context 改写)" });
  stageDef.compound.child_context = (child, frag) => ({ ...frag, owner: "pg-stage(经 child_context 改写)" });

  // 初始两件,演示预定义 + 动态
  const t = KIND_MAP["text-editor"];
  _stackInst.add_child(t.kind, { state: t.state(), surface: "card", options: t.options() });
  const j = KIND_MAP["json-editor"];
  _stackInst.add_child(j.kind, { state: j.state(), surface: "card", options: j.options() });
}

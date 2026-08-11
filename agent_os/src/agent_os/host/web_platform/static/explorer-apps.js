/* explorer 系 app 薄壳(docs/DESKTOP-WIDGET.md §5-1;C4.3):
   skills-explorer / runs-explorer / tools-explorer / lab / debug-console
   五个 app 的 desktop 薄壳 compound——行为零回退:
   - skills/tools/lab = legacy ES module 原样包装(openX/closeX 不动,页面
     逻辑一行不改);最小化摘 view 时子树挪进**保活囊**(DOM 移动不销毁,
     监听/模块单件态全保),重挂原树接回——hidden 语义逐字级;
   - debug-console 同样包装,但其模块运行时引用宿主元素(dh.main),
     重挂 = 重开(数据在服务端/localStorage,刷新即还原);
   - runs-explorer = app.js 的 runs 装配(_legacyRunsHtml + W-date 时间窗 +
     行内 run 链接)迁正为自装薄壳:state(range/locate/rows)全 canonical,
     重挂从 state 重渲 + 重拉。
   locate(ref) = 各 app 的「打开详情」定位面:未开 = 首挂带名打开;
   已开 = 按模块能力重开定位(导航语义,dirty  caveat 见 DESKTOP-WIDGET §11)。

   铁律同 conversation-app:def 不出海;fetch 只在实例适配层。 */

import { copy } from "/static/js/themes.js";
import { createCompound, registerWidgetDef, mountDatePicker } from "/static/js/widgets/index.js";
import { openSkillsView, closeSkillsView } from "/static/js/components/skills-view.js";
import { openToolsView, closeToolsView } from "/static/js/components/tools-view.js";
import { openLab, closeLab } from "/static/js/components/lab.js";
import { openDebugHome, closeDebugHome } from "/static/js/components/debug-home.js";

/* ── legacy 包装器(skills/tools/lab 保活囊;debug 重开)────────────── */

function _legacyDef(kind, label) {
  return registerWidgetDef({
    kind,
    v: 1,
    state_schema: { type: "object" },
    state_defaults: { title: label },
    actions: [],
    events: ["change"],
    aria: { role: "application", label },
    surfaces: ["tab"],
    compound: {
      dynamic: { allow: [], max: 0 },
      layout: () => `<div class="xp-legacy" data-xp="${kind}"></div>`,
    },
  });
}

/* 子树挪移(stub/真实 DOM 通吃):真实 DOM appendChild 自移出旧父;
   stub 的 children 是普通数组且 appendChild 不摘旧父,须显式 splice。 */
function _moveChildren(from, to) {
  while (from.children.length) {
    const c = from.children[0];
    from.children.splice?.(0, 1);
    to.appendChild(c);
  }
}

function _createLegacyApp({ def, open, close, reopenOnAttach = false }) {
  const inst = createCompound(def, { path: `/${def.kind}`, state: { title: def.aria.label } });
  let pouch = null; // 保活囊:摘 view 时挪出的活子树(监听/模块单件态随元素存活)
  let opened = false;
  let shellHost = null; // 当前挂接的壳元素(locate 重开定位用)
  let pendingLocate = null;

  const _baseMountView = inst.mount_view.bind(inst);
  inst.mount_view = (viewHost, mopts = {}) => {
    const view = _baseMountView(viewHost, mopts);
    shellHost = viewHost.querySelector("[data-xp]") ?? viewHost;
    if (pouch) {
      _moveChildren(pouch, shellHost); // 原树接回(逐字级 hidden)
      pouch = null;
    } else if (reopenOnAttach || !opened) {
      open(shellHost, pendingLocate); // 首挂(带定位名)/重开面
      pendingLocate = null;
    }
    opened = true;
    return {
      host: viewHost,
      detach: () => {
        if (!reopenOnAttach) {
          pouch = (viewHost.ownerDocument ?? globalThis.document).createElement("div");
          _moveChildren(shellHost, pouch); // 挪出保活(不 close)
        }
        view.detach();
      },
    };
  };
  const _destroy = inst.destroy.bind(inst);
  inst.destroy = () => {
    close?.(); // 显式销毁才 close legacy 模块(退订/清单件)
    _destroy();
  };

  /* 「打开详情」定位:未开/摘挂 = 记到(重)挂;已开可见 = 关模块重开带名
     (导航语义,dirty caveat 见 DESKTOP-WIDGET §11) */
  const locate = (ref) => {
    if (!ref) return;
    if (reopenOnAttach) return; // debug-console:模块无定位面,激活即定位(§11 注)
    if (!opened) {
      pendingLocate = ref;
      return;
    }
    close?.();
    pouch = null;
    opened = false;
    if (shellHost && shellHost.isConnected !== false) {
      open(shellHost, ref);
      opened = true;
    } else {
      pendingLocate = ref; // 摘挂态:重挂时按定位重开
    }
  };
  return { inst, locate };
}

/* 四 legacy def(import 即注册;registry 面与使用解耦) */
const SKILLS_DEF = _legacyDef("skills-explorer", "技能");
const TOOLS_DEF = _legacyDef("tools-explorer", "工具");
const LAB_DEF = _legacyDef("lab", "Lab");
const DEBUG_DEF = _legacyDef("debug-console", "调试");

export const createSkillsExplorer = () =>
  _createLegacyApp({ def: SKILLS_DEF, open: openSkillsView, close: closeSkillsView });

export const createToolsExplorer = () =>
  _createLegacyApp({ def: TOOLS_DEF, open: openToolsView, close: closeToolsView });

export const createLabApp = () =>
  _createLegacyApp({ def: LAB_DEF, open: openLab, close: closeLab });

export const createDebugConsole = () =>
  _createLegacyApp({ def: DEBUG_DEF, open: (h) => openDebugHome(h), close: closeDebugHome, reopenOnAttach: true });

/* ── runs-explorer(自装;app.js runs 装配迁正)───────────────────────── */

export const RUNS_EXPLORER_DEF = registerWidgetDef({
  kind: "runs-explorer",
  v: 1,
  state_schema: { type: "object" },
  state_defaults: { title: "运行", range: null, locate: "", rows: [], total: 0, failed: 0 },
  actions: [],
  events: ["change"],
  aria: { role: "application", label: "运行" },
  surfaces: ["tab"],
  compound: {
    dynamic: { allow: [], max: 0 },
    layout: (state) => _runsLayout(state),
  },
});

/* 行集(纯;与 app.js _runsRowsHtml 同构 + locate 高亮):
   locate 命中行提首(定位语义——slice(8) 截断也不漏,高亮必在视野) */
export function runsRowsHtml(state) {
  const all = state.rows ?? [];
  const range = state.range;
  const inRange = (r) => {
    const day = String(r.started_at ?? "").slice(0, 10);
    if (range?.start && day < range.start) return false;
    if (range?.end && day > range.end) return false;
    return true;
  };
  const filtered = all.filter(inRange);
  const hit = state.locate ? filtered.find((r) => r.run_id === state.locate) : null;
  const ordered = hit ? [hit, ...filtered.filter((r) => r !== hit)] : filtered;
  return ordered
    .slice(0, 8)
    .map(
      (r) =>
        `<div class="pf-ln${state.locate && r.run_id === state.locate ? " doc-flash" : ""}" ` +
        `data-run-row="${esc(r.run_id ?? "")}">` +
        `<button class="pf-detail-link" data-detail-kind="run" ` +
        `data-detail-ref="${esc(r.run_id)}" data-detail='{}'>${esc(r.skill ?? r.run_id)}</button> ` +
        `<span class="pf-dim">${esc(r.status ?? "")}</span></div>`
    )
    .join("");
}

function _runsLayout(state) {
  const rows = state.rows ?? [];
  const range = state.range;
  const inRange = (r) => {
    const day = String(r.started_at ?? "").slice(0, 10);
    if (range?.start && day < range.start) return false;
    if (range?.end && day > range.end) return false;
    return true;
  };
  const shown = rows.filter(inRange);
  const failed = shown.filter((r) => r.status === "failed").length;
  return (
    `<div class="pf-detail xp-runs">` +
    `<div class="pf-detail-head">${esc(copy("platform.app.runs"))}</div>` +
    `<div data-browse-range="1"></div>` +
    `<div class="pf-card-sub" data-runs-count="1">${esc(copy("platform.legacy.runs.line"))
      .replace("{n}", String(shown.length))
      .replace("{f}", String(failed))}</div>` +
    `<div class="pf-card-actions"><a class="btn" href="/#/runs">${esc(copy("platform.legacy.open"))}</a></div>` +
    `<div data-runs-rows="1">${runsRowsHtml(state)}</div>` +
    `</div>`
  );
}

export function createRunsExplorer() {
  const inst = createCompound(RUNS_EXPLORER_DEF, {
    path: "/runs-explorer",
    state: { title: "运行", range: null, locate: "", rows: [], total: 0, failed: 0 },
  });
  const viewHosts = new Set();

  /* 行区/计数按 state 重渲(各 view;增量不重排 layout) */
  const _renderRows = () => {
    for (const host of viewHosts) {
      const rowsHost = host.querySelector("[data-runs-rows]");
      if (rowsHost) rowsHost.innerHTML = runsRowsHtml(inst.state);
      const count = host.querySelector("[data-runs-count]");
      if (count) {
        const all = inst.state.rows ?? [];
        const range = inst.state.range;
        const shown = all.filter((r) => {
          const day = String(r.started_at ?? "").slice(0, 10);
          if (range?.start && day < range.start) return false;
          if (range?.end && day > range.end) return false;
          return true;
        });
        count.textContent = copy("platform.legacy.runs.line")
          .replace("{n}", String(shown.length))
          .replace("{f}", String(shown.filter((r) => r.status === "failed").length));
      }
    }
  };

  const load = async () => {
    try {
      const rows = await (await fetch("/api/runs")).json();
      inst.state.rows = rows ?? [];
      inst.state.total = inst.state.rows.length;
      inst.state.failed = inst.state.rows.filter((r) => r.status === "failed").length;
      _renderRows();
      inst.emit("change", { total: inst.state.total });
    } catch {
      /* 读面故障留空态(fail-safe) */
    }
  };

  const _baseMountView = inst.mount_view.bind(inst);
  inst.mount_view = (viewHost, mopts = {}) => {
    const view = _baseMountView(viewHost, mopts);
    viewHosts.add(viewHost);
    // W-date 时间窗(每 view 新挂;初值 = state.range,hidden 语义 canonical 兜底)
    const picker = mountDatePicker(viewHost.querySelector("[data-browse-range]"), {
      mode: "range",
      value: inst.state.range ?? { start: "", end: "" },
    });
    picker.on("change", ({ value }) => {
      inst.state.range = value;
      _renderRows();
    });
    if (!(inst.state.rows ?? []).length) load(); // 首挂拉取;重挂从 canonical 先渲
    return {
      host: viewHost,
      detach: () => {
        picker.destroy?.();
        viewHosts.delete(viewHost);
        view.detach();
      },
    };
  };

  /* 「打开详情」定位:行内高亮 + 滚动(同 app.js widgetFocus 脉冲手法) */
  const locate = (ref) => {
    if (!ref) return;
    inst.state.locate = ref;
    _renderRows();
    for (const host of viewHosts) {
      const row = host.querySelector(`[data-run-row="${ref}"]`);
      if (row) {
        row.scrollIntoView?.();
        break;
      }
    }
    if (!(inst.state.rows ?? []).length) load();
  };
  return { inst, locate, load };
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/* 共享纯工具(docs/WEB-UI.md §5/§6.1):esc、时间/cost/skill 格式化、复制、Toast。
   函数级 DOM 访问(toast/copyText 调用时才碰 document),import 无副作用,node 单测可载。 */

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ── 时间 / cost 格式化(§5:相对时间 + title 绝对时间)────────────── */

export function relTime(iso) {
  const t = Date.parse(iso ?? "");
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 10) return "刚刚";
  if (s < 60) return `${Math.floor(s)} 秒前`;
  const m = s / 60;
  if (m < 60) return `${Math.floor(m)} 分钟前`;
  const h = m / 60;
  if (h < 24) return `${Math.floor(h)} 小时前`;
  const d = h / 24;
  if (d < 30) return `${Math.floor(d)} 天前`;
  return new Date(t).toLocaleDateString();
}

export function absTime(iso) {
  const t = Date.parse(iso ?? "");
  return Number.isNaN(t) ? "—" : new Date(t).toLocaleString();
}

/* 信号时间戳(float 秒)→ 绝对时间文本 */
export function absTs(ts) {
  const t = Number(ts);
  return Number.isFinite(t) ? new Date(t * 1000).toLocaleString() : "—";
}

export const fmtCost = (c) => `$${(Number(c) || 0).toFixed(2)}`;

/* 技能名缩写:"local:fib@1.0.0" → "fib"(帧树/时间线/检视器共用) */
export function shortSkill(skill) {
  const s = String(skill ?? "");
  const noNs = s.includes(":") ? s.slice(s.lastIndexOf(":") + 1) : s;
  return noNs.split("@")[0] || s || "—";
}

/* 帧 id 缩写:32 位 hex 取前 6,便于"已过滤 f-xxx"等紧凑显示 */
export const shortId = (id) => String(id ?? "").slice(0, 6) || "—";

/* ── 空态(§5 三态之 empty;各面板共用,样式 .empty 在 app.css)────────
   简洁线条风内联 SVG 图标(stroke currentColor)+ 标题 + 引导 + 可选动作按钮。 */

const EMPTY_ICONS = {
  /* 收件箱托盘:空列表(还没有数据) */
  inbox:
    `<path d="M2.5 9.6l1.6-4.8A1 1 0 0 1 5 4.2h6a1 1 0 0 1 .9.6l1.6 4.8"/>` +
    `<path d="M2.5 9.6V12A1.5 1.5 0 0 0 4 13.5h8a1.5 1.5 0 0 0 1.5-1.5V9.6"/>` +
    `<path d="M2.5 9.6h3.2l.9 1.6h2.8l.9-1.6h3.2"/>`,
  /* 放大镜:搜索/筛选无匹配、目标不存在 */
  search: `<circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2L13.8 13.8"/>`,
  /* 左入箭头 + 侧栏:从列表选择一项 */
  select:
    `<path d="M9.5 2.8H4A1.3 1.3 0 0 0 2.7 4v8A1.3 1.3 0 0 0 4 13.2h5.5"/>` +
    `<path d="M6.8 8h6.4M10.7 5.3L13.4 8l-2.7 2.7"/>`,
  /* 层叠:帧/帧树 */
  layers:
    `<path d="M8 2.5l5.5 2.8L8 8 2.5 5.3z"/>` +
    `<path d="M2.5 8.3L8 11l5.5-2.7M2.5 11.3L8 14l5.5-2.7"/>`,
  /* 波形:信号/时间线 */
  activity: `<path d="M2 8h2.8l2-4.5 2.6 9L11.4 8H14"/>`,
  /* 立方体:技能 */
  box:
    `<path d="M8 1.9l5.2 3v6.2L8 14.1l-5.2-3V4.9z"/>` +
    `<path d="M13.2 4.9L8 8m0 0L2.8 4.9M8 8v6"/>`,
  /* 终端:工具 */
  terminal: `<path d="M3 4.2l3.8 3.8L3 11.8M8 12h5"/>`,
  /* 播放:发起运行 */
  run:
    `<rect x="2.5" y="2.5" width="11" height="11" rx="2.5"/>` +
    `<path d="M6.6 5.6l3.9 2.4-3.9 2.4z"/>`,
  /* 柱状图:用量/统计 */
  chart: `<path d="M3.5 13.2V9.4M8 13.2V2.8M12.5 13.2V6.4"/>`,
};

const emptyIcon = (name) =>
  `<span class="empty-icon" aria-hidden="true">` +
  `<svg viewBox="0 0 16 16" width="22" height="22" fill="none" stroke="currentColor"` +
  ` stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">` +
  (EMPTY_ICONS[name] ?? EMPTY_ICONS.inbox) +
  `</svg></span>`;

/* 空态块:title 一行 + hint 一行;icon 取 EMPTY_ICONS 键名;
   action = { label, dataAction, primary } 时在引导下放一个按钮。 */
export const emptyBlock = (title, hint, icon = "inbox", action = null) =>
  `<div class="empty">` +
  emptyIcon(icon) +
  `<span class="empty-title">${esc(title)}</span>` +
  `<span class="empty-hint">${esc(hint)}</span>` +
  (action
    ? `<button class="btn${action.primary ? " btn-primary" : ""}"` +
      ` data-action="${esc(action.dataAction)}">${esc(action.label)}</button>`
    : "") +
  `</div>`;

/* ── 路由规则描述(§4.6/§4.7):"Use when / Do not use when" 前缀分行高亮 ── */

/* description → [{ kind: "plain"|"use"|"avoid", text }](纯函数,node 单测可载)。
   按 "Use when" / "Do not use when" 标记切分(行内出现同样切开);
   标记前的前缀为 plain,标记段分别归 use / avoid。 */
export function splitRouteDescription(desc) {
  const text = String(desc ?? "").trim();
  if (!text) return [];
  const marks = [];
  const re = /Do not use when|Use when/g;
  let m;
  while ((m = re.exec(text))) marks.push({ mark: m[0], start: m.index });
  if (!marks.length) return [{ kind: "plain", text }];
  const segs = [];
  const head = text.slice(0, marks[0].start).trim();
  if (head) segs.push({ kind: "plain", text: head });
  marks.forEach(({ mark, start }, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].start : text.length;
    const body = text.slice(start, end).trim();
    if (body) segs.push({ kind: mark === "Use when" ? "use" : "avoid", text: body });
  });
  return segs;
}

/* 路由规则描述 → 分行 HTML(use/avoid 段左色条高亮,样式 .desc-seg 在 app.css) */
export function routeDescHtml(desc) {
  const segs = splitRouteDescription(desc);
  if (!segs.length) return `<div class="desc-seg" data-kind="plain">(无描述)</div>`;
  return segs
    .map((s) => `<div class="desc-seg" data-kind="${s.kind}">${esc(s.text)}</div>`)
    .join("");
}

/* ── 复制(§5:run_id / 消息 / 帧 JSON 等一键复制)────────────────── */

export const COPY_SVG =
  `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor"` +
  ` stroke-width="1.5" stroke-linecap="round" aria-hidden="true">` +
  `<rect x="5.5" y="5.5" width="8" height="9" rx="1.5"/>` +
  `<path d="M10.5 5.5V3A1.5 1.5 0 0 0 9 1.5H4A1.5 1.5 0 0 0 2.5 3v7A1.5 1.5 0 0 0 4 11.5h1.5"/>` +
  `</svg>`;

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

/* ── Toast(§5:右下,3s 自动消失,带关闭钮)───────────────────────── */

export function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.kind = kind;
  el.setAttribute("role", "status");
  const msgEl = document.createElement("span");
  msgEl.className = "toast-msg";
  msgEl.textContent = msg;
  const closeBtn = document.createElement("button");
  closeBtn.className = "toast-close";
  closeBtn.setAttribute("aria-label", "关闭");
  closeBtn.textContent = "✕";
  el.appendChild(msgEl);
  el.appendChild(closeBtn);
  const remove = () => el.remove();
  closeBtn.addEventListener("click", remove);
  document.querySelector("#toastStack").appendChild(el);
  setTimeout(remove, 3000);
}

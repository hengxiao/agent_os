/* 共享纯工具(WEB-UI.md §5/§6.1):esc、时间/cost/skill 格式化、复制、Toast。
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

/* 空态块(§5 三态之 empty;各面板共用,样式 .empty 在 app.css) */
export const emptyBlock = (title, hint) =>
  `<div class="empty">` +
  `<div class="empty-illust" aria-hidden="true">插画位</div>` +
  `<span class="empty-title">${esc(title)}</span>` +
  `<span class="empty-hint">${esc(hint)}</span>` +
  `</div>`;

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
  el.innerHTML =
    `<span class="toast-msg"></span>` +
    `<button class="toast-close" aria-label="关闭">✕</button>`;
  el.querySelector(".toast-msg").textContent = msg;
  const remove = () => el.remove();
  el.querySelector(".toast-close").addEventListener("click", remove);
  document.querySelector("#toastStack").appendChild(el);
  setTimeout(remove, 3000);
}

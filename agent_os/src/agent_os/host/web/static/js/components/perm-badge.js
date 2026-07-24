/* PermBadge(WEB-UI.md §3.3/§4.7):工具权限等级徽标。
   四级(§3.1 --perm-* 色板):READ / WRITE / NET / EXEC;颜色 + 文字双编码(§3.1 约束)。
   纯函数组件:返回 HTML 字符串,不碰 DOM;颜色由 app.css 按 data-perm 消费 token。 */

const LEVELS = ["READ", "WRITE", "NET", "EXEC"];

export function normalizePerm(permission) {
  const p = String(permission ?? "").toUpperCase();
  return LEVELS.includes(p) ? p : "READ";
}

/* permission → 徽标 HTML;large=true 用于详情头部(§4.7:权限等级是首要视觉信息) */
export function permBadge(permission, { large = false } = {}) {
  const level = normalizePerm(permission);
  return (
    `<span class="perm-badge${large ? " perm-badge-lg" : ""}" data-perm="${level}">` +
    `<span class="perm-dot" aria-hidden="true"></span>${level}` +
    `</span>`
  );
}

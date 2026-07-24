/* Banner(WEB-UI.md §3.3):页级/嵌入级通告条。
   tone:ok(绿,完成)/ warn(黄,纠偏/提醒)/ danger(红,异常/veto)/
   aborted(紫,中止)/ info(蓝,一般信息)。
   纯函数:返回 HTML 字符串;颜色由 app.css 按 data-tone 消费 token。 */

const ICONS = { ok: "✓", warn: "⚠", danger: "✖", aborted: "■", info: "ℹ" };

export function banner(tone, titleHtml, bodyHtml = "") {
  return (
    `<div class="banner" data-tone="${tone}" role="alert">` +
    `<span class="banner-icon" aria-hidden="true">${ICONS[tone] ?? ICONS.info}</span>` +
    `<div class="banner-main">` +
    `<span class="banner-title">${titleHtml}</span>` +
    (bodyHtml ? `<span class="banner-body">${bodyHtml}</span>` : "") +
    `</div></div>`
  );
}

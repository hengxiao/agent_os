/* StatusPill(WEB-UI.md §3.3):run/frame 状态徽标。
   六状态双编码(色点 + 文字,§3.1 约束):done / failed / aborted / running / paused / unknown。
   纯函数组件:返回 HTML 字符串,不碰 DOM;颜色由 app.css 按 data-status 消费 token。
   主题 T1:文字通道走文案表 copy("status.*")(§2.2;classic 表 = 现状文案,行为不变)。 */

import { copy } from "../themes.js";

const STATES = {
  done: { label: "完成" },
  failed: { label: "失败" },
  aborted: { label: "中止" },
  running: { label: "进行中" },
  paused: { label: "已暂停" },
  unknown: { label: "未知" },
};

export function normalizeStatus(status) {
  return Object.hasOwn(STATES, status) ? status : "unknown";
}

export function statusPill(status) {
  const key = normalizeStatus(status);
  return (
    `<span class="status-pill" data-status="${key}">` +
    `<span class="pill-dot" aria-hidden="true"></span>` +
    `<span class="pill-label">${copy(`status.${key}`)}</span>` +
    `</span>`
  );
}

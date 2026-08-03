/* MascotLayer(docs/DEBUG-UI-THEMES.md §2.4;docs/DEBUG-UI-MOE.md §2 Mochi M1):
   主题声明 mascot 时才渲染的独立层——组件零分支:层自己读当前主题(currentTheme().mascot),
   mascot 为 null 的主题(classic/terminal/blueprint/ink)下 mascotHtml 返回空串。
   多 mascot 注册表(MASCOTS):每个 mascot = { name, exprs, sprite },
   sprite8(pixel 主题)是 mascot 抽象的第二实例,证明层可换(§3.6)。
   M1:纯 SVG <symbol> 精灵表 + 基础表情(无动效,动效 M2)。
   视觉样式全部在 css/themes/<id>.css,此处只出结构。 */

import { currentTheme } from "../themes.js";

/* 会话快照 → Mochi 表情(failed 差分 T1.3 哭脸;aborted/待机暂不映射 → 不渲染) */
export function mascotStateFor(doc, endStatus = null) {
  const st = doc?.state;
  if (st === "running") return "running";
  if (st === "paused") return "paused";
  if (st === "detached" && endStatus === "done") return "done";
  if (st === "detached" && endStatus === "failed") return "failed";
  return null;
}

/* Mochi 精灵表:圆团子(.mb 身/.me 眼/.mm 嘴/.mp 爪/.mbh 腮红)+ 表情差分。
   32×32 viewBox;色彩全走 moe.css 的类规则(消费 token),SVG 内零色值。 */
const MOCHI_SPRITE =
  `<svg class="mascot-sprite" width="0" height="0" style="position:absolute" aria-hidden="true">` +
  // running:原地小跑——身体微前倾,双腿一前一后,眼睛盯前方,嘴抿紧
  `<symbol id="mochi-running" viewBox="0 0 32 32">` +
  `<circle class="mb" cx="16" cy="16" r="10.5"/>` +
  `<circle class="mbh" cx="10.5" cy="18.5" r="2"/>` +
  `<circle class="mbh" cx="21.5" cy="18.5" r="2"/>` +
  `<circle class="me" cx="12.5" cy="14.5" r="1.6"/>` +
  `<circle class="me" cx="19.5" cy="14.5" r="1.6"/>` +
  `<path class="mm" d="M13.5 19.5h5"/>` +
  `<path class="mp" d="M11 26.5l-2.5 3"/>` +
  `<path class="mp" d="M21 26.5l3 2.2"/>` +
  `</symbol>` +
  // paused:立正+举爪——双眼瞪圆(带高光),右爪高举,嘴张圆("轮到你了!")
  `<symbol id="mochi-paused" viewBox="0 0 32 32">` +
  `<circle class="mb" cx="16" cy="17" r="10.5"/>` +
  `<circle class="mbh" cx="10.5" cy="19.5" r="2"/>` +
  `<circle class="mbh" cx="21.5" cy="19.5" r="2"/>` +
  `<circle class="me" cx="12.5" cy="15" r="2.2"/>` +
  `<circle class="me" cx="19.5" cy="15" r="2.2"/>` +
  `<circle class="mbh2" cx="13.2" cy="14.2" r="0.7"/>` +
  `<circle class="mbh2" cx="20.2" cy="14.2" r="0.7"/>` +
  `<circle class="mm2" cx="16" cy="20.5" r="1.6"/>` +
  `<path class="mp" d="M25.5 12.5L29 5.5"/>` +
  `<circle class="mp2" cx="29.3" cy="4.6" r="1.9"/>` +
  `</symbol>` +
  // done:撒花静态帧——闭眼笑(拱形),双爪举起,头顶 8 片花瓣
  `<symbol id="mochi-done" viewBox="0 0 32 32">` +
  `<circle class="mb" cx="16" cy="18" r="10"/>` +
  `<circle class="mbh" cx="10.8" cy="20" r="2"/>` +
  `<circle class="mbh" cx="21.2" cy="20" r="2"/>` +
  `<path class="mm" d="M11.2 15.6q1.4-1.8 2.8 0"/>` +
  `<path class="mm" d="M18 15.6q1.4-1.8 2.8 0"/>` +
  `<path class="mm" d="M13.5 20.5q2.5 2.4 5 0"/>` +
  `<path class="mp" d="M7 15L3.5 11"/>` +
  `<path class="mp" d="M25 15l3.5-4"/>` +
  `<rect class="cf1" x="6" y="3" width="2.2" height="2.2" rx="0.6"/>` +
  `<rect class="cf2" x="12" y="1.5" width="2.2" height="2.2" rx="0.6"/>` +
  `<rect class="cf3" x="18" y="1" width="2.2" height="2.2" rx="0.6"/>` +
  `<rect class="cf4" x="24" y="2.5" width="2.2" height="2.2" rx="0.6"/>` +
  `<circle class="cf2" cx="9" cy="6.5" r="1.1"/>` +
  `<circle class="cf3" cx="15.5" cy="5" r="1.1"/>` +
  `<circle class="cf1" cx="21.5" cy="6" r="1.1"/>` +
  `<circle class="cf4" cx="27" cy="6.5" r="1.1"/>` +
  `</symbol>` +
  // failed:哭脸——皱眉闭眼(下垂弧),双颊挂泪(.mt),嘴下撇,双爪垂落
  `<symbol id="mochi-failed" viewBox="0 0 32 32">` +
  `<circle class="mb" cx="16" cy="17" r="10.5"/>` +
  `<circle class="mbh" cx="10.5" cy="19.5" r="2"/>` +
  `<circle class="mbh" cx="21.5" cy="19.5" r="2"/>` +
  `<path class="mm" d="M10.6 15.4q1.9-1.5 3.8 0"/>` +
  `<path class="mm" d="M17.6 15.4q1.9-1.5 3.8 0"/>` +
  `<path class="mt" d="M11.6 17.6q-1.6 2.8 0 4.4 1.6-1.6 0-4.4z"/>` +
  `<path class="mt" d="M20.4 17.6q-1.6 2.8 0 4.4 1.6-1.6 0-4.4z"/>` +
  `<path class="mm" d="M13 21.6q3-2.2 6 0"/>` +
  `<path class="mp" d="M10.5 26l-2.5 3"/>` +
  `<path class="mp" d="M21.5 26l2.5 3"/>` +
  `</symbol>` +
  `</svg>`;

/* sprite8 精灵表(pixel 主题;8-bit 小勇者,2px 像素块):
   .p8a 甲/.p8s 肤/.p8e 眼·痕/.p8w 武器/.p8c 金币·星;色彩全在 pixel.css。 */
const SPRITE8_SPRITE =
  `<svg class="mascot-sprite" width="0" height="0" style="position:absolute" aria-hidden="true">` +
  // running:前进——头+甲盔+甲身,双腿前后叉开,持剑手前伸
  `<symbol id="sprite8-running" viewBox="0 0 32 32">` +
  `<rect class="p8a" x="12" y="2" width="8" height="3"/>` +
  `<rect class="p8s" x="13" y="5" width="6" height="5"/>` +
  `<rect class="p8e" x="14" y="6" width="2" height="2"/>` +
  `<rect class="p8e" x="18" y="6" width="2" height="2"/>` +
  `<rect class="p8a" x="13" y="10" width="6" height="8"/>` +
  `<rect class="p8a" x="10" y="18" width="3" height="6"/>` +
  `<rect class="p8a" x="19" y="18" width="3" height="6"/>` +
  `<rect class="p8s" x="19" y="11" width="5" height="3"/>` +
  `<rect class="p8w" x="24" y="8" width="2" height="7"/>` +
  `</symbol>` +
  // paused:立正举剑——双腿并拢,右臂上举,剑朝天
  `<symbol id="sprite8-paused" viewBox="0 0 32 32">` +
  `<rect class="p8a" x="12" y="4" width="8" height="3"/>` +
  `<rect class="p8s" x="13" y="7" width="6" height="5"/>` +
  `<rect class="p8e" x="14" y="8" width="2" height="2"/>` +
  `<rect class="p8e" x="18" y="8" width="2" height="2"/>` +
  `<rect class="p8a" x="13" y="12" width="6" height="8"/>` +
  `<rect class="p8a" x="13" y="20" width="2" height="6"/>` +
  `<rect class="p8a" x="17" y="20" width="2" height="6"/>` +
  `<rect class="p8s" x="19" y="8" width="3" height="5"/>` +
  `<rect class="p8w" x="21" y="1" width="2" height="8"/>` +
  `</symbol>` +
  // done:胜利跳——整体上移,双腿收起,头顶金币星
  `<symbol id="sprite8-done" viewBox="0 0 32 32">` +
  `<rect class="p8c" x="15" y="1" width="2" height="2"/>` +
  `<rect class="p8c" x="13" y="3" width="6" height="2"/>` +
  `<rect class="p8c" x="15" y="5" width="2" height="2"/>` +
  `<rect class="p8a" x="12" y="7" width="8" height="3"/>` +
  `<rect class="p8s" x="13" y="10" width="6" height="5"/>` +
  `<rect class="p8e" x="14" y="11" width="2" height="2"/>` +
  `<rect class="p8e" x="18" y="11" width="2" height="2"/>` +
  `<rect class="p8a" x="13" y="15" width="6" height="8"/>` +
  `<rect class="p8a" x="12" y="23" width="3" height="3"/>` +
  `<rect class="p8a" x="17" y="23" width="3" height="3"/>` +
  `<rect class="p8s" x="8" y="13" width="4" height="3"/>` +
  `<rect class="p8s" x="20" y="13" width="4" height="3"/>` +
  `</symbol>` +
  // failed:摔倒——甲身横躺,头侧放,X 形双眼,头上冒金星
  `<symbol id="sprite8-failed" viewBox="0 0 32 32">` +
  `<rect class="p8a" x="12" y="20" width="14" height="6"/>` +
  `<rect class="p8s" x="5" y="18" width="6" height="6"/>` +
  `<path class="p8e" d="M6.5 19.5l3 3M9.5 19.5l-3 3" fill="none" stroke-width="1.2"/>` +
  `<rect class="p8c" x="5" y="14" width="2" height="2"/>` +
  `<rect class="p8c" x="9" y="12" width="2" height="2"/>` +
  `</symbol>` +
  `</svg>`;

/* mascot 注册表:mochi(萌系团子)/ sprite8(8-bit 勇者);exprs key 全集一致
   (running/paused/done/failed/ready),ready 经 symbol 复用 paused 精灵。 */
const MASCOTS = {
  mochi: {
    name: "Mochi",
    exprs: {
      running: { label: "运行中" },
      paused: { label: "已暂停,轮到你了" },
      done: { label: "完成,撒花" },
      failed: { label: "出错啦,求抱抱" },
      ready: { label: "准备出发", symbol: "paused" }, // 调试首页待机(复用举爪精灵)
    },
    sprite: MOCHI_SPRITE,
  },
  sprite8: {
    name: "Sprite8",
    exprs: {
      running: { label: "前进!" },
      paused: { label: "待命,轮到你操作" },
      done: { label: "LEVEL CLEAR!" },
      failed: { label: "摔倒了…GAME OVER" },
      ready: { label: "PRESS ▶", symbol: "paused" }, // 调试首页待机(复用举剑精灵)
    },
    sprite: SPRITE8_SPRITE,
  },
};

/* 渲染 mascot 层:主题未声明 mascot / mascot 未知 / 未映射状态 → 空串(层整体消失) */
export function mascotHtml(expr) {
  const theme = currentTheme();
  const m = theme?.mascot ? MASCOTS[theme.mascot] : null;
  if (!m || !expr || !m.exprs[expr]) return "";
  const e = m.exprs[expr];
  const symbol = e.symbol ?? expr; // ready 等复用既有精灵
  return (
    `<span class="mascot-layer" data-mascot="${theme.mascot}" data-expr="${expr}"` +
    ` role="img" aria-label="${m.name}:${e.label}">` +
    m.sprite +
    `<svg class="mascot-svg" viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">` +
    `<use href="#${theme.mascot}-${symbol}"></use></svg></span>`
  );
}

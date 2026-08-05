/* 主题契约测试骨架(主题系统 T1,docs/DEBUG-UI-THEMES.md §4;本期做 1/2/3/4/6):
   遍历注册表逐主题断言——
   1) token 完整性:契约清单(CONTRACT_TOKENS,§2.1)每个变量在主题的
      [data-theme="<id>"] 规则下都有定义且非空(直接解析 css 源,不依赖浏览器);
   2) 对比度:状态色/文本色关键配对 ≥ 4.5:1(WCAG AA;程序化相对亮度计算,
      主题作者改色即时反馈);弱化层级(fg-2/sig-frame/perm-read)≥ 3:1;
   3) 双编码:渲染 fixture 后,状态元素同时带颜色钩子(data-status/data-on)与
      文字/图标(label 文本/●/▶),不依赖色觉单通道;
   4) 文案键完整:copy 表覆盖 COPY_KEYS 全量(T2 补全);
   6) 组件无分支:静态扫描 js/components,禁止出现主题 id 字符串/data-theme 属性
      (差异必须走契约层;T2 补全)。
   (5 动效降级待动效播放层落地后补。)
   运行:node static/tests/themes-contract.test.mjs */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { StubEl, makeDocument } from "./dom-stub.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.resolve(here, "..");

/* ── 最小全局 stub(debug-view 等模块导入用;本测试不驱动 DOM)── */
const doc = makeDocument();
const toastStack = new StubEl("div");
toastStack.setAttribute("id", "toastStack");
doc.body.appendChild(toastStack);
globalThis.document = doc;
globalThis.location = { hash: "" };
globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

const { CONTRACT_TOKENS, COPY_KEYS, listThemes } = await import("../js/themes.js");
const { statusPill } = await import("../js/components/status-pill.js");
const { renderDebugTrace } = await import("../js/components/debug-view.js");

/* ── css 源解析:合并主题全部 [data-theme="<id>"] { ... } 块的 --var 声明 ── */
function themeTokens(theme) {
  const css = readFileSync(path.join(staticDir, theme.css), "utf8");
  const tokens = {};
  const blockRe = new RegExp(`\\[data-theme="${theme.id}"\\]\\s*\\{([^}]*)\\}`, "g");
  for (const m of css.matchAll(blockRe)) {
    for (const d of m[1].matchAll(/(--[\w-]+)\s*:\s*([^;]+)/g)) {
      tokens[d[1]] = d[2].trim();
    }
  }
  return tokens;
}

/* ── WCAG 相对亮度与对比度(仅处理 #rgb/#rrggbb;非色值配对不会出现)── */
function hexRgb(v) {
  const m = String(v).trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!m) return null;
  let c = m[1];
  if (c.length === 3) c = [...c].map((ch) => ch + ch).join("");
  return [0, 2, 4].map((i) => parseInt(c.slice(i, i + 2), 16) / 255);
}
function luminance(v) {
  const [r, g, b] = hexRgb(v).map((x) =>
    x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a, b) {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

/* 关键配对(§4.2;与 app.css 实际消费面一致):
   · 文本:fg-0 全基底,fg-1 bg-0..2;
   · 状态色(ok/warn/danger/aborted/live 作 pill/标签/链接文字色):bg-0..2;
   · 信号/权限色(IconBadge/PermBadge 文字色):bg-1;
   hover 态 bg-3 不约束状态色(瞬态;两主题同政策),只约束正文色。 */
const TEXT_PAIRS = [
  ["--fg-0", "--bg-0"], ["--fg-0", "--bg-1"], ["--fg-0", "--bg-2"], ["--fg-0", "--bg-3"],
  ["--fg-1", "--bg-0"], ["--fg-1", "--bg-1"], ["--fg-1", "--bg-2"],
  ...["--ok", "--warn", "--danger", "--aborted", "--live"].flatMap((s) =>
    ["--bg-0", "--bg-1", "--bg-2"].map((bg) => [s, bg])),
  ...["--sig-llm", "--sig-tool", "--sig-sidecar", "--sig-compress", "--sig-budget",
    "--perm-write", "--perm-net", "--perm-exec"].map((s) => [s, "--bg-1"]),
];
/* 弱化层级(占位/最低权限/弹栈帧;WCAG 对非关键文本无强制,保底 3:1) */
const DIM_PAIRS = [
  ["--fg-2", "--bg-0"], ["--fg-2", "--bg-1"], ["--sig-frame", "--bg-1"], ["--perm-read", "--bg-1"],
];

const themes = listThemes();
assert.ok(themes.length >= 2, "注册表至少含 classic + moe");

for (const theme of themes) {
  /* ══ 1. token 完整性(§2.1 契约清单全量定义非空)══ */
  const tokens = themeTokens(theme);
  for (const name of CONTRACT_TOKENS) {
    assert.ok(
      tokens[name] && tokens[name].length > 0,
      `[${theme.id}] 契约变量 ${name} 未定义或为空`);
  }

  /* ══ 2. 对比度(关键配对 ≥4.5:1;弱化层级 ≥3:1)══ */
  for (const [fg, bg] of TEXT_PAIRS) {
    const ratio = contrast(tokens[fg], tokens[bg]);
    assert.ok(
      ratio >= 4.5,
      `[${theme.id}] ${fg}(${tokens[fg]}) vs ${bg}(${tokens[bg]}) = ${ratio.toFixed(2)}:1 < 4.5:1`);
  }
  /* ══ 2b. 深面板配对(F1 验收:W-md 代码块/W-log 面板内文字 = --log-fg,
     底色 = --log-bg,六主题必须 ≥4.5:1——亮主题主 fg 是深色,压暗面板
     须用亮色正文 token)══ */
  for (const [fg, bg] of [["--log-fg", "--log-bg"]]) {
    const ratio = contrast(tokens[fg], tokens[bg]);
    assert.ok(
      ratio >= 4.5,
      `[${theme.id}] ${fg}(${tokens[fg]}) vs ${bg}(${tokens[bg]}) = ${ratio.toFixed(2)}:1 < 4.5:1(F1 深面板)`);
  }
  for (const [fg, bg] of DIM_PAIRS) {
    const ratio = contrast(tokens[fg], tokens[bg]);
    assert.ok(
      ratio >= 3,
      `[${theme.id}] ${fg}(${tokens[fg]}) vs ${bg}(${tokens[bg]}) = ${ratio.toFixed(2)}:1 < 3:1`);
  }

  /* ══ 3. 双编码(颜色钩子 + 文字/图标,不依赖色觉单通道)══ */
  for (const st of ["done", "failed", "aborted", "running", "paused"]) {
    const pill = statusPill(st);
    assert.match(pill, new RegExp(`data-status="${st}"`), `[${theme.id}] pill ${st} 缺颜色钩子`);
    assert.match(
      pill, /<span class="pill-label">[^<]+<\/span>/, `[${theme.id}] pill ${st} 缺文字通道`);
  }
  const traceHtml = renderDebugTrace(
    [{ line: 1, kind: "tool", depth: 1, label: "t", detail: "", durMs: null,
      frameId: "f1", sigIndex: 0, sigEnd: 0, step: 1, payload: { pre: { tool: "t" } },
      ts: 0, names: "pre:tool.call" }],
    { breakpoints: [{ kind: "tool_call", match: "t", enabled: true, id: "bx", hits: 1 }],
      pausedLine: 1 });
  assert.match(traceHtml, /data-on="true"/, `[${theme.id}] 断点 gutter 缺颜色钩子`);
  assert.match(traceHtml, /aria-pressed="true"/, `[${theme.id}] 断点 gutter 缺 aria 通道`);
  assert.match(traceHtml, /">●<\/button>/, `[${theme.id}] 断点 gutter 缺图标通道(●)`);
  assert.match(traceHtml, /data-paused="true"/, `[${theme.id}] 暂停行缺颜色钩子`);
  assert.match(
    traceHtml, /<span class="tr-mark" aria-hidden="true">▶<\/span>/,
    `[${theme.id}] 暂停行缺图标通道(▶)`);

  /* ══ 4. 文案键完整(§2.2:copy 表覆盖 COPY_KEYS 全量)══ */
  const missingKeys = COPY_KEYS.filter((k) => !(k in (theme.copy ?? {})));
  assert.deepEqual(missingKeys, [], `[${theme.id}] 文案 key 缺失:${missingKeys.join(", ")}`);

  /* ══ 7. 焦点环对比度(docs/WEB-A11Y.md §5.2;WCAG 1.4.11 非文本对比 ≥3:1)══
     焦点环要在**任何**背景层上都看得见——四层全查,而不是只查主背景。 */
  for (const bg of ["--bg-0", "--bg-1", "--bg-2", "--bg-3"]) {
    const ratio = contrast(tokens["--focus-ring"], tokens[bg]);
    assert.ok(
      ratio >= 3,
      `[${theme.id}] 焦点环 --focus-ring(${tokens["--focus-ring"]}) vs ${bg}` +
      `(${tokens[bg]}) = ${ratio.toFixed(2)}:1 < 3:1(WCAG 1.4.11)`);
  }
}

/* ══ 6. 组件无分支(§2:差异必须走契约层;静态扫描 js/components)══
   禁:主题 id 字符串字面量、data-theme 属性、按主题 id 的比较。
   例外:"terminal" 字面量同时是 emptyBlock 的图标名,故只禁其比较形态。 */
{
  const { readdirSync } = await import("node:fs");
  const componentsDir = path.join(staticDir, "js/components");
  const themeIds = listThemes().map((t) => t.id);
  const quoted = themeIds.filter((id) => id !== "terminal").join("|");
  const FORBIDDEN = [
    { re: /\bdata-theme\b/, desc: "data-theme 属性" },
    { re: new RegExp(`\\btheme\\s*===?\\s*["'](${themeIds.join("|")})["']`), desc: "按主题 id 比较" },
    { re: new RegExp(`["'](${quoted})["']`), desc: "主题 id 字符串字面量" },
  ];
  for (const file of readdirSync(componentsDir)) {
    if (!file.endsWith(".js")) continue;
    const src = readFileSync(path.join(componentsDir, file), "utf8");
    for (const { re, desc } of FORBIDDEN) {
      assert.ok(!re.test(src), `组件无分支:${file} 出现 ${desc}`);
    }
  }
}

/* ══ 8. 焦点可见性的策略常量与不可削弱性(docs/WEB-A11Y.md §5.2)══
   宽度/偏移/目标尺寸是**策略**不是风格:只在 tokens.css :root 定义一份,
   主题不得覆盖——允许主题改宽度,就等于允许主题把焦点环调成 0 悄悄消失。
   同时守住 app.css 的全局规则与 forced-colors 段存在(防被"清理"掉)。 */
{
  const tokensCss = readFileSync(path.join(staticDir, "css/tokens.css"), "utf8");
  const rootBlock = tokensCss.match(/:root\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  const rootTokens = {};
  for (const d of rootBlock.matchAll(/(--[\w-]+)\s*:\s*([^;]+)/g)) rootTokens[d[1]] = d[2].trim();

  const MINIMA = [
    ["--focus-ring-width", 2, "WCAG 2.4.11 焦点外观下限"],
    ["--focus-ring-offset", 1, "环不被相邻内容压住"],
    ["--target-min", 24, "WCAG 2.2 §2.5.8 目标尺寸(最小)"],
  ];
  for (const [name, min, why] of MINIMA) {
    const px = parseFloat(rootTokens[name] ?? "");
    assert.ok(
      Number.isFinite(px) && px >= min,
      `tokens.css :root 的 ${name} 应为 ≥${min}px(${why}),实为 ${rootTokens[name]}`);
  }

  /* 主题不得覆盖策略常量 */
  for (const theme of listThemes()) {
    const t = themeTokens(theme);
    for (const [name] of MINIMA.slice(0, 2)) {
      assert.ok(
        !(name in t),
        `[${theme.id}] 不得覆盖 ${name}——它是策略常量,只在 tokens.css :root 定义`);
    }
  }

  /* app.css 的全局焦点规则与高对比度兜底必须在场 */
  const appCss = readFileSync(path.join(staticDir, "css/app.css"), "utf8");
  assert.match(
    appCss, /:focus-visible\s*\{[^}]*outline:\s*var\(--focus-ring-width\)/,
    "app.css 缺全局 :focus-visible outline 规则(docs/WEB-A11Y.md §5.2)");
  assert.match(
    appCss, /@media\s*\(forced-colors:\s*active\)/,
    "app.css 缺 forced-colors 段——box-shadow 焦点环在 Windows 高对比度下会被丢弃");

  /* 任何样式表都不得抑制 outline(docs/WEB-A11Y.md §4 P0-2 / §5.3 A7)。
     全局焦点环靠 outline 实现,而主题在 app.css **之后**加载——一句
     `outline: none` 就能把整套主题的焦点指示悄悄抹掉,且在高对比度模式下
     没有 box-shadow 兜底。光晕效果请**叠加** box-shadow,不要替代 outline。 */
  const allCss = ["css/tokens.css", "css/app.css",
    ...listThemes().map((t) => t.css)];
  for (const rel of allCss) {
    const src = readFileSync(path.join(staticDir, rel), "utf8");
    const hit = src.match(/outline:\s*(none|0)\b/);
    assert.ok(
      !hit,
      `${rel} 出现 ${hit?.[0]}——焦点环靠 outline 实现,不得抑制` +
      `(要光晕请叠加 box-shadow;docs/WEB-A11Y.md §5.3 A7)`);
  }

  /* skip link 必须在场且落点可编程聚焦(docs/WEB-A11Y.md §4 P1-6) */
  const indexHtml = readFileSync(path.join(staticDir, "index.html"), "utf8");
  assert.match(indexHtml, /class="skip-link"[^>]*href="#main"/, "index.html 缺跳过导航链接");
  assert.match(
    indexHtml, /<main[^>]*id="main"[^>]*tabindex="-1"/,
    'skip link 落点 <main> 缺 tabindex="-1"(否则只滚动不移焦)');
}

console.log("themes-contract.test.mjs: all assertions passed");

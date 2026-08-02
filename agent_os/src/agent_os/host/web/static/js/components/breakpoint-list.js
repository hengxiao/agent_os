/* 断点列表(Agent OS Debugger P4,#/debug/<sid> 左下栏;计划 §4 ASCII):
   每条 = enabled 勾选(状态反射,P3 无启用/停用端点,只读)+ kind chip +
   match(glob,mono)+ hits 计数 + 删除钮;底部新增断点小表单
   (kind 下拉 + match glob 输入;kind=step/error 时 match 无意义,禁用)。

   纯函数(不碰 DOM,node 单测可载):
     BREAKPOINT_KINDS              断点 kind 全集(与 kernel/debug.py 同序)
     matchEditable(kind)           该 kind 的 match 是否可编辑(step/error 忽略 match)
     renderBreakpoints(breakpoints) 断点列表 HTML(空态引导)
     renderBpForm()                新增断点表单 HTML(kind 下拉 + match 输入 + 添加钮)
   事件(添加/删除)由 debug-view 经事件委托接线(data-action="dbg-bp-add"/"dbg-bp-del")。 */

import { esc } from "../util.js";
import { copy } from "../themes.js";

/* 断点 kind 全集(与 kernel/debug.py BREAKPOINT_KINDS 一致;顺序即下拉顺序) */
export const BREAKPOINT_KINDS = ["step", "tool_call", "skill_invoke", "error"];

const KIND_LABELS = {
  step: "step",
  tool_call: "tool",
  skill_invoke: "skill",
  error: "error",
};

/* kind=step/error 时 match 被内核忽略(kernel/debug.py:仅 tool_call/skill_invoke 做 glob) */
export const matchEditable = (kind) => kind === "tool_call" || kind === "skill_invoke";

/* 单条断点行:勾选(enabled 状态反射,只读——P3 无启用/停用端点)+ kind + match +
   hits(命中计数,SSE bp_hit 事件实时刷新)+ 删除钮。 */
function bpRowHtml(bp) {
  const b = bp ?? {};
  return (
    `<div class="dbg-bp" data-bp-id="${esc(b.id)}">` +
    `<input type="checkbox" class="dbg-bp-enabled" disabled${b.enabled ? " checked" : ""}` +
    ` title="启用状态(P3 暂无启用/停用端点,仅作状态显示)" aria-label="断点启用状态">` +
    `<span class="dbg-bp-kind" data-kind="${esc(b.kind)}">${esc(KIND_LABELS[b.kind] ?? b.kind)}</span>` +
    `<span class="dbg-bp-match mono" title="${esc(b.match)}">${esc(b.match)}</span>` +
    `<span class="dbg-bp-hits" title="命中次数">×${Number(b.hits) || 0}</span>` +
    `<button class="icon-btn dbg-bp-del" data-action="dbg-bp-del" data-bp-id="${esc(b.id)}"` +
    ` title="删除断点" aria-label="删除断点 ${esc(b.kind)} ${esc(b.match)}">✕</button>` +
    `</div>`
  );
}

/* 断点列表整体 HTML;空态给"从轨迹行 gutter 点击也可打断点"的引导。
   主题 T1:空态文案走文案表 copy("bp.empty")(§2.2)。 */
export function renderBreakpoints(breakpoints) {
  const list = Array.isArray(breakpoints) ? breakpoints : [];
  if (!list.length) {
    return (
      `<div class="dbg-bp-empty">` +
      esc(copy("bp.empty")) +
      `</div>`
    );
  }
  return list.map(bpRowHtml).join("");
}

/* 新增断点小表单:kind 下拉 + match glob 输入 + 添加钮。
   match 输入的禁用态跟随 kind(debug-view 的 change 委托切 dbg-bp-add-form)。 */
export function renderBpForm() {
  return (
    `<div class="dbg-bp-form">` +
    `<select class="input dbg-bp-add-kind" aria-label="断点类型">` +
    BREAKPOINT_KINDS.map(
      (k) => `<option value="${k}">${esc(KIND_LABELS[k] ?? k)}</option>`).join("") +
    `</select>` +
    `<input class="input mono dbg-bp-add-match" placeholder="glob,如 system.*" disabled` +
    ` aria-label="断点匹配 glob">` +
    `<button class="btn" data-action="dbg-bp-add" title="添加断点">添加</button>` +
    `</div>`
  );
}

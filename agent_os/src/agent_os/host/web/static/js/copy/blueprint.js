/* blueprint 文案表(docs/DEBUG-UI-THEMES.md §3.4):制图腔,克制不卖萌。
   key 清单与 classic 完全一致;技术文本豁免——状态原文/错误原文/工具参数
   由组件照常并列直渲(本表不吞),换了说法的状态标签把技术原文并列在括号里。 */

export const COPY = {
  "status.paused": "HALT(paused)",
  "status.running": "RUNNING",
  "status.done": "APPROVED(done)",
  "status.failed": "REJECT(failed)",
  "status.aborted": "VOID(aborted)",
  "status.unknown": "N/A",
  "bp.empty": "图面无标注——在下方添加,或点击轨迹 gutter",
  "intervene.modified": "已改注并放行(REV+1)",
  "intervene.injected": "已批注并放行",
  "session.waiting": "出图准备中…",
  "home.submit": "开图调试 ▶",
  "home.submitting": "晒图中…",
  "home.sessions.empty": "图档为空",
  "home.sessions.empty.hint": "选择技能与输入,可预置标注",
  "debug.end.title": "run 已归档",
  "escalation.params": "调用参数(已校)",
  "escalation.requested": "申请权限面",
};

/* terminal 文案表(DEBUG-UI-THEMES.md §2.2 / §3.3):shell 腔,状态带方括号。
   key 清单与 classic 完全一致;技术文本豁免——状态原文/错误原文/工具参数
   由组件照常并列直渲(换了说法的状态把技术原文并列在括号里,moe 模式)。 */

export const COPY = {
  // 状态短语(StatusPill 标签;色点 + 文字双编码的文字通道)
  "status.paused": "[halted](paused)",
  "status.running": "[running]",
  "status.done": "[done]",
  "status.failed": "[error](failed)",
  "status.aborted": "[aborted]",
  "status.unknown": "[?]",
  // 空断点列表(含操作引导)
  "bp.empty": "no bps. add one below, or click a trace gutter.",
  // 干预成功(Modify / Inject 提交放行后的 Toast)
  "intervene.modified": "args patched. resumed.",
  "intervene.injected": "message injected. resumed.",
  // 等待就绪(调试台加载态的 aria 提示)
  "session.waiting": "attaching session…",
  // 调试首页:提交按钮两态 + 活跃会话空态(标题/引导)
  "home.submit": "run --debug ▶",
  "home.submitting": "attaching…",
  "home.sessions.empty": "no debug sessions.",
  "home.sessions.empty.hint": "select skill + input above; preset bps optional.",
  // 调试台:run 结束横幅标题(状态原文由组件并列直渲,本 key 不吞)
  "debug.end.title": "run finished",
};

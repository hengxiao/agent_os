/* classic 文案表(DEBUG-UI-THEMES.md §2.2):现状文案的抽离,行为零变化。
   规则(继承萌系策划 §6):技术文本豁免——错误原文、状态原文、工具参数永远直读,
   不进文案表;本表只是"翻译层"。新增 key 需两主题表同步(themes.js 注册时校验覆盖)。 */

export const COPY = {
  // 状态短语(StatusPill 标签;色点 + 文字双编码的文字通道)
  "status.paused": "已暂停",
  "status.running": "进行中",
  "status.done": "完成",
  "status.failed": "失败",
  "status.aborted": "中止",
  "status.unknown": "未知",
  // 空断点列表(含操作引导)
  "bp.empty": "还没有断点——在下方添加,或点击中间轨迹行首的 gutter",
  // 干预成功(Modify / Inject 提交放行后的 Toast)
  "intervene.modified": "已修改工具参数并放行",
  "intervene.injected": "已注入消息并放行",
  // 等待就绪(调试台加载态的 aria 提示)
  "session.waiting": "装配中,等待会话就绪…",
};

/* pixel 文案表(docs/DEBUG-UI-THEMES.md §3.6):8-bit 游戏腔。
   key 清单与 classic 完全一致;技术文本豁免——错误原文/状态原文/工具参数
   由组件照常并列直渲(本表不吞);换了说法的状态标签把技术原文并列在括号里。 */

export const COPY = {
  // 状态短语(StatusPill 标签;方块色点 + 文字双编码的文字通道)
  "status.paused": "PAUSED",
  "status.running": "GO!",
  "status.done": "CLEAR!",
  "status.failed": "GAME OVER(failed)",
  "status.aborted": "QUIT(aborted)",
  "status.unknown": "???",
  // 空断点列表(checkpoint = 断点)
  "bp.empty": "no checkpoints — add one below, or click a gutter",
  // 干预成功(Modify / Inject 提交放行后的 Toast)
  "intervene.modified": "CHEAT: args modified. GO!",
  "intervene.injected": "MESSAGE INJECTED. GO!",
  // 等待就绪(调试台加载态的 aria 提示)
  "session.waiting": "LOADING…",
  // 调试首页:提交按钮两态 + 活跃会话空态(标题/引导)
  "home.submit": "START ▶",
  "home.submitting": "LOADING…",
  "home.sessions.empty": "NO SAVE DATA",
  "home.sessions.empty.hint": "choose skill + input to start your quest",
  // 调试台:run 结束横幅标题(状态原文由组件并列直渲,本 key 不吞)
  "debug.end.title": "QUEST END",
  "escalation.params": "PARAMS (validated)",
  "escalation.requested": "ASKED POWERS",
  // Skill Lab(docs/SKILL-DEV.md;L1):编辑器分组/顶部条/状态栏/空态
  "lab.group.identity": "ID",
  "lab.group.contract": "STATS",
  "lab.group.instruction": "SCROLL",
  "lab.group.permissions": "POWERS",
  "lab.group.policy": "TUNING",
  "lab.group.trust": "TRUST",
  "lab.group.behavior": "FLAGS",
  "lab.new": "NEW+",
  "lab.new.empty": "blank sheet",
  "lab.delete": "DROP",
  "lab.delete.confirm": "DROP for sure?",
  "lab.check": "CHECK",
  "lab.promote": "SHIP",
  "lab.save": "SAVE",
  "lab.saved": "SAVED",
  "lab.uncommitted": "draft (not shipped)",
  "lab.json.invalid": "bad JSON",
  "lab.name.invalid": "name: >=2 dotted lowercase parts (docs/NAMING.md)",
  "lab.inline.blocked": "inline locked: tier >= L2 (docs/ESCALATION.md §3.4)",
  "lab.no.selection": "NO SAVE DATA",
  "lab.no.selection.hint": "name a draft above, or copy from production",
  "lab.agent.empty": "sidekick (L4)",
  "lab.test.empty": "test arena (L3)",
};

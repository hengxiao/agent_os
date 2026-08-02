/* terminal 文案表(docs/DEBUG-UI-THEMES.md §2.2 / §3.3):shell 腔,状态带方括号。
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
  "escalation.params": "argv (validated)",
  "escalation.requested": "requested caps",
  // Skill Lab(docs/SKILL-DEV.md;L1):编辑器分组/顶部条/状态栏/空态
  "lab.group.identity": "id",
  "lab.group.contract": "schema",
  "lab.group.instruction": "prompt",
  "lab.group.permissions": "caps",
  "lab.group.policy": "policy",
  "lab.group.trust": "trust",
  "lab.group.behavior": "flags",
  "lab.new": "new",
  "lab.new.empty": "empty template",
  "lab.delete": "rm",
  "lab.delete.confirm": "rm -i: confirm?",
  "lab.check": "lint",
  "lab.promote": "commit",
  "lab.save": "write",
  "lab.saved": "written",
  "lab.uncommitted": "draft (uncommitted)",
  "lab.json.invalid": "invalid JSON",
  "lab.name.invalid": "name: >=2 dotted lowercase segments (docs/NAMING.md)",
  "lab.inline.blocked": "inline blocked: tier >= L2 (docs/ESCALATION.md §3.4)",
  "lab.no.selection": "no drafts",
  "lab.no.selection.hint": "create above, or copy from a production skill",
  "lab.agent.empty": "agent assistant (L4)",
  "lab.test.empty": "test panel (L3)",
  // Skill Lab 闸门(docs/SKILL-DEV.md §1.4;L2):五关/确认/过期提示
  "lab.gate.g1": "G1 metadata",
  "lab.gate.g2": "G2 schema",
  "lab.gate.g3": "G3 tier",
  "lab.gate.g4": "G4 smoke",
  "lab.gate.g5": "G5 hygiene",
  "lab.gate.pass": "pass",
  "lab.gate.warn": "warn",
  "lab.gate.fail": "fail",
  "lab.gate.skip": "skip",
  "lab.gate.ack": "warnings read",
  "lab.promote.confirm": "confirm ship",
  "lab.promote.done": "shipped",
  "lab.promote.version": "version (empty = auto bump)",
  "lab.cancel": "abort",
  "lab.status.stale": "dirty since last check ⚠",
};

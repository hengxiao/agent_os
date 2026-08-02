# Agent OS 技术文档索引

本目录是仓库全部技术文档的归档入口,按主题分组。设计文档互相引用时
同级直接用文件名;代码注释统一用仓库根相对路径(`docs/<NAME>.md`)。

## 升权与权限

- [ESCALATION.md](ESCALATION.md) — Skill 升权系统设计稿:三档信任层级(none/reversible/irreversible)、升权判定与挂起确认、approve-once/run Grant、干净 context 不变量(E1/E2 已实现)。
- [TIER-STANDARDS.md](TIER-STANDARDS.md) — 分档工程标准:L1/L2/L3 的 skill 与 tool 生产规范(定档判定树、逐档 design/implement/test 要求、PR checklist、反模式)。
- [DATA-AUTHZ.md](DATA-AUTHZ.md) — 数据层 authN+Z 设计稿:Principal 身份模型、数据域与三级敏感度、dispatch 强制点、默认拒绝(D1 已实现)。
- [SUPERVISOR.md](SUPERVISOR.md) — supervisor 裁决通道协议:ask_supervisor 伪工具、pending 挂起闭环、超时兜底、收件箱/CLI 宿主通道。

## 调试台与主题

- [DEBUGGER.md](DEBUGGER.md) — 内核调试器设计:断点/单步/检视、暂停-恢复语义、调试会话与 Web 调试台接线。
- [DEBUG-UI-THEMES.md](DEBUG-UI-THEMES.md) — 主题系统契约:语义 token、copy(key) 文案表、组件零分支红线。
- [DEBUG-UI-MOE.md](DEBUG-UI-MOE.md) — 萌系(moe)主题策划:文案腔调与吉祥物层的个例规范。
- [WEB-UI.md](WEB-UI.md) — Web UI 总体设计:页面结构、权限徽标色板(--perm-*)、收件箱与 run 详情契约。
- [WEB-UI-BLOCKS.md](WEB-UI-BLOCKS.md) — Web UI 组件块清单:各视图块的职责与拼装约束。

## 架构与规范

- [WHITEPAPER.md](WHITEPAPER.md) — Agent OS 技术白皮书(中文版):原型、核心理念、架构、子系统、信任与安全模型、解决的问题;英文版 [WHITEPAPER.en.md](WHITEPAPER.en.md)。
- [SKILL-DEV.md](SKILL-DEV.md) — Skill 开发平台(Skill Lab)方案:草稿存储(DraftStore/OverlayRegistry)、全字段编辑器、Agent 助手(skill.dev.assistant)、测试面板、五关提交闸门。
- [DESIGN.md](DESIGN.md) — 微内核总体设计:九子系统契约、agent loop、帧模型、权限三层交集、信号与记账(全仓架构基准)。
- [NAMING.md](NAMING.md) — 命名规范:`<域>.<动作>[.<对象>]` 点分命名与动词-副作用对应规则。
- [RUNNERS.md](RUNNERS.md) — 宿主运行器:CLI/Web 薄宿主、配置文件(agent-os.toml)各段语义、退出码契约。
- [SKILL-INLINING.md](SKILL-INLINING.md) — 技能内联(merge)机制:prompt 并入调用方 SYSTEM 的纯度闸门与快照冻结。
- [CODE-ORCHESTRATION.md](CODE-ORCHESTRATION.md) — python_orchestrate 编排:沙箱脚本 + syscall 通道、无权限提升的编排模型。
- [STDLIB.md](STDLIB.md) — 标准技能库(std/)总纲:域划分与技能面规划。
- [STDLIB-CATALOG.md](STDLIB-CATALOG.md) — 标准库目录:逐技能的契约、波次(W0-W4)与门槛声明。

## 报告与研究

- [reports/](reports/) — 书稿章节笔记(ch00-ch10)与审计/评审报告(code-quality-audit、dev-status、usability-review、stdlib-vs-agent-book、synthesis-microkernel)。
- [research/](research/) — 前期调研:library-design-plan(标准库设计方案)、stdlib-research(标准库调研)。

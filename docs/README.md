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
- [WEB-A11Y.md](WEB-A11Y.md) — 无障碍审计报告与执行标准(WCAG 2.2 AA):现状盘点(对比度/双编码已机检)、P0-P2 问题清单(焦点陷阱/焦点可见/状态播报)、三层标准(token 契约 / 组件不变量静态扫描 / 交互模式库)、PR checklist、分期 A0-A4。

## 架构与规范

- [whitepaper/](whitepaper/) — Agent OS 技术白皮书(章节制 v2.0):执行摘要 + 15 个分项目深度章节(原因/问题/方法/效果/局限五段式),中英双版本,附校勘记。
- [SKILL-DEV.md](SKILL-DEV.md) — Skill 开发平台(Skill Lab)方案:草稿存储(DraftStore/OverlayRegistry)、全字段编辑器、Agent 助手(skill.dev.assistant)、测试面板、五关提交闸门。
- [SKILL-PACKAGES.md](SKILL-PACKAGES.md) — 能力包设计报告(v0.1):用户心智单位是"功能"而非技能;包 = 根技能的依赖闭包(推导不声明);包视图/包级闸门/原子提交/后端配合清单。
- [SKILL-PACKAGES-V2.md](SKILL-PACKAGES-V2.md) — 能力包详细设计(v2.1,设计依据 + 实施记录;P1-P4 已落地):业界十系统对照(Nix/Helm/Claude Plugin/Salesforce/VS Code/Agentforce/Terraform/ComfyUI/Agent Skills/GlassWorm)、四份方案与推荐路线、编辑闭包 vs 运行闭包、提交计划与包哈希、先证后换的原子事务、依赖变更档位告警、注册表分层与草稿身份、与并行稿的对照裁决。
- [LAB-ITERATION.md](LAB-ITERATION.md) — Lab 迭代工作流 spec(v0.2):访谈初始化(brief)→ 首稿(包+测试+LLM judge rubric)→ 批注式迭代(左既有/中批注/右候选)→ 版本回溯;judge 作为技能走内核(不开第二条 LLM 路径)、包级状态存 `drafts/.packages/<root>/`、candidate 围栏与 accept 越界校验;数据模型、API/前端改动、状态机、分期 V0-V4。
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

## Lab 与能力包(Skill Dev / Packages)

- [SKILL-DEV.md](SKILL-DEV.md) — Skill Lab 设计方案:草稿存储/编辑器/五关闸门/promote/测试面板/Agent 助手(L1-L5 已实现)。
- [SKILL-PACKAGES.md](SKILL-PACKAGES.md) — 能力包设计报告:包 = 根技能依赖闭包,闭包 API/包视图/原子提交/助手包级化/set 落盘(P1-P4 已实现)。
- [SKILL-PACKAGES-V2.md](SKILL-PACKAGES-V2.md) — 能力包详细设计:业界对照与四方案,推荐路线(两闭包/两阶段严格性/plan/先证后换,P2 按此实现)。
- [LAB-ITERATION.md](LAB-ITERATION.md) — Lab 迭代工作流 spec:访谈初始化 → 首稿 → 批注式迭代 → 版本回溯(spec v0.2;功能样板已实现:边注存储/版本快照 rewind/iterate 生成端点/lab.cand.write/Flow C 前端;包级 `.packages` 布局、访谈初始化与 judge 属 V1+ 未做)。
- [WEB-PLATFORM.md](WEB-PLATFORM.md) — Web Platform(方案 A 对话中枢)技术档案:会话模型/产物卡协议/意图编排/卡片动作白名单(后端骨架已实现,前端下一步)。
- [APP-MODEL.md](APP-MODEL.md) — App 化 UI 模型:app/双表面/exec 三态/shell as app/§14 寻址/§16 cascade(M1-M5 已实现)。
- [WIDGETS.md](WIDGETS.md) — 基础 Widget 库设计:widget 协议/注册表/W-text 至 W-chart 十二控件(W1-W4 已实现)。
- [DOC-EDITOR.md](DOC-EDITOR.md) — 文档编辑器(doc app kind):doc_store/双表面编辑器/段落气泡/评审气泡雨/NOTES 接点/导出(D1-D4 已实现;含架构实弹测试报告终版)。
- [LAB-ITERATION-FLOWS.md](LAB-ITERATION-FLOWS.md) — 迭代交互流对比报告:三栏批注 / 对话驱动 / diff+边注 三方案对比与选型指引(静态原型见 `agent_os/src/agent_os/host/web/static/proto/`)。
- [FLOWS-EVAL.md](FLOWS-EVAL.md) — 二十个使用流程评测目录:评分表 + F01-F20 流程定义(驱动脚本 `scripts/flow_eval.py`),附循环 1 三轮实测记录与缺陷清单(B1-B7)。
- [FLOWS-OPTIMIZATION.md](FLOWS-OPTIMIZATION.md) — 二十流程评测的优化目标与项目计划:循环 2 目标 O1-O7、循环 3 节点 N1-N7 与测试库定义(循环 4 记录:N1-N7 已全部落地)。
- [PLAN-ANNOTATION-WORKFLOW-V2.md](PLAN-ANNOTATION-WORKFLOW-V2.md) — 批注批处理工作流 v2 执行计划(P1-P5 已落地):Annotation 单条模型与状态机、generate 批处理端点、reanchor 三策略、批注卡简化、Diff 视图与版本历史面板。
- [AGENTIC-UI.md](AGENTIC-UI.md) — Agentic 时代 UI/UX 重设计:"人是操作员 vs 指挥者"诊断、三套方案(A 对话中枢 / B 任务中心 / C 活动流工作台)、逐部分流程再造、推荐路线(C 先做 → B 概念 → A 主干)。

## Widget 与桌面

- [WIDGET-ARCH.md](WIDGET-ARCH.md) — Widget 基座重构计划(W5):渲染与逻辑分离,widget 从装饰器改为自渲染组件(渲染纯函数化、逻辑不碰 DOM)。
- [WIDGET-DESIGN.md](WIDGET-DESIGN.md) — Widget 视觉与交互终稿(验收基准;效果图见 `docs/widgets/design/*.svg`),逐控件验收清单。
- [COMPOUND-WIDGET.md](COMPOUND-WIDGET.md) — Compound Widget 协议:层级组装体系(子 widget/渲染组合/连接/context 下发/动态生灭/多视图 hard link),根是 desktop widget。
- [DESKTOP-WIDGET.md](DESKTOP-WIDGET.md) — Desktop Widget 设计(C4):层级体系的根 compound;platform 壳迁为 desktop,app = 动态子件,任务栏/图标/窗口为子件的 card/tab 面。
- [DOC-BUBBLE.md](DOC-BUBBLE.md) — Text ↔ Bubble 交互协议(实然文档):v4 批注卡(pending/applied/ignored/outdated 状态机,annotations 端点,generate 批处理),逐节标注代码出处。

## 游戏引擎 UI(Godot)

- [GAME-UI-DOC.md](GAME-UI-DOC.md) — 游戏引擎 Doc Editor 阅览与展现设计(信件模型;示例图见 `docs/game-ui/*.svg`)。
- [GAME-UI-FLOWS.md](GAME-UI-FLOWS.md) — 信件版使用流定义:Godot 信件宿主的全部用户流(每个用户行为的流程/所见/进度/可知性)。

## 终端 TUI

- [TUI-DOC.md](TUI-DOC.md) — 终端 TUI Doc Editor 设计稿(第四宿主·终端信件模型):使用原则(信纸不可直改)、内核 Python 对译 + cell buffer 呈现层、KeymapPack(vim/emacs 双群体)、动效/主题终端映射、里程碑 T1-T5(T1-T3 已实现)。
- [TUI-DEBUG.md](TUI-DEBUG.md) — 终端 TUI Agent 调试器设计稿(GDB 操作模型·以 skill 调用栈为基础):常驻命令窗 + GDB 命令方言(裸 Enter 重复/C-c pause/kill 两段确认)、GDB TUI 式窗口布局(轨迹窗/调用栈/断点表/命令窗)、DebugSource 分层(Online/Offline/Demo)、SSE→queue→主循环 drain 的 live 注入纪律、里程碑 D1-D5。

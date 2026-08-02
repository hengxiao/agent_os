# Agent OS 技术白皮书(章节制)

> 版本:v2.0(2026-08)
> 构成:执行摘要(00)+ 15 个分项目深度章节(01-15),中英双版本
> 体例:每章七节——概述 / 动机与背景(原因) / 问题陈述(解决的问题) /
>   设计与机制(解决的方法) / 效果与验证(效果) / 局限性与边界(局限性) / 引用
> 纪律:一切论断可回溯到源码或文档(path 或 path:line);严格区分"已实现"与
>   "已设计未实现";资料与代码矛盾时以代码为准并加注(见 §校勘记)

## 阅读指南

- 想快速了解全貌:读 [00 执行摘要](zh/00-executive-summary.md)(约 15 分钟);
- 想研究某个系统:按下表直入对应章节;各章自足,可乱序阅读;
- 想评审安全性:优先 09(升权)→ 10(数据 authZ)→ 11(生产标准与闸门)→ 03(工具仲裁);
- 想评审工程质量:优先 06(确定性)→ 01(微内核)→ 12(调试器)→ 15(宿主);
- English edition: [en/](en/) 目录下同名章节(parallel versions, not literal translations)。

## 章节目录

| 章 | 主题 | 状态 |
|---|---|---|
| [00](zh/00-executive-summary.md) | 执行摘要:原型、公理、架构总览、问题-机制映射 | — |
| [01](zh/01-microkernel.md) | 微内核与执行模型:F/P/I 判据、agent loop、帧栈、safe-point、恢复熔断 | 已实现 |
| [02](zh/02-skills.md) | Skills:manifest 契约、加载流水线、伪工具与内联纯度闸门 | 已实现 |
| [03](zh/03-tools.md) | Tools:分发流水线、三层权限交集、side_effect 声明、路径沙箱 | 已实现 |
| [04](zh/04-logic-kernel.md) | Logic Kernel 与编排沙箱:信任路由、syscall 通道、无权限提升 | 已实现 |
| [05](zh/05-context.md) | Context:组装、压缩(rolling-window)、前缀缓存稳定性 | 已实现(基线) |
| [06](zh/06-determinism.md) | 信号、Telemetry 与确定性工程:31 信号目录、WAL、checkpoint/resume/replay/diff | 已实现 |
| [07](zh/07-sidecars.md) | Sidecars:信号驱动监督、verdict 仲裁矩阵、双预算结构 | 部分实现(HumanApproval 骨架) |
| [08](zh/08-supervisor.md) | Supervisor:裁决路由、挂起-作答-恢复闭环、三宿主通道 | 已实现 |
| [09](zh/09-escalation.md) | 升权系统:三档信任、升权闸、Grant、干净 context 不变量 | 已实现(E1/E2) |
| [10](zh/10-data-authz.md) | 数据层 authN+Z:Principal、数据域、dispatch 双闸串联、默认拒绝 | 部分实现(D1) |
| [11](zh/11-tier-standards.md) | 分档生产标准与提交闸门:判定树、逐档标准、五关、promote 三重防 | 已实现 |
| [12](zh/12-debugger.md) | 调试器:GDB 语义、断点四类、单步/pause、干预与时间旅行 | 已实现 |
| [13](zh/13-themes.md) | 主题系统:三层契约、六主题、mascot 抽象两实例 | 已实现(动效层设计) |
| [14](zh/14-skill-lab.md) | Skill Lab:草稿层、五关闸门、Agent 助手(能改不能发)、测试面板 | 已实现 |
| [15](zh/15-hosts.md) | 宿主:CLI/Web 薄宿主、产物四件套、RunRecord、线程模型 | 已实现 |

## 校勘记(章节写作中发现并处置的资料-代码矛盾)

各章作者按"以代码为准"处置,以下为较重要的条目;完整清单散见各章 §6/文末注。

- **伪工具前缀**:DESIGN.md §3.3 写 `skill__<name>`,代码实际生成 `skill.<name>`
  (runner 两种前缀都受理);各章统一按代码写(第 01/02 章注)。
- **执行摘要三处失真(已修正)**:调试器断点实为四类(漏 `skill.invoke`);
  `lab validate` 退出码实为 pass/warn=0、fail=2(无 4);`[ESCALATED:...]`
  返回标记已设计未实现(第 09/12/14 章勘,00 章已改)。
- **版本约束求解未实现**:DESIGN.md §6.1 承诺 semver 求解,代码只查依赖存在
  (第 02 章注)。
- **副车仲裁面小于设计**:`pre:skill.invoke`/`pre:llm.request`/`pre:compress`
  只发射不仲裁;`budget.warning/exceeded` 无发射点(第 07 章注)。
- **数据层 D1 偏差**:未配置域 = 不拦截(设计原文为 confidential;D2 恢复);
  `system.shell.exec` 不在数据闸覆盖面(第 10 章注)。
- **supervisor 挂起非状态机迁移**:run 保持 RUNNING,无 PAUSED 迁移
  (第 08 章注,以 runner.py:607-610 注释为准)。
- **RUNNERS.md 多处过时**:asyncio task → 实际每 run 独立线程;`stderr.log`
  不落盘;`trace --kind`/`resume <run_id>`/`--sandbox` 未实现(第 15 章注)。
- **Docker 后端不支持编排**:`dispatch_fn` 被忽略,编排仅属 subprocess 后端
  (第 04 章注)。
- **gate.py / test_gate.py docstring 残文**:G4/G5 "skip 占位"系 L2 期旧注,
  两关均已实现(第 11 章注)。

> 这些矛盾本身也是证据:本白皮书的每章都以源码为最终事实源重写了一遍,
> 上述清单建议作为后续文档修复的任务池。

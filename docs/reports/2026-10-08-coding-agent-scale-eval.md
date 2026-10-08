# Coding Agent 规模评测报告(2026-10-08)

对象:`skillsets/coding_agent` 任务式 agent(`coding.agent.run`)+ 交互宿主 `agent-os-chat`(`coding.agent.chat`)。
模型:openai/kimi-for-coding。闸门应答:全部 approve-once(脚本化)。

## 结果矩阵

| 项目 | 规模 | 任务类型 | 结果 | 步数 | 耗时 | 独立复核 |
|---|---|---|---|---|---|---|
| P1 | 单文件(~60 行) | bug 修复 | done | 26 | 168s | 4 passed ✓ |
| P2 | 多文件包(~500 行,7 文件) | 跨层特性(--since 过滤:query/cli/tests 三层) | done | 43 | 338s | 7 passed ✓(agent 自增 2 测试) |
| P3 | 大型仓库(agent_os 本体,169 源文件) | 真实特性(SessionStore.latest_turn + 测试) | done | 35 | 295s | 11 passed ✓,review pass |
| P4 | 空目录从零创建(交互两轮) | 通讯录 CLI(add/list/search)+ delete 子命令 | done(turns=2) | — | 874s | 19 passed ✓ |

P3 首次运行 401(token 在提取瞬间陈旧),重跑即过——见「暴露问题」。

## 亮点

1. **工作流保真**:四档全部走全 探索→计划落盘→人审→修复循环→collect_diff→审查;计划文档在 `.agent-os/plans/`。
2. **探索质量超预期**:结论带行号;主动模仿仓库先例(原子写、JSON 容错、测试隔离模式);P4 中用显式 Unicode 转义正则做全/半角标点审计以核实插话约束。
3. **修复循环真有用**:P4 首轮 1 failed → 分析全部 16 个测试的约束冲突(add 回显 vs search 断言)→ 找到唯一兼容口径 → 通过。
4. **审查形成闭环(涌现)**:P4 第二轮 review 抓到过期的模块 docstring(「三个子命令」未同步为四个),entry 自动回流 fix_loop 修复并复审 pass——超出 prompt 编排的行为。
5. **降级语义可靠**:explore 输出不合 schema → entry 自行 file.read 兜底;collect_diff 遇非 git 目录 → 优雅降级为文件核实;review 被砍 → 保守标 failed 不粉饰。
6. **基础设施全链路生效**:门(升权/EXEC/计划)逐次真实交互;token_refresh 在长会话中自动续期(日志可见);暂停/恢复与事件队列插话在 P4 及此前 dogfood 验证。

## 暴露问题与处置

| 问题 | 处置 |
|---|---|
| explore 长分析后散文+```json 混排触发 OutputValidationError | 已修:全 6 个 prompt 技能统一「只输出一个 JSON 对象」收尾 |
| review max_steps=8 在 8 文件项目第 9 步被砍(测试已过的 run 只能标 failed) | 已修:review 8→16、explore 20→30(实测调参) |
| P3 首次 401:任务式 `agent-os run` 无 token 刷新(chat 已接) | 待修:CLI run 接 `start_token_refresher`(host/shared 现成) |
| 插话约束对后续任务解释有「染色」(措辞被当新任务线索) | 使用须知:插话写清是「约束」还是「新任务」;后续可在注入批头加类型标记 |
| status 语义偏严(tests 过但 review 缺席 → failed) | 保留保守语义;如需可给 outputs 增 done_unreviewed 档(另议) |

## 判词

小到中型任务(单文件 bug、跨层特性、大仓库定向改动)已可交付使用;从零创建可完整交付带测试的项目。规模-耗时大致线性(单文件 ~3min,多文件包 ~6min,从零两轮 ~15min)。当前主要限制不在 agent 本身,而在 OAuth token 15 分钟寿命对超长任务的截断(已缓解于 chat,待补 CLI)与 prompt 遵从长尾(本轮已基本收敛)。

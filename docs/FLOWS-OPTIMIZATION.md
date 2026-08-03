# 二十流程评测:优化目标与项目计划(循环 2/3)

> 输入:docs/FLOWS-EVAL.md 循环 1 实测记录(缺陷 B1-B7)
> 本文 = 循环 2(优化目标)+ 循环 3(项目计划、节点与流程定义、测试库定义)

## 循环 2:优化目标

按"用户会不会在第一小时撞上"排序:

| # | 目标 | 来源 | 验收 |
|---|---|---|---|
| **O1** | 摘要层零英文错误类名泄漏 | B1 | F03 三轮 ✅;browse 行/why_failed 摘要经 humanError 全覆盖 |
| **O2** | 快照与版本链路正确:接受存的是候选,rewind 恢复被接受版本 | B4 | F18 三轮 ✅;vNNN 内容与 candidate 逐字节一致 |
| **O3** | 首稿即带冒烟用例(≥1 个合 schema 用例) | B2 | F07:首稿 G4 不再必然 warn(模板骨架除外);scaffold 产物含 tests/ |
| **O4** | prompt 花括号预检 | B5 | 含裸露 {} 的 prompt 在保存/校验时给出明确错误与修复建议;G 关有断言 |
| **O5** | API 卫生:未知字段 400;validate 形状与邻居一致 | B3/B7 | create 带 manifest 字段 → 400 指明;validate 响应嵌套统一(向后兼容期内双形) |
| **O6** | 意图路由可观测 | B6 | plan 卡/agent 消息标注路由来源(llm/rule);LLM 失败率可被统计 |
| **O7** | 凭证降级体验 | 全程 | token 过期时助手/LLM 路由明确说"模型服务暂不可用,已用规则模式",不静默 |

## 循环 3:项目计划

### 节点定义(每个都是独立可验的最小闭环)

**N1 摘要泄漏清零(O1)**
- 设计:`orchestrator._recent_runs` 与 `_why_failed` 的行摘要统一过 `human_error()`(已有,扩到行级);browse 行结构 {skill, status_human, error_human};错误原文仍进详情 tab。
- 文件:web_platform/orchestrator.py、tests/web_platform/(行级断言)。
- 测试:B1 三轮回归进 tests/ + scripts/flow_eval.py 的 F03 断言转 ✅。

**N2 快照源修正(O2)**
- 设计:`accept` 改为**先 snapshot(candidate)再覆盖 working**:snapshot 源 = candidate 目录;candidate 缺省时退回 working 并记日志(防裸 accept)。
- 文件:skills/draft_store.py(snapshot 签名加 source_dir)、app.py(调用序)。
- 测试:iterate→accept→读 vNNN 与 candidate 逐字节一致;rewind vNNN 后 working = vNNN 内容;既有 accept 测试适配。

**N3 首稿用例(O3)**
- 设计:scaffold.approve 生成首稿时,按 inputs schema 用 skeleton 逻辑(复用 launch-dialog 同款)生成 1 个 `tests/smoke.json`({input, expect?});写入 DraftStore tests。
- 文件:web_platform/app.py(_act_scaffold_approve)、draft_store.py。
- 测试:批准首稿 → G4 非 warn(有用例);用例内容合 schema。

**N4 花括号预检(O4)**
- 设计:闸门新增轻量检查(G1 或 G2 内):prompt 经 render_prompt 同款 format 探测({var} 合法占位 vs 裸露 braces);裸露 → fail/warn 带"用 {{ }} 转义或改用自然语言"提示。保存端(lab.draft.write/PUT)同步提示(不硬拦,编辑器不打断原则)。
- 文件:skills/gate.py、tools/lab_tools.py(提示)、tests/skills/test_gate.py。
- 测试:裸 {} → fail 带转义提示;合法 {input} 占位不误伤。

**N5 API 卫生(O5)**
- 设计:create 端点白名单化 body 字段(多余字段 → 400 列出);validate 响应加嵌套形 `{"report": {...}}` 并保留平铺字段一个版本期(双形兼容,文档注明)。
- 文件:host/web/app.py。
- 测试:未知字段 400;双形读取均可用。

**N6 路由可观测(O6)**
- 设计:orchestrator 返回的 agent 消息带 `meta: {route: "llm"|"rule", reason?: str}`;plan 卡 data 加同字段;前端在卡角标小字显示(详情层)。
- 文件:orchestrator.py、artifacts.py、cards.js。
- 测试:LLM 命中标 llm;故障回落标 rule + reason。

**N7 凭证降级(O7)**
- 设计:assistant/LLM 路由遇 ProviderError/401 → 人话消息"模型服务暂不可用(凭证),已用规则模式/请重启 run-web.sh 刷新";前端以系统气泡呈现而非英文错误。
- 文件:app.py(assistant 端点错误映射)、orchestrator.py。
- 测试:401 → 人话系统消息,不 500 不裸错。

### 流程定义(开发序)

N2 → N1 → N4 → N3 → N5 → N6 → N7(N2 是数据正确性最优先;N1/N4 是体验止血;N3 补首稿;N5-N7 卫生与可观测)

### 测试库定义(本轮必须完成)

| 测试 | 覆盖 |
|---|---|
| tests/web_platform/test_o1_leak.py | 行级 humanError 全盖;F03 无泄漏断言 |
| tests/web/test_lab_iterate.py(+N2 块) | accept 快照=candidate;rewind 恢复被接受版 |
| tests/web_platform/test_scaffold_smoke.py | 首稿含 tests/smoke.json;G4 不 warn |
| tests/skills/test_gate.py(+N4 块) | 裸 {} fail / 合法占位 pass / 转义提示 |
| tests/web/test_lab_api.py(+N5 块) | 未知字段 400;validate 双形 |
| tests/web_platform/test_route_meta.py | llm/rule 标注与 reason |
| tests/web_platform/test_cred_degrade.py | 401 → 人话系统消息 |
| 回归线 | 全量 pytest + 前端 26 文件全绿;flow_eval.py F01-F06 全 ✅ |

## 循环 4/5 记录位

### 循环 4 开发记录(2026-08-03,分支 debugger,未 commit)

N2 → N1 → N4 → N3 → N5 → N6 → N7 全部落地,测试库定义全表完成:

| 节点 | 达成 | 要点 |
|---|---|---|
| N2(O2) | ✅ | snapshot 加 `prefer_candidate`(candidate 优先,缺省退 working 记日志);两个 accept 调用点(web/web_platform)同改;vNNN 与 candidate 逐字节一致有断言;既有 accept 测试适配新语义 |
| N1(O1) | ✅ | orchestrator 后端 `human_error()`(auth > timeout > provider 优先级),`_why_failed`/`_browse` 行级全覆盖;前端 humanError 同步调序变纯兜底;数据源原文不动(详情层留全量) |
| N4(O4) | ✅ | `_brace_finding` 入 G2:`string.Formatter.parse` 同款语义(未闭合/裸露 {}/非法占位名三类 fail,合法 {name} 与 {{ }} 不拦);修复建议 = "{{ }} 转义或自然语言";lab.draft.write 保存端 warning 不硬拦 |
| N3(O3) | ✅ | `draft_store.skeleton_from_schema`/`smoke_case_from_schema`(launch-dialog 后端等价);scaffold.approve 首稿自动写 tests/smoke.json;G4 有用例即 pass |
| N5(O5) | ✅ | LabCreateBody model_extra 未知字段 400 逐个点名;validate 双形(平铺兼容期 + {"report": …}) |
| N6(O6) | ✅ | meta {route, reason?} 进 agent 消息 + plan 卡 route_meta;reason 机器码(llm_unavailable/llm_bad_schema)可统计;plan 卡新增 decompose 详情 tab(分解结构 + 路由角标),摘要层零标注 |
| N7(O7) | ✅ | LLM 路由降级 → meta.reason=llm_unavailable → 前端系统气泡(copy 六主题);cards/action ProviderError → 503 人话;不 500 不裸错 |

测试:新增 20(test_o1_leak×3 / test_lab_iterate+N2×1 / test_gate+N4×4 /
test_scaffold_smoke×3 / test_lab_api+N5×2 / test_route_meta×5 /
test_cred_degrade×2);全量 pytest 894+1(SSE flake,孤立 3/3 过,与本次无关);
前端 26 文件全绿(themes-contract 同步扫)。

遗留:flow_eval.py 需 live instance(8391)复核 F01-F06,由主代理执行;
`test_debug_api.py::test_debug_sse_bp_hit_resumed_run_end` 时序 flake 属既有;
assistant 端点(旧 web /api/lab/assistant)是异步 run,凭证故障落在 run 记录
(轮询面),端点本身无同步错误可映射——降级提示由轮询侧与平台两处覆盖。

### 主代理验收(2026-08-03,live 8391)

- 全量 pytest **895 passed**(875+20,对账一致);前端 26 文件全绿;
- flow_eval.py 四轮全 ✅:F03 摘要泄漏由循环 1 的 ❌×3 转为不泄漏(O1 实证);
  F06 LLM 命名 dinner.weather_recommender(本轮 LLM 路由正常);
- O2 实测:iterate(加"火锅")→ accept → v002 快照含改动(循环 1 的 B4 不复现),
  rewind 后 working 恢复被接受版——快照链路修复实证。

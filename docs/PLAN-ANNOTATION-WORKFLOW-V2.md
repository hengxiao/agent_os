# 执行计划:批注批处理工作流 v2

> 依据设计:`UI_analysis_reports/2026-08-13/批注批处理工作流设计v2.md`(下称「设计」)
> + 交互细化:`.../批注批处理工作流交互细化v2.1.md`(下称「v2.1」,已对齐——
> 对齐结论:交互细节以 v2.1 为准;冲突裁决见 §9)
> 落点代码:`web_platform/static/doc-editor.js`、`web/static/js/widgets/w-bubble.*`、
> `web_platform/app.py`、`skills/doc_store.py`
> 开工门槛:用户批准本计划。批准后按 P1→P5 执行,每期两套测试(stub + tests-ui)+ pytest 全绿才可进下一期。

## 0. 现状对账(设计 ↔ 既有资产)

| 设计需要 | 既有资产 | 差距 |
|---|---|---|
| 锚点(行+列+quote) | v3.1 列级锚点 `L3:C4-L3:C20` + quote + 高亮 | ✅ 已就绪;补 `version`(创建时版本号)与 quote 哈希 |
| 批注持久化 | DocStore `bubbles/`(anchor-hash 文件) | 记录结构要从「消息流」扩为「Annotation 单条 + status/generation」 |
| 版本链 | DocStore `versions/vNNN`(不可变快照 + parent) | meta 要扩 `generationInput`/`annotationResults`;前端无时间线/Diff |
| 生成端点 | 无(现有 comment 逐条回复 + apply) | 新增 generate:LLM 批处理全部 pending 批注 + chat 上下文 |
| 气泡交互 | v3.2 完整(定位/队列/折叠/删除/原位) | **简化**:消息流退役,单条批注卡;定位/高亮/标记/删除全部保留 |
| Diff 视图 | W-diff 控件(split/unified/tier 徽标) | 复用;加「来源批注」标注与采纳/回滚操作行 |
| 状态栏 | 字数统计 | 加「N 批注待处理 · v5」 |

**明确退役**(设计 §1.1/§8.2):泡内消息流、发送队列、typing、pill、未读分隔线。
**保留**:右键原位/选区锚定/行内高亮/一行多泡/标记跟随/点外收起(草稿不丢)/垃圾桶删除/几何定位(Floating UI)。

## 1. P1 —— 数据模型与生成端点(后端先行)✅(2026-08-13 落地)

**Annotation 记录**(DocStore bubbles 扩展,向后兼容:无 status 字段的旧记录读为 pending):
```jsonc
{
  "anchor": "doc.md#L3:C4-L3:C20",   // 既有格式不变
  "quote": "选中文本快照",
  "version": 5,                       // 创建时版本号
  "content": "改为正式书面语",         // 单条意见(对话流的首条用户消息迁移进来)
  "createdAt": "...", "createdBy": "user",
  "status": "pending|applied|ignored|outdated",
  "appliedInVersion": null,
  "generation": null | { "appliedByVersion": 6, "result": "accepted|rejected|partial", "aiNote": "..." }
}
```
- `doc_store.py`:`save_annotation/read_annotations/set_annotation_status/delete_bubble(已有)`;旧消息流记录读取时压缩迁移(首条用户消息 → content,其余进 history 字段);
- **generate 端点** `POST /platform/api/docs/{name}/generate`:
  入参 `{baseVersion, chatContext, annotations, userPrompt?}` →
  按设计 §7.3 prompt 模板走 LLM → 解析 `<modified_document>`/`<annotation_results>` →
  `snapshot` 新版本(meta 带 generationInput/annotationResults)→ 逐条写批注状态 →
  **reanchor**(设计 §7.1:精确匹配 → ±3 行模糊匹配 quote → outdated)→ 返回 `{newVersion, annotationResults, diff(unified)}`;
- LLM 输出不合格式 → 重试一次,再败 → 502 带原文摘要(不半截落库);
- pytest:端点全链(mock LLM)、reanchor 三策略、迁移读取兼容、版本不可变不破。

**验收**:pytest `tests/web_platform` 全绿 + 新增 ≥6 例。

> **P1 实现注**(2026-08-13;BUILD 不变,纯后端):
> - **端点契约** `POST /platform/api/docs/{name}/generate`:入参
>   `{baseVersion?, chatContext?, annotations?, userPrompt?}`——baseVersion
>   接受序号或 "vNNN"(缺省 = 当前最新快照,不符 → 409);annotations 缺省 =
>   库内全部 pending;chatContext 缺省 = 主对话最近 20 条。返回
>   `{newVersion(序号), versionId("vNNN"), annotationResults, diff(unified,
>   fromfile= name@旧版本/tofile= name@新版本)}`。
> - **LLM 面**:与 chat 同一 ProviderManager + 默认 model,按
>   `orchestrator._route_llm` 先例直取**原文**(kernel.run 强制 JSON 最终答案,
>   走不通 §7.3 的 XML 块契约);prompt 拆 system(角色+要求+输出格式)/
>   user(文档+chat+批注+指令);解析 `<modified_document>`/`<annotation_results>`
>   (纯函数 `web_platform/doc_generate.py`),不合契约 → 重试一次 → 再败 502
>   带原文摘要,**不半截落库**;超时 300s(真机长文档 98s 实测)。
> - **存储面**(skills/doc_store.py):新记录写 `annotations/<anchor-hash>.json`,
>   旧 `bubbles/` 消息流**只读不删**;`read_annotations` = 新记录 + 旧流压缩
>   (首条 user → content,其余 → history,status=pending,quote 按锚点从当前
>   全文截,version = 最新快照号);`set_annotation_status` 对旧流**set 即迁移
>   写新**并记 `migratedFrom`(reanchor 改锚后旧流不复活);`delete_bubble`
>   扩为两面都删(幂等语义不变);`snapshot` 扩 `extra` 合并进 meta
>   (generationInput/annotationResults 封存时写一次,版本不可变不破)。
> - **reanchor**(skills/reanchor.py 纯函数):① 精确——行+列+quote 原位一致
>   (列 = 1-based 闭区间,与前端 anchorColOffsetsOf 对齐;零宽点锚点 quote 空,
>   只按行存在判);② 模糊——±3 行窗内 quote **全文匹配**(可跨行,命中复算
>   行列并保持行级/列级形态);③ 找不到——pending → outdated(锚点原样保留),
>   **applied/ignored 终态状态保留不抹**(applied 的原文通常就是被应用改掉的);
>   已 outdated 不再锚。
> - **状态映射**:LLM result `applied|partial → applied`、`ignored → ignored`,
>   原 result 与 aiNote 记进 `generation`;LLM 未回的批注保持 pending 并照常重锚。
> - **测试**:pytest 12 新例(迁移/set 迁移/reanchor 三策略/全链/409/502 重试/
>   重试成功/版本不可变/解析坏例)→ `tests/web_platform` 113 全绿;ruff 净。
>   **真机冒烟**(8391,kimi-for-coding):demo.test 10 条旧流迁移 → generate
>   98s → v133(4 applied / 2 partial→applied / 4 ignored,LLM 自行识别测试
>   残留为 ignored),meta/状态/旧流保留全对。

## 2. P2 —— 气泡简化为批注卡(控件 + 宿主)✅(2026-08-13 落地,BUILD 2026-08-13.2)

- w-bubble 改**批注卡**形态(保留 def/events 面,内部重写),子状态机按 v2.1 §1.2:
  hidden / composing(输入态)/ preview(悬停预览)/ expanded(展示态):
  - 输入态:右键即开,输入框按 v2.1 §3 硬规格(位置=右键点右下 8px/选区右下角;
    尺寸 240–400px、auto-grow 1→3 行;Enter 提交、Shift+Enter 换行、Esc 取消;
    **点外 = 有内容提交/空取消**(裁决 C1);空输入抖动;500 字截断);
  - 展示态:quote + content + 状态徽标 + 编辑/删除(垃圾桶两击,裁决 C3);
  - 悬停预览:marker hover 200ms 出 tooltip 卡(内容摘录 + 锚点 + 相对时间);
  - **重新编辑**:applied/ignored/outdated 可编辑回 pending;outdated 编辑时
    quote 区显示「原文快照:...」(v2.1 §1.2/§8.1);
- 标记状态色环(v2.1 §11.2,pending=粉/applied=绿/ignored=灰/outdated=橙虚线;
  **色值走 token 不硬编码**)+ 高亮状态样式(applied 淡出/ignored 删除线);
- 点泡外语义改为 v2.1:已提交 → 收起成标记;输入中 → 提交(空则取消)——
  覆盖 v3.2「收起保草稿」(Esc/取消场景草稿可弃);
- 消息流/队列/typing/pill/分隔线代码删除(card 面同步简化:内容摘录 + 状态徽标);
- 宿主:开泡流程不变;提交 = `save_annotation`(不再调 comment 端点,**无即时 AI 回复**);
  标记随状态着色;高亮保持;
- 「采纳成批注」类回复链路退役(comment 端点保留只读兼容或直接退役,报告里定)。

**验收**:stub + tests-ui 全绿;新断言(提交即记录/状态徽标/无消息流 DOM)。

> **P2 实现注**(2026-08-13):全链落地明细见 WIDGET-DESIGN.md §3.13 v4 实现注;
> 生命周期改写见 DOC-BUBBLE.md §3/§4。要点:REST 补 `GET/POST /api/docs/
> {name}/annotations`(upsert,编辑回 pending);comment 端点保留只读兼容
> (旧流迁移面数据源,前端不再调用);lab-iterate 跟随 v4(提交即落边注);
> 修复 _refEl 块引用过期致重开页顶跳(参考点改每次现找);pytest 115 绿
> (113+2 端点例),stub 32 绿,tests-ui 全绿(含新 v4 组)。

## 3. P3 —— 生成工作流(前端主链)✅(2026-08-13 落地,BUILD 2026-08-13.3)

- 工具栏:「生成下一版本」钮(四态按 v2.1 §4.2:无待处理禁用/正常主色/生成中
  loading/失败红色重试,带 pending 计数徽标)+「Diff」「版本历史」入口;
- 状态栏(v2.1 §4.3):字数 · vN · N 批注待处理(可点击滚到第一条)· N 对话待应用;
- 快捷键(v2.1 §9):Ctrl+Shift+A 添加批注 / G 生成 / H 版本历史 / D Diff / 1 预览 / 2 源码;
- 生成中:按钮 loading + 批注标记转 processing;失败 toast + 红色重试(不半截落库);
- 生成后自动进 **Diff 视图**(W-diff 复用):摘要卡(按状态计数)+ 每 hunk 下
  来源批注卡(v2.1 §5.4,可展开收起、点击定位回标记);行型含修改行(▲ 黄底行内 diff);
  顶部「✓ 采纳 vN」「↩ 回滚」;Diff 行内编辑不做(待决策项,默认裁决 5);
- 采纳 = 切预览(新版本为当前);回滚 = restore 父版本,**v6 不删**,meta 标
  `rolledBackTo`(裁决 C2:版本不可变);
- 批注状态随 annotationResults 更新(标记变色);状态栏「N 批注待处理 · vN」;
- chat 通道:消息发送不再即时改文档(doc-editor 窗内);消息旁状态徽章「待应用/已应用(vN)」。

**验收**:tests-ui 全链(建批注 → 生成 → Diff → 采纳/回滚 → 状态流转);截图证据。

> **P3 实现注**(2026-08-13):
> - **生成钮四态**(工具条 `data-doc-generate`):无 pending 禁用 / 正常主色 +
>   计数徽标 / loading(`⏳ 生成中…`)/ 失败红(`⚠ 重试`,toast 同发);计数 =
>   库内 pending 批注数(bubbles map 全覆盖,种子出标后);
> - **生成链** `_runGenerate`:POST generate(baseVersion = 当前快照号)→
>   409 = toast + 自动刷新(§8.3);成功 → 本地版本链推进 + reload 重拉新文
>   + `_syncAnnotations`(标记/高亮变色)→ **自动切 Diff 视图**;失败 →
>   红色重试(不落半截,P1 后端保证);
> - **回滚锚补洞**:generate 端点在**无快照**时先封存 pre-generate v001——
>   否则回滚无目标(P1 测试随之适配,版本号 +1);回滚 = action 管道
>   doc.rewind + `rolled_back_from` → 被弃版本 meta 标 `rolledBackTo`
>   (C2,不删;restore 扩参,pytest +1);
> - **Diff 视图**(viewseg 第三态,纯宿主面不进 compound state):标题
>   版本范围 + ✓ 采纳 / ↩ 回滚 + 摘要卡(应用/忽略/部分计数)+ 来源批注卡
>   (状态徽标 + 意见摘录 + aiNote,点击切预览定位回标记)+ unified 行
>   (add/del/same;色系走 --ok/--danger token);
> - **状态栏**:字数 · vN · N 批注待处理(点击滚到第一条 pending)· N 对话
>   待应用;生成后摘要顶替对话位(下一次输入/批注变化清);
> - **快捷键**(viewHost 委托):Ctrl/Cmd+Shift+A 添加批注(选区在块内走选区
>   链,否则首块)/ G 生成 / H 版本历史(本期 toast + 聚焦版本下拉,P4 正式
>   面板)/ D Diff / 1 预览 / 2 源码;
> - **chat 通道(最小改动落法)**:发送行为**不动**(doc_editor 工具面即时改
>   文档保留——「写一篇/改一节」是生产面,打断超出本期);assistant 消息旁
>   徽章:changed →「已改文档」,未改 →「待生成处理」,generate 后全部 →
>   「已参与 vN」(已参与边界 = 生成时消息数);toast 面补上 `__docToast`
>   接线(desktop boot 挂 util toast——此前只有消费面);
> - **测试**:stub +1 组(四态/状态栏/生成链/Diff 渲染/采纳,compound.test);
>   tests-ui +run_generate_p3(真 LLM 两次生成:四态/自动切 Diff/摘要卡/
>   来源卡定位/变色/采纳版本推进/回滚文本回落/Ctrl+Shift+D/chat 徽章)+
>   2 截图(.shots/gen-p3-*);pytest 116 绿。

## 4. P4 —— 版本历史面板 + 批注列表面 ✅(2026-08-24 落地,BUILD 2026-08-24.1)

- **版本历史**:右侧抽屉(v2.1 §6.2),版本卡四态(当前/预览中/历史/已回滚),
  每卡 = 相对时间 + 差异统计(+a/-b)+ 批注处理统计 + 查看/查看差异/回滚;
  回滚确认框列后果(丢弃 pending 批注等,v2.1 §6.4);
- **批注列表**:右栏新 tab「💬 批注 (N)」(v2.1 §7):按状态分组、按文档位置排序、
  筛选/搜索,行操作 编辑/删除/定位/查看效果/重新编辑;
- 数据面:generate 写 meta 的 generationInput/annotationResults 读出渲染。

**验收**:tests-ui(时间线渲染/查看/差异/回滚链)。

> **P4 实现注**(2026-08-24):
> - **后端**:`GET /api/docs/{name}/versions/tree` 扩字段(每版本带
>   rolledBackTo/annotationResults/adds/dels——adds/dels 由 difflib 与 parent
>   现算,无 parent → null);新增 `GET /api/docs/{name}/diff?from&to`
>   (任意两版本 unified diff + 行统计,「查看差异」数据面);pytest +1(127)。
> - **版本历史抽屉**(doc-editor 右栏 absolute,宽 min(320px,46%)):唤出三入口
>   = 工具栏 🕘(toggle)/ 状态栏版本号 / Ctrl+Shift+H;版本卡四态
>   = 当前(live 边)/ 预览中(P3 待采纳)/ 已回滚(rolledBackTo,暗化)/ 历史;
>   卡 = 相对时间 + 差异统计 + 批注处理统计 + 操作;
>   - 查看完整 = preview 区只读渲染(顶条「查看 vN · 回到当前」;锚点面清空
>     防误开泡),回到当前 = `setDocText` 重渲;
>   - 查看差异 = P3 Diff 视图复用(readonly 形态:回到当前,无采纳/回滚/摘要);
>   - **查看操作后抽屉自关**(抽屉挡预览区,实测点击拦截抓出);
>   - 回滚 = 确认框(列后果:内容恢复/{latest} 标 rolledBackTo 不删/批注保留)
>     → action 管道 doc.rewind → reload + sync + 卡面随刷;
> - **批注列表 tab**(左栏「对话 | 💬 批注 (N)」):按状态分组
>   (pending/applied/ignored/outdated 固定序),组内按文档位置排序(锚点行列);
>   行 = content 摘录 + 锚点 + 相对时间 + 操作(pending:编辑/删除/定位;
>   其余:查看效果=定位/重新编辑);重新编辑经 widget.startEdit(新暴露)
>   进输入态,提交回 pending;删除与垃圾桶同一链;计数/tab 内容随
>   renderBubbleBar 同步;
> - **修复**:setText 原仅挂 api,闭包 `_backToCurrent` 误调 ReferenceError——
>   提闭包函数 `setDocText`,api 与内部共用;
> - **测试**:stub +P4 组(抽屉结构/四态/差异与批注统计/readonly diff/
>   分组组序/组内位置序/行操作面/计数);tests-ui +run_history_p4(抽屉开合/
>   版本卡/查看差异切换/只读预览/列表分组/定位开泡/重新编辑回 pending;
>   applied 行选择器限定组——.first 撞 pending 组编辑钮实测抓出)+ 2 截图。

## 5. P5 —— 迁移、文档、验收 ✅(2026-08-24 落地)

- 迁移:既有对话式批注 → 首条用户消息压缩为 content(状态 pending),原流进 history;demo.test 数据清洗一次;
- 文档:DOC-BUBBLE.md 重写为 v2 工作流(锚点/状态机/生成链);WIDGET-DESIGN.md §3.13 v4;DESKTOP-WIDGET.md 牵连核对;
- 全量回归:stub + tests-ui + pytest + 端点矩阵;BUILD 戳 bump;
- 外部验收:更新 BUBBLE-UX-TEST-BRIEF 为新工作流版(E 条目重排),交 WebBridge 复测。

> **P5 实现注**(2026-08-24;BUILD 2026-08-24.1 不变,本期零产品代码):
> - **迁移核对**:三文档 bubbles/ 全空(待迁 = 0)——P1 冒烟已实证 10 条
>   旧流读时迁移全链(迁移 → generate → 状态写回);迁移机制本身由 pytest
>   三例锁定(压缩/set 即迁移/migratedFrom 防复活);当前库内 3 条新记录
>   全是 v2 原生(migratedFrom=0);旧流文件保留不删(既定);
> - **文档收官**:DOC-BUBBLE 复审清理——§1 切割线/§2 持久化 v4 化,
>   §4 重号节(老 4.1 右键/4.2 队列/4.3 旧点外)删除,结构复位
>   (4.1 开泡/4.2 提交/4.3 点泡外/4.4 几何/4.5 删除 + 5.1 生成链/
>   5.2 版本抽屉/5.3 批注列表);DESKTOP-WIDGET 牵连一处(openDocWindow
>   种子源 bubbles → annotations);
> - **外部验收任务书**:BUBBLE-UX-TEST-BRIEF 全改写为新工作流版
>   (E1 右键输入框/E2 选区高亮+quote/E3 一行两条/E4 点外提交或取消/
>   E5 悬停预览/E6 展开编辑删除/E7 生成钮四态/E8 生成→Diff/E9 采纳回滚/
>   E10 版本抽屉/E11 批注列表;心智说明「无即时回复是设计」+ 已知边界);
> - **全量回归**:stub 32 全绿;pytest 全套 994 passed(+10 skip/32 xfail);
>   tests-ui(BASE=8391)全绿;端点矩阵 11 项全 200(/、/platform/、
>   widget.html、compound.html、runs、skills、sessions、decisions、docs、
>   annotations、versions/tree)。

## 6. 设计 §10 待决策事项的默认裁决(如不同意请指出)

(v2.1 对齐后)

1. @mention:**不做**(单用户);
2. AI 主动批注:**不做**(createdBy 字段预留);
3. 附件:**不做**;
4. 版本上限:**不设限**(磁盘便宜;将来加归档);
5. Diff 行内编辑:**不做**(回滚重生成是正式路径)。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 输出格式漂移(annotation_results 解析失败) | 严格 schema 校验 + 一次重试 + 失败不落库;pytest 用 fixture 锁死解析面 |
| reanchor 误锚(模糊匹配错行) | ±3 行窗 + quote 全文匹配才接受;outdated 宁多勿错 |
| 气泡简化引发既有测试大面积改写 | 退役面一次性列清单;v3.2 行为断言中与批注卡冲突的按新语义改写并逐个核对 |
| 迁移误伤用户数据 | 迁移只读旧写新(新字段),旧流文件保留不删;迁移报告输出 |

## 8. 工期切块(参考)

P1 后端 ≈ 最大块;P2 控件简化居中;P3 前端主链居中;P4 面板小;P5 收尾小。
每期独立可验、可提交、可回滚。

## 9. v2.1 冲突裁决记录(2026-08-13)

| # | v2.1 | 调和 |
|---|---|---|
| C1 点泡外 | 有内容=提交/空=取消 | 按 v2.1;覆盖 v3.2「收起保草稿」 |
| C2 回滚 | 「v6 从版本链删除或标废弃」 | 标 `rolledBackTo` 废弃,不删——版本不可变铁律 |
| C3 删除确认 | 确认弹窗 | 保留垃圾桶两击武装态(平台惯例,desktop 关闭同款) |
| C4 组件拆分 | React/TSX+Zustand | 映射到 widget/compound:批注卡=w-bubble,列表/时间线=compound 子件,Diff=W-diff |
| C5 版本冲突(多人) | baseVersion 409 + 刷新引导 | 端点保留校验;多人面不做(单用户) |

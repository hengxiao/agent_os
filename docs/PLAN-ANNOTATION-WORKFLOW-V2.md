# 执行计划:批注批处理工作流 v2

> 依据设计:`UI_analysis_reports/2026-08-13/批注批处理工作流设计v2.md`(下称「设计」)
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

## 1. P1 —— 数据模型与生成端点(后端先行)

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

## 2. P2 —— 气泡简化为批注卡(控件 + 宿主)

- w-bubble 改**批注卡**形态(保留 def/events 面,内部重写):
  输入态(右键即开):textarea(autosize 保留)+ Enter 提交/Esc 取消;
  展示态(已提交):quote + content + 状态徽标(⏳ pending 粉 / ✅ applied 绿 / ❌ ignored 灰 / ⚠️ outdated 虚线)+ 编辑/删除;
  点泡外:已提交 → 收起成标记;输入中 → 收起但草稿保留(现行语义);
- 消息流/队列/typing/pill/分隔线代码删除(card 面同步简化:内容摘录 + 状态徽标);
- 宿主:开泡流程不变;提交 = `save_annotation`(不再调 comment 端点,**无即时 AI 回复**);
  标记随状态着色;高亮保持;
- 「采纳成批注」类回复链路退役(comment 端点保留只读兼容或直接退役,报告里定)。

**验收**:stub + tests-ui 全绿;新断言(提交即记录/状态徽标/无消息流 DOM)。

## 3. P3 —— 生成工作流(前端主链)

- 工具栏:「生成下一版本」钮(有 pending 才可用,带计数徽标)+「版本历史」入口;
- 生成中:按钮 loading + 批注标记转 processing;失败行内报错 + 重试;
- 生成后自动进 **Diff 视图**(W-diff 复用):每 hunk 旁注来源批注/状态;顶部「✓ 采纳 vN」「↩ 回滚」;
- 采纳 = 切预览(新版本为当前);回滚 = restore 父版本(既有端点);
- 批注状态随 annotationResults 更新(标记变色);状态栏「N 批注待处理 · vN」;
- chat 通道:消息发送不再即时改文档(doc-editor 窗内);消息旁状态徽章「待应用/已应用(vN)」。

**验收**:tests-ui 全链(建批注 → 生成 → Diff → 采纳/回滚 → 状态流转);截图证据。

## 4. P4 —— 版本历史面板

- 左栏新 tab「版本历史」:时间线(设计 §4.2/§6.3),每版本 = 摘要行(相对时间 + 批注处理统计)+ 查看/查看差异/回滚;
- 查看 = 只读预览(md-viewer readonly);查看差异 = W-diff(该版本 vs 父版本);
- 数据面:generate 写 meta 的 generationInput/annotationResults 读出渲染。

**验收**:tests-ui(时间线渲染/查看/差异/回滚链)。

## 5. P5 —— 迁移、文档、验收

- 迁移:既有对话式批注 → 首条用户消息压缩为 content(状态 pending),原流进 history;demo.test 数据清洗一次;
- 文档:DOC-BUBBLE.md 重写为 v2 工作流(锚点/状态机/生成链);WIDGET-DESIGN.md §3.13 v4;DESKTOP-WIDGET.md 牵连核对;
- 全量回归:stub + tests-ui + pytest + 端点矩阵;BUILD 戳 bump;
- 外部验收:更新 BUBBLE-UX-TEST-BRIEF 为新工作流版(E 条目重排),交 WebBridge 复测。

## 6. 设计 §10 待决策事项的默认裁决(如不同意请指出)

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

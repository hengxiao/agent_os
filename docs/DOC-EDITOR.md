# 文档编辑器(Doc Editor)设计稿

> 版本:v0.1(设计)
> 对象:web_platform 的 `doc` app kind——Markdown 文档的编写/评审/版本/导出
> 关系:架构基线 APP-MODEL.md v0.3(app/双表面/exec 三态/§14 寻址/§16 cascade);
>   控件基线 WIDGETS.md(W1-W4 全量);本文**只组合,不新造基础件**
> 一句话:**用 W-text 写、用 W-md 看、用 W-bubble 在段落上讨论、
>   用 W-diff 对比版本、cascade 让 agent 看着全文改一段。**

---

## 1. 定位与场景

一个 Markdown 文档编辑器，面向三类真实工作：

1. **写设计文档**(就像本仓库 docs/ 里的那些)——边写边让 agent 评审段落;
2. **写 skill 的 NOTES.md / prompt 长文**——与 Lab 打通,技能文档随技能走;
3. **写报告/纪要**——版本快照 + diff + rewind(Flow C 已验证的迭代语义)。

它不是一个"文本域 + 保存按钮"。它的三个系统级特色全部来自既有资产:

- **段落级对话**(W-bubble + §16 cascade):选中一段 → 气泡提问"这段太绕"/"改成表格" → agent 看着全文(span+段落+全文+文档状态)回答;
- **版本即数据**(沿用 iterate 的 versions 语义):accept 即快照 vNNN,diff 对比,rewind 恢复;
- **评审即气泡雨**:agent 通读全文后,把意见**自动挂成各段落上的气泡**(不是一整块报告,是定位到段的批注流)。

## 2. App kind:`doc`

### 2.1 双表面

**Card Surface(嵌进对话/桌面)**:
标题 + 一句摘要(首行)+ 字数 + 最近编辑时间 + 状态(草稿/已快照/有未读气泡);
动作:打开(tab)、导出。

**Tab Surface(完整编辑器)**:

```
┌──────────────────────────────────────────────────────┐
│ 标题栏:doc 名 · 版本下拉(v003 ▾ rewind) · [导出]      │
├──────────────┬───────────────────────┬───────────────┤
│ 大纲(§树)    │ 编辑区(左:W-text)      │ 气泡栏(锚点列表) │
│ §1 概述       │ ─── 或 ───            │ 💬 §2 这段绕?   │
│ §2 动机       │ 预览区(右:W-md)      │ 💬 §4 缺例子    │
│ §3 设计       │ (分屏 toggle)         │               │
│              │ 段落锚点 💬 随处可见    │               │
├──────────────┴───────────────────────┴───────────────┤
│ 状态栏:字数 · dirty ● · v003 · [快照][评审][导出]      │
└──────────────────────────────────────────────────────┘
```

- **编辑/预览分屏**:左 W-text(mono,选区保留),右 W-md 实时预览(白名单渲染);分屏可切(纯编辑/纯预览/分屏);
- **大纲树**:从 markdown 标题解析,点击滚动定位(W-list 语义);
- **段落锚点**:每段(标题/段落/表格行)可开 W-bubble——锚点 = `doc.md#L<start>-L<end>`;
- **气泡栏**:全文档气泡的聚合视图(锚点/未读/跳转)。

### 2.2 state

```jsonc
{
  "name": "design.new-ui",
  "text": "…markdown 全文…",
  "dirty": false,
  "savedAt": 1785000000,
  "view": "split",              // edit | preview | split
  "versions": ["v001", "v002"], // 列表由 store 提供
  "bubbles": [{ "anchor": "doc.md#L12-L18", "unread": 2 }]
}
```

## 3. 动作(exec 归态;全部经 action 管道)

| action | exec | 说明 |
|---|---|---|
| save | endpoint | 保存全文(.bak 同 DraftStore 惯例) |
| snapshot | endpoint | 封存 versions/vNNN(内容=当前全文;与 iterate 同语义) |
| rewind | endpoint | 恢复某版本到全文(历史不动) |
| comment.send | **run + cascade** | 段落气泡提问;级联 = span/段落/全文(W-text/文档段)+ 文档名/版本/脏状态(app)+ 活跃会话(shell) |
| comment.apply | endpoint | 接受 agent 建议:把回复转成对全文的**一次编辑**(替换 span 或插入段;人按确认键,agent 永不直改) |
| review | **run + cascade** | agent 通读全文,输出**锚点批注集**(每条 = anchor + 意见);落成各段气泡 |
| export | endpoint | 导出 .md / 复制全文 / (可选)写进 skill 的 NOTES.md |
| meta.set | local | 标题/视图切换等纯 state |

权限面:save/snapshot/export 是 host 函数(endpoint,单用户 principal);
comment/review 起 run(内核全套,白名单只读级联+写候选编辑,不能直接写文档);
**apply 永远是人按按钮**(与升权哲学一致)。

## 4. 数据模型(复用 DraftStore 布局哲学)

```
docs/<name>/
├── doc.md            # 当前全文(working)
├── meta.json         # {title, created_at, savedAt}
├── versions/vNNN/    # 快照(doc.md + meta.json{source, parent, at})
├── bubbles/<anchor-hash>.json  # 每锚点一个消息流
└── review/<ts>.json  # 历次评审的批注集
```

存储实现:`skills/doc_store.py`(新,照 DraftStore 同构:CRUD/.bak/路径
穿越防护/快照/气泡存取);docs_root 配置缺省 `<artifacts>/docs`。

## 5. Agent 功能(都由 §16 cascade 驱动)

| 功能 | 触发 | cascade 内容 | 产出 |
|---|---|---|---|
| 段落问答 | 段落 💬 提问 | span+段落+全文+文档状态 | 气泡回复 |
| 段落改写 | 气泡里说"改成 X" | 同上 | **候选替换文本**(不是直接改);人按 apply 才落 |
| 全文评审 | 状态栏[评审] | 全文+大纲+最近 diff(v-1→v) | 锚点批注集 → 自动挂气泡到各段 |
| 续写/起草 | 空文档/大纲态 | 大纲+已有章节 | 候选章节文本(人审后插入) |

评论技能:`skill.dev.doc_commenter`(新,白名单 = 空;输入 = cascade 信封;
输出 = {reply} 或 {edits: [{anchor, suggestion, replace_text?}]}——apply 时
由 host 按 anchor 应用,技能本身无写权限)。

## 6. 与既有系统的接点

- **skill NOTES.md**:lab-draft app 的"编辑文档"动作直接开 doc tab(ref = 技能的 NOTES 路径)——技能文档享受同一编辑器;
- **本仓库 docs/**:编辑器根可指到 repo docs(只读+另存模式,防误写设计档);
- **对话 app**:doc 卡片可入对话("把刚才的评审结果发我"→ 批注集卡片);
- **导出即发布**:docs 不作为生产面;要进 skills.yaml/docs 仓库走人工 git 流(本编辑器不开新发布通道)。

## 7. 测试库定义

| 层 | 内容 |
|---|---|
| store | CRUD/.bak/穿越防护/快照/rewind/气泡存取/评审批注集 |
| app 协议 | doc manifest 合法;save/snapshot/rewind/export 转发;cascade 信封三级(段/文档/会话) |
| 编辑器 | 分屏切换/大纲解析与跳转/锚点解析(L 起止)/dirty/保存 |
| 气泡 | 段落提问(信封含 span+全文)/回复渲染/apply 只经确认/评审批注自动挂段 |
| 版本 | snapshot=v 内容一致;diff(W-diff 复用);rewind 恢复 |
| 边界 | 空文档/超长文档(分段渲染阈值)/锚点越界/非 markdown 文本 |

## 8. 分期

| 期 | 内容 |
|---|---|
| D1 | doc_store + doc app kind + 编辑器(分屏/大纲/dirty/save/snapshot/rewind) |
| D2 | 段落锚点 + W-bubble 接入(comment.send/apply)+ doc_commenter 技能 |
| D3 | 全文评审(锚点批注集自动挂段)+ lab NOTES.md 接点 |
| D4 | 导出(.md/NOTES 写回)+ 对话卡片 + 打磨 |

## 9. 不做

- 不做富文本/WYSIWYG(Markdown 源编辑 + W-md 预览已覆盖,所见即所得是另一个产品);
- 不做协同编辑/多人评论(单用户;气泡是人与 agent 的对话);
- 不做 PDF/docx 导出(.md 与纯文本已够,格式转换走 pandoc 外部工具);
- 不做文档的权限共享(单用户 principal;分享 = git);
- 不重写 W-md 的渲染器(白名单协议不变,长文档分段渲染优化随 D1 做)。

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

| D1 ✅ | doc_store + doc app kind + 编辑器(分屏/大纲/dirty/save/snapshot/rewind) |
| D2 ✅ | 段落锚点 + W-bubble 接入(comment.send/apply)+ doc_commenter 技能 |
| D3 ✅ | 全文评审(锚点批注集自动挂段)+ lab NOTES.md 接点 |
| D4 | 导出(.md/NOTES 写回)+ 对话卡片 + 打磨 |

> **D3 实现注**(2026-08-03,分支 debugger):
> `doc_reviewer_skill`(tools=[];输入 text/outline/diff,输出 {notes:
> [{anchor,severity,text}]});`POST /api/docs/{name}/review`(信封 =
> 全文+大纲+最近 diff[difflib 行级变体行,封顶 4000 字符]→ 批注集
> 校验[锚点格式+severity 白名单]→ 落 review/<ts>.json + 逐条挂
> bubbles[severity 随消息]);前端 runReview 气泡雨(severity token 着色
> must=danger/should=perm-write/nit=fg-2;越界锚点前端挂空不建泡)+
> 气泡栏聚合视图(锚点/severity/assistant 计数,点击跳转聚焦);
> NOTES 接点 = draft tab [编辑文档] → 建/开 `notes.<draft>` doc tab
> (只读+另存:首开空种子,绝不写生产),保存时经 `_act_doc_save` 把
> 全文**写回草稿 manifest.notes 键**(未知键容忍已验证,随草稿版本走;
> 草稿缺失跳过不炸)。

### 架构测试记录(D3 增补;2026-08-03)

**顺畅点**:
1. **NOTES 写回零新通道**:manifest 未知键容忍(事先实测)让 notes 成为
   草稿的普通元数据——版本快照/包闭包/diff 语义全部自动继承,没有发明
   第二种文档存储。这是对"数据模型先行"的一次正收益验证。
2. **批注集 = 消息流的一种角色**:评审挂段复用 D2 的 bubbles/ 与 W-bubble
   渲染,只加 `severity` 字段随消息走——store/端点/前端三处各加 ~5 行,
   协议零改动。消息流的开放字段(role/severity/review)证明比预定义
   schema 耐生长。

**弯腰点**:
1. **severity 白名单校验在服务端,前端着色靠类名约定**(doc-sev-<sev>)。
   三档语义两端各一份(后端校验集/前端类名+copy)。弯法:常量小,手写
   对齐。建议:留特例(severity 是 doc 域概念,不进 APP-MODEL;第四个
   消费方出现时进 widgets 的 badge 控件)。
2. **评审输出 {notes} 的格式校验偏"宽容丢弃"**(不合条静默丢,不炸)。
   弯法:过滤式校验。建议:留特例——LLM 输出的纪律应该是"能救则救,
   救不了就丢并记录",本期丢了不记录(logger 都省了,因为批注集本身
   落盘 review/<ts>.json 留证原始面)。

> **D2 实现注**(2026-08-03,分支 debugger):
> `doc_commenter_skill`(skills/lab_assistant.py;tools=[] 白名单空,
> 输出 {reply} 或 {edits:[{anchor,suggestion,replace_text}]});段落块切分
> `mdBlocks`(空行分块,标题/列表项/表格行独占,1-based 行号区间,与大纲
> 同源);预览按块渲染带 💬 锚点钮;`POST /api/docs/{name}/comment`
> (comment.send,与 W2 lab comment 同先例:信封 → commenter run →
> reply/edits,双方消息落 bubbles/);`comment.apply` 进 manifest 与管道
> (endpoint;_act_doc_apply 按行号区间替换 + expected 校验,越界/已变
> 报"文档已变化,请重新评审",应用后 store.save(.bak)+ system 留痕);
> 气泡种子经 `GET /api/docs/{name}/bubbles`(开关不丢,服务端事实源)。

### 架构测试记录(D2 增补;2026-08-03)

**顺畅点**:
1. **N4 花括号纪律在 doc_commenter 上又救一次场**:技能 prompt 里的
   JSON 示例({reply}/{edits})撞上 render_prompt 的 str.format——
   正是 N4 预检防的那类运行时必炸;`{{ }}` 转义解决。架构的"提示词
   即模板"约定迫使所有技能作者面对转义,换来渲染语义唯一。
2. **comment.send 没有进通用管道,是对的**:它需要大信封(cascade)+
   即时回复回填气泡,通用管道(args_from+args_input)装 cascade 会逼出
   怪 schema;专属端点与 W2 lab comment 同先例(读多写一,信封即参数)。
   结论:§3 表里 send 标 run 是"内部起 run 语义",不是"必须过管道"——
   管道的 run 态(M4a)服务的是**用户发起的长任务**,不是每次问答。
3. **气泡持久化零前端状态**:bubbles/ 是服务端事实源,前端只挂种子——
   开关/刷新/重渲都不丢,没有发明任何前端持久层。

**弯腰点**:
1. **W-bubble 的 anchor 是对象而 doc 锚点是字符串**。气泡引用行按
   {member, path} 渲染,doc 锚点 = "doc.md#L2-L2" 单串。弯法:挂载时包
   成 {member: 文档名, path: 锚点串},submit/apply 时拆回字符串。
   建议:**留特例**(W-bubble 的 anchor 形态本来就没协议化,两个消费方
   各包一层,比定协议便宜;第三个消费方出现时再统一 anchor schema)。
2. **innerHTML 重渲与气泡宿主的矛盾**(D1 已撞,D2 正面解):
   预览每输入重渲会把块内气泡宿主摘出 DOM。弯法:宿主元素打
   data-anchor,重渲后按引用挂回(气泡不重建,消息流/未读都在)。
   建议:**改协议**——给宿主类组件一个标准解法:widget mount 时若宿主
   在"会被重渲的容器"里,由 mount 方传入 reattach 回调,控件库不假设
   DOM 稳定性。这也是 D1 弯腰点①(host shim)的同源问题,值得 W5 合并
   处理(字符串骨架 + 后挂载模式在整个项目里是常态)。

> **D1 实现注**(2026-08-03,分支 debugger):
> `skills/doc_store.py`(DraftStore 同构:点分名校验/.bak/versions/bubbles/
> review);doc manifest(apps.py;save/snapshot/rewind/export=endpoint,
> meta.set=local 进 `_LOCAL_MUTATORS`);`/api/docs` CRUD(读面直给,写动作
> 全走管道);前端 `doc-editor.js`(W-text 编辑/W-md 预览/大纲/dirty/状态栏/
> rewind 两击);长文档 >200KB 预览截断提示(§7 边界)。

## 架构测试记录(D1 实弹;2026-08-03)

> 本期把 doc 当架构的实弹测试:每个协议(APP-MODEL/WIDGETS)在实现中过一遍,
> 顺手/弯腰都记录。格式:场景 → 结果 → 建议。

### 顺畅点(协议替你省了什么)

1. **app 协议闸零成本接住新 kind**:doc manifest 一次通过 validate_manifest
   (双表面/state_schema/args_from 在 state 内/args_input 声明/exec 归态)。
   伪参数测试(`args:{name:"evil.doc"}` 冒充 args_from → 400)证明
   "客户端不可控 state 绑定"对新 kind 自动成立——**没写一行新防御代码**。
2. **exec 归态表与设计一一对应**:save/snapshot/rewind/export=endpoint
   (薄 handler 转发 DocStore),meta.set=local(mutator 合并 state)。
   归错态会立刻被注册闸拦(M3.5 的强制项),设计表(§3)就是代码。
3. **local mutator 注册面平滑扩展**:`_LOCAL_MUTATORS` 加一行
   `"meta.set": _mut_meta_set`,M5 建的机制(M3.5 local=400 裁决)无需改。
4. **控件组合只向下,一次过**:编辑器 = doc tab(app)→ doc-cols(section)→
   W-text/W-json/W-md(widget),没有任何反向引用;静态扫描(widgets 目录
   零 fetch)对新文件天然适用——**新文件没有值得扫的,因为不需要**。
5. **W-md 是白拿的**:实时预览 = 每 input 一次 `mdToHtml`(纯函数),
   白名单/XSS/代码块语义 W4 已测过,doc 不用重测渲染正确性,只测接线。
6. **rewind 两击确认有现成范式**(lab-iterate 同款 armed 模式),协议外
   零决策;tabAction 的 args_input 收集点($("#detailHost").querySelector)
   与 run.launch 完全同构,加 doc.save/doc.rewind 两支共 6 行。
7. **store 与 DraftStore 同构哲学直接复用**:.bak/版本不可变/坏文件隔离/
   路径穿越校验,测试用例几乎是 DraftStore 测试的文档版重写,一次全绿。

### 弯腰点(协议不够的地方,怎么弯的)

1. **W-text 的 aria-label 必填纪律 vs HTML 字符串骨架**。
   场景:doc tab 的 html 是字符串(renderDetail innerHTML),真实 DOM 里
   textarea 的 aria-label 是从字符串长出来的;W-text mount 时要
   `getAttribute("aria-label")`。真实 DOM 没问题,但**虚拟 DOM/延迟挂载
   场景**(本仓库 dom-stub 的 region 按选择器字符串缓存,`"textarea"` 与
   `[data-doc-text]` 是两个对象)属性读不到。
   弯法:mount 前给 textarea 显式补属性 + host 适配层(把 W-text 消费的
   四个面包成 shim)。建议:**改协议**——W-text 接受显式
   `ariaLabel` 选项(`mountTextEditor(host, {ariaLabel})`),优先级高于
   DOM 读取;这样"字符串骨架 + 后挂载"模式不需要 shim(W2-W4 的
   mount 点都有同类需求,lab 的既有 textarea 不受影响)。
2. **大纲树不在任何现有控件语义内**。
   场景:大纲需要"标题列表 + 点击 → 字符偏移选区跳转"。W-list 是
   "选择语义"(selected 是业务值),大纲是"导航语义"(无选中态,只有定位)。
   弯法:自制 15 行 outline 渲染(按钮 + data-offset + selectionStart 定位),
   没硬塞 W-list。建议:**留特例**(不扩展协议)——W-list 若加
   `navigate` 事件 + item.payload 就能覆盖,但 YAGNI;等第二个导航型
   列表出现(很可能 = D3 的气泡栏)再提 W-nav 或扩 W-list。
3. **W-text 与 W-md 的同步滚动**。
   场景:分屏编辑预览的经典体验是双栏滚动同步。协议里两个控件互不知道
   对方存在(组合只向下,事件上行),没有"兄弟控件联动"语义。
   弯法:**不同步**——只做"输入即重渲预览"(数据源同一,滚动各自)。
   建议:留特例,不造"兄弟总线"。理由:section 持有两个控件的引用,
   联动是 section 的职责(§3"事件映射表在 section"),若要同步,应在
   section 层订阅 W-text 的 scroll 事件再调 W-md 的 scrollTo——
   W-text 目前没有 scroll 事件,等 D2+ 真需要时给 W-text 补
   `events: ["scroll"]` 即可,协议不用改。
4. **长文档阈值是 spec 自定常数**。
   场景:>200KB 不炸(§7 边界)。协议没有"大载荷降级"语义。
   弯法:预览截断(前 200KB + 提示行),编辑器本体不受影响
   (textarea 原生能吃 MB 级)。建议:留特例(常数进代码注释);
   若多个控件都遇大载荷(W-log 截断已有 self-contained 方案),
   可归纳一个"降级模式"惯例,但不成文协议项。
5. **doc 的入口(tab 从哪开)**。
   场景:协议有 spawn/openDetail,但"文档列表/新建"这个入口本身
   是个小 app(列表+新建表单)。D1 只做编辑器本体,入口暂以
   API/测试驱动。弯法:留 D2/D4(与对话卡接入一起),不临时拼。
   建议:入口 = doc 列表 kind 或对话 app 的"新建文档"动作
   (shell.session.create 同构),属 D4 对话卡接入的自然延伸。
6. **store 与 DraftStore 的不同构点(记录,不算弯)**。
   DraftStore 有 candidate/成员包闭包(技能包语义),doc 是单文件全文,
   不需要;versions meta 因此更简(source/parent/at 三键)。
   结论:同构的是**哲学**(不可变版本/备份/隔离),不是**形状**——
   这是对的,不值得抽象公共基类(两个 store 共 ~300 行,抽象反而糊)。

### 裁决摘要

| 项 | 结果 |
|---|---|
| validate_manifest 协议闸 | ✅ 通过(零改动接住 doc kind) |
| 组合只向下 | ✅ 通过(app→section→widget,无反向) |
| 出海全经 app action | ✅ 通过(写动作四件 endpoint + meta.set local;读面直给与既有 decisions/runs 读面同先例) |
| 控件零 fetch | ✅ 通过(doc-editor.js 无 fetch(在 widgets/ 外,fetch 只在 app.js 父级) |
| exec 归态 | ✅ 与 §3 设计表一致 |
| 协议修改需求 | 1 项(W-text 显式 ariaLabel 选项,建议 W5 顺手带) |

## 9. 不做

- 不做富文本/WYSIWYG(Markdown 源编辑 + W-md 预览已覆盖,所见即所得是另一个产品);
- 不做协同编辑/多人评论(单用户;气泡是人与 agent 的对话);
- 不做 PDF/docx 导出(.md 与纯文本已够,格式转换走 pandoc 外部工具);
- 不做文档的权限共享(单用户 principal;分享 = git);
- 不重写 W-md 的渲染器(白名单协议不变,长文档分段渲染优化随 D1 做)。

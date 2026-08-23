# Text ↔ Bubble 交互协议(实然文档)

> 记录**当前实现**的文档↔批注卡操作逻辑。
> **P2 完成态(2026-08-13,BUILD 2026-08-13.2):v4 批注卡**——消息流/队列/未读
> 全部退役,单条批注 + 状态机(pending/applied/ignored/outdated),提交走
> annotations 端点(无即时 AI 回复,攒着等 generate 批处理)。
> 每节标注代码出处;设计与验收条文见 `WIDGET-DESIGN.md` §3.13(v4),
> compound 协议见 `COMPOUND-WIDGET.md`(本文不重复协议,只记这条具体链路)。

## 1. 角色与归属

```
doc-editor(compound,父,path=/doc/<文档名>,挂进 desktop 后 /root/<文档名>)
 ├─ doc(md-viewer,预定义 slot)            文档主体:预览段落块 / 源码态
 └─ chat-bubble × N(动态子件,dynamic.allow) 每个批注一条 = 一个子件实例
```

切割线(W5.4 定案,C3/v3 均遵守):

- **控件本体**(w-bubble):卡面四区(header/quote/log/composer)、发送队列、
  滚底语义、折叠、未读分隔线、delete action;
- **宿主壳**(doc-editor.js):浮出几何(定位/翻转/maxHeight)、标记(marker)、
  未读游标、选区捕获、出海(所有后端调用)。

气泡 view 经 `link_view` 挂进壳内(§5 hard link:挂载点不限 slot 内),
不进父 layout 占位;批注的 add/remove 走 `add_child`/`remove_child`(§4)。

## 2. 锚点模型

格式:`doc.md#L<始>[:C<始列>]-L<末>[:C<末末>]`,列**可选**,行级向后兼容。

- 正则(前后端同一):`^doc\.md#L(\d+)(?::C(\d+))?-L(\d+)(?::C(\d+))?$`
  (app.py `_ANCHOR_RE`;skills/platform/tools.py 同形);
- 解析:`parseAnchor(anchor) → {start, end, sc, ec}`(sc/ec 可空);
- **列级映射**(宿主 `_selectionAnchor`/`_pointAnchor`):
  - 有选区 → 选区行列范围(TreeWalker 累加文本偏移);
  - 无选区右键(v3.1)→ `caretRangeFromPoint` 把点击点映射为**零宽点锚点**
    `L3:C8-L3:C8`(点锚点 quote = 空串,徽标仍显示位置);
  - **标记语法块**(#/-/| 前缀)渲染文与源文有符号差,回落行级——
    quote 仍是选中文本,语义不丢;
  - 偏移计算跳过泡壳/标记子树(它们挂在块内,不剔除会把批注卡文本算进锚点偏移);
- **行文级呈现**(v3.1):列范围锚点的文本包行内高亮 `.doc-hl`(--live 浅底 +
  底部 2px 细线,Notion 式;split text nodes 包 span;重渲后随 `_relayout` 重挂,
  幂等不叠包);行级锚点维持段落左条,不出行内高亮;
- 持久化:DocStore 按 anchor-hash 文件名存消息流;点/列/行级锚点天然并存。

## 3. 生命周期状态机(v4 · P2 完成态)

**卡形态**(hidden/composing/expanded 三态;preview = 宿主 tooltip):

```
                右键(原位)/ 💬 锚点钮 / 批注栏点击
                       ↓ add_child
[hidden] ──右键──→ [composing 输入态] ──提交──→ 落库(pending)→ 收起成标记
                       │   │                              ↑
                       │   └─Esc/点外空──→ 取消摘除(无内容不落库)
                       │                                  │
[hidden] ←──✕/点外── [expanded 展示态] ←──点标记/点高亮/批注栏── 标记
                       │
                       └──编辑──→ [composing](draft 预填;提交 → 回 pending)
```

**批注状态机**(v2.1 §1.2;与库记录一致):
`pending ─generate→ applied / ignored(partial 归 applied,result 留痕)`;
锚点失效 → `outdated`(reanchor:精确 → ±3 行全文匹配 → outdated);
applied/ignored/outdated 均可**重新编辑回 pending**(参与下一轮生成)。

- **打开**(`openBubble`):同锚点重开 = 聚焦,不建第二个实例;一行多泡
  (v3.1)保持;有内容种子 = expanded,无 = composing;
- **收起**:壳隐藏 + 标记显出(状态色环);canonical state 不动;
- **重开**:点标记 / 再右键 / 点行内高亮区;
- **删除**:唯一销毁路径(垃圾桶两击),后端读面是事实源。

## 4. 关键链路

### 4.1 右键原位开泡(v3 用户裁决;v4 语义)

1. `contextmenu` 落在预览段落块上(按 view 绑定,`cur.preview` 委托);
2. 有选区且在块内 → `_selectionAnchor` 算列级锚点;否则 caretRangeFromPoint
   点锚点(v3.1;无效坐标按块中心再试,仍不成回落行级——v3.2 F2a);
   quote = 选中文本或锚段摘录;
3. `add_child("chat-bubble", {slot: anchor, state:{anchor, quote, content,
   status, view…}})` —— 批注 = 动态子件,path = `/root/<文档名>/<anchor>`;
4. 宿主建浮出壳 `.doc-bubble-pop`,按右键点的块内偏移定位(F1:左缘 = 点击点,
   只钳 8px);`link_view(body, {surface:"tab"})` 挂卡;
5. `_fitBubble` 几何精算(§4.4);提交前标记不显(composing 无标记)。

### 4.2 提交批注(v4;消息流队列退役)

1. composer Enter(Shift+Enter 换行;空 = 抖动;>500 字截断)→ 控件**乐观
   更新**(content/status=pending,转展示态);
2. `submit` 事件(负载 {anchor, content, quote, cascade})→ 父闸门
   `on_child_event` 放行 → `child_event` 接管(widget 不出海);
3. 父 `_saveAnnotation` → `POST /platform/api/docs/{name}/annotations`
   `{anchor, quote, content, version, status:"pending"}` —— upsert
   (编辑 = 同锚点覆盖,回 pending,generation 清零);
4. 成功 → 收起成段旁标记(v2.1 §2.1 帧 3);失败 → notifyError 回输入态
   (草稿恢复);
5. **无即时 AI 回复**——comment 对话链退役(comment 端点保留只读兼容,
   是旧流迁移面的数据源;前端不再调用)。

### 4.3 点泡外 / Esc(v4 · 裁决 C1)

- 点泡外:**输入态有内容提交 / 空取消;展示态收起**(控件 `submitOrCancel`;
  覆盖 v3.2「收起保草稿」);判定在控件 data 面白名单(卡面重渲会摘目标
  元素,DOM contains 判不住,须按 data 面判内);
- Esc:输入态取消(新建 = 摘除,编辑 = 回展示态);展示态 = 收起;
- 重开(点标记/再右键):`link_view` 重挂(若 view 被摘)+ 几何重算
  (参考点每次现找块——首开 add_child 的基座 relayout 会重渲 preview,
  捕获的块引用会过期,P2 实测抓出)。

## 4. 关键链路

### 4.1 右键原位开泡(v3 用户裁决)

1. `contextmenu` 落在预览段落块上(按 view 绑定,`cur.preview` 委托);
2. 有选区且在块内 → `_selectionAnchor` 算列级锚点;否则 = 点击点所在行(行级);
   quote = 选中文本或锚段摘录;
3. `add_child("chat-bubble", {slot: anchor, state:{anchor, messages: 种子, newFrom…}})`
   —— 批注 = 动态子件,path = `/root/<文档名>/<anchor>`;
4. 宿主建浮出壳 `.doc-bubble-pop`,**按右键点的块内偏移定位**(offTop/offLeft,
   不是段落开头);`link_view(body, {surface:"tab"})` 挂卡;
5. `_fitBubble` 几何精算(§4.4);标记 `.doc-bubble-marker` 留在锚点行尾;
6. seen 游标即记为已读;聚焦输入框。

### 4.2 发送消息(v2 队列)

1. composer Enter(Shift+Enter 换行)→ 控件**本地队列**:用户消息立即入流
   (不等回包,连发不吞),在飞一条,串行 pump;
2. `submit` 事件 → 父闸门 `on_child_event` 放行 → `child_event` 接管
   (widget 不出海,出海在父级);
3. 父 `_submitComment` → `POST /platform/api/docs/{name}/comment`
   信封 `{anchor, text, cascade}` —— **cascade = context 级联的产物**(§5);
4. 回复经 `receiveReply` 入流;失败行内红条 + 重试(不重复追加);
5. 回复带 edits(替换建议)→ `editsMap` 存证,卡面出「采纳」→ apply 经 action 管道。

### 4.3 点泡外收起 / 重开

- 点泡外:壳藏 + 原位标记显(**草稿不丢**);判定在控件 data 面白名单
  (卡面重渲会摘目标元素,DOM contains 判不住,须按 data 面判内);
- 重开(点标记/再右键):`link_view` 重挂(若 view 被摘)+ 几何重算 + seen 前进;
- Esc / 卡面 ✕ 同收起;**点外收起不删任何东西**。

### 4.4 几何算法(宿主壳 `_fitBubble`,v2/v3)

```
pointY  = 锚点块 rect.top + 块内偏移(点击点/选区纵位)
spaceBelow = 视口底 − pointY − 16;spaceAbove = pointY − 16
翻转向上 ⟺ spaceBelow < 240 且 spaceAbove > spaceBelow
maxHeight = clamp(200px, min(380px, 45vh), 可用空间 − 16px)  # v3.2 起 380 绝对上限
```

触发:开泡/重开/文档滚动/窗口 resize(监听随 destroy 摘除)。
滚动跟随由 DOM 挂载天然完成(壳挂在锚点块内),JS 只重算翻转与上限。

### 4.5 删除(v3;action = skill)

1. 泡头 🗑 两击确认(armed 态)→ 控件 `delete` action(exec:local——
   local 也是 skill,协议原则:所有 action 是 skill);
2. 闸门放行 → 父 `_deleteBubble`:壳/标记摘除 → `remove_child(destroy)` →
   `POST /platform/api/docs/{name}/bubbles/delete`(幂等:无流也 200);
3. 后端失败不挡 UI(读面是事实源,下次加载以持久化为准)。

## 5. Context 供给(气泡怎么知道自己在批什么)

两级注入,都是 compound 协议通道(§7-2):

1. **widget 级**(def `child_context`):每个 bubble 的 fragment 注入
   `{anchor, paragraph, full_text}` —— paragraph 按锚点实时取(列级锚点取选中跨度);
2. **app 级**:`/doc/<名>` 注册 provider({name, versions, dirty});
   reparent 进 desktop 后经 `_rebindAppProvider` 按新 path 重注。

发送时控件经 cascade 协议向上收集 fragment 链(bubble → doc-editor → …),
打成信封随 submit 上送——**skill 自己决定怎么用这些 context**,UI 不预设。

### 5.1 生成链(P3;批注批处理主链)

1. 工具条「🔄 生成下一版本」四态:无 pending 禁用 / 正常主色 + 计数徽标 /
   `⏳ 生成中…` / 失败红(⚠ 重试 + toast);
2. 点击 → `POST /platform/api/docs/{name}/generate`(baseVersion = 当前快照号;
   annotations 缺省 = 库内全部 pending;chatContext 缺省 = 主对话最近 20 条);
   - **409**(C5 版本冲突):toast + 自动刷新;
   - **502**(输出两次不合契约):不落库,红色重试;
3. 成功 → 本地版本链推进 + reload 重拉新文 + `_syncAnnotations`(批注状态
   /重锚刷新,标记/高亮变色)→ **自动切 Diff 视图**;
4. Diff 视图(viewseg 第三态,宿主面):标题版本范围 + ✓ 采纳 / ↩ 回滚 +
   摘要卡 + 来源批注卡(点击定位回标记)+ unified 行;
5. 采纳 = 切预览(库已是新文);回滚 = doc.rewind(restore 父版本)+ 被弃
   版本 meta 标 `rolledBackTo`(C2,不删);无快照时 generate 先封存
   pre-generate v001(回滚锚);
6. 状态栏:字数 · vN · N 批注待处理(点击滚第一条)· N 对话待应用;
   生成后摘要顶替对话位;
7. 快捷键:Ctrl/Cmd+Shift+A 添加批注 / G 生成 / H 版本历史(P4)/ D Diff /
   1 预览 / 2 源码(document 级委托,实例幂等);
8. chat 通道:发送行为不动(doc_editor 即时改文档保留);assistant 消息旁
   徽章 = 已改文档 / 待生成处理 / 已参与 vN(generate 推进已参与边界)。

## 6. 未读语义(v4 退役)

消息流没了,未读游标(seen/unread/bubbleNewFrom)随之全删(P2);
状态可见性由**标记色环 + 状态徽标**承担(pending 粉/applied 绿/ignored 灰/
outdated 橙虚线)。生成后的状态流转通知面在 P3(生成工作流)。

## 7. 持久化与事实源(v4)

- DocStore `annotations/`(按 anchor-hash 文件):**单条批注记录**(quote/
  version/content/status/generation);旧 `bubbles/` 消息流**只读不删**,
  读取时压缩迁移(首条 user → content,其余进 history,status=pending;
  无 user 的评审流取首条消息,severity 随);
- REST 面:`GET/POST /api/docs/{name}/annotations`(upsert;content ≤500;
  锚点格式校验);删除沿用 `POST .../bubbles/delete`(两面都删,幂等);
- 前端种子 = GET annotations(desktop 打开时拉);开关不丢,删除即清;
- 生成后锚点由 **reanchor** 重定位(P1,skills/reanchor.py)。

## 8. 文件与测试索引

| 层 | 文件 |
|---|---|
| 宿主壳 | `web_platform/static/doc-editor.js`(openBubble/_fitBubble/_selectionAnchor/_deleteBubble/child_event 接管) |
| 控件 | `web/static/js/widgets/w-bubble.{js,render.js}`(v4:两形态/状态徽标/输入态硬规格/delete action) |
| 后端 | `web_platform/app.py`(annotations/generate/bubbles/delete)+ `skills/doc_store.py`(save/read_annotations/set_annotation_status/delete_bubble)+ `skills/reanchor.py` |
| 协议 | `docs/COMPOUND-WIDGET.md` §3-§7;验收条文 `docs/WIDGET-DESIGN.md` §3.13 v3 |

测试:stub `tests/widgets.test.mjs`(v2/v3 断言块)+ `tests/compound.test.mjs`;
端点 `agent_os/tests/web_platform/test_doc_store.py`;
真实浏览器 `tests-ui/test_doceditor.py`(v4 组:输入态规格/提交成标记/悬停 tooltip/点外 C1/编辑回 pending/垃圾桶;几何回归 v2/v32 段)。

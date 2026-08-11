# Text ↔ Bubble 交互协议(实然文档)

> 记录**当前实现**的文档↔气泡批注操作逻辑(as-built,2026-08-11,构建 2026-08-11.9)。
> 每节标注代码出处;设计与验收条文见 `WIDGET-DESIGN.md` §3.13(v3),
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

格式:`doc.md#L<始>[:C<始列>]-L<末>[:C<末列>]`,列**可选**,行级向后兼容。

- 正则(前后端同一):`^doc\.md#L(\d+)(?::C(\d+))?-L(\d+)(?::C(\d+))?$`
  (app.py `_ANCHOR_RE`;skills/platform/tools.py 同形);
- 解析:`parseAnchor(anchor) → {start, end, sc, ec}`(sc/ec 可空);
- **列级映射**(宿主 `_selectionAnchor`):普通段落块 TreeWalker 累加文本偏移 →
  行列;**标记语法块**(#/-/| 前缀)渲染文与源文有符号差,回落行级——
  quote 仍是选中文本,语义不丢;
- 持久化:DocStore 按 anchor-hash 文件名存消息流;列级与行级锚点天然并存。

## 3. 生命周期状态机

```
            右键(原位)/ 💬 锚点钮 / 批注栏点击
                   ↓ add_child
[打开] ──点泡外 / ✕ / Esc──→ [收起为原位标记] ──点标记/再右键──→ [打开](重开=聚焦,不重复建)
  │                                                              ↑ 草稿/消息全保留(canonical state)
  └──垃圾桶两击──→ [删除](remove_child + 后端删持久化,不可逆)
```

- **打开**(`openBubble`):同锚点重开 = 聚焦(seen 游标前进),不建第二个实例;
- **收起**:壳隐藏 + 标记显出;canonical state 不动(草稿逐字保留);
- **删除**:唯一销毁路径,后端读面是事实源。

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
maxHeight = clamp(200px, 45vh, 可用空间 − 16px)
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

## 6. 未读语义(D4 增量)

- 事实源:seen 游标(assistant 消息数),双写 compound state(可序列化)
  + localStorage(跨会话);
- 未读数 = assistant 总数 − min(seen, 总数);打开/重开/收起即记已读;
- 首开的未读数 → `newFrom`(分隔线「以下是新消息」插位,控件本体渲染)。

## 7. 持久化与事实源

- DocStore `bubbles/`(按 anchor-hash 文件):消息流;**后端读面是事实源**,
  前端种子只影响首渲;
- 打开时 seed = 持久化消息流 → canonical state;开关不丢,删除即清;
- 文档内容变更后锚点不失效重映射(行号锚定的已知边界,未做——记此备查)。

## 8. 文件与测试索引

| 层 | 文件 |
|---|---|
| 宿主壳 | `web_platform/static/doc-editor.js`(openBubble/_fitBubble/_selectionAnchor/_deleteBubble/child_event 接管) |
| 控件 | `web/static/js/widgets/w-bubble.{js,render.js}`(四区/队列/滚底/折叠/delete action) |
| 后端 | `web_platform/app.py`(comment/bubbles/delete,_ANCHOR_RE)+ `skills/doc_store.py`(save/delete_bubble) |
| 协议 | `docs/COMPOUND-WIDGET.md` §3-§7;验收条文 `docs/WIDGET-DESIGN.md` §3.13 v3 |

测试:stub `tests/widgets.test.mjs`(v2/v3 断言块)+ `tests/compound.test.mjs`;
端点 `agent_os/tests/web_platform/test_doc_store.py`;
真实浏览器 `tests-ui/test_doceditor.py`(run_bubble_v2/v3:原位/选区/收起/删除/翻转/队列)。

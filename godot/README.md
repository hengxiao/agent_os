# Agent OS · Godot 宿主(UI 内核 + Doc Editor)

> 对象:本目录是一个自包含 Godot 4 工程(GDScript,零插件、零外部依赖;
> CJK 字体已内置)。前身 `unity/` 因 Unity 许可证摩擦弃用,架构同构移植。
> 定位:Web Platform 的游戏引擎宿主——把 `docs/APP-MODEL.md` 的 UI 模型
> (widget 协议 / compound / 寻址 / cascade / action 管道 / 主题契约)落到
> Godot,第一个 app = Doc Editor(`docs/DOC-EDITOR.md`)。
> 红线(继承 WEB-PLATFORM §7):**不动内核与信任模型**——所有写动作走既有
> action 管道与专属端点,本工程零新通道;state 服务端权威,客户端只发事件。

## 运行

```bash
# 1. 起后端(缺省 http://127.0.0.1:8391)
instance/run-web.sh

# 2. 跑 Doc Editor(启动器会补上用户态 sysroot 的 X11 库;无 root 环境)
godot/run-local.sh

# 调试直通:自动开档/开便签/开版本叠/自拍截图
godot/run-local.sh -- --open-doc dev.uitest --open-note "doc.md#L3-L3" --shot /tmp/x.png

# 无头自检(不需要后端;41 + 32 项断言)
godot --headless --path godot/ -s res://tests/smoke.gd
godot --headless --path godot/ -s res://tests/scene_smoke.gd

# 活端集成自检(需要先起后端;14 项断言,含越权构造反面例)
godot --headless --path godot/ -s res://tests/live.gd
```

## 两种视图模式

顶栏「视图」按钮切换,**同一个 app instance、同一份 state、同一组动作**:

- **信件(默认;设计 = docs/GAME-UI-DOC.md,示例图 docs/game-ui/*.svg)**:
  - 信箱 = 左栏信列表;信纸 = 居中暖白纸 + 版本朱砂印 + 行文块(点击 =
    当前段微光左道,右键 = 写批注);信末回执联 = doc 主对话;
  - 批注 = **行文末尾的内联 `[注×N]` 标记**(点选开签);批注坞 = 纯用户批注
    (引文 + 内容 + 状态章 + 存/删)——annotations 模型:无即时 AI 回复,
    攒着批处理(对应 P2;不再是 comment 对话气泡);
  - 回溯 = **版本页栈 + 版本树**(v1.8):顶栏页栈回推 ≤20 版、悬停翻书
    展开(详情卡 = LLM 摘要 + 「回到这版」两段确认走 doc.rewind);版本树 =
    parent 链分支图覆盖层(回推线性、前衍可分支,链长可拖拽平移);
  - 动效全走 MotionPlayer 具名动效(reduced-motion 降级):text-reveal /
    note-arrive / stamp-press / change-flash / page-fan。
- **面板**:经典编辑器(编辑/预览/分屏 + 批注栏)——精确操作与无障碍的回落面
  (DOM 版 `/platform/` 仍是最终的无障碍客户端)。

布局纪律(踩过的坑,已写进代码注释):widget root 是透明 PanelContainer,
子件铺满靠容器逻辑,**不用锚点**;ScrollContainer 子件必须显式给
`custom_minimum_size.x`;自由摆放 + 零宽量体 = 天高最小高度,禁止。

## 文稿 → 代码映射

| 设计文稿 | 概念 | 代码(GDScript) |
|---|---|---|
| GAME-UI-DOC.md | 信纸/页边便签/版本叠/回执联(信件视图层) | `apps/doc_editor/letter/letter_page.gd` `letter_host.gd` `page_stack.gd` `version_tree.gd` |
| WIDGETS.md §1.2 | WidgetDef 注册即校验 | `kernel/widget_def.gd` `kernel/widget_registry.gd` |
| WIDGETS.md §1.3 | state 可序列化 / 事件上行 / 零 fetch(基类无网络面) | `kernel/widget.gd` |
| COMPOUND-WIDGET.md §2-7 | slots/dynamic.allow/事件闸门/child_context/badge 记账 | `kernel/compound_widget.gd` |
| APP-MODEL.md §14 | /root/... 寻址,read/focus 动词 | `kernel/widget_tree.gd` |
| APP-MODEL.md §16 | context cascade(逐级/单向/父改写) | `kernel/context_cascade.gd` |
| APP-MODEL.md §4/§2 | action 管道 + AppInstance 镜像 | `kernel/action_pipeline.gd` |
| COMPOUND-WIDGET.md §8 | desktop 根(/root,shell 级 fragment) | `kernel/widget_tree.gd` 内部类 |
| web_platform/app.py | REST 契约(统一信封 {ok,status,json\|error}) | `kernel/agent_os_client.gd` |
| app.py /api/stream(M4b) | SSE live 通道(HTTPClient 分块读流) | `kernel/sse_client.gd` |
| DEBUG-UI-THEMES.md §2 | 主题契约(缺项不注册;组件零分支;顺序循环) | `kernel/theme_pack.gd` `theme_registry.gd` |
| §2.3 动效档案 | 具名动效 + reduced-motion 强制 Instant | `kernel/motion_player.gd` |
| themes/{classic,pixel}.css | 主题值(逐字移植) | `kernel/built_in_themes.gd` |
| WIDGETS.md §1.3-3 四态 | 按钮 normal/hover/pressed/disabled + focus 环 | `kernel/sty.gd` |
| DOC-EDITOR.md D2 | mdBlocks 分块 / doc.md#Lx-Ly 锚点 | `apps/doc_editor/md_blocks.gd` |
| W-md / W-bubble / W-list | md 预览(BBCode 白名单)/ 锚点气泡 / 文档列表 | `md_viewer.gd` `chat_bubble.gd` `doc_list.gd` |
| DOC-EDITOR.md | 编辑器 app(compound,三通道全用) | `apps/doc_editor/doc_editor_app.gd` |

## 用到的后端端点(v1.8 新增 versions/tree,余皆既有)

```
GET  /platform/api/docs                         列表
POST /platform/api/docs                         新建
GET  /platform/api/docs/{name}                  全文 + versions + chat 种子
GET  /platform/api/docs/{name}/bubbles          气泡流(服务端事实源)
POST /platform/api/docs/{name}/comment          段落批注(D2 专属端点先例,cascade 随信)
POST /platform/api/docs/{name}/chat             doc 作用域主对话(changed=全文对比)
POST /platform/api/docs/{name}/review           全文评审 → 批注挂段
GET  /platform/api/docs/{name}/annotations      批注列表(P2;无即时 AI 回复)
POST /platform/api/docs/{name}/annotations      批注存(upsert,pending)
POST /platform/api/docs/{name}/bubbles/delete   批注删(两面,幂等)
GET  /platform/api/docs/{name}/versions/tree    版本树(base + parent 链;v1.8 新增)
GET  /platform/api/docs/{name}/versions/{v}     版本快照内容(只读;页栈/树提示用)
GET  /platform/api/docs/{name}/diff-summary     版本差异 LLM 摘要(difsum 缓存在服务端)
POST /platform/api/apps/spawn                   kind=doc 登记(action 管道锚)
POST /platform/api/apps/{id}/actions/{action}   doc.save / doc.snapshot / doc.rewind / comment.apply
```

cascade 信封(与 web doc-editor 同构):widget 级 `{anchor, quote}` +
app 注入 `{paragraph, full_text}` + app 级 `{name, versions, dirty}` +
shell 级 `{user, theme, at}`。

## 已知偏差与留口(v1)

- **多 view(hard link)/ reparent 未实现**:compound 基座只有 ownership 树;
  COMPOUND §5/§6 留待桌面化需要时补。
- **气泡挂批注栏,不内联块旁**:结构性规避 web 侧"重渲摘宿主"弯腰点
  (DOC-EDITOR.md D2 弯腰点 ②);块上保留「注 N」计数标记 + 右键开泡。
- **批注标记用汉字「注」不用 💬**:内置字体链无 emoji 字形;要 emoji 需再挂
  Noto Emoji 字体(留口)。
- **导出 = 复制全文到剪贴板**(DisplayServer.clipboard);另存对话框留口。
- **SSE 未消费**:平台流暂无 doc 事件;`sse_client.gd` 是给 run 态势类 app 备的。
- **view 切换是客户端本地态**(web 的 meta.set 走服务端 local mutator)。
- **agent 的 act 动词未开放**(§14.4 权限收口);read/focus 已可用。
- **单文档打开**:同窗只开一篇;多文档并存是 desktop 的事。
- **无障碍**:Godot 的读屏通道有限——DOM 版 `/platform/` 永久保留为
  无障碍/专家客户端(WEB-PLATFORM §8 降级面);双编码(状态=色+文字)、
  reduced-motion(`ThemeRegistry.reduced_motion`)在本侧继续执行。

## 与 web 版的行为对照(自检清单)

1. 后端未起:状态栏红字 + 顶栏状态点黄色(tooltip 文字双编码)。
2. 列表出文档;新建重名 409;打开文档 → 分块预览(标题/列表/代码块)。
3. 右键块或「注」开泡;发批注 → 回复落气泡(关窗重开仍在,服务端事实源)。
4. 编辑 → 保存(管道 doc.save);快照 → 版本下拉出现 v001;回滚两击确认。
5. 评审 → 批注自动挂段(块上「注 N」)。
6. 对话条改文档 → changed=true → 自动重载。
7. 主题 classic ↔ pixel 全量换肤,内容不丢;重启保持(user:// 配置)。

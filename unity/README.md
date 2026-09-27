# Agent OS · Unity 宿主(第一期:UI 内核 + Doc Editor)

> 对象:本目录是一个自包含 Unity 工程(Unity 6 LTS,`6000.0.x`)。
> 定位:Web Platform 的游戏引擎宿主——把 `docs/APP-MODEL.md` 的 UI 模型
> (widget 协议 / compound / 寻址 / cascade / action 管道 / 主题契约)落到 C#,
> 第一个 app = Doc Editor(`docs/DOC-EDITOR.md`)。
> 红线(继承 WEB-PLATFORM §7):**不动内核与信任模型**——所有写动作走既有
> action 管道与专属端点,本工程零新通道;state 服务端权威,客户端只发事件。
> 渲染:本期用 **UI Toolkit**(EditorWindow 宿主),不是 3D 场景——协议层与
> 渲染层解耦,3D 化时 widget/compound/管道层原样保留,只换视图基座。

## 打开方式

1. 启动后端:`instance/run-web.sh`(缺省 `http://127.0.0.1:8391`)。
2. Unity Hub 用 Unity 6(6000.0+)打开本目录(`unity/`)。
3. 菜单 **Agent OS ▸ Doc Editor** → 左栏文档列表 → 选一篇或新建。

无需任何资产导入:无场景/无 prefab/无 USS 文件,全部代码驱动(主题 token
以 C# 内联样式应用,USS 资产化是后续优化)。

## 文稿 → 代码映射

| 设计文稿 | 概念 | 代码 |
|---|---|---|
| WIDGETS.md §1.2 | WidgetDef(kind/actions/events/surfaces) | `Kernel/Core/WidgetDef.cs` |
| WIDGETS.md §1.3 | state 可序列化 / 事件上行 / 零 fetch | `Kernel/Core/Widget.cs`(基类无网络面) |
| COMPOUND-WIDGET.md §2-7 | slots/dynamic.allow/事件闸门/ChildContext/badge 记账 | `Kernel/Core/CompoundWidget.cs` |
| APP-MODEL.md §14 | /root/... 寻址,read/focus 动词 | `Kernel/Core/WidgetTree.cs` |
| APP-MODEL.md §16 | context cascade(逐级、单向、父改写) | `Kernel/Core/ContextCascade.cs` |
| APP-MODEL.md §4 | action 管道(surface/args_input/cascade;state 服务端权威) | `Kernel/Core/ActionPipeline.cs` |
| APP-MODEL.md §2 | AppInstance 镜像 | `Kernel/Core/ActionPipeline.cs` AppInstance |
| COMPOUND-WIDGET.md §8 | desktop 根 | `Kernel/Core/DesktopRoot.cs` |
| DEBUG-UI-THEMES.md §2 | 主题契约(token/copy/motion;缺项不注册;组件零分支) | `Kernel/Theme/ThemePack.cs` `ThemeRegistry.cs` `Sty.cs` |
| DEBUG-UI-THEMES.md §2.3 | 动效档案 + reduced-motion 强制 Instant | `Kernel/Theme/MotionPlayer.cs` |
| themes/classic.css pixel.css | 主题值(逐字移植) | `Kernel/Theme/BuiltInThemes.cs` |
| DOC-EDITOR.md D2 | mdBlocks 分块 / doc.md#Lx-Ly 锚点 | `Apps/DocEditor/MdBlocks.cs` |
| DOC-EDITOR.md | 编辑器 app(compound) | `Apps/DocEditor/DocEditorApp.cs` |
| WIDGETS.md W-md / W-bubble / W-list | md 预览 / 锚点气泡 / 文档列表 | `MdViewerWidget.cs` `ChatBubbleWidget.cs` `DocListWidget.cs` |
| web_platform/app.py | REST 契约 | `Kernel/Net/AgentOsClient.cs` |
| app.py /api/stream(M4b) | SSE live 通道 | `Kernel/Net/SseClient.cs` |

## 用到的后端端点(全部既有,零新增)

```
GET  /platform/api/docs                         列表
POST /platform/api/docs                         新建
GET  /platform/api/docs/{name}                  全文 + versions + chat 种子
GET  /platform/api/docs/{name}/bubbles          气泡流(服务端事实源)
POST /platform/api/docs/{name}/comment          段落批注(D2 专属端点先例,cascade 随信)
POST /platform/api/docs/{name}/chat             doc 作用域主对话(changed=全文对比)
POST /platform/api/docs/{name}/review           全文评审 → 批注挂段
POST /platform/api/apps/spawn                   kind=doc 登记(action 管道锚)
POST /platform/api/apps/{id}/actions/{action}   doc.save / doc.snapshot / doc.rewind / comment.apply
```

cascade 信封(与 web doc-editor 同构):widget 级 `{anchor, quote}` +
app 注入 `{paragraph, full_text}` + app 级 `{name, versions, dirty}` +
shell 级 `{user, theme, at}`。

## 已知偏差与留口(v1)

- **多 view(hard link)/ reparent 未实现**:compound 基座只有 ownership 树;
  COMPOUND §5/§6 的 link_view/move_child 留待桌面化需要时补。
- **气泡挂批注栏,不内联块旁**:结构性规避 web 侧"重渲摘宿主"弯腰点
  (DOC-EDITOR.md D2 弯腰点 ②);块上保留 💬 计数标记 + 右键开泡。
- **导出 = 复制全文到剪贴板**(web 的"复制全文"路径);另存 .md 留了
  `IExportSink.SaveFile`(Editor 宿主已实现,工具条未接钮)。
- **SSE 未消费**:平台流暂无 doc 事件;`SseClient` 是给 run 态势类 app 备的。
- **view 切换是客户端本地态**(web 的 meta.set 走服务端 local mutator 持久化);
  会话内有效,重启回 split。
- **agent 的 act 动词未开放**(§14.4 权限收口,M6 语义);read/focus 已可用。
- **单文档打开**:同窗只开一篇(切换即 remove_child + 全新 instance);
  多文档并存是 desktop 的事。
- **无障碍**:UI Toolkit 无读屏器通道——DOM 版 `/platform/` 永久保留为
  无障碍/专家客户端(对齐 WEB-PLATFORM §8 降级面);双编码(状态=色+文字)、
  reduced-motion(ThemeRegistry.ReducedMotion 强制 Instant)在本侧继续执行。

## 自检清单(无 CI 环境,打开后按此走查)

1. 编译零错;菜单 Agent OS ▸ Doc Editor 打开窗口。
2. 后端未起时:状态栏红字提示,顶栏状态点黄色(双编码:tooltip 文字)。
3. 起后端:列表出文档;新建 `test.unity` → 409/创建成功语义正确。
4. 打开文档 → 预览分块正确(标题/列表/代码块);右键块开泡;发一条批注
   (LLM 正常时回复落气泡,关窗重开仍在——服务端事实源)。
5. 编辑 → 保存(管道 doc.save);快照 → 版本下拉出现 v001;改几行 →
   选 v001 → 回滚(两击确认)→ 全文恢复。
6. 评审 → 批注自动挂段(块上 💬 计数)。
7. 对话条输入"把标题改成 X" → changed=true → 右侧自动重载。
8. 主题切换 classic ↔ pixel:全量换肤,文档内容/气泡/对话不丢;
   重启编辑器主题保持(PlayerPrefs)。
9. agent 视角:Console 里 `_tree.DumpPaths()` 可打到 /root 全树寻址
   (窗口留调试钩:见 DocEditorWindow 字段 `_tree`)。

# Desktop Widget 设计(C4)—— 层级体系的根

> 前置:`docs/COMPOUND-WIDGET.md`(协议 + C1.1/C3 实现注)。
> 本文定义根 compound:`desktop`。platform 壳(app.js)迁为它,
> app = desktop 的动态子件;任务栏/图标 = 子件的 card 面,窗口 = 子件的 tab 面。
> 验收:两套测试(stub + tests-ui)+ 逐条验收清单。

## 1. 现状盘点(platform 壳做了什么)

`web_platform/static/app.js` 当前壳层职责(迁移的对照表):

| 职责 | 现状 | compound 语义归宿 |
|---|---|---|
| 桌面主屏(壁纸 + 图标行 + 壁纸切换) | 全部 app 最小化后显示 | desktop layout 的「无激活子件」分支 |
| 窗口区(单激活最大化,标题栏:图标/标题/—/✕) | app.js 手写 DOM | 激活子件的 slot + desktop chrome |
| 任务栏(tab 列表:标题 + ✕;长按重排;卡片拖入打开) | app.js 手写 DOM | children 摘要行(读 slotRefs + badge) |
| tab 激活/最小化/关闭 | shell 状态机 | desktop state.active / view detach(hidden)/ remove_child |
| 托盘收件箱(supervisor pending) | 独立面板 | 第一个**系统子件**(预定义 slot) |
| 对话卡拖入 tab 条打开详情页 | DnD + openDetail | attach_existing / add_child |

## 2. desktop def

```js
{
  kind: "desktop",
  state_schema: { type: "object" },           // + wallpaper/active/icon_order/taskbar_order
  state_defaults: { wallpaper: "default", active: null, icon_order: [], taskbar_order: [] },
  events: ["child_event", "reparent", "activate", "close"],
  surfaces: ["tab"],                          // desktop 只有完整面(它就是根)
  compound: {
    slots: [
      { id: "inbox", kind: "supervisor-inbox", surface: "card" },  // 预定义系统件
    ],
    dynamic: { allow: ["conversation", "doc-editor", "skills-explorer", "runs-explorer",
                        "tools-explorer", "lab", "debug-console"], max: 30 },
    layout(state, slotRefs) -> html,          // §3 三分支:桌面/单窗/桌面+任务栏
    on_child_event(child, event, payload),    // 转发 + 记未读(badge 数据源,§4)
    child_context(child, frag),               // 注入 desktop 级上下文(用户/主题/全局状态)
  },
}
```

**寻址**:desktop path = `/root`;子件 `/root/<app-id>`;子件的子件(doc-editor 的
bubble)顺延 `/root/<app-id>/<bubble-id>`。全树寻址自此唯一。

## 3. layout 三分支(state 驱动,纯函数)

1. `active == null && children 全最小化` → **桌面**:壁纸 + 图标栅格
   (每个 child 一个图标:icon + 名称 + badge;图标 = 子的 card 面缩略或图标位);
2. `active == <id>` → **单窗**:窗口 chrome(标题栏:icon/标题/—/✕)+
   `[data-slot="<id>"]` 激活子件的 tab 面;其余子件 view detach(**hidden 语义**:
   instance 活着,state/context 照旧——最小化不丢状态是协议给的,不用自己造);
3. 任务栏恒在底部:children 摘要行(见 §4)。

**不发明浮动窗口**(红线 §11 不变):单激活最大化是外观与行为一致的窗口模型;
将来若要双窗并排,= 两个 slot 同屏(desktop state 加 left/right active),协议已兼容。

## 4. 任务栏与 badge:一个协议补丁

矛盾:§7 规定父 layout 只读 slotRefs 元信息,读不到子 state——但任务栏需要
每子的**未读/活跃徽标**(badge)。C3 的批注栏为此留在了宿主层;桌面不能留,
否则任务栏又回到手写。

补丁(协议级,COMPOUND-WIDGET §7 增补一条):
**slotRefs 增加 `badge` 元信息**——父在 `on_child_event` 闸门里维护
`state.badges[childId]`(闸门本来就是父的合法信息面),layout 读 slotRefs
时拼上。即:badge 不是父偷读子 state,是**父对子事件的记账**,语义干净。

任务栏行 = slotRefs 迭代:icon + 名称(子 def 的 title 元信息)+ badge +
激活态;点击 = activate(local action);✕ = close(remove_child,确认后);
长按重排 = taskbar_order state 变更 → relayout。

## 5. app 子件的两种来源

1. **静态注册**:每个 app 一个薄 def(conversation/doc-editor/skills/...),
   多半是已有页面的薄壳 compound——包一层 layout 把现有页面逻辑装进去,
   行为不动;doc-editor 已经是 compound,直接进 allow 白名单;
2. **动态打开**:对话卡「打开详情」/ 卡片拖入任务栏 =
   `add_child(kind, {state: {ref}})` 或 attach_existing(已在别处的 instance
   迁进窗口区——hard link 的真实用例:对话流里的 doc 卡( card 面)与窗口区的
   完整 doc-editor 是同一 instance)。

## 6. 迁移路径(四小步,每步两套测试绿)

| 步 | 内容 | 验收 |
|---|---|---|
| C4.1 | desktop def + layout 三分支 + 任务栏(badge 补丁);**与旧壳并存**:新页 `/platform/desktop.html` 先跑通 | tests-ui:开 app/最小化/重开 state 不动/关闭/重排 |
| C4.2 | conversation app 与 doc-editor 进白名单(薄壳/直进) | 既有行为测试 + 新 UI 测试 |
| C4.3 | 其余 apps(skills/runs/tools/lab/debug)薄壳化 | 同上 |
| C4.4 | platform index 切换到 desktop 根,旧壳退役 | 全量回归 + 旧壳代码删除 |

## 7. 验收清单(C4 全量)

- [ ] 桌面/单窗/任务栏三分支渲染正确,切换无 JS 错误(真实浏览器);
- [ ] 最小化 → 重开:app 内容逐字在(hidden 语义,不重建 instance);
- [ ] 关闭 = remove_child;重开后是全新 instance;
- [ ] 任务栏 badge 与子事件一致(闸门记账);重排持久于 state;
- [ ] 对话卡拖入 → 窗口区打开同一 ref;doc 卡 ↔ doc-editor 同 instance 双形态;
- [ ] inbox 预定义子件在托盘,升权请求到达有 badge;
- [ ] 全部 app action 仍走三态 exec;desktop 自身 action(activate/close/reorder)是 local;
- [ ] 寻址全树唯一:/root/... 通到 bubble 级。

## 8. 不做清单

- 不做浮动窗口/层叠/拖拽自由摆放(红线);
- 不做多桌面/workspace 切换(协议兼容,需求到了再开);
- 不做跨 app 的 state 总线(app 间通信只许经 skill/后端,不许 desktop 开后门);
- 旧壳(app.js)在 C4.4 之前不删(并存期双轨验收)。

# 基础 Widget 库设计计划

> 版本:v0.1(计划)
> 对象:web_platform 的基础控件层——text editor / table editor 等标准 widget
> 关系:APP-MODEL.md v0.3 是架构基线(app→surfaces→sections→**widgets**);
>   widget 注册与寻址遵守 §14;动作归态遵守三态 exec(§4);主题契约遵守
>   DEBUG-UI-THEMES.md(token + copy 六主题 + 组件零分支)
> 一句话:**widget 是叶子控件——有状态、发事件、可调 exec;永不直接调后端。**

---

## 1. 分层与协议

### 1.1 分层

```
app(业务对象:skill-pack/run/...)
└─ surface(card / tab 两张面孔)
   └─ section(逻辑区块:tests/findings/members)
      └─ widget(基础控件:本文目录)
```

### 1.2 Widget 协议(每个控件的定义面)

```jsonc
// WidgetDef(注册表静态面;与 AppManifest 同哲学但更小)
{
  "kind": "json-editor",
  "v": 1,
  "state_schema": { /* 控件的本地状态 */ },
  "actions": [   // 控件自己的动作(只许 local;出海参见 §1.3)
    { "id": "set_value", "exec": "local", "args_input": {"value": {"type": "string"}} },
    { "id": "format",    "exec": "local" }
  ],
  "events": ["change", "commit", "error"],  // 发给父组件的事件(父组件映射到 app action)
  "aria": { "role": "textbox", "keys": ["Enter", "Escape"] },
  "surfaces": ["card", "tab"]   // 允许出现的面孔(控件也可声明只进 tab)
}
```

### 1.3 三条铁律

1. **widget 永不直接调后端**:它只有本地状态(state)与事件(events);
   需要出海时,父组件(app/section)把事件映射成自己的 action(exec 三态)
   ——授权面因此只在 app 层存在一次,控件不需要权限概念;
2. **本地状态可序列化**:刷新/重渲染可恢复(dirty/value/selection);
   widget 注册进 §14 registry 后获得路径,agent 可 read/focus;
3. **四态齐备**:每个可交互控件都有 hover/focus-visible/active/disabled
   设计,focus 环六主题可见(商业 widget 标准,沿用)。

## 2. Widget 目录(每个的定义)

### W-text — text editor(文本编辑器)

- **用途**:description/prompt/notes 等多行文本;变量:mono(prompt/code)与正文两种;
- **state**:{value, dirty, readonly, lang: "plain"|"prompt"|"md", wrap: bool};
- **actions**:set_value(local)、commit(local,发 commit 事件)、revert(local);
- **细节**:行号(可选 mono 变体)、选区保留(重渲染不丢)、占位符、字数/行数微标;
- **a11y**:role=textbox-multiline,aria-label 必填,Esc=blur;
- **测试**:输入/选区保留/dirty 标记/readonly 拒输入/六主题 copy。

### W-json — JSON editor(带校验的文本编辑器)

- **用途**:inputs/outputs schema、参数 JSON(F11 的 textarea 升级版);
- **state**:{value, dirty, error: {line, message}|null, schema: object|null};
- **actions**:set_value(local,即时 JSON 合法性校验)、format(local)、validate(local,按 schema);
- **细节**:错误行高亮 + 行号、失焦校验、format 一键美化、schema 不合时**行级**错误提示;
- **测试**:非法 JSON 行定位/schema 不合提示/format 幂等/错误恢复。

### W-table — table editor(表格编辑器)

- **用途**:tests 用例表、members 表、键值参数表;
- **state**:{rows: [{id, cells: {...}}], selected: [ids], schema: {columns: [{key, type, label, required?}]}};
- **actions**:add_row(local)、remove_row(local)、move_row(local,**走 §15 DnD envelope**)、set_cell(local)、remove_selected(local);
- **细节**:列定义驱动(文本/数字/布尔/枚举单元格编辑器)、行 DnD 排序(标准 envelope)、新增行骨架、空态("还没有行,点下方添加");
- **a11y**:role=grid,行可键盘上下移动(Alt+↑/↓);
- **测试**:列类型渲染/行增删移/DnD envelope 合规/空态。

### W-kv — key-value editor(键值编辑器)

- **用途**:attrs/metadata/配置映射(table 的两列特化);
- **state**:{entries: [{key, value}], allow_dup: bool};
- **actions**:add/remove/set(local);重复 key 即时警示(非硬拦,警告态);
- **测试**:重复 key 警示/序列化往返。

### W-form — schema 驱动表单

- **用途**:run.launch 参数面(textarea JSON 的正式升级)、skill inputs 填写;
- **state**:{values: {...}, errors: {field: msg}, schema: object};
- **actions**:set_field(local)、validate(local)、reset(local);
- **细节**:按 JSON Schema 生成字段(string/number/integer/boolean/enum/array/object 嵌套);required 星标;integer 的 minimum/maximum 约束;数组项编辑器(内嵌 W-table 单行版);
- **测试**:六类型字段生成/required 校验/嵌套对象/与 skeletonFromSchema 一致的默认值。

### W-list — 可选列表

- **用途**:草稿列表、技能选择、会话列表;
- **state**:{items: [{id, label, hint?, icon?}], selected: id|[ids], filter: string};
- **actions**:select(local)、filter(local)、activate(local,Enter);
- **细节**:搜索过滤(命中祖先链展开——复用 ns-tree 语义)、单/多选、键盘导航(↑↓/Enter);
- **测试**:过滤/多选/键盘路径/空态。

### W-tree — 命名空间树(ns-tree 控件化)

- **用途**:Skills/Tools 树、包成员树;
- **state**:{nodes, expanded: Set, selected};
- **actions**:toggle(local)、select(local)、filter(local);
- **细节**:单层链折叠、默认展开策略(沿用 L4.5)、可拖节点(§15);
- **测试**:沿用 ns-tree 现有测试语义 + 控件协议面。

### W-diff — diff 查看器(现 diff 渲染控件化)

- **用途**:iterate diff、版本对比;
- **state**:{left, right, mode: "split"|"unified"};
- **actions**:set_mode(local);
- **细节**:字段两列 + 行级红绿(既有呈现提取为控件)、长文本折叠上下文;
- **测试**:三态渲染/折叠/模式切换。

### W-md — Markdown 查看器(安全渲染)

- **用途**:agent 消息、NOTES.md、报告;
- **state**:{source};
- **细节**:白名单标签(标题/列表/代码块/链接/表格),**禁 HTML 注入**(纯文本转义后白名单渲染,不 innerHTML 原文);
- **测试**:XSS 向量(<script>/onerror 属性)全部转义、代码块 mono。

### W-log — 日志/终端查看器

- **用途**:run 输出、trace、调试控制台;
- **state**:{lines: [...], follow: bool, filter};
- **actions**:append(local)、toggle_follow(local)、filter(local)、copy_all(local);
- **细节**:跟随模式(新行自动滚底,上滚即暂停跟随并显示"回到底部"钮)、长窗口截断(保留尾部 N 行)、kind 着色(信号色 token);
- **测试**:跟随/暂停/截断/复制。

## 3. 组合与装配规则

- **widget 组合只允许向下**:section 可以组合 widget,widget 可以组合 widget(W-form 内嵌 W-table);禁止反向(app 进 widget);
- **事件映射表在 section**:widget 的 change/commit → section 声明的 handler → app action(exec);控件不知道 app 存在;
- **数据流单向**:state 从 app → section → widget 下行;事件上行;widget 不拉取;
- **寻址自动**:section 渲染 widget 时把 §14 路径前缀传给控件,控件自己登记叶子路径(`.../section/tests/table/row/3/cell/input`)。

## 4. 主题与文案

- 全部样式只消费契约 token;每控件一个 css 区块(app.css 或平台 css);六主题走查项写进各控件测试;
- 文案全走 copy key(六主题):空态/按钮/错误提示;技术原文(JSON 错误消息)豁免直读;
- reduced-motion:所有动效(高亮脉冲/展开)在媒体查询下停用。

## 5. 测试库定义(每控件一套)

| 层 | 内容 |
|---|---|
| 协议 | state_schema 合法;actions 全 local(无 endpoint/run 断言);events 清单完整 |
| 渲染 | 四态/空态/错误态/禁用态截图级断言(类与属性) |
| 交互 | 状态机路径(输入→dirty→commit→事件);键盘;a11y 属性 |
| 边界 | XSS(md)/非法 JSON/超长文本/空数据 |
| 主题 | 六主题 copy key 覆盖;focus 环存在 |

## 6. 分期

| 期 | 内容 |
|---|---|
| W1 | Widget 协议与注册表 + W-text + W-json(编辑器是基础中的基础) |
| W2 | W-table + W-kv(表格家族;tests/attrs 的直接受益者) |
| W3 | W-form + W-list + W-tree(数据输入与导航;run.launch 表单化落地) |
| W4 | W-diff + W-md + W-log(呈现家族;agent 消息与调试面的统一) |

每期交付:协议实现 + 该期控件 + 测试库 + 至少一个真实装配点
(W1 装进 lab 编辑器;W2 装进 tests 编辑;W3 装进 run.launch;W4 装进对话流)。

## 7. 不做

- 不做富文本/WYSIWYG(W-md 是查看器;编辑器是纯文本,代码编辑器属另一产品级组件);
- 不做图表/chart(数据可视化未在需求面);
- 不做日期时间控件(无使用场景);
- 不做拖拽造布局的 GUI 构建器(widget 是代码资产,组装在代码里);
- 不重写已有稳定组件(ns-tree/diff 只控件化提取,不改行为)。

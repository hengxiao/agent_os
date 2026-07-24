# 运行状态展示设计:Block 模型

> 版本:v0.1(设计稿)
> 上位文档:WEB-UI.md(应用壳/设计系统);本文档定义 **run 的展示模型**(Block 模型)与导入导出、底部 metrics 栏
> 约束:无构建无框架(WEB-UI.md §7);**后端零改动**——全部数据从现有 API(signals/trace、checkpoint、/usage、/rca、/stream)推导

---

## 1. 现状与目标

现 Workbench 是三栏(帧树/信号时间线/消息检视器)。它忠实于内核数据结构,但不符合阅读直觉:**用户想读的是"这个技能怎么跑的"——以 SkillCall 为主轴的递归块流**,而不是三张表的拼接。本文档把 run 的展示重构为 Block 模型:

- **SkillCall 是主轴**(栈的基础):每个 SkillCall 块 = 一帧,内部嵌套子块(LLMReasoning / ToolCall / 子 SkillCall),递归展开;
- 每个 Block 有**缩略态与展开态**、**记录的 metadata**、**明确的数据来源**;
- run 可**导出/导入**(纯前端);每个 run 有**实时底部 metrics 栏**。

## 2. Block 模型

```ts
type Block = {
  id: string;                    // 稳定 id(帧 id / call id / 消息序号)
  type: "skill" | "tool" | "llm" | "text";
  status: "running" | "done" | "failed" | "aborted" | "vetoed";
  collapsed: boolean;            // 默认:完成帧缩略、当前帧展开
  input: BlockIO;                // 输入区(类型各异,见 §3)
  body: Block[] | BlockBody;     // 内部逻辑(skill = 子块列表;llm = context 部分;text/tool = 内容)
  output: BlockIO;               // 输出区
  meta: BlockMeta;               // metadata(§3 各类清单;导出时随块携带)
}
```

数据映射(后端零改动的关键):

| Block | 数据来源 |
|---|---|
| SkillCall | `SkillFrame`(checkpoint / detail.frames)+ 其信号区间 |
| ToolCall | 帧上下文里的 tool_call/tool_result 配对 + `pre/post:tool.call` |
| LLMReasoning | 帧内一次 llm.request→response 周期 + `post:llm.response` payload |
| Text | system 指令、状态注入行、纠偏/错误观察消息 |

**重建规则**:消息只追加 ⇒ 任意 step N 的"当时完整上下文" = 该 assistant 消息及其前缀(§3.3 展开态的数据基础);live 中 = 当前快照持续追加。status bar 是 ephemeral 注入(不在 context.messages),作为独立 Text 块标注"内核状态注入"。

## 3. 各 Block 展示规格

### 3.1 SkillCall(主轴)

**缩略态(默认,一行半)**:

```
▸ fib  n=5                     ● done  3 steps · 10 tok · $0.002 · 1.2s
  → {seq: [0,1,1,2,3]}
```

- 左:状态点 + skill 名 + **输入摘要**(query:input 的 JSON 单行截断);
- 右:metadata 摘要(steps / tokens / cost / 时长);
- 次行:**输出摘要**(result JSON 单行截断;failed 则红字 error 单行)。

**展开态**:头部同行,体内为**子块流**(本帧的 LLMReasoning / ToolCall / 子 SkillCall / Text 块,按发生序),尾部输出卡(完整 result JSON,可复制)。

**metadata(记录)**:skill ref、depth、status、steps、prompt/completion/cache_read/cache_write/thinking tokens、cost、时长(首信号→末信号)、子帧数、error。

### 3.2 ToolCall

**缩略态(一行)**:

```
▸ python_exec  {"code":"result = 2+1\nprint(result)"}     ✓ 0.3s
```

- 工具名 + **输入摘要**(参数 JSON 单行)+ ok 图标(✓ 绿 / ✗ 红 / veto 黄)+ 时长;
- veto 时工具名旁标 `vetoed` 黄章,理由单行显示。

**展开态**:两栏卡——**输入**(完整参数 JSON,折叠/复制)与**输出**(ok 时完整 value,大输出 head+tail;失败时 error 四层:kind/message/retryable/hint);veto 卡(裁决来源、理由、被否决参数)。

**metadata**:工具名、ok/vetoed/error kind、时长(pre→post 信号)、结果字节数、是否 spill、retryable。

### 3.3 LLMReasoning

**缩略态(两行)**:

```
▸ LLM  mock/fib                       in +2 msgs · out 1 · 15 tok · 0.4s
  ↳ user: {"n": 4}
  ↳ assistant: skill__fib(n=3)
```

- **输入摘要 = 新增消息**(自上一 assistant 后的 user/tool 消息 delta,逐条一行);
- **输出摘要 = assistant 消息**(文本截断或 tool_calls 名列表);
- 右:metadata 摘要(模型 / prompt+completion tokens / 时长)。

**展开态**:**"当时的完整上下文"分区展示**——

```
┌ system(指令体,折叠) ┌ 历史消息 N 条(列表,可逐条展开) ┌ 本次新增(高亮)
└ 可用工具 schema(k 个,名称列表) ── reasoning(折叠,若有) ──→ assistant 输出卡
```

**metadata**:model、prompt/completion/cache_read/cache_write/thinking tokens、cost、TTFT(有则)、新增消息数、上下文总长(估算 tokens)。

### 3.4 基础块(Text / 系统)

- `text`:system 指令、状态注入行、纠偏消息、错误观察;
- 缩略一行(role/source 标签 + 单行截断 + 字节数);展开为 format text(mono 保留换行,>2k 折叠 head+tail,可复制);
- **metadata**:role、source、字节数、估算 tokens。

## 4. 整体展示(Run 视图)

### 4.1 块流主视图

Workbench 主视图从"三栏"重构为**以 SkillCall 树为主轴的块流**:

```
┌ Run 头(skill / StatusPill / run_id ⎘ / 开始时间 / 结果摘要) ─────┐
├──────────────────────────────────────────────────────────────┤
│ [块流]                                            [导航 ▾]    │
│ ▾ SkillCall fib n=5                              ● done       │
│   ▸ LLM  in+1 out1                                 15 tok     │
│   ▸ SkillCall fib n=4                            ● done       │
│   ▸ LLM  in+2 out1                                 18 tok     │
│   ▸ ToolCall python_exec                         ✓ 0.3s       │
│   ▸ LLM  in+2 out1                                 12 tok     │
│   → {seq: [0,1,1,2,3]}                                        │
├──────────────────────────────────────────────────────────────┤
│ ● live │ frames 4 │ steps 10 │ 12.4s │ in 128 / out 46 tok   │
│ cache r/w 96/12 │ thinking 0 │ $0.002 │ ▓▓░░ steps │ ▓░░ cost │
└──────────────────────────────────────────────────────────────┘
```

- **导航**:右上可折叠的帧树缩略导航(现 frame-tree 组件降级为导航用途),点击 = 滚动到对应 SkillCall 块并选中;
- **实时生长**(进行中 run):SSE 增量追加子块;running 的 SkillCall 块保持展开态并显示进度行(step x / 当前动作);新块滑入动画(reduced-motion 时关闭);自动跟随(同 §4.2 既有规则);
- **深链接**:`#/runs/<id>?block=<block_id>` 直接定位到块(展开祖先 + 高亮);
- 原三栏(时间线/检视器)不再单独成栏:其能力分别被 LLMReasoning 块(full context)与块内输出卡吸收;**时间线降级为导航抽屉**(排查时仍可拉出)。

### 4.2 底部 metrics 栏(实时)

固定底栏(全宽,36px),字段与数据源:

| 字段 | 源(live / 完成) |
|---|---|
| 状态点(running 脉冲) | detail.status |
| frames 数 | SSE frame.push 计数 / detail.frames |
| steps | SSE post:step / usage |
| 已用时长 | started_at 秒级走动 / 首末信号差 |
| tokens in / out | SSE post:llm.response 累加 / usage.prompt/completion |
| cache r/w | 同上(cache_read/cache_write) |
| thinking | 同上 |
| cost | 同上 + 占 max_cost 的 mini bar |
| steps 进度 | 占 max_steps 的 mini bar |

实现:`MetricsBar` 组件订阅 selection 所在 run 的 metrics store;live 时事件驱动更新(≤4 次/秒节流),完成后从 `/usage` 一次性填充;进度条 >80% 转 `--warn` 色(与 BudgetGuard 语义一致)。

## 5. 导入 / 导出(纯前端实现)

### 5.1 导出

Workbench 头部 "⤓ Export" 按钮:从已加载的 API 数据**在前端组装**自包含文件并触发浏览器下载:

```json
// run-export.json(schema 版本化)
{ "v": 1, "kind": "agent-os.run-export",
  "exported_at": "...",
  "meta": {...}, "detail": {...}, "signals": [...],
  "checkpoint": {...}, "usage": {...}, "rca": {...} }
```

- 文件名:`agent-os-run-<skill>-<run_id 前8>.json`;
- 无需后端新端点:meta/detail/signals/usage/rca/checkpoint 全部来自现有 GET;
- 大 run 流式拼包(信号 >10k 条时按 1k 分片取,避免一次性阻塞)。

### 5.2 导入

Runs 页与 TopBar 提供 "⤒ Import" 按钮(支持点击选择与拖拽):

1. 读文件 → JSON.parse → **校验**(`validateRunExport(doc) -> errors[]`:版本号、必备键、signals 数组形态;纯函数进单测);
2. 合法 → 作为**只读快照**渲染:同一套 Block 组件,数据源换成文件内容;页面角标 `导入快照`(区分实时 run);不进 runs 列表、不写产物目录、不支持 stop/resume;
3. 校验失败 → 错误条列出问题(版本不支持 / 缺键 / 结构不符);
4. 多个导入快照以 tab 并存,可关闭;深链接 `#/import/<n>`。

## 6. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **D6 Block 模型** | `js/blocks/` 四种块组件 + 数据装配纯函数(frame→SkillCallBlock、messages→ToolCall/LLM/Text 块、meta 计算) | 四种块缩略/展开规格符合 §3;fib(5) 装配单测全过 |
| **D7 块流主视图 + 底栏** | 块流渲染 + 帧树导航降级 + live 生长 + MetricsBar | live fib 实时追加;底栏字段与 usage 对账一致 |
| **D8 导入/导出** | 导出组装 + 下载;导入校验 + 只读快照模式 | 导出→导入→渲染与原 run 一致;坏文件错误条正确 |

## 7. 非目标

导入快照的编辑/再运行(重放请用 CLI `replay`)、导出加密/签名、跨实例分享协议、Block 的自由拖拽重排。

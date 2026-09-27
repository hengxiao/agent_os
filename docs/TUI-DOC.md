# 终端 TUI Doc Editor 设计(第四宿主·终端信件模型)

> 版本:v0.1(设计稿)
> 上位文档:WIDGET-ARCH.md、APP-MODEL.md(协议内核与寻址/cascade/action 管道);
> 姊妹篇:GAME-UI-DOC.md(Godot 信件模型)、GAME-UI-FLOWS.md(使用流)、
> DOC-BUBBLE.md(批注协议实然)、DEBUG-UI-THEMES.md(主题契约)。
> 本文定义第四个薄宿主:基于 terminal 的 TUI 文档编辑器。
> **协议内核与后端零改动**;本文只定义呈现层、交互层与使用原则。

---

## 0. 定位

```
旧 Web SPA ──┐
Web Platform ─┼─→ agent-os-web :8391(REST + SSE;state 服务端权威)
Godot ───────┤
TUI(本文)────┘   ← 同一种客户端,同一组端点,零新通道
```

- 代码位置:`agent_os/src/agent_os/host/tui/`(与 cli/web/web_platform 平级的薄宿主);
- **Python stdlib-only**(`termios`/`tty`/`select`/`unicodedata`/`signal`),不引
  Textual/urwid/rich——本项目的内核本身就是产品,web 侧"无框架无构建"的精神
  在 TUI 侧就是零依赖;渲染层唯一新写的是 cell buffer + ANSI diff 输出;
- 默认 client 模式打既有服务;离线模式直读产物/文档目录做**只读**回放
  ("产物即真相"先例,web 侧 run 导入快照同语义),离线模式无任何写面;
- **内嵌模式(第三形态,裁决记录 2026-08-30)**:前后端分离的载荷不是网络,
  是三条不变量——widget 层零裁决、state 权威在渲染层之外、变更必经管道;
  HTTP 只是传输层选项之一。内嵌模式进程内 import 同一份裁决代码
  (web_platform/apps.py 与 skills/doc_store.py 零 web 框架耦合,已核实),
  RunManager 纯 Python 可复用;两条纪律:**单一写者**(文件存储无跨进程锁,
  不与 web 服务同时写同一库;v1 互斥使用)、**LLM 长任务进 worker 线程**
  (per-run 线程 + 信号 hub 先例)。完全内嵌时 agent read/focus 端点够不到
  TUI(T5 再议进程内注册表暴露)。写面抽象为 DocSink 口,client/内嵌两实现
  共用同一裁决代码,不开第二条权限通道。

## 1. 使用原则(锁死)

**键位是借的,原则是守的。** Vim/Emacs 只贡献"手怎么动",不贡献"能干什么"。

1. **信纸不可直改。** 文档正文是只读投影,没有本地缓冲、没有 INSERT 进正文的
   路径。Godot 面板版的直编框(回落面遗留形态)在 TUI **不设等价物**。
2. 文档只以**版本**演进;每一次变更都是一次可审计的 action。"agent 永不直改"
   的升权哲学对人同样成立。
3. 可变更加口只有五个,全部出海、全部带 cascade、全部过服务端裁决:
   **批注**(annotations 端点)、**回执联**(chat)、**誊回**(apply)、
   **生成**(generate 批处理)、**版本动作**(snapshot/rewind,走 action 管道)。
4. 核心循环(与 GAME-UI-FLOWS §0.1 一致):
   `读信 → 写批注 / 撕回执 → 生成下一版 → 盖章封存 →(必要时)回溯`。
5. 误触的反馈是引导不是报错:信纸上按可打印键,状态行给一句人话(copy 键,
   随主题/键位包变),如 vim 包下"信不可涂改——`a` 批注,`Enter` 回执"。
6. 命令条是 **action 界面**(`:generate`、`:rewind v003`、`:set keymap emacs`),
   `:w` 等价于"保存/盖章"动作,不是"把缓冲写盘"。

## 2. 进程模型与模块树

```
host/tui/
├── __main__.py            # agent-os-tui 入口:raw 模式、信号恢复、主循环
├── kernel/                # 协议内核(godot/kernel 的 Python 对译,渲染无关)
│   ├── widget.py          #   Widget 实例基类:state/emit_event/render 三铁律
│   ├── widget_def.py      #   WidgetDef:kind/v/actions/events/surfaces/intents
│   ├── registry.py        #   注册即校验,不合规拒注册
│   ├── tree.py            #   WidgetTree:/root 寻址、read/focus 动词
│   ├── compound.py        #   CompoundWidget:slots/动态白名单/事件闸门/badge 记账
│   ├── cascade.py         #   ContextCascade:单向向上信封
│   ├── pipeline.py        #   ActionPipeline:只发事件不发 state,镜像 instance
│   ├── client.py          #   REST 客户端:统一信封 {ok,status,json|error},超时两档
│   └── sse.py             #   SSE 客户端:分块读流 + event:/data: 帧解析,断线只上报
├── tui/                   # 呈现层(终端特有,本文新写)
│   ├── cells.py           #   cell buffer:2D 格子,CJK 双宽收口,ANSI diff 输出
│   ├── keys.py            #   键解码:Ctrl/Meta/转义序列;Esc 超时裁决;关 IXON
│   ├── keymap.py          #   KeymapPack:intent 层 + 契约校验(§5)
│   ├── theme.py           #   ThemePack:ANSI 调色板 + copy + 动效档位(§6)
│   ├── motion.py          #   MotionPlayer:具名+档位,定时器重绘实现(§6)
│   └── sty.py             #   Sty:语义 token 取色/取文案/基础件工厂
└── apps/
    └── doc_editor/        # 信箱 index / 信纸 letter / 批注坞 dock / 版本条 reel
                           # / 版本树 tree / 回执联 slip(compound 组装)
```

主循环:键解码 → keymap 解析为 intent → widget/app 响应 intent → render() 全量
重渲 cell buffer → diff 输出。SSE 线程经队列注入主循环(同 RunManager 的
`call_soon_threadsafe` 纪律);断线回落 2s 轮询(web 前端既有语义)。

## 3. 内核:协议内核对译 + cell buffer

godot/kernel 14 个文件全部 RefCounted、唯一引擎耦合是 `root: Control`,对译规则:

- 三铁律不变:有状态(state 可序列化)、发事件(永不出海)、render 纯渲染;
  **注册即校验、不合规拒注册**、未注册 kind 显式拒绝渲染;
- `render()` 在 TUI 更纯:`state → 2D cell buffer` 是真纯函数;`root: Control`
  换成"cell buffer 上的一段区域";全量重渲语义不变(先清后建、幂等可重入);
- 寻址不变:path = ownership 链(`/root/doc-<name>/para-<n>`、`…/note-<anchor>`),
  查树不查视图;focus = 滚动 + 反色脉冲;
- compound 三通道管控不变:事件闸门 / child_context 改写 / surface 管控;
  badge = 父对子事件的记账;
- **cell buffer 是终端唯一真实新坑,收口在 `cells.py` 一个文件**:
  CJK 双宽(`unicodedata.east_asian_width`)、宽字符截断、ANSI 序列不跨入格子;
- 游标/选中项收进可序列化 state(Godot 踩坑史:全量重渲丢焦点 →
  `mutate_state` 静默改 + 局部重建先例,照抄)。

surfaces 映射:card = 列表行摘要(信箱里的一封信)、tab = 全屏。

## 4. 信件模型的终端翻译

空间隐喻(信纸居中、便签牵线、页栈重叠)不可移植;**隐喻的信息结构全部移植**:

| Godot 信件模型 | TUI 等价物 |
|---|---|
| 信箱(未读 = 信口翘起) | 索引屏:每封信一行,未读 = `▸` + `[注×N]` 计数(badge 语义不变) |
| 一页信纸 | 全屏居中列(等宽字体本身就是纸);**光标在字符上**(反色格),状态栏常驻 `行:列` |
| 页边便签(眉批) | **字符级 span**:锚定串下划线 + warn 色,末字符后跟 `[注×N]`(双编码);**框选字符串 → 基于选区创建批注**(锚点 `doc.md#L..:C..-L..:C..`,零后端改动) |
| 批注坞 | 底部分屏:引文 + 批注内容 + 状态章(pending/applied/ignored/outdated)+ 存/删 |
| 信纸叠 / 版本页栈 | 版本条:v008·v007·v006 一行排开,`h/l` scrub,选中展开详情 |
| 拨轴 diff 提示 | 版本条右侧随行显示 LLM 人话摘要(diff-summary 缓存端点直接复用);摘要故障回落红绿行 |
| 盖章 | 状态行印章回声:`[ 已盖章 v008 ]` 反色定格一闪(stamp-press) |
| 回执联 | 底部一行输入条,"写给编者" |
| 评审便签雨 | 批注逐行打入(text-reveal),自动锚到各段行尾 |
| 版本树覆盖层 | ASCII 分支图覆盖层,点节点 = 关层 + 版本条定位 |
| 镜头推镜 | 滚动 + 反色焦点脉冲(focus-pulse) |

三屏轮廓:

```
┌─ 信箱 ──────────────────────────────────────────────┐
│ ▸ 季度报告        [注×2]        v008   08-29        │
│   产品白皮书                      v012   08-27      │
│   发布检查单      已盖章          v003   08-25      │
└──────────────────────────────────────────────────────┘

┌─ 季度报告(v008 · 工作稿) ─────── NORMAL ─────────────────┐
│   本季度交付了三项核心能力。首先,内核的信号系统…[注×2]    │
│ ▸ 其次是渲染无关的 widget 协议。这一层让同一颗内核…        │
│   最后,主题契约保证了组件零分支。                          │
├─ 批注 ───────────────────────────────────────────────────┤
│ 引文:"其次是渲染无关的…"                                  │
│ 批注:把这段改写成更有信息量的两句话_                       │
│ 状态:[待处理]                                 C-Enter 存 │
├─ v008·v007·v006·v005 ── diff:标题改为…;新增两段 ─────────┤
│ ● 已盖章 v008 · 采纳 2/忽略 1               :命令 ?帮助  │
└──────────────────────────────────────────────────────────┘
```

## 5. 键位体系:KeymapPack(vim / emacs 双群体)

键位层与主题包同构——**组件绑定意图(intent),不绑定物理键**;"组件零按键
分支"与"组件零主题分支"是同一条红线。

### 5.1 intent 层与契约

```
物理键 ──decode──→ KeyEvent ──活动 KeymapPack──→ Intent ──→ widget/app 只响应 Intent
```

- `KeymapPack = {id, modal: bool, bindings: {context → {key → intent}}, copy}`;
- context 分级:global / index / letter / dock / input / command;子级缺省向
  global 回落;
- **契约清单 = 必备 intent 集合**(move_up/move_down/open/annotate/save/…),
  缺绑定 = 拒注册(与主题"缺项拒注册"同机制);WidgetDef 声明自己响应的
  intent 集合,注册时校验;
- hint 栏与 `?` 帮助面板**从活动 keymap 生成**,永不硬编码(WEB-A11Y 教训:
  没契约守着的维度全是洞);
- 键引擎持前缀/和弦态 + 超时(`gg`、`C-x C-s`);Esc 与转义序列歧义按
  vim `ttimeoutlen` 同款短超时裁决;进 raw 模式关 `IXON`,放出 `C-s`/`C-q`。

### 5.2 vim 包(modal)

信纸/信箱是"只读缓冲"心智(less/mutt/magit 传统),裸字母给 app 意图;
modal 只存在于**撰写控件**(dock/input/command),正文永远不进 INSERT。

| context | 意图 | 键位 |
|---|---|---|
| global | 命令条 / 帮助 / 返回 | `:` / `?` / `q` |
| letter | 字符/视觉行 / 词 / 段 / 首尾 / 翻页 | `h` `l` `j` `k` / `w` `b` / `{` `}` / `gg` `G` / `C-d` `C-u`;`0` `$` 行首尾 |
| letter | **框选**(字符级,选区随移动扩) | `v` 开关;选区中 `Esc` 清 |
| letter | 搜索 / 下一个 | `/` + `n` `N` |
| letter | 批注(基于选区)/ 版本条 / 评审 / 生成 | `a` / `<leader>v` / `r` / `<leader>g` |
| letter | 保存(盖章) | `:w`(命令条别名) |
| dock/input | 进入即 INSERT;`Esc`→NORMAL(控件级 h/l/x),`i` 回 INSERT | |
| dock | 存 / 弃 | INSERT 下 `C-Enter` 存;NORMAL 下 `q` 弃 |

状态行左格恒显 `NORMAL/INSERT`(vim 包的 copy 义务)。

### 5.3 emacs 包(modeless)

永远可输入;移动全靠 Ctrl/Meta;`C-c` 前缀给 app 意图(Emacs 惯例)。

| context | 意图 | 键位 |
|---|---|---|
| global | 命令条 / 帮助 / 取消 | `M-x` / `C-h` / `C-g`(万能取消) |
| letter | 字符/行 / 词 / 段 / 首尾 / 翻页 | `C-f` `C-b` `C-n` `C-p` / `M-f` `M-b` / `M-{` `M-}` / `M-<` `M->` / `C-v` `M-v`;`C-a` `C-e` 行首尾 |
| letter | **框选**(mark + 移动 = region) | `C-Space` 设/消 mark;`C-g` 清 |
| letter | 增量搜索(前进/回退) | `C-s` / `C-r` |
| letter | 批注(基于 region)/ 版本条 / 评审 / 生成 | `C-c a` / `C-c v` / `C-c r` / `C-c g` |
| letter | 保存(盖章) | `C-x C-s` |
| dock/input | 自插入;`C-a` `C-e` `C-f` `C-b` 行内编辑 | |
| dock | 存 / 弃 | `C-c C-c` 存(org 惯例)/ `C-g` 弃 |

### 5.4 裁决与默认

- `:` 与 `M-x` 进**同一条命令条**——命令条是两派之外的兜底入口;
- vim 包裸 `g` 不给 generate(`gg` 冲突)→ `<leader>g`;规则:**裸键只给
  阅读期高频动作,其余进命令条**;
- 方向键、Enter、退格在两包全局有效;`q` 在 emacs 包只读 context 同样退出
  (magit/help buffer 传统);
- 默认包探测:`$EDITOR`/`$VISUAL` 含 emacs → emacs 包,否则 vim 包;
  选择持久化到用户配置(语义同 godot `user://` 偏好);运行期
  `:set keymap vim|emacs` 切换,切换即全量重渲 hint 栏(同主题切换语义)。

## 6. 动效与主题

- **MotionPlayer 具名+档位抽象完整保留**(组件调名字,实现归主题);终端实现
  = 定时器重绘:focus-pulse = 反色闪烁 N 次;change-flash = 底色渐衰;
  text-reveal = 逐行打出;stamp-press = 印章行反色定格 ~300ms;
- **动画只随成功播放**(先管道,后盖章)不变;档位归主题,SSH 高延迟下 FULL
  诚实降级 SUBTLE;reduced-motion 全局强制 instant;
- ThemePack = ANSI 256 调色板 + copy 文案表 + 动效档位表;契约清单
  (色/copy/动效三族)不过不注册;web 侧 `css/themes/terminal.css` 是现成
  配色参考;sizes token 塌缩为格子间距;
- **双编码纪律在终端更重要**(单色/tmux):状态永远 色+符号(✓/✗/▶/…);
- 按钮"四态齐备"改述为:焦点态 + 禁用态 + 激活回声。

## 7. 与后端的接点(零新通道)

- 读面:`GET /platform/api/docs*`(list/read)、annotations 读、
  versions/tree、diff-summary(缓存端点,LLM 故障回落红绿行);
- 写面(§1 五口):annotations upsert/delete、chat、generate、review、
  apps/spawn + `POST /platform/api/apps/{id}/actions/{action}`(snapshot/rewind 等);
- live 面:`GET /platform/api/stream`(decision.new/run.finished);断线回落
  2s 轮询;坏帧丢弃;
- 客户端纪律:统一信封解包、超时两档(默认 30s / LLM 面 180s)、
  widget 层零网络面(唯一出海在 app 层经 pipeline/client)。

## 8. agent 面

- 同一棵 widget 树:path 与 Godot/web 同构(`/root/doc-<name>/para-<n>`);
  服务端 `widgets/register|read|focus` 端点已有,TUI 注册后 agent 可 read
  (人话摘要)可 focus(滚动 + 反色脉冲);
- **act 收口纪律与 Godot v1 一致**:本期不对 agent 开放;agent 改文档走与人
  相同的五口(§1),不绕过授权;
- read 摘要遵守禁忌词纪律(技术原文进展开态,不进摘要)。

## 9. 测试

- **cell buffer 断言**:渲染结果 = 纯字符串比对,比 GUI 截图走查容易得多;
  无头模式(输出重定向到伪终端宽度)跑全部渲染冒烟;
- **契约测试**:WidgetDef / ThemePack / KeymapPack 注册校验;双编码静态扫描
  (状态出现处必有符号伴随);
- **live 冒烟**:打真服务的端到端(含越权反面例:伪造 args_from 必须被服务端
  拒掉),对齐 godot `live.gd` 先例;
- 离线回放:读既有文档/版本目录渲染,与 live 渲染比对一致。

## 10. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **T1 内核+渲染** | kernel 对译 + cells/keys + keymap 引擎(两包最小表)+ 信箱/信纸只读阅览 | 两包键位各自可读可翻;focus-pulse;cell buffer 快照冒烟全绿 |
| **T2 批注** | 批注坞 + annotations 端点 + action 管道首个写面实证 | 写/删批注落库;state 服务端权威(客户端无本地改写路径);引导文案正确 |
| **T3 版本** | 版本条 scrub + diff 摘要 + 盖印回溯两段确认 + 版本树覆盖层 | rewind 走管道;stamp-press 只在成功后播;diff 缓存命中 |
| **T4 回执与生成** | 回执联 chat + generate 批处理 + 评审 + SSE live | generate 后新版落条、批注 applied/ignored、状态行一句人话;断线回落轮询 |
| **T5 主题与 agent 面** | 主题包×2(classic/terminal)+ 具名动效 + widgets register/read/focus | 换肤全量重渲零组件分支;agent read/focus 实操;契约测试全绿 |

## 11. 不做(v1)

- **不做文档直编**(§1 原则,显式列入非目标防回潮);
- 不做鼠标交互、不做分屏多窗;
- 不做 run workbench / Block 视图(另一个 app;WEB-UI-BLOCKS.md 是其终端蓝本,
  日后接进同一内核);
- 不做多人协同、不做音效(沿用主题系统政策);
- DOM 版 `/platform/` 与 Godot 版永久保留,互为回落面。

---

## 实现注(T1,2026-08-30;内核对译 + 呈现层 + 只读阅览)

实现:`agent_os/src/agent_os/host/tui/`(kernel 9 + tui 6 + apps/doc_editor 3 +
`__main__`);入口 `agent-os-tui`(pyproject scripts);测试 `tests/tui/` 68 断言
(契约 18 / cells 11 / keys 16 / kernel 9 / 冒烟 9 / 数据源 5),全量回归
1127 passed 无影响;ruff 干净;pty 实机冒烟(开信/翻页/退回/退出,备用屏幕
恢复)通过。

- **对译注意点**(有意偏离均已在代码注释标注):godot signal → 回调列表
  (app 接线走 compound `child_event` 通道,避免祖先链逐层重放);
  `root: Control` → `render_into(buf, Region)`;`def` 关键字 → `def_`;
  `WidgetTree.focus` → 宿主 `focus_target` 回调 + focus-pulse;
  ThemePack colors = ANSI 256 int(hex→256 收口在 theme.py);
  注册表 godot static → classmethod 单例 + `reset()`(测试隔离),
  持久化经 `save_cb` 回调交给宿主(`~/.config/agent-os/tui.toml`);
  MotionPlayer 实例化,pulse_region 跟布局走不冻结在 play 时刻。
- **WidgetDef 增 `get_intents()` + Registry `required_intents`**(§5.1 TUI
  特有面,godot 无此;不给清单则不校验,保持原型语义)。
- 内置主题 classic/terminal(terminal 包 token 逐字移植
  `host/web/static/css/themes/terminal.css`);动效 T1 仅 focus-pulse 真实现,
  其余 11 具名注册名字留 instant。
- **留口**:annotate/save/generate/review/versions intent 已入契约与键位表,
  按下 → 状态行"属后续里程碑"提示(copy 键 `doc.intent.later`);翻页按段
  不按行;ANSI 全量重渲(diff 输出是后续优化口);帮助面板超高截断无滚动;
  信纸路径段固定 `/root/doc-editor/letter`(逐文档分段待 T5 agent 面);
  批注计数锚定 = 子串匹配(精确 reanchor 属 T2)。
- demo:`agent-os-tui --demo`(内置两篇示例,无需服务);
  `--offline --docs-root <artifacts>/docs` 离线只读回放(DocStore 布局);
  位置参数 `FILE...` 直查本地文件(FileDocSource,只读,标题取首个 `# ` 行)。
- **实锤(T1.1)**:`to_ansi()` 裸 `\n` 拼行 + `tty.setraw` 关 OPOST(ONLCR
  失效)→ 换行不回车、满宽行折行挂起逐行漂移滚屏,实机只见框架不见内容。
  修为行间显式 `\r\n`(`\r` 兼清折行挂起态);回归测试钉死"输出流无裸 LF"
  (test_cells.test_to_ansi_uses_crlf_between_rows)。教训:无头冒烟走
  `plain_text()` 绕过 `to_ansi()` 真实终端语义,pty 字节级校验必须进冒烟面。

## 实现注(T2,2026-08-30;字符级光标/框选 + 批注坞首个写面)

实现:LetterWidget 重做(字符光标/框选)+ InputWidget(撰写控件)+ 批注坞
(开/存/删)+ 模式感知 keymap;测试 113 全绿(新增 anchor round-trip、span
三策略、字符移动/选区渲染、坞假 client),全量回归 1169 passed,ruff 干净;
pty 实机冒烟通过(框选显 `选N字`/`行:列`)。

- **锚点字符精度零后端改动**:`doc.md#L..:C..-L..:C..` 契约原生支持;
  `skills/reanchor.py` 的 parse/make/quote_at 直接复用。**实锤一处既有三家
  不一致**:reanchor.py docstring 说列绝对闭区间、doc-editor.js 实际末端
  半开、quote_at 对同行锚点把 ec 读作**长度**;TUI 以 quote_at 实现为权威
  (round-trip 硬约束下唯一自洽解):同行锚点 `ec = 选区长度`,跨行 = 末行
  绝对闭区间列。统一三家语义要动服务端共享面,单列后续任务,本轮未碰后端。
- **vim `v` 改框选**(视觉模式肌肉记忆优先),版本条挪 `<leader>v`;§5.2
  表已改。emacs `C-Space` mark + `C-g` 清,region 语义与 Emacs 一致。
- **模式感知 keymap**:`resolve(ev, context, mode)`;modal 包在撰写控件里
  INSERT(可打印 = self-insert)/NORMAL 两态,modeless 包永远可插入;
  modal 专属 intent(mode_normal/mode_insert)不进跨包契约面。
- **写面纪律**:坞的保存/删除只在 online 源出海(annotations 专属端点,
  同锚点 upsert = 重新编辑);FileDocSource/DemoDocSource 显式只读
  (annotate 给状态行人话,不开假写面)。成功回声 = 状态栏一句人话
  (先管道后盖章)。
- 留口:C-Enter 依赖终端 modifyOtherKeys/CSI-u(不支持的终端坞内"存"需
  命令条兜底,待补);帮助面板超高截断;outdated span 只有信箱计数、信纸
  无视觉面(重锚编辑属后续);翻页步长按估算屏高。
- 修正(验收轮):状态栏改显 title(合成名 file.NNN 只作寻址安全的内部名);
  FileDocSource 启动即拒不存在的文件(fail-fast)。

## 实现注(T2.1,2026-08-30;阅读面去铺底 + 光标吞字修复)

1. **用户裁决:段落不铺背景**。godot 信件的纸底(paper-0)直移到终端会淹没
   反色光标;`sty.paper()` 改为无底色 fg 样式,`ann_span` 同(下划线+warn,
   不铺底)。纸/印 token 只留给标题栏、印章回声等小面积件——**终端里"纸"
   的隐喻靠字色和边框表达,不靠铺底**。契约测试中新增钉死:正文区字符不得
   出现 paper-0 底(test_letter.test_paragraph_body_has_no_background_fill)。
2. **实锤:光标吞字**。光标在被剥修饰前缀(标题 `# `)上时,原实现在行首格
   写反色空白,把第一个真字符盖掉;修为反色落在行首字符本体
   (test_cursor_on_stripped_prefix_keeps_first_glyph)。
3. 测试教训:宽字符光标占两格,`_reverse_cells` 只数首格(cont 跳过)。

## 实现注(T3,2026-08-30;版本条/盖印回溯/版本树)

实现:`reel.py`(版本条/版本树纯布局)+ app.py(reel/tree 焦点路由、两段确认、
diff 分层)+ motion.py stamp-press 真实现(FULL 5 帧/SUBTLE 3 帧恒亮定格);
测试 132 全绿(test_versions 17 例),全量回归 1187 passed(唯一红为
shell 超时 flaky,单跑复测绿,与本改动无关),ruff 干净;pty 冒烟通过。

- **管道序列实测**:armed 阶段零出海(armed 是本地 UI 态);确认后
  spawn(kind+ref 服务端去重)→ `doc.rewind`(args 带 rolled_back_from 留痕;
  body 无 state);expected 越界校验在客户端先挡(版本 ∉ 清单 → 不调管道)。
- **两段确认**:Enter 第一击 armed(seal 色标 + 状态行"再按 Enter 确认"),
  第二击走管道;Esc/C-g/q 优先只取消 armed。stamp-press 只在成功路径记帧
  (先管道后盖章纪律)。
- **diff 分层**:online 先 diff-summary(LLM 面,故障回落行差集)→ 注意该
  端点只吃版本对,摘要对象实为"选中版 vs base";客户端行差集才是真"vs 当前
  工作稿"(含未封存改动)。offline 读落盘 diffsum 缓存(版本不可变天然安全)
  → 行差集。rewind:offline/file/demo 显式只读拒绝。
- **实锤**:行差集里空行增删会在版本条摘要渲成裸 `+`/`-` 符号(噪声)——
  line_diff 不记空行项。
- 留口:diff-summary 同步阻塞(scrub 连按排队,后续换异步+竞态丢弃);
  版本树 h/l 未做同层列跳;覆盖层超高截断无滚动;rewind 成功后 reel 保持
  打开回落"工作稿"锚("盖章后自动收条"是一行可改的产品决策)。

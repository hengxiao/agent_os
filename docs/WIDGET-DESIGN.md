# Widget 视觉与交互终稿（验收基准）

> 本文档 + `docs/widgets/design/*.svg` 效果图 = widget 层的**设计验收标准**。
> 后续实现按此验收:沙盒同形态渲染与效果图对比,无设计偏差(字体渲染差异除外);
> 每控件「验收清单」逐条可判。画效果图阶段的纪律:**不考虑实现可行性**,先定对的体验。
>
> 参照系(主流商业软件):Linear(密度与层级)、Notion(温和圆角与 hover 反馈)、
> VS Code(编辑器/树/快捷选择)、GitHub(diff/markdown 渲染)、Stripe(图表)、
> macOS 日历(日期弹层)、Slack/Notion comments(气泡)、Postman(kv)、Airtable(表格)。

## 1. 设计语言(全局契约)

### 1.1 度量

- **网格**:4px 基网;控件内边距 8/12/16,区间距 8/12/16/24;卡片 padding 12–14。
- **圆角**:输入/按钮 6;卡片/浮层 8;聊天气泡 12;徽标/chip 999(胶囊)。
- **字号阶梯**:20 大数值读数 / 15 标题 / 13 正文 / 12 辅助 / 11 元信息;mono 一律 12。
- **字重**:600 标题与关键数值;500 标签与列表主名;400 正文;元信息 400 弱色。

### 1.2 颜色与材质(全部走 token,六主题映射)

- 边框 hairline `--line`(弱对比);表面阶梯 `--bg-1`(卡)/ `--bg-2`(hover/轨道)/ `--bg-3`(凹槽);
- 强调 `--live`:焦点环、选中左条、激活态、链接、主按钮底;
- 语义 `--ok` / `--warn` / `--danger`:状态徽标、错误、校验;信号色 `--sig-*` 给日志/多序列图表;
- **阴影三级**(实现时进 tokens.css):`--shadow-1` 卡静态(0 1px 2px + 0 4px 12px,黑 6%);
  `--shadow-2` hover/浮层(0 4px 16px,黑 10%);`--shadow-3` 弹层(0 8px 28px,黑 14%)。
  映射既有 token:1≈`--shadow-sm`、2≈`--shadow-pop`、3≈`--shadow-md`(实现时以既有名为准,效果图按三级递进画);
- hover 反馈 = 底色换 `--bg-2`,**不改边框不改字色**;选中 = `--live` 2px 左条 + `--live` 8% 浅底。

### 1.3 动效

- 全场 120–160ms ease-out;hover 色变 120ms;浮层入场 140ms(上移 2px + 淡入);
- chevron 旋转 120ms;skeleton shimmer 1200ms 循环;**任何动效不超 200ms**;
- `prefers-reduced-motion`:全部动效降为瞬时。

### 1.4 焦点与可达性

- 焦点环:2px `--live`、offset 1px,一切键盘可达;Tab 序 = 视觉序;
- 对比度:正文 ≥ 4.5:1,元信息 ≥ 3:1(以 classic 主题为基准测定,其余主题不低于);
- 图标钮必有 aria-label;状态不只靠颜色(符号/文字双编码)。

### 1.5 三态通例(每控件验收清单都含)

- **empty**:图标位 + 一句人话引导 + 一个主操作(不是灰字一句"暂无数据");
- **loading**:skeleton 骨牌(shimmer,`--bg-2`/`--bg-3` 灰阶),不闪屏不布局跳变;
- **error**:图标 + 人话一句 + 技术细节一行 mono 弱色 + 重试钮。

## 2. 效果图阅读方式

- 每控件一张 SVG(`docs/widgets/design/<kind>.svg`,画布 1080×700):
  上部 = **tab 完整形态**(真实数据、含一个 hover 态与一个焦点环实例);
  左下 = **card 摘要形态**(340px 宽,真实内嵌宽度);右下 = **状态行**(empty/error/disabled 等变体小样)。
- 图是终稿:间距、圆角、层级、阴影、字号阶梯都按 §1 画。
- 实现验收时,沙盒(`widget.html?kind=<kind>&surface=tab|card`)渲染与图对照。

---

## 3. 逐控件终稿

### 3.1 W-text 文本编辑器 → [效果图](widgets/design/w-text.svg)

参照:VS Code 编辑器 + Notion 文档页。

**tab 效果要求**:
- 行号槽:右对齐 11px mono 弱色,与正文行距严格一致;当前行整行 `--bg-2` 高亮,行号转正文色;
- 光标 2px `--live`;选区 `--live` 18% 底;focus 时整个编辑区外圈焦点环;
- dirty:编辑区左缘 2px `--live` 竖条(替代粗色块),微标旁加未保存圆点;
- 右下微标:`12 行 · 340 字`,11px 弱色,hover 显详细(行列位置);
- empty:斜体禁用,占位文案常规字弱色居中偏上(如「开始输入,或让助手帮你起草」)。

**card 效果要求**:标题行(文档图标 + 名称 500 + dirty 圆点)+ 前三行预览(13px,末行渐隐)
+ 底部 meta 行(行数 · 字数 · 更新相对时间);hover 整卡抬升(--shadow-2)+ 右上「打开 →」渐显。

**验收清单**:
- [x] 行号与正文基线对齐,滚动同步;
- [x] 输入时微标/脏条即时刷新且**不重渲全文**(选区不动);
- [x] readonly:锁图标 + 无光标 + 无脏条;
- [x] card 点击任意处 emit open;hover 有抬升反馈。

> W6.1 实现偏差(2026-08-04,沙盒对照确认):
> 1. 光标宽度浏览器不可控,落 `caret-color: var(--live)`(1px 由引擎定),2px 未落;
> 2. 焦点环 offset 用策略常量 `--focus-ring-offset`(2px)而非 §1.4 的 1px——
>    宽度/偏移是 tokens.css 策略常量,主题与组件不得覆盖(WEB-A11Y §5.2);
> 3. empty 态落占位文案(常规字弱色,`w.text.ph`)——图标位 + 主操作不落:
>    「让助手起草」是 app 层动作,widget 铁律不出海,卡/编辑器内无可执行主操作;
> 4. mono 变体不软换行(wrap=off)——行号槽/着色层逐行对齐的前提(VS Code 同律),
>    plain 变体保持软换行(无槽不对齐问题);
> 5. 预览末行「渐隐」落 0.35 透明度(效果图 fill-opacity 同值),非渐变 mask。

### 3.2 W-json JSON 编辑器 → [效果图](widgets/design/w-json.svg)

参照:VS Code JSON + Postman body 编辑器。

**tab 效果要求**:
- 继承 W-text 基座;**语法着色**:key `--live`、string `--ok`、number/bool `--warn`、标点弱色(走 token,主题可调);
- 括号匹配:光标落在括号旁时,配对括号 `--live` 浅底;
- 错误:出错行号槽红点 + 行内红波浪下划 + 底部错误条(红图标 + 「第 4 行:缺右括号」+ 点击跳转并闪该行);
- 合法:右上 ✓ 绿徽标;format 钮 ghost 样式,hover 显形(平时 40% 透明),点击后光标位置保持。

**card 效果要求**:状态行(✓「JSON 合法 · 12 键」绿 / ✕「第 4 行:缺右括号」红)+ 首行 mono 预览
+ meta(大小)。错误态整卡左边条 `--danger`。

**验收清单**:
- [x] 着色仅渲染层,不进 state;失焦才校验(输入中不闪红);
- [x] 错误条点击 → 跳转 + 行闪烁 1.5s;
- [x] format 幂等,非法时禁用并说明。

> W6.1 实现偏差(2026-08-04,沙盒对照确认):
> 1. **行为变更(设计批准)**:即时校验 → 失焦才校验——widgets.test.mjs 的
>    「即时校验」块随之改写为「输入不闪红 / 失焦才出」,其余行为面零改动;
> 2. 红波浪落整行 wavy underline(效果图只画错误段;错误定位到行不到列,
>    列级波浪待 locateJsonError 出列号);
> 3. 着色实现 = pre 叠层 + 透明文字 textarea(caret/选区/placeholder 保持),
>    滚动由逻辑面同步;token 四色(key=--live/string=--ok/number·bool=--warn/
>    标点弱)全走契约;
> 4. ✓ 徽标键数 = 递归 object 键统计(数组元素不计);非法/空 → 徽标隐;
> 5. 括号匹配对向扫描经字符串掩码跳过串内括号,不做转义反向推断(编辑器级近似)。

### 3.3 W-table 表格编辑器 → [效果图](widgets/design/w-table.svg)

参照:Airtable / Notion database。

**tab 效果要求**:
- 列头 sticky:名称 500 + 类型图标(textAa/number#/enum≡/date📅 语义图标,12px)+ 必填红 *;hover 列头显排序/菜单 ⋯;
- 行 hover `--bg-2`;选中行 `--live` 左条 + 浅底;拖柄 ⠿ 平时 30% 透明,行 hover 全显,拖动中行 `--shadow-2` 浮起、目标位置 2px `--live` 指示线;
- 单元格点击进入内联编辑(input 无边框,焦点环);行尾 ✕ 仅行 hover 显;
- 底部「+ 添加行」整宽虚线框,hover 变实线 + `--live` 字色;
- 空态:表头 + 一行示意骨架 + 「添加第一行」主操作。

**card 效果要求**:标题行(表名 + 「N 行」徽标)+ 迷你列头(最多 3 列,溢出「+N」)+ 前 2 行只读
+ 底部「查看全部 →」。

**验收清单**:
- [x] DnD 全程有视觉反馈(浮起 + 指示线);键盘 Alt+↑/↓ 等价;
- [x] 类型徽标图标与文字双编码;
- [ ] 行增删有 120ms 高度动画(reduced-motion 除外)。

> W6.2 实现偏差(2026-08-04,沙盒对照确认):
> 1. **行增删高度动画未落**(不打勾):自渲染全量重渲架构下没有 per-row
>    FLIP 测量,入场动画会对所有行误触发;待有真实性能/体感诉求再引入;
> 2. 列头排序 ↑/⋯ 槽是纯视觉(aria-hidden)——排序/菜单行为设计未定义,
>    不臆造;
> 3. enum 单元格 chip 是中性 tone:效果图的 进行中=live/已完成=ok 是
>    值→语义映射,通用控件不臆造(消费方可经 data 属性/自定义列型扩展);
> 4. 单元格编辑态切换:focusout 退出用 relatedTarget 判表内转移(连续点击
>    相邻单元格一次到位);编辑中值仍走 set_cell 即时同步(不重渲);
> 5. card 预览行不带状态色点(同 3 的语义映射问题),网格对齐 + 首列强调。

### 3.4 W-kv 键值编辑器 → [效果图](widgets/design/w-kv.svg)

参照:Postman headers 编辑器。

**tab 效果要求**:
- 两列等宽(40%/60%),key 列 mono;行 hover `--bg-2`,行尾 ✕ hover 显;
- 重复 key:两行都 `--warn` 8% 浅底 + key 旁 ⚠ 图标,hover 出 tooltip「key 重复,后者覆盖前者」;不硬拦;
- 「+ 添加」虚线行;Tab 键 key→value→下一行 key 流转;末行 value 回车自动加行。

**card 效果要求**:「N 键值」徽标 +(有重复时)黄「重复 ×N」警示徽标 + 前 3 条 `key = value`(截断省略)。

**验收清单**:
- [x] 重复警示随输入即时更新;
- [x] 序列化顺序 = 视觉顺序;
- [x] card 警示徽标与 tab 警示同源(同 state)。

> W6.2 实现注:⚠ tooltip 用 `data-tip` + CSS ::after(attr)实现,零 JS;
> 输入框平时隐形(border/底透明),聚焦显形(焦点环承接全局 :focus-visible)。

### 3.5 W-form schema 表单 → [效果图](widgets/design/w-form.svg)

参照:Stripe 结账 / Linear 设置页。

**tab 效果要求**:
- label 上置(12px,500),必填红 *;帮助文字 11px 弱色贴控件下;
- 控件按型:text input / number(带 stepper)/ select(chevron,原生风)/ boolean(iOS 式 switch)/ enum(chip 单选组)/ date(W-date);
- 错误:控件红边 + 行内错误行(红 ⚠ + 人话),提交时首个错误自动滚动聚焦;
- 嵌套 object = 卡片化分组(标题 500 + 描述弱色 + 内边距);数组项 = 小卡 + 拖序柄 + 删除;
- 底部操作行:reset(ghost)+ 主操作(实心 --live);dirty 时 reset 可用,否则禁用。

**card 效果要求**:必填完成度进度条(--live,4px 高)+「必填 x/y」+ 缺失字段名 chips(最多 2 个,溢出 +N)。

**验收清单**:
- [x] 六类型控件视觉同族(同高 32px、同圆角、同焦点环);
- [x] 错误不弹窗,全部行内;
- [x] switch 有 120ms 滑动动画。

> W6.2 实现偏差(2026-08-04):
> 1. **行为变更(设计批准)**:boolean 由 checkbox 改 switch(role=switch,
>    点击翻转,aria-checked 局部刷新)、enum 由 select 改 chips 单选组
>    (radiogroup)——widgets.test.mjs 两处列型断言随之改写;number 加
>    stepper(钳 min/max,局部写回不重渲);
> 2. **新增行为**:dirty 跟踪(values ↔ 初始骨架深比较)驱动操作行
>    (reset dirty 才可用 + 「有未保存改动」圆点);submit 钮 = validate →
>    通过 emit submit / 不通过滚动聚焦首个错误(submit 事件早已声明,
>    此前无 UI 触发面,零回退);数组项拖序走 §15 envelope
>    (source_kind=form-arr-item,只收同数组行);
> 3. 空 schema 的「粘贴 schema」主操作不落:widget 不出海、不读剪贴板,
>    同 W6.1 W-text「让助手起草」裁决;图标 + 引导在;
> 4. 帮助文字取自 `spec.description`(schema 既有面),无 description 不显。

### 3.6 W-list 可选列表 → [效果图](widgets/design/w-list.svg)

参照:Linear ⌘K / VS Code quick pick。

**tab 效果要求**:
- 顶部过滤框:🔍 图标 + 输入 + Esc 清空;命中子串 `--live` 高亮;
- 项:主名 mono 500 + 右侧 meta(版本 · 类型,11px 弱);hover/键焦 `--bg-2`;选中 `--live` 左条 + 浅底 + 右侧 ✓;
- 键盘 ↑↓ 移动、Enter 激活、Esc 清搜索;多选时底部浮条「已选 N · 清除」;
- 空态:无结果「没有匹配『xx』的项」+ 清除搜索钮。

**card 效果要求**:当前选中项(名称 500 + meta)+「共 N 项」+ 右下「更换 →」。

**验收清单**:
- [ ] 过滤命中高亮是文字色不是底色;
- [ ] 键盘焦点环与 hover 态视觉一致(避免双态打架);
- [ ] 空态给出下一步动作。

### 3.7 W-tree 命名空间树 → [效果图](widgets/design/w-tree.svg)

参照:VS Code 资源管理器。

**tab 效果要求**:
- 缩进 12px/级;chevron ▸/▾ 120ms 旋转;目录行 500,叶子 mono;
- hover `--bg-2`;当前项 `--live` 左条 + 浅底;父链(当前项祖先)名称转正文色;
- 过滤:命中叶子 `--live` 文字高亮,祖先链自动展开,非命中 50% 透明;
- 计数徽标:目录右侧叶子数(11px 弱色胶囊);单层链折叠为一层(system.file.read 一行显示);
- 空态/过滤无结果同 W-list 通例。

**card 效果要求**:面包屑当前路径(`system / net / http_fetch`,mono)+「N 叶子」徽标。

**验收清单**:
- [ ] 折叠/展开只动高度,不闪;
- [ ] 键盘 ←→ 折叠展开、↑↓ 移动;
- [ ] 深层(>6 级)横向滚动条不截断缩进。

### 3.8 W-date 日期控件 → [效果图](widgets/design/w-date.svg)

参照:macOS 日历弹层 / Google Calendar。

**tab 效果要求**:
- 输入框:📅 图标 + 值,焦点环;支持手输 ISO,非法时红边 + 行内提示;
- 快捷 chips 行:今天/昨天/本周/上周;选中 chip = `--live` 浅底 + `--live` 字;
- 弹层(--shadow-3,140ms 入场):**range 模式双月并排**(macOS/Google 区间选择惯例),单月模式单历;月导航(‹ 「2026 年 8 月」 ›,‹› hover 圆底);星期头 11px 弱;
  日期格 28×28 圆角 6:hover `--bg-2`;今天 `--live` 描边圈;选中 `--live` 实底白字;
  区间 = 连续 `--live` 12% 带 + 端点实底;非当月日 35% 透明;
- range 双框,结束 < 开始自动纠序并闪一下提示;←→ 翻月,Esc 收层,点外收层。

**card 效果要求**:`2026-08-01 → 2026-08-04` mono 单行 + 命中快捷徽标(如「本周」)。

**验收清单**:
- [ ] 弹层定位不溢出视口(贴近边缘自动翻转);
- [ ] 纠序有可感知反馈(闪提示),不静默改值;
- [ ] 键盘可全程操作(方向键走格,Enter 选定)。

### 3.9 W-chart 图表 → [效果图](widgets/design/w-chart.svg)

参照:Stripe Dashboard / Linear Insights。

**tab 效果要求**:
- 头部:标题 500 + 图例(色点 + 名,点击显隐,隐藏态 40% 透明);右上 segmented「图表 | 表格」切换;
- 网格:水平虚线 `--line`,无垂直线;轴标签 11px 弱色,最多 5 条刻度(nice ticks);
- 折线 2px(序列色 = `--live` / `--sig-*`),端点无圆点;面积渐变 `--live` 10% → 0;
- hover:垂直参考线 + 各序列点 + tooltip 卡(--shadow-2:标题行 + 每序列 色点+名+值 mono);
- 空态通例;>500 点抽稀时右上显「已抽稀 12,000 → 500」弱色提示。

**card 效果要求**:最新值大读数(20px 600 mono)+ 涨跌徽标(▲ 绿 / ▼ 红,带百分比)
+ 120×40 迷你折线(无轴无网格,末端亮点)。

**验收清单**:
- [ ] 表格视图与图表**同数据等价**(可逐格对照);
- [ ] tooltip 跟随不抖动(贴点不贴鼠);
- [ ] 隐藏全部序列 → 空态而不是空白坐标系。

### 3.10 W-log 日志查看器 → [效果图](widgets/design/w-log.svg)

参照:Datadog / Vercel Runtime Logs。

**tab 效果要求**:
- 深色面板(独立 token `--log-bg`,六主题都压暗);mono 12,行高 1.5;
- 每行:时间戳(弱色,可隐藏)+ 2px kind 色条(`--sig-*`)+ kind 字标(色字,如 pre/post/err)+ 内容;
- 顶部工具行:过滤框 + 级别筛选 chips(all/pre/post/err)+ 复制全部 ⧉ + 暂停跟随 ⏸;
- 跟随:上滚即停跟,右下浮「↓ 回到底部」胶囊(--shadow-2);截断保尾时顶部一条「已截断,仅保留最近 500 行」弱色提示;
- 长行不wrap,横向滚动;选中复制友好(::selection 可见)。

**card 效果要求**:「N 行」徽标 +(有错误时)红「err ×N」+ 最近 3 行(色条保留,时间戳省略)。

**验收清单**:
- [ ] append 不在视野时不拽滚动;
- [ ] 过滤命中行不高亮底色,弱化未命中行(40% 透明);
- [ ] 深色面板在六主题下对比度都 ≥ 4.5:1。

### 3.11 W-diff 差异查看器 → [效果图](widgets/design/w-diff.svg)

参照:GitHub Files changed。

**tab 效果要求**:
- 文件头:路径 mono 500 + 增删计数徽标(绿 +N / 红 −N)+ 右 segmented「分屏 | 统一」;
- split:左右双列行号;unified:双行号列;行号 11px 弱色;
- add 行:`--ok` 8% 浅底 + 左 2px `--ok` 条 + 行首「+」;del 行:`--danger` 同构(符号+色双编码);
- hunk 头(@@ -12,4 +12,5 @@)`--live` 6% 底弱色字;折叠上下文「[+] 展开 6 行」整宽可点,hover `--bg-2`;
- 成员行(包场景):名称 mono + tier 徽标(●reversible 绿 / ▲escalate 黄)。

**card 效果要求**:绿「+N」红「−N」计数 + 首 hunk 2 行紧凑预览 + 「查看全部 →」。

**验收清单**:
- [ ] split/unified 切换不重取数据,瞬时;
- [ ] 红绿色弱可辨(符号/左条双编码);
- [ ] 展开上下文是原地插入,不跳滚动位置。

### 3.12 W-md Markdown 查看器 → [效果图](widgets/design/w-md.svg)

参照:GitHub README 渲染 / Notion 页面。

**tab 效果要求**:
- 排印阶梯:h1 20/600 + 下 hairline;h2 16/600;h3 14/600;段落 13px 行高 1.65;
- 行内 code:`--bg-3` 浅底 3px 圆角 mono 12;代码块:深底卡 + mono + 行号可选 + ⧉ 复制钮(hover 显,复制后 1s ✓);
- 引用:左 3px `--line` 粗条 + 弱色;列表缩进 20px;链接 `--live` + hover 下划线;
- 表格:hairline 边框,表头 `--bg-2`;图片圆角 8;
- 安全白名单不产生视觉残迹(被剥壳的内容整块不渲染,不留空框)。

**card 效果要求**:首个标题(15px 600 单行截断)+ 首段 2 行摘录(跳过代码块/表格)。

**验收清单**:
- [ ] 中文排版标点不顶行(避头尾);
- [ ] 代码块复制有成功反馈;
- [ ] 与 W-text 编辑态切换时滚动位置保持。

### 3.13 W-bubble 聊天气泡 → [效果图](widgets/design/w-bubble.svg)

参照:Notion comment / Slack thread。

**tab(展开态)效果要求**:
- 浮层卡(--shadow-3,圆角 12,140ms 入场)带小箭头指向锚点;
- 顶部锚点引用块:左 3px `--live` 条 + 原文摘录 2 行截断 + 位置徽标「L7」mono 弱;
- 消息流:每条 = 头像/图标圆 20px + 名称 500 + 相对时间弱 + 内容 13px;用户与 agent 不分左右,靠图标与名称区分(Notion 式,避免 IM 感);
- 输入区:圆角 8 输入框(「回复…」)+ Enter 发送 / Shift+Enter 换行 + 发送钮(--live 实心,空输入禁用);
- 发送中:输入区骨架条 + 消息流尾部 typing 三点;失败:行内红条 + 重试;
- 头部 ✕ 收起;Esc 同效。

**card(收起态)效果要求**:段旁 22px 圆标(`--live` 实底,未读数白字 11px;无未读则 💬 线稿图标 40% 透明)
+ hover 出预览条(--shadow-2:最后一条摘录 1 行 +「N 条」)。

**验收清单**:
- [ ] 气泡永远指向锚点,滚动跟随不错位;
- [ ] 未读数 = 我没看过的消息数(游标语义),不是总数;
- [ ] 多气泡同屏不互相遮挡(右侧栏堆叠或错列)。

---

## 4. 验收流程(实现期)

1. 实现某控件 → 沙盒 `?kind=<k>&surface=tab` 与效果图并排对照;
2. §3 对应小节「验收清单」逐条打勾;
3. 六主题各扫一遍(token 映射不破);
4. 既有行为测试全绿 + 新增视觉断言(类名/结构)进测试库;
5. 全部控件过完后,本文件标记为「已验收」,此后改动走变更记录。

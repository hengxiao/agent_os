# 易用性评估报告 — 四场景实测

> 日期:2026-07-24
> 范围:CLI Runner、Web UI Runner、内核、配置/部署。实测实例:8000(fib)、8001(support_desk)、8002(skills_100)。
> 方法:四个真实场景端到端执行,逐步记录摩擦点(命令输出 + 截图证据)。

---

## 一、场景与实测记录

### 场景 1:CLI 机器接口(coding agent 视角)

执行:`agent-os run fib --input '{"n": 6}' --json` → `trace` → `inspect` → `replay` → `diff`。

- ✅ RunRecord JSON 结构完整(status/result/usage/frames/artifacts),退出码语义正确(基础设施错 = 4);
- ✅ trace JSONL 逐行信号可机读;inspect 帧树/帧上下文可读;
- ✅ replay 确定性回放成功(重放结果与原 run 一致);
- ❌ **首次 replay 失败**:`ConfigError: 无法加载 dotted path 'brains:fib_brain'`——配置里的 brain dotted path 依赖**环境 PYTHONPATH**,而 `brains.py` 就在配置文件旁边。同类问题在 8001/8002 起服务时也各踩一次(skills 相对路径找不到)。

### 场景 2:技能迭代流(开发者视角)

执行:改 `skills.yaml` 描述 → `POST /api/skills/reload` → 验证新描述 → 跑备选路径(T-1004)→ 还原。

- ✅ 全链路顺畅:reload 返回 `true`,新描述即时可见,在跑帧钉住旧版;
- ✅ T-1004(咨询类)走与退款完全不同的简化路径(`answered`),验证 flow 分支可视性;
- 无明显摩擦。

### 场景 3:失败 RCA(开发者视角)

执行:8000 上跑 `fib(10)` → `MaxDepthExceeded` → 打开 Workbench。

- ✅ 红色 Banner(错误摘要)+"定位首个错误 ⌘J"按钮 + Resume 按钮就位;`/rca` 正确返回最深未完成帧;
- ✅ 侧栏失败状态徽标、帧树嵌套关系清晰;
- ❌ **失败 run 的祖先帧在 checkpoint 里滞留 `running` 状态**——UI 上失败的 run,帧树还转着"运行中"的圈(数据语义问题,早先已记录为遗留)。

### 场景 4:规模压力(skills_100)

执行:起 8002 → `GET /api/skills`(100 个,**2ms** 加载)→ `mega_pipeline`(12 帧纯 code 链,`{"total": 45}`)→ 截图评审。

- ✅ 100 技能加载与列表浏览流畅;搜索可用;mega 深链(12 帧/12 层)执行正确;
- ❌ Skills 列表**无排序与筛选**(kind、字母序、cluster 分组);100 个条目时只能依赖搜索;
- ❌ **code-only run 的呈现以 LLM 为中心**:run 头部显示 `0 steps`(code 技能没有 LLM 步);时间线分组全是"帧边界",对纯 code 链信息密度低。

---

## 二、优化点汇总(按优先级)

### P0 — 配置/部署摩擦(今天踩了三次)

1. **dotted path 相对配置文件目录解析**(`brains:fib_brain`、`support_tools:register`、code 技能 handler):装配时把配置文件所在目录注入 `sys.path`(或按文件位置定位),不再依赖环境 PYTHONPATH;同理 `[skills].path`、`[telemetry].dir` 支持相对配置文件的路径。
2. **`agent-os-web --reload` 选项**(uvicorn reload):代码更新后不再需要手动重启(此前"New Run 失败"的根因就是旧进程)。

### P1 — 内核数据语义

3. **失败/中止时未完成帧的状态归一**:run 失败弹栈时把 RUNNING 帧标记为 failed(现在 checkpoint 里滞留 running,UI 帧树在失败的 run 上转圈)。
4. **run metrics 去 LLM 中心化**:code-only run 显示 `0 steps`——头部与 Usage 面板应展示 logic exec 次数/耗时,或在无 LLM 活动时隐藏对应字段。

### P1 — CLI

5. **`agent-os run` 补覆盖参数**(`--model/--max-cost/--max-steps`,../RUNNERS.md 已列未实现),与 Web 的 overrides 对齐。
6. **错误消息给修复动作**:如 "无法加载 dotted path 'brains:fib_brain'(它应位于 PYTHONPATH 上;把配置目录加入 PYTHONPATH,或将模块放到配置文件旁并升级装配器)"。

### P1 — Web UI

7. **Skills 浏览器排序/筛选**:kind 筛选 chips、字母序/加载序切换、大库时按前缀 cluster 分组;
8. **code-only run 的时间线以 logic.exec 为中心分组**(现为 llm-step 分组,纯 code 链全是"帧边界");
9. **Run 列表项加输入摘要**(同名 run 靠输入区分,如 ticket_id/n)。

### P2 — 打磨

10. Usage 面板对 code-only run 展示 logic exec 计数/耗时;
11. 装配期检查:技能的 `model.prefer` 前缀若无已注册 provider,加载期警告(现在是运行期才报错);
12. 补一份 README/quickstart(安装、两个实例、systemd/nohup 常驻方式)。

---

## 三、结论

骨架是健康的:四场景中三条主路径(CLI 数据面、技能迭代、失败 RCA)端到端顺畅,性能没有问题(100 技能 2ms)。**主要易用性缺口集中在:配置的路径解析(P0,今天真实踩了三次)、失败帧状态语义(P1)、code-only run 的呈现(P1)、Skills 大库浏览(P1)。** 建议按 P0 → P1 顺序处理,P0 两项是投入最小、体感提升最大的。

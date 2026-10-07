# coding_agent(任务式 coding agent skillset)

给一个代码仓库派发"带测试验证的修复任务":探索 → 计划 → **人审计划** →
确定性修复循环(改代码 → 跑测试,最多 5 轮)→ 结构化 diff → 审查 → 结构化结果。
全部产物(计划文档、改动)落在本次 run 的 `--workdir` 沙箱内。

## 目录

```
skillsets/coding_agent/
├── README.md            # 本文件
├── agent-os.toml        # 实例配置(真实模型档 + 注释掉的 mock 档)
├── skills.yaml          # 8 个技能(7 个编排/叶子 + collect_diff)
├── handlers.py          # code 技能:fix_loop(确定性修复循环)+ collect_diff
├── brains.py            # mock 大脑:coding_brain / failing_verify_brain
└── tests/
    ├── conftest.py      # sys.path 钉入 + fixture 复制助手
    ├── pytest.ini       # asyncio_mode = "auto"
    ├── fixtures/        # 迷你目标仓库(calc.py 带故意 bug + 必失败的 pytest 用例)
    └── test_coding_agent.py
```

## 技能树与信任档阶梯

```
coding.agent.run (entry,prompt;L1 直接能力面:只读 + todo)
├── coding.explore    (prompt,L1 纯只读,无 shell)
├── coding.plan       (prompt,L2 可逆写:计划文档落盘,trust.reversal 注明 .bak)
├── coding.fix_loop   (code,handlers.py:fix_loop;推导档 L3,trust.blast_radius)
│   ├── coding.implement (prompt,L2 写改:file.edit/write,无 shell)
│   └── coding.verify    (prompt,EXEC:shell.exec 跑测试,逐次过 tool-confirm 门)
├── coding.collect_diff  (code,handlers.py:collect_diff;git diff 结构化,L3)
└── coding.review     (prompt,L1 只读 + git diff/status,L3)
```

- entry 的直接能力面只到 L2(todo 写入),调 L3 推导档的 fix_loop /
  collect_diff / review 时触发**升权门**(docs/ESCALATION.md §3)——设计意图,
  由 supervisor 通道逐次裁决;
- `system.shell.exec` 是 EXEC 档,`[sidecars] human_approval = {}` 在场时逐次过
  **tool-confirm 门**;拒绝折叠为工具结果里的错误观察,run 不崩(降级为 partial);
- 计划必须经 `ask_supervisor` 人类批准才进入修复循环;不批准可带意见重出 1 版,
  仍不批准则 `status=failed` 收尾。

## 用法

### 真实模型档(Kimi Code 端点)

```bash
export MOONSHOT_API_KEY=sk-...
source agent_os/.venv/bin/activate
agent-os run coding.agent.run \
  --input '{"task":"修复 calc 的 bug","test_command":"python -m pytest test_calc.py -x"}' \
  --config skillsets/coding_agent/agent-os.toml \
  --workdir /path/to/目标仓库 --json
```

run 挂起时 CLI 在 stderr 打印 `supervisor.ask` 协议行(plan 批准答
`approve`/`revise`/`reject`,升权与 tool-confirm 答 `approve-once`/`deny`),
从 stdin 读一行作答。

### 离线 mock 档(无 key 演示 / CI)

`agent-os.toml` 里把 `[run].model` 换成 `mock/coding` 并启用 `[providers.mock]`
(brain 与 handler 都是 dotted path,需 `PYTHONPATH` 含本目录):

```bash
PYTHONPATH=skillsets/coding_agent agent-os run coding.agent.run \
  --input '{"task":"修复 calc 的 bug","test_command":"python -m pytest test_calc.py -x"}' \
  --config <mock 版 toml> --workdir /tmp/任意目录 --json
```

mock 大脑(`brains.py`)是确定性剧本:真读源码定位矛盾 → 第一轮**故意改错**
(只交换操作数)→ 验证真跑失败 → 第二轮对症修复 → 验证真过。同输入同调用树。

### 测试

```bash
cd /home/hengxiao/agent_os
agent_os/.venv/bin/python -m pytest skillsets/coding_agent/tests -q
```

### 暂停与恢复(checkpoint)

run 级周期 checkpoint 用 K1 的 per-run 覆盖 flag;宿主(Web 暂停按钮 /
`RunControl.pause`)触发挂起后,run 落 PAUSED,checkpoint 照常落盘:

```bash
# 每步覆盖写"最近现场"(也可不配,暂停时 finalize 仍会快照一次)
agent-os run coding.agent.run --input ... --config ... --workdir ... \
  --checkpoint-interval 1 --artifacts .agent-os
# 从 checkpoint 续跑(产物写回原 run 目录;brain/handler 仍需 PYTHONPATH)
PYTHONPATH=skillsets/coding_agent agent-os resume \
  .agent-os/runs/<run_id>/checkpoint.json --config <同一份 toml> --json
```

已结算的步骤(todo/计划/人审回答)随 checkpoint 恢复,resume 只补未完成的
步骤,不重复副作用(测试 `test_pause_and_resume` 钉死此语义)。

## 输出契约

`coding.agent.run` 输出 `{status, summary, changed_files[], tests{command,ran,passed}, plan_path}`:

- `done`:fix_loop 通过且 review pass;
- `partial`:修复循环 5 轮耗尽未通过,或 review 有疑虑,或关键执行被拒绝;
- `failed`:计划未获批准(两版)或中途异常。
- `tests.ran=false` 表示验证命令从未真实执行(如 shell.exec 被逐次拒绝)。

## 已知限制

- **CLI 运行必须 `PYTHONPATH=skillsets/coding_agent`**:code 技能 handler
  (`handlers:fix_loop` / `handlers:collect_diff`)与 mock brain
  (`brains:coding_brain`)都按 dotted path 由 importlib 加载;缺了它,
  执行体找不到 `handlers` 模块,会把模块名当源码 exec,报
  `NameError: name 'handlers' is not defined` 并折叠为子技能错误观察
  (真实模型档与 mock 档同此要求;测试由 tests/conftest.py 钉入,不受影响)。
- **shell 会话是状态重放,非持久进程**:`system.shell.exec` 缺省每次调用是
  独立子进程;给 `session_id`(2026-10-07,K2)则同 (run, session) 跨调用保持
  `cd` 后的工作目录与环境变量**增量**(多步构建/激活 venv 用同一会话),
  但 run 结束/pause→resume 后会话重置(resume 按需重建,不进 checkpoint);
  `test_command` 自含 `cd` 到目标目录仍然是最稳的写法
  (shell 的初始 cwd 即 run 的 workdir)。
- **30s 硬超时**:shell.exec 单命令上限约 30s,验证命令必须秒级完成
  (大测试套件请先用 `-x`/筛选子集)。
- **git 可选**:workdir 非 git 仓库时 `collect_diff` 返回 `available=false`
  不报错,review 退化为纯文件审查。
- **文件工具圈在 workdir/read_paths**:fs 写不了 workdir 之外的路径
  (`--read-paths` 只读挂载除外)。
- **CPython 字节码缓存坑**:改完代码立刻跑测试时,若编辑前后文件大小相同且落在
  同一 mtime 时间刻度,`__pycache__` 里的陈旧 pyc 会被判有效,测试跑的是旧代码
  (本 skillset 的 mock 剧本实测踩中:改错与修复同尺寸,verify 恒执行第一轮旧
  字节码)。对策:编辑顺带改变文件大小(如附注释),或验证命令用 `python -B`。
- mock 档的 `compression` 建议用 `"off"`(剧本大脑按完整消息历史决策);
  真实模型档用 `"hierarchical"` 无碍(小上下文不触发压缩)。
- **CLI 宿主下 code 技能帧栈内的 supervisor 提问不可见(已上报的内核观察)**:
  fix_loop/collect_diff 这类 code 技能由 `InProcessLogicKernel` 执行,
  其 stdout/stderr 被 `contextlib.redirect_*` 捕获——这些帧的执行栈内触发的
  supervisor 提问行(如 verify 的 tool-confirm)不会打印到终端,但 stdin
  仍在等答案(supervisor.ask 信号与答案往返、run 结果均正确,仅提示行缺失)。
  阶段二交互 host 会在 host 层解决(改读 supervisor.ask 信号渲染提问)。

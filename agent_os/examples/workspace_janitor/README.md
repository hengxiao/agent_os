# workspace_janitor(工作区大扫除)——升权系统全真示例

四技能三档剧情,**全真工具、零 mock**:file.list/write/delete 与 shell.exec
全部真跑,副作用都是真的(真删文件、真停进程)。爆炸半径由 run 的 workdir
沙箱圈住:所有写入/删除只发生在 fixture 的 scratch 目录树内,进程只有
fixture 启动的那个 `sleep` 演示进程。

| 技能 | 档 | 白名单 | 剧情 |
|---|---|---|---|
| `ops.scan.workspace`(根) | L1 none | file.list/stat/read/search | 巡检 scratch,找出陈旧/临时文件 |
| `ops.plan.write` | L2 reversible | file.write | 写清理计划 + 巡检日志(**连调两次**,演示 approve-run) |
| `ops.cleanup.execute` | L3 irreversible | file.delete | 按指名清单真删文件(**每次必问**;dry_run 预览) |
| `ops.service.stop` | L3 irreversible | shell.exec | `kill <pid>` 停演示进程(每次必问) |

档位全部由权限面推导(`derive_skill_tier`),manifest 里没有一处自报;
`system.file.delete` 的 irreversible 来自显式标定(TIER-STANDARDS §1 命名规则)。

## 准备:生成 fixture

```bash
cd agent_os
python examples/workspace_janitor/make_fixture.py /tmp/janitor-demo
# stdout 打印 {"scratch_dir": ..., "pid": ..., "stale": [...], "keep": [...]}
# 同时写入 /tmp/janitor-demo/fixture.json
```

scratch 树里混着陈旧日志(`logs/app.log.*`)、临时文件(`tmp/*.tmp`、`index.cache`)
与正常文档(`docs/*.md`);`pid` 是一个真的 `sleep 3600` 演示进程。

## 玩法 a:CLI + supervisor 收件箱

让 run 的 workdir 指向 fixture 根目录(fs 沙箱 = fixture 树):

```toml
# agent-os.toml(本示例专用副本)
[run]
model = "kimi/k2"          # 任意真实模型
workdir = "/tmp/janitor-demo"
[tools]
builtins = true
[skills]
path = "examples/workspace_janitor/skills.yaml"
```

```bash
agent-os run --config demo.toml --skill ops.scan.workspace \
  --input '{"scratch_dir": "/tmp/janitor-demo/scratch", "pid": <pid>}'
```

每次升权(L1→L2/L3)run 挂起,stderr 打一行 `supervisor.ask` JSON
(`kind: "escalation"`,带 skill/tier/params/options);CLI 通道从 **stdin
读一行**作为回答(SUPERVISOR.md §2.3)——在 run 所在终端键入答案回车即可
(也可以 `echo approve-once | agent-os run ...` 管道喂答案):

- L2(写计划)第一次:`approve-run` —— 第二次写日志**不再问**(Grant 命中,
  信号 `post:skill.escalate decision="grant-run"`);
- L3(删除/停进程)每一次:`approve-once` —— 注意 L3 的 options 里**没有**
  approve-run,手工输入也会被 options 校验打回重问;
- `deny` —— 父帧收 PERMISSION_DENIED,文件原样保留,巡检以 partial 收尾。

## 玩法 b:Web 收件箱 + debugger 时间线

```bash
agent-os-web --config demo.toml
# 启动 run 后打开收件箱(TopBar 铃铛):
```

- `kind == "escalation"` 的 pending 渲染升权卡片:档位徽标(L2 黄/L3 红)、
  skill 名、`reason_hint`(none → reversible 等)、可折叠的参数 JSON
  (就是将要执行的那份)、请求的权限集;**L2 三枚选项、L3 两枚**;
- run 详情/debugger 时间线可读信号三枚:`pre:skill.escalate`(挂起瞬间,
  带参数)、`post:skill.escalate`(decision/scope/decided_by)、
  `skill.escalation.denied`(拒绝);
- 点开升权子帧:context 只有一条 `USER(参数 JSON)`——父帧的巡检对话、
  文件清单观察**物理上不在**子帧里(干净 context 不变量,ESCALATION.md §3)。

## 玩法 c:崩溃恢复

在 L3 确认挂起时(收件箱里有 pending)`kill -9` run 进程,然后:

```bash
agent-os resume <run_dir>/checkpoint.json --config demo.toml
```

resume 凭 `_pending_escalation` **重走升权闸门**:重新挂起等裁决(不是
按 interrupted 结算);批准后继续,拒绝则写 PERMISSION_DENIED。
若挂起前已批准过 approve-run(L2),run 级 Grant 随 checkpoint 恢复,
resume 后同 skill 的后续调用直接命中放行,不问第二次。

## 清理

```bash
python examples/workspace_janitor/make_fixture.py --cleanup /tmp/janitor-demo/fixture.json --purge
```

(杀掉演示进程——如果剧情里它还没被停掉的话——并删除 scratch 树。)

## 测试

`tests/examples/test_workspace_janitor.py`:scripted brain 驱动同一剧情,
approve-run Grant 命中、L3 每次必问、deny 保文件、真 kill 进程、dry_run
一致性、干净 context、真实文件系统断言,全程零 mock。

# supervision:嵌套监督示例(调用方是另一个 agent)

../../../docs/SUPERVISOR.md v2 §2.5 的最小可运行示例。**默认 yield 到 agent 的调用方**:
`ask_supervisor` 把裁决请求路由出 agent,交给启动本 run 的宿主;宿主可以
是人(Web 收件箱 / CLI 协议)、普通程序,也可以**是另一个 agent**——
本示例演示后者。

## 结构

```
outer.py(外层应用/嵌入方)
 ├─ 内层 run:junior_clerk(报销文员,mock 大脑 clerk_brain)
 │    └─ ask_supervisor("批准 ¥N 报销吗?", options=[approve, reject])
 │         → 帧就地挂起,问题路由给注入的 supervisor handler
 └─ team_lead_handler(外层应用的裁决点)
      └─ 外层 run:team_lead(团队主管,mock 大脑 lead_brain)
           └─ 按政策裁决(≤ ¥3000 自动批准)→ answer 回流内层
```

- `skills.yaml` — 两个 prompt 技能:`junior_clerk`(声明
  `permissions.tools: [ask_supervisor]`,声明即授权)与 `team_lead`;
- `brains.py` — 两个 mock 大脑(确定性,无需 API key);
- `outer.py` — 嵌入方脚本:`KernelBuilder.supervisor(handler)` 注入嵌套
  handler,handler 内部 `await outer.run("team_lead", ...)` 再问外层 LLM。

## 语义要点

- **内核不感知级数**(§2.5):内层内核只看到一次 handler 调用往返;外层
  run 用的是**另一个独立内核**(独立大脑/总线)。权威链
  子 agent → 调用 agent → … → 人,完全发生在 agent 边界之外;
- **decided_by 链可观察**:外层 handler 以 `"agent:team_lead"` 标注裁决
  来源,随 tool result 写回内层帧,`supervisor.answer` 信号同值;
- handler 也可以不再起 run(直接答),或**继续向它的调用方上报**——
  级联层数是调用方自己的事。

## 运行

```bash
.venv/bin/python examples/supervision/outer.py        # ¥5000 → reject
.venv/bin/python examples/supervision/outer.py 500    # ¥500  → approve
```

锚点测试:`tests/examples/test_supervision_nested.py`(嵌套 handler 被
调用、外层 LLM 答案流回内层、decided_by 链清晰)。

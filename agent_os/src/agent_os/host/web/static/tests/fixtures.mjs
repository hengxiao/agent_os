/* D2 单测共享 fixture:含 llm / tool(ok:false)/ veto(pre 无 post)/ 帧边界的信号流、
   帧摘要、成对/孤儿/reasoning 消息流。结构对齐后端契约:
   信号 { v, type:"signal", name, run_id, frame_id, ts, payload };
   帧摘要 { frame_id, skill, depth, status, usage:{steps} };
   消息 { role, content, tool_calls, tool_call_id, name, reasoning, source, meta }。 */

/* 信号流分组预期(供断言行内注释;边界 = pre:step 或 frame_id 变化):
   g0  run      step∅  [0]                          run.started
   g1  f1       step∅  [1,2]                        frame.push ×2
   g2  f1       step1  [4,5,6,7,8]                  llm + tool.call ✓ + post:step
   g3  f1       step2  [10..14]  anomaly(ok:false @13)
   g4  f1       step3  [16,17,18]                   llm + skill.invoke pre
   g5  f2       step∅  [19,20]                      frame.push ×2
   g6  f2       step1  [22,23,24,25] anomaly(veto @24:pre 无 post)
   g7  f2       step2  [27,28,29,30]                frame.pop 同帧,并入当前组
   g8  f1       step∅  [31,32,33,34]                回到 f1 新组(invoke post/post:step/pop)
   g9  run      step∅  [35]                         run.finished */
export function makeSignals() {
  const sig = (name, frame_id, payload = {}, ts = 0) => ({
    v: 1,
    type: "signal",
    name,
    run_id: "r1",
    frame_id,
    ts,
    payload: { frame_id, skill: "local:fib@1.0.0", depth: frame_id === "f2" ? 2 : 1, ...payload },
  });
  return [
    sig("run.started", null, { skill: "local:fib@1.0.0" }), // 0
    sig("pre:frame.push", "f1"), // 1
    sig("post:frame.push", "f1"), // 2
    sig("pre:step", "f1", { step: 1 }), // 3(组头,不占行)
    sig("pre:llm.request", "f1", { model: "mock/fib" }), // 4
    sig("post:llm.response", "f1", { model: "mock/fib", usage: { prompt: 1, completion: 1, cost: 0 } }), // 5
    sig("pre:tool.call", "f1", { tool: "python_exec", args: { code: "print(1)" } }), // 6
    sig("post:tool.call", "f1", { tool: "python_exec", ok: true }), // 7
    sig("post:step", "f1", { step: 1, calls: [{ name: "python_exec" }] }), // 8
    sig("pre:step", "f1", { step: 2 }), // 9
    sig("pre:llm.request", "f1", { model: "mock/fib" }), // 10
    sig("post:llm.response", "f1", { model: "mock/fib", usage: { prompt: 1, completion: 1, cost: 0 } }), // 11
    sig("pre:tool.call", "f1", { tool: "shell_exec", args: { cmd: "ls" } }), // 12
    sig("post:tool.call", "f1", { tool: "shell_exec", ok: false }), // 13 ← ok:false
    sig("post:step", "f1", { step: 2, calls: [] }), // 14
    sig("pre:step", "f1", { step: 3 }), // 15
    sig("pre:llm.request", "f1", { model: "mock/fib" }), // 16
    sig("post:llm.response", "f1", { model: "mock/fib", usage: { prompt: 2, completion: 1, cost: 0 } }), // 17
    sig("pre:skill.invoke", "f1", { skill: "fib" }), // 18
    sig("pre:frame.push", "f2"), // 19
    sig("post:frame.push", "f2"), // 20
    sig("pre:step", "f2", { step: 1 }), // 21
    sig("pre:llm.request", "f2", { model: "mock/fib" }), // 22
    sig("post:llm.response", "f2", { model: "mock/fib", usage: { prompt: 1, completion: 1, cost: 0 } }), // 23
    sig("pre:tool.call", "f2", { tool: "python_exec", args: { code: "rm -rf /" } }), // 24 ← vetoed
    sig("post:step", "f2", { step: 1, calls: [] }), // 25
    sig("pre:step", "f2", { step: 2 }), // 26
    sig("pre:llm.request", "f2", { model: "mock/fib" }), // 27
    sig("post:llm.response", "f2", { model: "mock/fib", usage: { prompt: 1, completion: 1, cost: 0 } }), // 28
    sig("pre:frame.pop", "f2"), // 29
    sig("post:frame.pop", "f2"), // 30
    sig("post:skill.invoke", "f1", { skill: "fib", ok: true }), // 31
    sig("post:step", "f1", { step: 3, calls: [] }), // 32
    sig("pre:frame.pop", "f1"), // 33
    sig("post:frame.pop", "f1"), // 34
    sig("run.finished", null, {}), // 35
  ];
}

/* 帧摘要(与 makeSignals 同拓扑:f1 根,f2 子) */
export function makeFrames() {
  return [
    { frame_id: "f1", skill: "local:fib@1.0.0", depth: 1, status: "done", usage: { steps: 3, cost: 0 } },
    { frame_id: "f2", skill: "local:fib@1.0.0", depth: 2, status: "done", usage: { steps: 2, cost: 0 } },
  ];
}

/* 兄弟 + 深链拓扑:depth 排序平表会误挂,pushOrder 可修正(见 frame-tree.test) */
export function makeBranchFrames() {
  return [
    { frame_id: "a", skill: "local:orchestrator@1.0.0", depth: 1, status: "done", usage: { steps: 4 } },
    { frame_id: "b", skill: "local:fetch@1.0.0", depth: 2, status: "done", usage: { steps: 2 } },
    { frame_id: "c", skill: "local:summarize@1.0.0", depth: 2, status: "done", usage: { steps: 1 } },
    { frame_id: "d", skill: "local:validate@1.0.0", depth: 3, status: "failed", usage: { steps: 1 } },
  ];
}

/* 消息流:成对 ×2(其一含 veto 结果)/ reasoning / 孤儿 tool result / 纠偏注入 */
export function makeMessages() {
  const msg = (role, over = {}) => ({
    role,
    content: "",
    tool_calls: [],
    tool_call_id: null,
    name: null,
    reasoning: null,
    source: "system",
    meta: {},
    ...over,
  });
  return [
    msg("user", { content: '{"n": 6}', source: "parent_input" }), // 0
    msg("assistant", { // 1
      tool_calls: [{ id: "c1", name: "python_exec", args: { code: "print(5)" } }],
    }),
    msg("tool", { // 2(与 1 成对)
      tool_call_id: "c1",
      name: "python_exec",
      content: '{"ok": true, "value": {"stdout": "5\\n"}, "error": null}',
      source: "tool_result",
    }),
    msg("assistant", { // 3(双 call)
      tool_calls: [
        { id: "c2", name: "shell_exec", args: { cmd: "rm -rf /" } },
        { id: "c3", name: "fs_read", args: { path: "/tmp/a" } },
      ],
    }),
    msg("tool", { // 4(veto 结果)
      tool_call_id: "c2",
      name: "shell_exec",
      content: '{"ok": false, "value": null, "error": {"kind": "vetoed", "message": "ToolGuard: 禁止危险命令"}}',
      source: "tool_result",
    }),
    msg("tool", { // 5
      tool_call_id: "c3",
      name: "fs_read",
      content: '{"ok": true, "value": "hello", "error": null}',
      source: "tool_result",
    }),
    msg("assistant", { // 6(reasoning + 终答)
      content: '{"seq": [0, 1, 1, 2, 3, 5]}',
      reasoning: "先算 f(4) 与 f(3),合并得 f(5);检查 outputs schema 通过,可以给出最终序列。",
    }),
    msg("tool", { // 7(孤儿:无配对 assistant)
      tool_call_id: "ghost",
      name: "python_exec",
      content: '{"ok": true, "value": 1, "error": null}',
      source: "tool_result",
    }),
    msg("user", { // 8(纠偏注入)
      content: "reviewer 打回:序列缺少最后一项。请继续完成任务。",
      source: "injected",
    }),
  ];
}

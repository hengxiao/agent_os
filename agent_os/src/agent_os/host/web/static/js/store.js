/* 极简响应式 store(docs/WEB-UI.md §6.1):get / set / subscribe,纯逻辑、不碰 DOM。
   D2/D3 的 selection store 复用同一实现。 */

export function createStore(initial = {}) {
  const state = { ...initial };
  const subs = new Set();
  return {
    get(key) {
      return key === undefined ? { ...state } : state[key];
    },
    set(patch) {
      Object.assign(state, patch);
      for (const fn of subs) fn({ ...state }, patch);
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn); // 退订(幂等)
    },
  };
}

/* 全局应用状态(§6.1):路由、run 列表、列表加载态、当前选中 run、Workbench 三联动选择。
   selection(§4.2 规则 1):{ frameId, signalIndex, source } | null;
   source ∈ "tree"(帧树选帧→时间线过滤)/ "timeline"(时间线选信号→帧树定位、检视器滚动);
   signalIndex 为信号在 run 信号流中的全局下标;frameId 与 signalIndex 均可为 null。
   D6 一站多 skill set:skillsets = GET /api/skillsets([{name, skills, path}]);
   skillSet = 当前 set 过滤(null = 全部;≥2 sets 时侧栏/Skills 页下拉可见)。 */
export const store = createStore({
  route: { name: "runs", runId: null }, // runs | run-detail | skills | tools
  runs: [],
  runsStatus: "loading", // loading | ready | error
  selectedRunId: null,
  selection: null,
  // SSE 连接态(§5 live 指示,workbench 写、app 渲染 TopBar):
  // null = 无 live 会话(按 API 健康渲染)| "connecting" | "ok"(蓝点脉冲)|
  // "down"(黄点 + "已断开,点击重连",点击重建该 run 的 SSE)
  liveConn: null,
  skillsets: [], // D6:[{name, skills, path}](空 = 未配置多 set,UI 与单站一致)
  skillSet: null, // D6:当前 set 过滤(null = 全部;hash ?set= 深链接恢复)
  inboxPending: [], // S3(docs/SUPERVISOR.md §5):GET /api/supervisor/pending 待答问题(app 5s 轮询)
});

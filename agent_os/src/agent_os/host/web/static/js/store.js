/* 极简响应式 store(WEB-UI.md §6.1):get / set / subscribe,纯逻辑、不碰 DOM。
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

/* 全局应用状态(§6.1):路由、run 列表、列表加载态、当前选中 run。 */
export const store = createStore({
  route: { name: "runs", runId: null }, // runs | run-detail | skills | tools
  runs: [],
  runsStatus: "loading", // loading | ready | error
  selectedRunId: null,
});

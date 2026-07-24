/* Live 进度区(WEB-UI.md §4.3):进行中 run 的 Workbench 顶部变体——
   live 脉冲点、已用时长(秒级走动)、steps 与 cost 双 ProgressBar(占
   max_steps/max_cost 比例;>80% 转 --warn 色,与 BudgetGuard 阈值语义一致)、
   右侧常驻 Stop 按钮(确认条 → loading → 结束态替换为结果 Banner)。

   纯函数(不碰 DOM,node 单测可载):
     deriveProgress(detail, signals)  进度派生 {steps, stepsMax, cost, costMax, stepsWarn, costWarn}
     createLiveState() / mergeSignal(live, sig)  SSE 增量合并(信号流 → 帧摘要 + steps/cost)
     fmtElapsed(startMs, nowMs)       已用时长文本("12.4s")
     liveBarHtml / endBannerHtml      HTML 字符串
   mountLiveBar 是唯一碰 DOM 的部分:事件委托处理 Stop 确认流,
   其余状态(update/setElapsed/end)由 workbench 驱动。 */

import { esc, fmtCost, shortSkill } from "../util.js";
import { banner } from "./banner.js";

/* 超 80% 转 --warn(§4.3,与 BudgetGuard 阈值语义一致) */
const WARN_RATIO = 0.8;

/* ── 进度派生 ─────────────────────────────────────────────────
   steps = post:step 信号计数(每个 = 某帧一次完成的 loop 迭代,合计即全 run
   总步数,对齐 RunConfig.max_steps 语义);cost = Σ post:llm.response 的
   payload.usage.cost(BudgetGuard 记账同源,§5.4)。信号流缺失(如轮询只拿到
   detail)时回退 detail.usage。max 来自 detail.config(在途 run 由后端给,
   含 overrides 合并后值)。 */
export function deriveProgress(detail, signals) {
  const cfg = detail?.config ?? {};
  const usage = detail?.usage ?? {};
  const stepsMax = Number(cfg.max_steps) || 0;
  const costMax = Number(cfg.max_cost) || 0;
  let steps = 0;
  let cost = 0;
  let sawLlm = false;
  for (const s of signals ?? []) {
    if (s?.name === "post:step") steps += 1;
    else if (s?.name === "post:llm.response") {
      sawLlm = true;
      cost += Number(s?.payload?.usage?.cost) || 0;
    }
  }
  if (!steps) steps = Number(usage.steps) || 0;
  if (!sawLlm) cost = Number(usage.cost) || 0;
  return {
    steps,
    stepsMax,
    cost,
    costMax,
    stepsWarn: stepsMax > 0 && steps / stepsMax > WARN_RATIO,
    costWarn: costMax > 0 && cost / costMax > WARN_RATIO,
  };
}

/* ── SSE 增量合并 ─────────────────────────────────────────────
   live = { signals, frames, lastEffect };mergeSignal 就地更新并返回 live
   (reducer 拥有自己的 state,非共享对象,无 DOM)。lastEffect 指引渲染:
   "tree"(帧增删/状态变,重绘帧树)/ "chip"(帧 steps/cost 变)/ "signal"(仅时间线)。
   帧状态:pre:frame.push → running;post:frame.pop → done;run.aborted → 在跑帧 aborted;
   run.finished 不猜帧终态(真实状态由结束后 detail/checkpoint 重建)。 */
export function createLiveState() {
  return { signals: [], frames: [], lastEffect: "signal" };
}

export function mergeSignal(live, sig) {
  live.lastEffect = "signal";
  if (!sig || typeof sig !== "object") return live;
  live.signals.push(sig);
  const name = String(sig.name ?? "");
  const fid = sig.frame_id ?? null;
  const p = sig.payload ?? {};
  const frame = fid ? live.frames.find((f) => f.frame_id === fid) : null;
  if (name === "pre:frame.push" && fid && !frame) {
    live.frames.push({
      frame_id: fid,
      skill: p.skill ?? "",
      depth: Number(p.depth) || 1,
      status: "running",
      usage: {},
    });
    live.lastEffect = "tree";
  } else if (name === "post:frame.pop" && frame) {
    frame.status = "done";
    live.lastEffect = "tree";
  } else if (name === "post:step" && frame) {
    frame.usage = { ...(frame.usage ?? {}), steps: Number(p.step) || frame.usage?.steps };
    live.lastEffect = "chip";
  } else if (name === "post:llm.response" && frame) {
    const add = Number(p.usage?.cost) || 0;
    frame.usage = { ...(frame.usage ?? {}), cost: (frame.usage?.cost ?? 0) + add };
    live.lastEffect = "chip";
  } else if (name === "run.aborted") {
    for (const f of live.frames) if (f.status === "running") f.status = "aborted";
    live.lastEffect = "tree";
  }
  return live;
}

/* ── 已用时长(§5:live 时长秒级更新)────────────────────────── */
export function fmtElapsed(startMs, nowMs) {
  const t0 = Number(startMs);
  const t1 = Number(nowMs);
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return "—";
  return `${(Math.max(0, t1 - t0) / 1000).toFixed(1)}s`;
}

/* ── HTML 渲染(纯)─────────────────────────────────────────── */

const pct = (value, max) =>
  max > 0 ? Math.min(100, Math.round((value / max) * 1000) / 10) : 0;

function barHtml(kind, label, text, value, max, warn) {
  return (
    `<div class="pb pb-${kind}${warn ? " is-warn" : ""}"` +
    ` title="${esc(label)} ${esc(text)}(>${WARN_RATIO * 100}% 转警示色)">` +
    `<span class="pb-label">${esc(label)}</span>` +
    `<span class="pb-track" role="progressbar" aria-valuemin="0" aria-valuemax="${esc(max || 0)}"` +
    ` aria-valuenow="${esc(value)}">` +
    `<span class="pb-fill" style="width:${pct(value, max)}%"></span>` +
    `</span>` +
    `<span class="pb-text mono">${esc(text)}</span>` +
    `</div>`
  );
}

/* 进度条区(§4.3):live 脉冲点 + 已用时长 + 双 ProgressBar + 右侧 Stop。
   phase: "live"(Stop 常驻)/ "confirm"(不可逆确认条)/ "stopping"(loading)。 */
export function liveBarHtml(prog, { elapsed = "—", phase = "live" } = {}) {
  const stepsText = prog.stepsMax > 0 ? `${prog.steps}/${prog.stepsMax}` : `${prog.steps}/—`;
  const costText =
    prog.costMax > 0 ? `${fmtCost(prog.cost)}/${fmtCost(prog.costMax)}` : `${fmtCost(prog.cost)}/—`;
  let stopArea;
  if (phase === "confirm") {
    stopArea =
      `<span class="stop-confirm">中止不可逆,在下一个 safe point 生效</span>` +
      `<button class="btn btn-danger" data-lb="confirm">确认中止</button>` +
      `<button class="btn" data-lb="cancel">取消</button>`;
  } else if (phase === "stopping") {
    stopArea = `<button class="btn btn-danger" disabled aria-busy="true">中止中…</button>`;
  } else {
    stopArea =
      `<button class="btn btn-danger" data-lb="stop" data-tip="中止该 run(不可逆)">Stop</button>`;
  }
  return (
    `<div class="live-bar card" data-phase="${esc(phase)}">` +
    `<span class="live-dot" title="实时进行中" aria-label="实时进行中"></span>` +
    `<span class="live-elapsed mono" title="已用时长">${esc(elapsed)}</span>` +
    barHtml("steps", "steps", stepsText, prog.steps, prog.stepsMax, prog.stepsWarn) +
    barHtml("cost", "cost", costText, prog.cost, prog.costMax, prog.costWarn) +
    `<span class="live-stop-area">${stopArea}</span>` +
    `</div>`
  );
}

/* 结束态(§4.3):进度条区替换为结果 Banner(done 绿 / failed 红 / aborted 紫),
   live 脉冲随之熄灭(整个 live-bar 被替换)。 */
export function endBannerHtml(status, error) {
  const s = String(status ?? "unknown");
  const body = error ? esc(String(error)) : "";
  if (s === "done") return banner("ok", "run done — 已完成", body);
  if (s === "aborted") return banner("aborted", `run aborted — ${esc(String(error ?? "已中止"))}`);
  if (s === "failed") return banner("danger", `run failed — ${esc(String(error ?? "未知错误"))}`);
  return banner("info", `run ${esc(s)}`, body);
}

/* ── DOM 挂载(唯一碰 DOM 的部分)──────────────────────────────
   mountLiveBar(container, { onStop }) → { update, setElapsed, end, destroy }。
   Stop 流(§4.3/§5):Stop → 确认条(不可逆提示)→ onStop()(workbench 调
   POST /stop 并 Toast 结果)→ 按钮 loading;onStop 返回 false 退回确认前。
   结束由 workbench 侦测(SSE end / 轮询)后调 end(status, error)。 */
export function mountLiveBar(container, { onStop } = {}) {
  let prog = deriveProgress(null, []);
  let elapsed = "—";
  let phase = "live"; // live | confirm | stopping | ended

  const render = () => {
    if (phase === "ended") return; // end() 自行写入 Banner
    container.innerHTML = liveBarHtml(prog, { elapsed, phase });
  };

  const onClick = (e) => {
    const btn = e.target.closest?.("[data-lb]");
    if (!btn || !container.contains(btn)) return;
    const act = btn.dataset.lb;
    if (act === "stop" && phase === "live") {
      phase = "confirm";
      render();
    } else if (act === "cancel" && phase === "confirm") {
      phase = "live";
      render();
    } else if (act === "confirm" && phase === "confirm") {
      phase = "stopping";
      render();
      Promise.resolve(onStop?.()).then((ok) => {
        if (ok === false && phase === "stopping") {
          phase = "live"; // stop 失败(如已结束):回到 live,Toast 由 workbench 给
          render();
        }
      });
    }
  };
  container.addEventListener("click", onClick);
  render();

  return {
    update(next) {
      prog = next ?? prog;
      if (phase === "live") render(); // 确认/中止中不打断用户操作
    },
    setElapsed(text) {
      elapsed = text;
      const el = container.querySelector(".live-elapsed");
      if (el) el.textContent = text; // 秒级走动:只动文本节点,不整体重绘
    },
    end(status, error) {
      phase = "ended";
      container.innerHTML = endBannerHtml(status, error);
    },
    /* 命令条 Stop(§5 ⌘K):programmatic 进入确认条,复用同一不可逆确认流 */
    requestStop() {
      if (phase !== "live") return;
      phase = "confirm";
      render();
    },
    destroy() {
      container.removeEventListener("click", onClick);
    },
  };
}

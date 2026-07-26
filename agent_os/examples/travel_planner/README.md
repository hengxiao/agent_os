# travel_planner — 现场查互联网的旅游攻略

目的地无关的旅行规划技能包:观众说任何一个地方,系统**现场上 Wikivoyage 查资料**
(开放 API,零 key),从正文中提取真实价目(景点门票/餐饮人均/住宿区间/大交通),
产出逐日攻略与可逐数追溯的预算;超预算时挂起请求上级裁决(approve/trim)。

## 现场演示(真实模型 + 真实互联网)

```bash
cd /home/hengx/agent_os/agent_os
export MOONSHOT_API_KEY=sk-...        # Kimi key(kimi/kimi-k2-thinking)

# 观众出题:"带老人去西安 3 天,预算 3000"
PYTHONPATH=examples/travel_planner .venv/bin/agent-os run plan_trip \
  --input '{"request": "想带老人从上海去西安玩 3 天 2 夜,预算 3000 元,节奏别太赶"}' \
  --config examples/travel_planner/agent-os.toml --json
```

- 任意目的地同一条流:`--input '{"request": "带孩子去成都玩 2 天,预算 1500"}'`。
- 超预算时 run 挂起,CLI 把裁决问题写到 stderr(approve / trim),回答后继续。
- `PYTHONPATH` 让 `[tools.custom]` 的 `travel_tools` 与 code 技能的
  `travel_handlers` 可 import(与 research_pipeline 示例同一约定)。

## 确定性复现(mock 模式,无需 key / 无需网络)

锚点测试用 mock wiki 工具(返回罐装西安正文)与数据驱动 mock 大脑
`brains.py:travel_brain` 跑同一条技能流,同输入同输出:

```bash
cd /home/hengx/agent_os/agent_os
.venv/bin/pytest tests/examples/test_travel_planner.py -q
```

其中 `test_live_wiki_fetch_hits_real_internet` 是唯一碰真实网络的用例
(wikivoyage.org 不可达时自动 skip)。

## 组成

- `skills.yaml` — 10 个技能:`plan_trip`(入口编排)→ `parse_request` /
  `research_destination`(wiki_search + wiki_fetch 取回正文并提取事实)→
  `plan_route`(code,按区域聚类分天,同一远郊同日)/ `plan_meals` /
  `select_hotel` / `plan_budget`(code,全算术)→ 超支 `ask_supervisor` →
  `adjust_for_budget` 削减重排 → `review_itinerary` / `format_itinerary`(code,markdown);
- `travel_tools.py` — `wiki_search` / `wiki_fetch` 真实联网工具(NET 档,
  结构化错误带 retryable hint,超长显式截断,`untrusted_source` 标记);
- `travel_handlers.py` — code 技能 handlers(确定性纯函数);
- `brains.py` — mock 模式的数据驱动大脑(抽取逻辑即演示讲解稿);
- `agent-os.toml` — CLI 演示配置(kimi provider + custom tools + supervisor 策略)。

# 批注气泡 UX 修复报告(F1–F4,widget-libs 分支)

日期:2026-08-13 · BUILD 2026-08-13.1 · 对应验收:`批注气泡UX验收报告.md`(E1/E3/E6/E7)

## 一、根因 ↔ 修法对应

### F1(E1 泡吸行右缘,不像"点旁批注")
- **根因**:`openBubble` 计算块内偏移时有 `min(point.x - bRect.left, bRect.width - 48)`
  右缘 clamp,点击点靠右半时泡被吸到行右缘,呈固定面板感。
- **修法**(`doc-editor.js` openBubble):`offLeft = Math.max(8, point.x - bRect.left)`——
  左缘 = 点击点块内偏移,只钳 8px 左边距;视口边界交给 Floating UI
  `shift({padding:16})`;纵向保持点击行下方(offset(8) 中间件)。

### F2(E3 无效坐标整段泡 + 页顶跳 y≈16)
- **根因**:合成/过渡态 contextmenu 的 `clientX/Y` 为 (0,0) 或非有限数时,
  `caretRangeFromPoint` 映射失败直接回落行级,且把坏坐标当 point 偏移;
  块零 rect(detached 旧块/过渡态)时 Floating UI 以零参考点定位,泡甩到页顶。
- **修法**(`doc-editor.js`):
  1. contextmenu handler:坐标无效 → 按块矩形中心再试一次 `_pointAnchor`
     (caret 映射);仍不成 → 行级锚点,且 point 传 null(参考点回落块右上旧语义,
     不拿坏坐标当偏移);该行已有行级泡 → `openBubble` 同锚点聚焦不新建(既有语义)。
  2. `_fitBubble` 开头零 rect 守卫:块 rect 宽高皆 0 → 跳本拍 +
     `requestAnimationFrame` 下一帧重试(stub/无 rAF 环境不守卫,照旧跑)。
  3. 重开路径(existing):`_fitBubble` 前 `if (!existing.el.isConnected)
     _rehangShells()`——壳被布局重渲摘出时先挂回再定位。

### F3(E6 长线程泡高度无上限)
- **根因**:maxHeight = clamp(200, 45vh, 可用−16),45vh 在高大屏上形同无顶
  (900px 视口即 405px)。
- **修法**(`doc-editor.js` `_fitBubble` size apply):
  `maxH = max(200, min(min(380, 45vh), 可用−16))`——380px 绝对上限,超出 log 内滚。
  `docs/WIDGET-DESIGN.md` §3.13 壳几何契约与验收清单同步,加 v3.2 实现注。

### F4(E7 连发吞消息)
- **根因**:`_submit` 只读 `state.draft`;直写 textarea.value(脚本/快速连发)
  不经 input 事件时 draft 滞后,后一条被空 draft 顶掉。
- **修法**(`w-bubble.js` `_submit`):发送时读 composer DOM 当前值
  (`host.querySelector("[data-bubble-draft]").value`,只采信 TEXTAREA/INPUT——
  stub region 面的 div 也带空 value 不能当真),DOM 不可得才回落 state.draft;
  retry 仍按 lastText。发送钮只按空输入禁用(busy 不禁用,v2 已是,本次复核未动)。

## 二、变更清单

| 文件 | 改动 |
|---|---|
| `agent_os/src/agent_os/host/web_platform/static/doc-editor.js` | F1 去右缘 clamp(此前已落);F2a handler 无效坐标回落链;F2b `_fitBubble` 零 rect 守卫 + rAF 重试;F2b 重开先 `_rehangShells()`;F3 size apply 380 上限 + 头注契约 |
| `agent_os/src/agent_os/host/web/static/js/widgets/w-bubble.js` | F4 `_submit` 读 composer DOM 值(tagName 守卫);后段重复 `const ta` 归并 |
| `docs/WIDGET-DESIGN.md` | §3.13 壳几何契约加 380px、验收清单加 v3.2 行、新增 v3.2 实现注 |
| `agent_os/src/agent_os/host/web/static/tests-ui/test_doceditor.py` | 新增 `run_bubble_v32(t)` 并 wire 进 `run()`(14 条断言 + 3 截图) |
| BUILD bump(4 文件 32 处)| `widget.html`/`compound.html`/`widget-sandbox.js`/`desktop.html`:2026-08-12.4 → 2026-08-13.1 |

## 三、测试汇总(全绿)

- **stub(node,32 文件)**:`tests/*.test.mjs` 全过(/usr/bin/node)。
  中途发现 F4 初版误采信 stub region div 的空 value(compound/lab-iterate/
  widgets 三例红),加 tagName 守卫后回绿——该守卫对真浏览器无影响。
- **pytest**:`tests/web_platform` 101 passed。
- **tests-ui(playwright,1400×900)**:全套通过(含 test_doceditor 单模块复跑);
  新增 v3.2 断言组:
  - F1:泡左缘 = 点击点 ±60px;不吸行右缘(pop_x < block_right−100);纵向在点击行下方;
  - F2:合成 contextmenu(0,0)首发开泡(回落链建成一只)、可见泡 y>50 不页顶跳、
    该块已有泡时聚焦不新建(children_snapshot 数不变);
  - F3:30 条回复灌入,泡高 ≤ min(380, 45vh)=380(视口 900 下 45vh=405,
    380 是制约项,能有效区分新旧行为);`.w-bubble-log` scrollHeight > clientHeight;
  - F4:直写 composer value(不发 input 事件)+ Enter 连发 5 条全入流。
- **截图**(`.shots/`):`bubble-v32-point.png`(点旁开泡,目检:泡贴身点击点、
  箭头指点位)、`bubble-v32-capped.png`(380 封顶内滚,滚底停在第 26–29 条)、
  `bubble-v32-burst.png`(连发 5 条全在流)。

## 四、偏差与遗留

- 刷新后标记延迟出现:裁决为可接受,未修。
- 测试残留批注(demo.test 的 v32 种子消息):由用户自行清理,未动 instance/。
- F2 块中心重试成功时会建点锚点泡(裁决允许);测试用首块(标题语法块)使中心
  映射被既有规则挡下,稳定走行级回落,断言与实现语义一致。
- 未做 git 操作;`instance/` 的 diff 为运行中服务自写,未触碰。

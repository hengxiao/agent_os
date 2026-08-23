# tests-ui —— 真实浏览器 UI 测试

stub DOM 测试(`../tests/*.test.mjs`)测不出的那一层:模块加载、真实 DOM 挂载、
焦点/选区、事件委托、层叠、弹层定位——**date 三 bug 与 compound 首崩都是 stub 全绿、
真实浏览器全坏的实例**。本目录用 Playwright + headless Chromium 补这层。

## 跑

```bash
cd agent_os/src/agent_os/host/web/static/tests-ui
./run.sh                  # 自起静态服务器(服务 web/ 根),跑全部 test_*.py
./run.sh test_compound    # 只跑一个
BASE=http://127.0.0.1:8391 ./run.sh   # 打已部署的服务器(页面路径 /static/...)
```

退出码:0 全绿,1 有失败。每个用例打印 ✓/✗ 明细;console error、pageerror、
HTTP ≥400 都会被收集进「无 JS 错误」断言。

## 数据 fixture(seed)

test_desktop 的「哪些失败/为什么挂」流程需要系统里**至少有一条成功 run 和一条
失败 run**;数据重置(清 sessions/drafts/runs/traces)后系统裸奔,这两步会超时。
重置后跑一次:

```bash
python3 seed.py   # 缺省打 http://127.0.0.1:8391;需服务 token 有效(15 分钟窗内)
```

种子 = 1 条成功 run(API 真跑 demo.fib)+ 1 条失败 run 记录(落盘 fixture,
原因写在 seed.py 头注:「启动后跑挂」的 run API 造不出来)。

## 环境(全部项目内,不入库,已 gitignore)

| 目录 | 内容 | 重建 |
|---|---|---|
| `.venv-ui/` | Python venv + playwright 包 | `python3 -m venv .venv-ui && .venv-ui/bin/pip install playwright` |
| `.browsers/` | chromium-headless-shell(`PLAYWRIGHT_BROWSERS_PATH` 指到这里) | `PLAYWRIGHT_BROWSERS_PATH=$PWD/.browsers .venv-ui/bin/playwright install chromium-headless-shell` |
| `.syslibs/` | 系统库本地提取(nspr/nss/atk/X11 等,无 sudo 方案) | 见下 |
| `.rpms/` | 上述库的 RPM 包 | `dnf download --destdir=.rpms <pkg...>` |

.syslibs 重建(机器无 cpio,用 python 解 newc):

```bash
dnf download --destdir=.rpms nspr nss nss-util atk at-spi2-atk at-spi2-core \
  alsa-lib libgbm libXcomposite libXdamage libXfixes libXrandr libdrm libXi
# rpm2cpio <rpm> 输出 newc cpio,用 run 时随附的解包逻辑(或重跑本 README 历史里的脚本)
```

验证环境完好:`ldd .browsers/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell | grep "not found"` 应为空。

## 写新用例

`test_<name>.py` 导出 `run(t)`:

- `t.open(path)` — 打开页面,重置错误收集;`t.page` 是 Playwright Page
- `t.check(name, cond, detail="")` — 一项断言
- `t.no_errors()` — 断言无 console error / pageerror / HTTP≥400

纪律:

- **textarea 的内容在 `.input_value()`,不在 `inner_text()`**(test_sandbox 踩过);
- 轮询型面板(State 500ms)留足等待;
- 选择器以页面真实 id/class 为准,写用例前先 grep 一遍,别猜。

## 共享活服务器治理(BASE=8391 模式;2026-08-13)

打活服务器 = 共享可变状态:会话/版本/批注在跑次与人工冒烟之间**累积**,
断言依赖「最新」「唯一」「从没被碰过」都会变 flake。纪律(治本,不加 sleep):

- **夹具隔离**:test_doceditor 用**专用文档 `dev.uitest`**(`DOC` 常量),
  不与人共用的 demo.test;每段开跑前 `ensure_doc(pg)`(不存在则建
  FIXTURE_TEXT,存在则 restore 最旧快照复位文本——版本链累积无害,
  断言一律不依赖绝对版本号)+ `clean_annotations(pg)`(清批注,两面)。
  **顺序必须先复位/清库再打开文档**(种子在打开时读取,P2 踩过)。
- **会话类断言显式新建**:不假设「最新会话」——test_desktop ③ 留证段先
  `POST /platform/api/sessions` 拿 sid,`#dt-sessions` 按 value 选中再开窗,
  且全程用**窗口作用域选择器**(多窗并存时全局 `[data-cv-input]` 会撞);
  用完两段 ✕ 关掉,不占后续段落视野。
- **LLM 等待 = 条件等待 + 给足超时 + 失败有信息**:generate 链等 Diff
  用 `wait_for_selector(timeout=240000)`,超时时 dump 按钮态/错误收集再
  抛(见 run_generate_p3 的 try/except 模式);
- **失败明细直打**:run.py 每模块末尾打印全部失败明细(含 traceback),
  排障不需要复跑。


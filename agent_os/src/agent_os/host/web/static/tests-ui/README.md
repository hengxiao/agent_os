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

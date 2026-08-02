/* 全局键盘接线(docs/WEB-UI.md §5 键盘):
     全局:`/` 聚焦当前页搜索框;`?` 快捷键面板;Esc 关闭浮层(各浮层自管);
           ⌘K/Ctrl+K 命令条(输入框聚焦时也生效);
     Workbench:`j`/`k` 下/上一条信号;`gg`/`G` 首/尾条;⌘J/Ctrl+J 定位首个错误(异常 run)。
   不劫持输入控件(§5):textarea/input/select/contenteditable 聚焦时,
   除 Esc(浮层自管)与 ⌘K 外一律不生效;⌘J 也在此豁免之外(不生效)。
   有浮层(.modal-overlay)打开时,除 ⌘K 外不穿透到底层页面。 */

/* 输入控件判定(node 单测可载) */
export function isEditableTarget(t) {
  if (!t || typeof t !== "object") return false;
  const tag = String(t.tagName ?? "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable === true;
}

/* gg 连击窗口(ms) */
const GG_WINDOW = 500;

/* installGlobalKeys(handlers, { doc, hasOverlay }):注册一次全局 keydown;
   handlers 全部可选:onPalette / onHelp / onSearchFocus /
   onSignalStep(delta) / onSignalEdge("first"|"last") / onJumpError。
   返回卸载函数(幂等)。 */
export function installGlobalKeys(handlers = {}, { doc = document, hasOverlay } = {}) {
  let lastG = 0;
  const overlayOpen = () =>
    (hasOverlay ? hasOverlay() : Boolean(doc.querySelector?.(".modal-overlay"))) === true;

  const onKeydown = (e) => {
    if (e.defaultPrevented) return;
    const key = e.key;
    const mod = e.metaKey || e.ctrlKey;

    // ⌘K / Ctrl+K:始终生效(输入框聚焦 / 浮层打开均可,toggle 语义由调用方实现)
    if (mod && (key === "k" || key === "K")) {
      e.preventDefault?.();
      handlers.onPalette?.();
      return;
    }
    if (key === "Escape") return; // 各浮层自管 Esc
    if (isEditableTarget(e.target)) return; // 不劫持输入控件(⌘J 同样不生效)
    if (overlayOpen()) return; // 浮层打开:其余键不穿透
    if (mod && (key === "j" || key === "J")) {
      e.preventDefault?.(); // 屏蔽浏览器下载栏等默认行为
      handlers.onJumpError?.();
      return;
    }
    if (mod || e.altKey) return; // 其余带修饰键的组合放行(浏览器/系统快捷键)

    if (key === "/") {
      e.preventDefault?.(); // 不入字
      handlers.onSearchFocus?.();
    } else if (key === "?") {
      e.preventDefault?.();
      handlers.onHelp?.();
    } else if (key === "j") {
      handlers.onSignalStep?.(1);
    } else if (key === "k") {
      handlers.onSignalStep?.(-1);
    } else if (key === "g") {
      const now = Date.now();
      if (now - lastG < GG_WINDOW) {
        lastG = 0;
        handlers.onSignalEdge?.("first");
      } else {
        lastG = now;
      }
    } else if (key === "G") {
      handlers.onSignalEdge?.("last");
    }
  };
  doc.addEventListener("keydown", onKeydown);
  let off = false;
  return () => {
    if (off) return;
    off = true;
    doc.removeEventListener("keydown", onKeydown);
  };
}

/* DOM stub(D3 冒烟测试用):launch-dialog / progress-bar 用到的最小 DOM 面。
   只实现被测代码真实消费的 API:createElement / appendChild / remove /
   classList / dataset / setAttribute / addEventListener·removeEventListener·trigger /
   closest / contains / querySelector(简单选择器:.class / #id / tag / [data-x])/
   innerHTML(仅存字符串,不解析)/ textContent / value / disabled / hidden / focus。
   不做:布局、样式计算、事件冒泡(trigger 只打当前目标监听器)。 */

export class StubEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    this.textContent = "";
    this.value = "";
    this.title = "";
    this.disabled = false;
    this.hidden = false;
    this.rows = 0;
    this.spellcheck = true;
    this.placeholder = "";
    this.focused = false;
    this.ownerDocument = null;
    this._innerHTML = "";
    this._classes = new Set();
  }

  get className() {
    return [...this._classes].join(" ");
  }

  set className(v) {
    this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }

  get classList() {
    const s = this._classes;
    return {
      add: (...cs) => cs.forEach((c) => s.add(c)),
      remove: (...cs) => cs.forEach((c) => s.delete(c)),
      toggle: (c, force) => {
        const want = force ?? !s.has(c);
        if (want) s.add(c);
        else s.delete(c);
        return want;
      },
      contains: (c) => s.has(c),
    };
  }

  set innerHTML(v) {
    this._innerHTML = String(v);
    this.children = []; // 不解析:子树清空(被测代码的后续查询会拿到 null 并走守卫)
  }

  get innerHTML() {
    return this._innerHTML;
  }

  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k.startsWith("data-")) {
      const key = k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      this.dataset[key] = String(v);
    }
  }

  getAttribute(k) {
    return this.attributes[k] ?? null;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  remove() {
    const p = this.parentNode;
    if (p) {
      const i = p.children.indexOf(this);
      if (i >= 0) p.children.splice(i, 1);
    }
    this.parentNode = null;
  }

  contains(other) {
    for (let n = other; n; n = n.parentNode) if (n === this) return true;
    return false;
  }

  matches(sel) {
    const s = String(sel).trim();
    if (s.startsWith("[data-") && s.endsWith("]")) {
      const key = s.slice(6, -1).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      return this.dataset[key] !== undefined;
    }
    if (s.startsWith(".")) return this._classes.has(s.slice(1));
    if (s.startsWith("#")) return this.attributes.id === s.slice(1);
    return this.tagName === s.toUpperCase();
  }

  closest(sel) {
    for (let n = this; n; n = n.parentNode) if (n.matches?.(sel)) return n;
    return null;
  }

  querySelector(sel) {
    const walk = (node) => {
      for (const c of node.children) {
        if (c.matches(sel)) return c;
        const hit = walk(c);
        if (hit) return hit;
      }
      return null;
    };
    return walk(this);
  }

  addEventListener(type, fn) {
    (this.listeners[type] ??= []).push(fn);
  }

  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn);
  }

  /* 测试侧驱动:在当前目标上触发监听器(不冒泡;ev 可覆盖 target 等) */
  trigger(type, ev = {}) {
    for (const fn of this.listeners[type] ?? []) fn({ target: this, ...ev });
  }

  focus() {
    this.focused = true;
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
}

export function makeDocument() {
  const listeners = {};
  const doc = {
    body: null,
    activeElement: null,
    createElement(tag) {
      const el = new StubEl(tag);
      el.ownerDocument = doc;
      return el;
    },
    addEventListener(type, fn) {
      (listeners[type] ??= []).push(fn);
    },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] ?? []).filter((f) => f !== fn);
    },
    trigger(type, ev = {}) {
      for (const fn of listeners[type] ?? []) fn({ ...ev });
    },
    listenerCount(type) {
      return (listeners[type] ?? []).length;
    },
  };
  doc.body = new StubEl("body");
  doc.body.ownerDocument = doc;
  return doc;
}

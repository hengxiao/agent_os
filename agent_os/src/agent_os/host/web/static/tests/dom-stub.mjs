/* DOM stub(D3/D4/D5 冒烟测试用):被测代码真实消费的最小 DOM 面。
   实现:createElement / appendChild / remove / classList / dataset /
   setAttribute / addEventListener·removeEventListener·trigger / closest / contains /
   querySelector·querySelectorAll(简单选择器:.class / #id / tag / [data-x])/
   innerHTML / textContent / value / disabled / hidden / focus / scrollIntoView /
   滚动面(scrollTop·clientHeight·scrollHeight,窗口化冒烟用)。
   innerHTML 仅存字符串不解析;D5 起支持"区域提取":querySelector 简单选择器
   若在 innerHTML 串中命中(id="x" / class 含 x / <tag / [data-x]=),返回持久
   虚拟元素(可写 innerHTML、可挂监听、可被断言),无命中返回 null。
   不做:布局、样式计算、事件冒泡(trigger 只打当前目标监听器)、复合选择器。 */

const escapeRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

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
    this.isContentEditable = false;
    this.scrollTop = 0;
    this.clientHeight = 600;
    this.scrollHeight = 0;
    this.ownerDocument = null;
    this._innerHTML = "";
    this._classes = new Set();
    this._regions = new Map(); // 选择器 → 虚拟元素(innerHTML 串内区域,D5)
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
    this.children = []; // 不解析:子树清空(真实子元素随之失效)
    this._regions = new Map(); // 区域提取缓存随内容重建
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

  /* 区域提取(D5):简单选择器在 innerHTML 串中命中时返回持久虚拟元素;
     持久化保证"写入 innerHTML / 挂监听 / 后续断言"对同一区域生效。 */
  _regionFor(sel) {
    const s = String(sel).trim();
    let pattern = null;
    if (s.startsWith("#")) pattern = new RegExp(`id="${escapeRe(s.slice(1))}"`);
    else if (s.startsWith(".")) pattern = new RegExp(`class="[^"]*\\b${escapeRe(s.slice(1))}\\b`);
    else if (s.startsWith("[data-") && s.endsWith("]") && !s.includes("=")) {
      pattern = new RegExp(`${escapeRe(s.slice(1, -1))}=`);
    } else if (/^[a-zA-Z][\w-]*$/.test(s)) pattern = new RegExp(`<${escapeRe(s)}[\\s>]`);
    if (!pattern || !pattern.test(this._innerHTML)) return null;
    if (!this._regions.has(s)) {
      const el = new StubEl("div");
      el.ownerDocument = this.ownerDocument;
      el.parentNode = this; // closest() 可上溯到宿主
      this._regions.set(s, el);
    }
    return this._regions.get(s);
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
    return walk(this) ?? this._regionFor(sel);
  }

  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      for (const c of node.children) {
        if (c.matches(sel)) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out; // 不做区域提取(行集断言走 innerHTML 字符串)
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

  scrollIntoView() {} // 布局无关 noop(真实浏览器负责滚动)

  select() {} // input 全选 noop
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

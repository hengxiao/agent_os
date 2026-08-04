/* WidgetDef 注册表(docs/WIDGETS.md §1.2;与 AppManifest 同哲学但更小)。

   校验(注册即过闸,非法拒注册 → Error):
   - kind/v(=1)/state_schema(object schema)齐;
   - actions 结构齐(id 必填),且**只允许 local**——endpoint/run 出现即拒
     (§1.3 铁律 1:widget 永不直接调后端,出海只有 events,授权面只在 app 层);
   - events 是字符串清单(未声明的事件实例侧 emit 不发,见 widget.js);
   - aria.role 必填(商业 widget 标准);surfaces ⊆ {card, tab} 且非空;
   - **context_provider 必有**(§17.7-3:"所有 widget 都有 context"的注册面)——
     缺省给内置缺省(kind + state 摘要),声明者可覆盖;给了非函数 = 不合规拒。 */

const _defs = new Map();

/* 缺省 context fragment:kind + state 摘要(紧凑、可 JSON 序列化;
   完整 state 由声明方自己的 provider 给出,缺省只给"我是谁+大概状态") */
function _defaultProvider(kind) {
  return (state) => {
    let summary = "";
    try {
      summary = JSON.stringify(state ?? {}).slice(0, 120);
    } catch {
      summary = String(state); // 循环引用等:降级为字符串,不炸级联
    }
    return { kind, state_summary: summary };
  };
}

export function registerWidgetDef(def) {
  const kind = def?.kind;
  if (!kind || typeof kind !== "string") throw new Error("WidgetDef 缺 kind");
  if (def.v !== 1) throw new Error(`widget ${kind}: v 应为 1,得到 ${def.v}`);
  if (def.state_schema?.type !== "object") {
    throw new Error(`widget ${kind}: state_schema 必须是 object schema`);
  }
  for (const action of def.actions ?? []) {
    if (!action?.id) throw new Error(`widget ${kind}: action 缺 id`);
    if (action.exec !== "local") {
      throw new Error(
        `widget ${kind}: action ${action.id} 的 exec 必须是 local(铁律 1:widget 不出海),得到 ${action.exec}`
      );
    }
  }
  if (!Array.isArray(def.events) || def.events.some((e) => typeof e !== "string")) {
    throw new Error(`widget ${kind}: events 必须是字符串清单`);
  }
  if (!def.aria?.role) throw new Error(`widget ${kind}: aria.role 必填`);
  const faces = def.surfaces ?? [];
  if (!faces.length || faces.some((f) => !["card", "tab"].includes(f))) {
    throw new Error(`widget ${kind}: surfaces 非法: ${faces}`);
  }
  // §17.7-3:缺省 provider 兜底,声明者可覆盖;非函数 = 协议不合规
  if (def.context_provider === undefined || def.context_provider === null) {
    def.context_provider = _defaultProvider(kind);
  }
  if (typeof def.context_provider !== "function") {
    throw new Error(`widget ${kind}: context_provider 必须是函数(§17.7-3)`);
  }
  // W5.1(docs/WIDGET-ARCH.md §1):render 面校验——声明了 render 的 def
  // 必须是 ``state → html 字符串`` 的纯函数(自渲染件;副作用纪律在
  // render 文件注释 + 评审,注册期只能验形态)
  if (def.render !== undefined && typeof def.render !== "function") {
    throw new Error(`widget ${kind}: render 必须是纯函数(state→html;W5.1)`);
  }
  _defs.set(kind, def);
  return def;
}

export function getWidgetDef(kind) {
  return _defs.get(kind) ?? null;
}

export function listWidgetKinds() {
  return [..._defs.keys()].sort();
}

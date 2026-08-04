/* WidgetDef 注册表(docs/WIDGETS.md §1.2;与 AppManifest 同哲学但更小)。

   校验(注册即过闸,非法拒注册 → Error):
   - kind/v(=1)/state_schema(object schema)齐;
   - actions 结构齐(id 必填),且**只允许 local**——endpoint/run 出现即拒
     (§1.3 铁律 1:widget 永不直接调后端,出海只有 events,授权面只在 app 层);
   - events 是字符串清单(未声明的事件实例侧 emit 不发,见 widget.js);
   - aria.role 必填(商业 widget 标准);surfaces ⊆ {card, tab} 且非空。 */

const _defs = new Map();

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
  _defs.set(kind, def);
  return def;
}

export function getWidgetDef(kind) {
  return _defs.get(kind) ?? null;
}

export function listWidgetKinds() {
  return [..._defs.keys()].sort();
}

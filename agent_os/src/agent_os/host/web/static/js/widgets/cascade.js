/* Context Cascade 运行时(docs/APP-MODEL.md §16;W2 首个落地)。

   动作触发时,沿 §14 路径**逐级向上**收集上下文:每个祖先级若有注册的
   context_provider,就贡献一个 fragment,组成级联信封:
   ``{trigger, cascade: [{scope, path, data}, ...]}``(近→远)。

   纪律(§16.1):
   - 每级只出自己的 fragment(provider 是控件/宿主声明的一部分,
     ``fn({path, trigger}) → data``);
   - **级联单向向上,不横向打听**:只有触发路径的祖先前缀能命中 provider
     (上下文边界 = 树边界 = 权限边界);
   - 级数裁剪:``levels``(scope 白名单;缺省全链)——轻动作不背大信封;
   - 缺 provider 的级**跳过不炸**;
   - 本模块纯本地(widgets 铁律:零 fetch——信封的出海由父组件完成)。 */

const _providers = []; // {prefix, scope, fn}

/* 注册某级路径前缀的 context_provider;返回注销函数(注销随 widget destroy)。

   §17.7-3(注册面启用):同 (prefix, scope) 重复注册 = **替换**(最新赢)——
   宿主重挂载(doc tab reload 等)不会在同一路径堆叠过期闭包;注销只摘自己
   (若已被替换则不动新注册)。 */
export function registerContextProvider(prefix, scope, fn) {
  const entry = { prefix, scope, fn };
  const stale = _providers.findIndex((p) => p.prefix === prefix && p.scope === scope);
  if (stale >= 0) _providers.splice(stale, 1); // 同位替换(重挂载语义)
  _providers.push(entry);
  return () => {
    const i = _providers.indexOf(entry);
    if (i >= 0) _providers.splice(i, 1);
  };
}

/* 沿触发路径向上逐级收集(近→远;provider 在自己的 prefix 级贡献,
   与 §16 信封一致——app fragment 在 app 路径,不在中间级) */
export function contextCascade(triggerPath, { levels = null, providers = _providers } = {}) {
  const cascade = [];
  const sorted = [...providers].sort((a, b) => b.prefix.length - a.prefix.length); // 近→远
  for (const p of sorted) {
    const isAncestor = triggerPath === p.prefix || String(triggerPath).startsWith(p.prefix + "/");
    if (!isAncestor) continue; // 单向向上:非祖先链不看(无横向)
    if (levels && !levels.includes(p.scope)) continue; // 级数裁剪
    try {
      const data = p.fn({ path: p.prefix, trigger: triggerPath });
      if (data && typeof data === "object") {
        cascade.push({ scope: p.scope, path: p.prefix, data });
      }
    } catch {
      // provider 异常 = 该级缺席(不炸级联)
    }
  }
  return { trigger: triggerPath, cascade };
}

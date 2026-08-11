/* node ESM 加载钩子(platform 测试用):浏览器里 web_platform 的静态文件以
   绝对路径 "/static/js/"(宿主无关共享模块:themes/util)与 "/static/vendor/"
   (vendored 三方库,widget-libs)import,node 直跑不认识该前缀——在此映射到
   旧 web static 下的真实文件。
   用法:测试文件顶部 ``await register("./platform-loader.mjs", import.meta.url)``,
   然后 ``await import("../js/components 或 web_platform 的模块)``。 */

import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const STATIC = path.resolve(fileURLToPath(new URL("../", import.meta.url)));

export async function resolve(specifier, context, next) {
  if (specifier.startsWith("/static/js/") || specifier.startsWith("/static/vendor/")) {
    return next(
      pathToFileURL(path.join(STATIC, specifier.slice("/static/".length))).href,
      context,
    );
  }
  return next(specifier, context);
}

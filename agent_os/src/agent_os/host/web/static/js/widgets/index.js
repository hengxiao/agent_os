/* Widget 协议面(docs/WIDGETS.md §1):注册表 + 实例工厂 + W1 两控件。
   铁律(§1.3):本目录禁止 fetch((静态扫描进 widgets 测试);
   事件上行,数据下行,widget 不知道 app 存在。 */

export { registerWidgetDef, getWidgetDef, listWidgetKinds } from "./registry.js";
export { createWidget, preserveSelection, bindCardOpen } from "./widget.js";
export { registerContextProvider, contextCascade } from "./cascade.js";
export { createCompound } from "./compound.js"; // C1:compound 基座(docs/COMPOUND-WIDGET.md)
export { TEXT_EDITOR_DEF, mountTextEditor } from "./w-text.js";
export { renderTextEditor, textEditorMicro, relTime } from "./w-text.render.js";
export { renderJsonEditor, jsonHighlightHtml, jsonKeyCount, jsonOkText } from "./w-json.render.js";
export {
  JSON_EDITOR_DEF_KIND,
  locateJsonError,
  jsonErrorAt,
  schemaErrorAt,
  formatJson,
  matchBrace,
  mountJsonEditor,
} from "./w-json.js";
export { TABLE_EDITOR_DEF, mountTableEditor } from "./w-table.js";
export { renderTableEditor } from "./w-table.render.js";
export { KV_EDITOR_DEF, mountKvEditor, entriesToObject, objectToEntries, dupKeys } from "./w-kv.js";
export { renderKvEditor } from "./w-kv.render.js";
export { BUBBLE_DEF, mountBubble } from "./w-bubble.js";
export { FORM_EDITOR_DEF, mountFormEditor, validateValues } from "./w-form.js";
export { renderFormEditor } from "./w-form.render.js";
export { LIST_EDITOR_DEF, mountSelectList } from "./w-list.js";
export { renderSelectList, visibleItems } from "./w-list.render.js";
export { TREE_EDITOR_DEF, mountNsTreeWidget } from "./w-tree.js";
export { renderTreeWidget } from "./w-tree.render.js";
export { DATE_EDITOR_DEF, mountDatePicker, parseIso, quickRange, rangeInverted, monthGridHtml } from "./w-date.js";
export { renderDatePicker } from "./w-date.render.js";
export { DIFF_VIEWER_DEF, mountDiffViewer, diffBodyHtml } from "./w-diff.js";
export { renderDiffViewer } from "./w-diff.render.js";
export { MD_VIEWER_DEF, mountMarkdownViewer, mdToHtml, looksMarkdown } from "./w-md.js";
export { renderMarkdownViewer } from "./w-md.render.js";
export { LOG_VIEWER_DEF, mountLogViewer } from "./w-log.js";
export { renderLogViewer } from "./w-log.render.js";
export { CHART_DEF, mountChart, chartSvg, chartTableHtml, downsample, niceTicks } from "./w-chart.js";
export { renderChart, chartTipHtml } from "./w-chart.render.js";
export { renderBubble } from "./w-bubble.render.js";

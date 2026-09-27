class_name AgentOsClient
extends Node

## Agent OS Web Platform 的 REST 客户端(web_platform/app.py 的端点契约)。
## 只负责出海;动作语义(白名单/闸门/promote)全部在服务端,客户端零裁决。
## 默认 base_url = 本地实例(run-web.sh 缺省 8391),平台挂载前缀 /platform。
##
## 所有方法返回统一信封:{ok: bool, status: int, json: Variant} 或
## {ok: false, status: int, error: String}(FastAPI 的 {detail} 已拆包)。

var base_url := "http://127.0.0.1:8391"

const DEFAULT_TIMEOUT := 30.0
const LLM_TIMEOUT := 180.0 # comment/chat/review 起 LLM run,给宽松上限


func _send(method: int, path: String, body: Variant, timeout: float) -> Dictionary:
	var req := HTTPRequest.new()
	req.timeout = timeout
	add_child(req)
	var data := ""
	var headers := PackedStringArray()
	if body != null:
		data = JSON.stringify(body)
		headers.append("Content-Type: application/json")
	var err := req.request(base_url + path, headers, method, data)
	if err != OK:
		req.queue_free()
		return {"ok": false, "status": 0, "error": "HTTP 发起失败: %s" % error_string(err)}
	var r: Array = await req.request_completed
	req.queue_free()
	var code: int = r[1]
	var text: String = (r[3] as PackedByteArray).get_string_from_utf8()
	if r[0] != HTTPRequest.RESULT_SUCCESS or code >= 400:
		return {"ok": false, "status": code, "error": _extract_detail(text, code)}
	var parsed: Variant = null
	if not text.is_empty():
		parsed = JSON.parse_string(text)
	return {"ok": true, "status": code, "json": parsed}


func _extract_detail(text: String, code: int) -> String:
	var j: Variant = JSON.parse_string(text) if not text.is_empty() else null
	if j is Dictionary and j.has("detail"):
		return "%d: %s" % [code, str(j["detail"])]
	return "%d: %s" % [code, text.left(200)]


## 便捷解包:ok 时返回 json,否则返回默认值(读面用;写面请检查 ok)
static func json_or(resp: Dictionary, fallback: Variant) -> Variant:
	if resp.get("ok", false):
		var j: Variant = resp.get("json")
		if j != null:
			return j
	return fallback


# ---------------------------------------------------------------- docs 读面(web_platform/app.py)

## GET /platform/api/docs:文档索引(标题/首行/字数/最近编辑)
func list_docs() -> Dictionary:
	return await _send(HTTPClient.METHOD_GET, "/platform/api/docs", null, DEFAULT_TIMEOUT)


## POST /platform/api/docs {name,title?,text?}:新建(点分名校验在 store;重名 409)
func create_doc(doc_name: String, title := "", text := "") -> Dictionary:
	return await _send(HTTPClient.METHOD_POST, "/platform/api/docs",
		{"name": doc_name, "title": title, "text": text}, DEFAULT_TIMEOUT)


## GET /platform/api/docs/{name}:全文 + meta + versions + chat 种子
func read_doc(doc_name: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_GET,
		"/platform/api/docs/" + doc_name.uri_encode(), null, DEFAULT_TIMEOUT)


## GET /platform/api/docs/{name}/bubbles:全文档气泡流(服务端事实源)
func read_bubbles(doc_name: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_GET,
		"/platform/api/docs/" + doc_name.uri_encode() + "/bubbles", null, DEFAULT_TIMEOUT)


## POST …/comment {anchor,text,cascade} → {reply,edits}(D2 专属端点先例)
func send_comment(doc_name: String, anchor: String, text: String, cascade: Array) -> Dictionary:
	return await _send(HTTPClient.METHOD_POST,
		"/platform/api/docs/" + doc_name.uri_encode() + "/comment",
		{"anchor": anchor, "text": text, "cascade": cascade}, LLM_TIMEOUT)


## POST …/chat {text} → {reply,changed};changed=服务端全文对比(不信技能自报)
func send_chat(doc_name: String, text: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_POST,
		"/platform/api/docs/" + doc_name.uri_encode() + "/chat",
		{"text": text}, LLM_TIMEOUT)


## GET …/versions/{version}:读版本快照内容(只读;回溯卷轴 diff 提示数据面)
func read_doc_version(doc_name: String, version: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_GET,
		"/platform/api/docs/" + doc_name.uri_encode() + "/versions/" + version.uri_encode(),
		null, DEFAULT_TIMEOUT)


## GET …/versions/tree:版本树(parent 链 + 工作稿祖版;v1.8 树状模型)
func read_version_tree(doc_name: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_GET,
		"/platform/api/docs/" + doc_name.uri_encode() + "/versions/tree", null, DEFAULT_TIMEOUT)


## GET …/diff-summary?from=vA&to=vB:版本差异的 LLM 人话摘要(difsum 缓存在服务端)
func diff_summary(doc_name: String, from_v: String, to_v: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_GET,
		"/platform/api/docs/" + doc_name.uri_encode() + "/diff-summary?from="
			+ from_v.uri_encode() + "&to=" + to_v.uri_encode(),
		null, LLM_TIMEOUT)


## POST …/generate {baseVersion?,userPrompt?}:批注批处理生成下一版
## (pending 批注 + chat 上下文 → LLM 完整新文档 → 自动快照新版本 + 批注逐条标状态 +
## 重锚定;版本冲突 409;不合解析重试一次再败 502,不半截落库)
func generate_doc(doc_name: String, user_prompt := "") -> Dictionary:
	var body := {}
	if not user_prompt.is_empty():
		body["userPrompt"] = user_prompt
	return await _send(HTTPClient.METHOD_POST,
		"/platform/api/docs/" + doc_name.uri_encode() + "/generate", body, LLM_TIMEOUT)


## POST …/review:全文评审 → 锚点批注集自动挂段(带 severity)
func review_doc(doc_name: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_POST,
		"/platform/api/docs/" + doc_name.uri_encode() + "/review", {}, LLM_TIMEOUT)


# ---------------------------------------------------------------- 批注(P2 annotations:纯用户批注,无即时 AI 回复,攒着批处理)

## GET …/annotations:批注列表(新记录 + bubbles/ 旧流压缩迁移,同锚点新优先)
func read_annotations(doc_name: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_GET,
		"/platform/api/docs/" + doc_name.uri_encode() + "/annotations", null, DEFAULT_TIMEOUT)


## POST …/annotations {anchor,quote,content}:单条 upsert(创建=pending;重编覆盖回 pending)
func save_annotation(doc_name: String, anchor: String, quote: String, content: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_POST,
		"/platform/api/docs/" + doc_name.uri_encode() + "/annotations",
		{"anchor": anchor, "quote": quote, "content": content}, DEFAULT_TIMEOUT)


## POST …/bubbles/delete {anchor}:删除(bubbles/ 旧流 + annotations/ 新记录两面都删,幂等)
func delete_annotation(doc_name: String, anchor: String) -> Dictionary:
	return await _send(HTTPClient.METHOD_POST,
		"/platform/api/docs/" + doc_name.uri_encode() + "/bubbles/delete",
		{"anchor": anchor}, DEFAULT_TIMEOUT)


# ---------------------------------------------------------------- app 管道(APP-MODEL §4)

## POST /platform/api/apps/spawn {kind,ref,title,state,created_by} → AppInstance(kind+ref 去重)
func spawn(kind: String, ref_name: String, title: String, state: Dictionary, created_by := "") -> Dictionary:
	var resp := await _send(HTTPClient.METHOD_POST, "/platform/api/apps/spawn", {
		"kind": kind,
		"ref": ref_name,
		"title": title if not title.is_empty() else ref_name,
		"state": state,
		"created_by": created_by,
	}, DEFAULT_TIMEOUT)
	if resp.get("ok", false) and resp.get("json") is Dictionary:
		var inst = resp["json"].get("instance")
		if inst is Dictionary:
			resp["app"] = ActionPipeline.AppInstance.from_json(inst)
	return resp


## POST /platform/api/apps/{id}/actions/{action}(surface/args/session_id/cascade)
func invoke_action(instance_id: String, action_id: String, body: Dictionary) -> Dictionary:
	return await _send(HTTPClient.METHOD_POST,
		"/platform/api/apps/" + instance_id.uri_encode() + "/actions/" + action_id.uri_encode(),
		body, DEFAULT_TIMEOUT)


## 连通性探针(sessions 数组读面;兼作状态点数据源)
func ping() -> bool:
	var resp := await _send(HTTPClient.METHOD_GET, "/platform/api/sessions", null, 8.0)
	return resp.get("ok", false)

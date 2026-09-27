class_name SseClient
extends Node

## SSE 客户端:GET /platform/api/stream(M4b;decision.new/run.finished/keepalive 帧)。
## 用 HTTPClient 分块读流,按 "event:/data:" 帧解析;断线只上报,重连策略在宿主
## (回落 5s 轮询是 web 前端的既有语义,此处不替它做决定)。
## Doc Editor v1 未消费本通道(平台流暂无 doc 事件);这是内核给后续 app 备的 live 面。

signal event_received(evt: String, data: Dictionary)
signal connection_changed(connected: bool)

var _http: HTTPClient
var _base: String = ""
var _buf := ""
var _running := false
var _requested := false


func start_stream(base_url: String) -> void:
	stop_stream()
	_base = base_url.rstrip("/")
	var url := _base + "/platform/api/stream"
	var host := ""
	var port := 80
	var rest := ""
	var use_tls := false
	var u := url
	if u.begins_with("https://"):
		use_tls = true
		u = u.substr(8)
	elif u.begins_with("http://"):
		u = u.substr(7)
	var slash := u.find("/")
	if slash >= 0:
		rest = u.substr(slash)
		u = u.substr(0, slash)
	else:
		rest = "/"
	var colon := u.find(":")
	if colon >= 0:
		host = u.substr(0, colon)
		port = int(u.substr(colon + 1))
	else:
		host = u
		port = 443 if use_tls else 80
	_http = HTTPClient.new()
	_http.connect_to_host(host, port, TLSOptions.client() if use_tls else null)
	_running = true
	_requested = false
	connection_changed.emit(false) # 先未连上;_process 里连上后转 true


func stop_stream() -> void:
	_running = false
	if _http != null:
		_http.close()
		_http = null


func _process(_delta: float) -> void:
	if not _running or _http == null:
		return
	_http.poll()
	var status := _http.get_status()
	if status == HTTPClient.STATUS_CONNECTED and not _requested:
		var err := _http.request(HTTPClient.METHOD_GET, _stream_path(), PackedStringArray())
		if err != OK:
			_fail()
		else:
			_requested = true
			connection_changed.emit(true)
	elif status == HTTPClient.STATUS_BODY or _http.has_response():
		var chunk := _http.read_response_body_chunk()
		while chunk.size() > 0:
			_buf += chunk.get_string_from_utf8()
			_drain_frames()
			chunk = _http.read_response_body_chunk()
	elif status == HTTPClient.STATUS_DISCONNECTED or status == HTTPClient.STATUS_CONNECTION_ERROR \
		or status == HTTPClient.STATUS_CANT_CONNECT or status == HTTPClient.STATUS_CANT_RESOLVE:
		_fail()


func _stream_path() -> String:
	var url := _base + "/platform/api/stream"
	var slash := url.find("/", url.find("://") + 3)
	return url.substr(slash) if slash >= 0 else "/platform/api/stream"


func _drain_frames() -> void:
	while true:
		var idx := _buf.find("\n\n")
		if idx < 0:
			return
		var frame := _buf.substr(0, idx)
		_buf = _buf.substr(idx + 2)
		_on_frame(frame)


func _on_frame(raw: String) -> void:
	var evt := "message"
	var data := ""
	for line in raw.split("\n"):
		if line.begins_with("event:"):
			evt = line.substr(6).strip_edges()
		elif line.begins_with("data:"):
			data += line.substr(5).strip_edges(true, false)
		# ":" 开头 = keepalive 注释帧,忽略
	if data.is_empty():
		return
	var parsed: Variant = JSON.parse_string(data)
	if parsed is Dictionary:
		event_received.emit(evt, parsed) # 坏帧丢弃(流是只读聚合,容错优先)


func _fail() -> void:
	_running = false
	if _http != null:
		_http.close()
	connection_changed.emit(false)

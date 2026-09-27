extends SceneTree

func _initialize() -> void:
	print("root valid: ", get_root() != null)
	var c := Node.new()
	if get_root() != null:
		get_root().add_child(c)
		print("child in tree: ", c.is_inside_tree())
	var req := HTTPRequest.new()
	c.add_child(req)
	print("req in tree: ", req.is_inside_tree())
	var err := req.request("http://127.0.0.1:8391/platform/api/sessions", PackedStringArray(), HTTPClient.METHOD_GET, "")
	print("request err: ", err, " (OK=", OK, ")")
	var r = await req.request_completed
	print("completed code: ", r[1])
	quit(0)

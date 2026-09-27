class_name MdBlocks
extends RefCounted

## md 块切分(镜像 web doc-editor.js 的 mdBlocks:D2 实现注"空行分块,
## 标题/列表项/表格行独占,1-based 行号区间")。锚点格式与服务端
## _ANCHOR_RE(^doc\.md#L(\d+)-L(\d+)$,列跨度 :C 可选)兼容。

static var _regex_cache: Dictionary = {}


static func _re(pattern: String) -> RegEx:
	if not _regex_cache.has(pattern):
		var r := RegEx.new()
		r.compile(pattern)
		_regex_cache[pattern] = r
	return _regex_cache[pattern]


## 返回 [{kind, start, end, text, anchor}](start/end 为 1-based 行号闭区间)
static func split(text: String) -> Array:
	var blocks: Array = []
	if text.is_empty():
		return blocks
	var lines := text.replace("\r\n", "\n").split("\n")
	var heading := _re("^\\s*#{1,6}\\s")
	var list_re := _re("^\\s*([-*+]|\\d+\\.)\\s")
	var table := _re("^\\s*\\|.*\\|\\s*$")
	var i := 0
	while i < lines.size():
		var ln := lines[i]
		if ln.strip_edges().is_empty():
			i += 1
			continue
		var start := i + 1 # 1-based
		if ln.strip_edges(true, false).begins_with("```"):
			var parts := PackedStringArray([ln])
			i += 1
			while i < lines.size() and not lines[i].strip_edges(true, false).begins_with("```"):
				parts.append(lines[i])
				i += 1
			if i < lines.size():
				parts.append(lines[i]) # 收尾围栏
				i += 1
			blocks.append(_mk("code", start, i, "\n".join(parts)))
			continue
		if heading.search(ln) != null:
			blocks.append(_mk("heading", start, start, ln))
			i += 1
			continue
		if table.search(ln) != null:
			blocks.append(_mk("table-row", start, start, ln))
			i += 1
			continue
		if list_re.search(ln) != null:
			blocks.append(_mk("list-item", start, start, ln))
			i += 1
			continue
		# 段落:累积到空行或特殊起始行
		var pparts := PackedStringArray([ln])
		i += 1
		while i < lines.size() and not lines[i].strip_edges().is_empty() \
			and heading.search(lines[i]) == null and list_re.search(lines[i]) == null \
			and table.search(lines[i]) == null and not lines[i].strip_edges(true, false).begins_with("```"):
			pparts.append(lines[i])
			i += 1
		blocks.append(_mk("paragraph", start, i, "\n".join(pparts)))
	return blocks


static func _mk(kind: String, start: int, end: int, text: String) -> Dictionary:
	return {
		"kind": kind,
		"start": start,
		"end": end,
		"text": text,
		"anchor": "doc.md#L%d-L%d" % [start, end],
	}


## 锚点 → [start, end];非法返回空数组
static func parse_anchor(anchor: String) -> Array:
	var m := _re("^doc\\.md#L(\\d+)(?::C(\\d+))?-L(\\d+)(?::C(\\d+))?$").search(anchor)
	if m == null:
		return []
	var start := int(m.get_string(1))
	var end := int(m.get_string(3))
	if end < start:
		return []
	return [start, end]


## 按锚点取当前全文里的块原文(apply 的 expected 校验面)
static func block_text(full_text: String, anchor: String) -> String:
	var rng := parse_anchor(anchor)
	if rng.is_empty() or full_text.is_empty():
		return ""
	var lines := full_text.replace("\r\n", "\n").split("\n")
	if rng[0] < 1 or rng[1] > lines.size():
		return ""
	# Array.slice 的 end 为不含端下标(0-based):1-based 行号闭区间正好直接用
	return "\n".join(lines.slice(rng[0] - 1, rng[1]))

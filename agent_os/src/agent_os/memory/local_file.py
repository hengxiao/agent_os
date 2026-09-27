"""LocalFileMemoryService(docs/DESIGN.md §11.2 baseline;M6)。

Markdown 文件 + frontmatter + BM25 检索(``agent_os.memory.rank``,与 std 检索
技能同一份实现,口径不许漂移):可人读人改、保序(文件名日期前缀)、Git 可版本化。
检索层权限过滤:数据离开存储层之前按 principal 过滤(§11.2);freshness(TTL)
是一等属性,过期条目不进结果。

存储布局::

    <root>/<entry_id>.md      # entry_id = YYYYMMDD-<slug>-<shortid>(保序、Git 友好)
    <root>/.evictions.log     # 驱逐审计 JSONL:{ts, id, reason, existed}

frontmatter(手写 YAML 子集:``key: <json|plain>`` 单行,零新依赖;值 JSON 即
合法 YAML 子集,解析 ``json.loads`` 失败时按纯文本字符串兜底)::

    ---
    id: 20260927-retry-lesson-a1b2c3d4
    tags: ["python", "retry"]
    source: {"kind": "experience", "user": "user:hengxiao"}
    created_at: 1727412345.678
    freshness: {"ttl_s": 3600}
    trust: experience
    provenance: {"run_id": "r1", "task": "f1", "note": "复盘沉淀"}
    ---

    正文 = content
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from agent_os.api.v1 import EntryRef, MemoryEntry, MemoryPrincipal, Provenance
from agent_os.memory.rank import bm25_score

#: entry_id 字符白名单(evict 按 id 解析文件路径,防目录逃逸)
_ENTRY_ID_RE = re.compile(r"[A-Za-z0-9-]+")

_SLUG_WORD_RE = re.compile(r"[a-z0-9]+")

#: frontmatter 标量可直接裸写的字符集(其余走 json.dumps 保往返)
_PLAIN_SCALAR_RE = re.compile(r"[A-Za-z0-9_.:/-]+")


def _slug(text: str) -> str:
    """content → 文件名片段(ASCII 词连字符,截 40 字符;空兜底 ``entry``)。"""
    slug = "-".join(_SLUG_WORD_RE.findall(text.lower()))[:40].strip("-")
    return slug or "entry"


def _dump_value(value: Any) -> str:
    """frontmatter 值序列化:简单字符串裸写(人读友好),其余 JSON(YAML 子集)。"""
    if isinstance(value, str) and _PLAIN_SCALAR_RE.fullmatch(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _parse_value(raw: str) -> Any:
    """frontmatter 值解析:先试 JSON(覆盖 list/dict/数字/布尔/带引号串),失败按纯文本串。"""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _render(entry_id: str, entry: MemoryEntry, provenance: Provenance) -> str:
    """条目 → 文件全文(frontmatter 键序固定,Git diff 稳定)。"""
    prov: dict[str, Any] = {
        "run_id": provenance.run_id,
        "task": provenance.task,
        "note": provenance.note,
    }
    if provenance.detail:
        prov["detail"] = dict(provenance.detail)
    meta: list[tuple[str, Any]] = [
        ("id", entry_id),
        ("tags", list(entry.tags)),
        ("source", dict(entry.source)),
        ("created_at", entry.created_at),
        ("freshness", dict(entry.freshness)),
        ("trust", entry.trust),
        ("provenance", prov),
    ]
    lines = ["---", *(f"{k}: {_dump_value(v)}" for k, v in meta), "---"]
    body = entry.content if isinstance(entry.content, str) else json.dumps(entry.content, ensure_ascii=False, indent=2)
    return "\n".join(lines) + "\n\n" + body + "\n"


def _parse(text: str) -> tuple[dict[str, Any], str]:
    """文件全文 → (frontmatter dict, 正文)。结构性损坏(缺围栏/无冒号行)抛 ValueError。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("缺少 frontmatter 起始围栏 ---")
    meta: dict[str, Any] = {}
    end = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            raise ValueError(f"frontmatter 畸形行(缺冒号): {line!r}")
        meta[key.strip()] = _parse_value(value)
    if end < 0:
        raise ValueError("缺少 frontmatter 结束围栏 ---")
    return meta, "\n".join(lines[end + 1 :]).strip("\n")


def _entry_from(meta: dict[str, Any], body: str) -> MemoryEntry:
    """frontmatter + 正文 → MemoryEntry;字段类型不对按缺省兜底(容错,不炸检索)。"""
    tags = meta.get("tags")
    source = meta.get("source")
    freshness = meta.get("freshness")
    try:
        created_at = float(meta.get("created_at") or 0.0)
    except (TypeError, ValueError):
        created_at = 0.0
    return MemoryEntry(
        content=body,
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        source=dict(source) if isinstance(source, dict) else {},
        created_at=created_at,
        freshness=dict(freshness) if isinstance(freshness, dict) else {},
        trust=str(meta.get("trust") or "experience"),
    )


def _principal_match(source: dict[str, Any], principal: MemoryPrincipal | None) -> bool:
    """存储出口前的 principal 过滤(§11.2):条目 source 带 user/tenant 键才受限,
    未标记 = 全局条目全通;principal 为 None = v1 单用户语义,全通。"""
    if principal is None:
        return True
    tagged_user = source.get("user")
    if tagged_user is not None and tagged_user != principal.user:
        return False
    tagged_tenant = source.get("tenant")
    return not (tagged_tenant is not None and tagged_tenant != principal.tenant)


def _is_fresh(entry: MemoryEntry, now: float) -> bool:
    """freshness 治理:``ttl_s``(相对 created_at)/``valid_until``(绝对 epoch)过期不进结果。"""
    freshness = entry.freshness
    try:
        ttl = freshness.get("ttl_s")
        if ttl is not None and now > entry.created_at + float(ttl):
            return False
        valid_until = freshness.get("valid_until")
        if valid_until is not None and now > float(valid_until):
            return False
    except (TypeError, ValueError):
        return False  # 新鲜度写坏了按过期计(fail closed:不放行来路不明的旧货)
    return True


class LocalFileMemoryService:
    """``agent_os.api.v1.MemoryService`` 协议实现(M6):每条目一 Markdown 文件 + BM25。"""

    def __init__(self, root: str = "./memory") -> None:
        self.root = root

    def _root(self) -> Path:
        return Path(self.root).expanduser()

    async def search(
        self, query: str, k: int, principal: MemoryPrincipal | None = None
    ) -> list[MemoryEntry]:
        """frontmatter 预过滤(principal + freshness)→ 正文 BM25(``memory.rank``)→ k 截断。

        损坏文件(frontmatter 结构坏/编码坏)容错跳过;零分条目(无查询词命中)不进结果。
        """
        root = self._root()
        if not root.is_dir():
            return []
        now = time.time()
        candidates: dict[str, MemoryEntry] = {}
        for path in sorted(root.glob("*.md")):
            try:
                meta, body = _parse(path.read_text(encoding="utf-8"))
            except (ValueError, OSError, UnicodeDecodeError):
                continue  # 损坏容错:跳过,不拖垮检索
            entry = _entry_from(meta, body)
            if not _principal_match(entry.source, principal) or not _is_fresh(entry, now):
                continue
            entry_id = meta.get("id") if isinstance(meta.get("id"), str) else path.stem
            candidates[entry_id] = entry
        docs = [{"id": eid, "text": str(e.content)} for eid, e in candidates.items()]
        ranked = bm25_score(query, docs, k=k)
        return [candidates[r["id"]] for r in ranked if r["score"] > 0]

    async def write(self, entry: MemoryEntry, provenance: Provenance) -> EntryRef:
        """写 ``<root>/<entry_id>.md`` 返回 ``EntryRef``;同日同 slug 撞名时 shortid 重摇。"""
        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        date = time.strftime("%Y%m%d", time.localtime(entry.created_at))
        stem = f"{date}-{_slug(str(entry.content))}"
        entry_id = f"{stem}-{uuid.uuid4().hex[:8]}"
        while (root / f"{entry_id}.md").exists():
            entry_id = f"{stem}-{uuid.uuid4().hex[:8]}"
        (root / f"{entry_id}.md").write_text(_render(entry_id, entry, provenance), encoding="utf-8")
        return EntryRef(id=entry_id)

    async def evict(self, ref: EntryRef, reason: str) -> None:
        """删条目文件 + 追加 ``.evictions.log`` 审计(可驱逐 + 可审计是安全底线,§11.1)。

        幂等:文件已不存在不报错,审计行记 ``existed=false``;
        id 含非法字符(目录逃逸嫌疑)按不存在处理,绝不解析到 root 之外。
        """
        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{ref.id}.md"
        existed = bool(_ENTRY_ID_RE.fullmatch(ref.id)) and path.is_file()
        if existed:
            path.unlink()
        record = {"ts": time.time(), "id": ref.id, "reason": reason, "existed": existed}
        with (root / ".evictions.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

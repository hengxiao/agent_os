"""LocalFileMemoryService 锚点测试(docs/DESIGN.md §11.2;M6)。

固定约定:

- 布局 ``<root>/<entry_id>.md``,entry_id = ``YYYYMMDD-<slug>-<shortid>``(保序、Git 友好);
- frontmatter = ``key: <json|plain>`` 单行 YAML 子集(零新依赖),承载
  tags/source/created_at/freshness/trust/provenance,正文 = content;
- search:frontmatter 预过滤(principal + freshness)→ 正文 BM25(``memory.rank``)→ k 截断;
- evict:删文件 + 追加 ``.evictions.log`` 审计(可驱逐 + 可审计,§11.1);
- 损坏 frontmatter 容错跳过,不拖垮检索。
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from agent_os.api.v1 import EntryRef, MemoryEntry, MemoryPrincipal, Provenance
from agent_os.memory.local_file import LocalFileMemoryService, _parse, _render

# ---------------------------------------------------------------------------
# frontmatter 往返
# ---------------------------------------------------------------------------


def test_frontmatter_round_trip():
    entry = MemoryEntry(
        content="重试三次后成功,根因是 DNS 抖动",
        tags=["retry", "dns"],
        source={"kind": "experience", "user": "user:hengxiao"},
        created_at=1_700_000_000.5,
        freshness={"ttl_s": 3600},
        trust="experience",
    )
    prov = Provenance(run_id="r1", task="f1", note="复盘沉淀", detail={"step": 3})
    text = _render("20231114-retry-dns-a1b2c3d4", entry, prov)
    assert text.startswith("---\n"), "frontmatter 围栏"
    assert "重试三次后成功" not in text.split("---")[1], "正文不应进 frontmatter"
    meta, body = _parse(text)
    assert meta["id"] == "20231114-retry-dns-a1b2c3d4"
    assert meta["tags"] == ["retry", "dns"]
    assert meta["source"] == {"kind": "experience", "user": "user:hengxiao"}
    assert meta["created_at"] == 1_700_000_000.5
    assert meta["freshness"] == {"ttl_s": 3600}
    assert meta["trust"] == "experience"
    assert meta["provenance"]["run_id"] == "r1"
    assert meta["provenance"]["detail"] == {"step": 3}
    assert body == entry.content


def test_parse_rejects_structural_corruption():
    for bad in ("没有围栏的正文", "---\ntags: [a]\n(缺结束围栏)", "---\n无冒号行\n---\nbody"):
        try:
            _parse(bad)
        except ValueError:
            pass
        else:  # pragma: no cover - 断言语义
            raise AssertionError(f"畸形 frontmatter 应抛 ValueError: {bad!r}")


# ---------------------------------------------------------------------------
# write → EntryRef / 布局
# ---------------------------------------------------------------------------


def _svc(tmp_path):
    return LocalFileMemoryService(str(tmp_path / "memory"))


def _entry(content: str, **kw) -> MemoryEntry:
    return MemoryEntry(content=content, **kw)


def test_write_returns_ref_and_lays_out_file(tmp_path):
    svc = _svc(tmp_path)

    async def main():
        return await svc.write(
            _entry("retry backoff lesson learned", created_at=1_700_000_000.0),
            Provenance(run_id="r1", task="f1", note="n"),
        )

    ref = asyncio.run(main())
    assert re.fullmatch(r"\d{8}-retry-backoff-lesson-learned-[0-9a-f]{8}", ref.id), (
        f"entry_id 应为 YYYYMMDD-<slug>-<shortid>: {ref.id}"
    )
    files = list((tmp_path / "memory").glob("*.md"))
    assert len(files) == 1 and files[0].stem == ref.id
    meta, body = _parse(files[0].read_text(encoding="utf-8"))
    assert body == "retry backoff lesson learned"
    assert meta["provenance"]["run_id"] == "r1"


def test_write_two_entries_same_content_no_collision(tmp_path):
    svc = _svc(tmp_path)

    async def main():
        a = await svc.write(_entry("same content"), Provenance())
        b = await svc.write(_entry("same content"), Provenance())
        return a, b

    a, b = asyncio.run(main())
    assert a.id != b.id, "同 slug 撞名须靠 shortid 区分"
    assert len(list((tmp_path / "memory").glob("*.md"))) == 2


# ---------------------------------------------------------------------------
# search:BM25 / k 截断 / freshness / principal
# ---------------------------------------------------------------------------


def _write_entries(svc, entries):
    async def main():
        for e in entries:
            await svc.write(e, Provenance())

    asyncio.run(main())


def _search(svc, query, k=10, principal=None):
    return asyncio.run(svc.search(query, k, principal))


def test_search_bm25_ranks_cjk_and_ascii(tmp_path):
    svc = _svc(tmp_path)
    _write_entries(svc, [
        _entry("服务重启后必须先检查日志再放量"),  # CJK 命中
        _entry("retry retry retry with exponential backoff"),  # ASCII 高频命中
        _entry("retry once"),  # ASCII 低频命中
        _entry("完全不相关的采购流程记录"),  # 不命中
    ])
    cjk = _search(svc, "重启")
    assert len(cjk) == 1 and "检查日志" in cjk[0].content, "CJK bigram 应命中且零分条目被滤掉"
    ascii_hits = _search(svc, "retry")
    assert [e.content for e in ascii_hits][:2] == [
        "retry retry retry with exponential backoff",
        "retry once",
    ], "BM25 应按相关度降序(高频在前)"
    assert all("retry" in e.content for e in ascii_hits)


def test_search_k_truncation(tmp_path):
    svc = _svc(tmp_path)
    _write_entries(svc, [_entry(f"lesson number {i} about retry") for i in range(3)])
    hits = _search(svc, "retry", k=2)
    assert len(hits) == 2, "k 截断"


def test_search_freshness_ttl_and_valid_until(tmp_path):
    svc = _svc(tmp_path)
    now = time.time()
    _write_entries(svc, [
        _entry("fresh entry about cache", created_at=now, freshness={"ttl_s": 3600}),
        _entry("stale ttl entry about cache", created_at=now - 7200, freshness={"ttl_s": 3600}),
        _entry("stale until entry about cache", created_at=now, freshness={"valid_until": now - 1}),
        _entry("future until entry about cache", created_at=now, freshness={"valid_until": now + 3600}),
        _entry("timeless entry about cache", created_at=now - 10_000),
    ])
    contents = [e.content for e in _search(svc, "cache")]
    assert "fresh entry about cache" in contents
    assert "future until entry about cache" in contents
    assert "timeless entry about cache" in contents, "无 freshness 标记 = 不过期"
    assert "stale ttl entry about cache" not in contents, "ttl_s 过期不进结果"
    assert "stale until entry about cache" not in contents, "valid_until 过期不进结果"


def test_search_principal_filter(tmp_path):
    svc = _svc(tmp_path)
    _write_entries(svc, [
        _entry("alice 的私有偏好 preference", source={"user": "user:alice"}),
        _entry("bob 的私有偏好 preference", source={"user": "user:bob"}),
        _entry("全局共享经验 preference", source={"kind": "experience"}),
    ])
    everyone = _search(svc, "preference", principal=None)
    assert len(everyone) == 3, "principal=None = 单用户全通"
    alice = _search(svc, "preference", principal=MemoryPrincipal(user="user:alice"))
    assert {e.content for e in alice} == {"alice 的私有偏好 preference", "全局共享经验 preference"}, (
        "他人条目在存储出口前被过滤;未标记条目全局可见"
    )
    tenant = _search(svc, "preference", principal=MemoryPrincipal(user="user:alice", tenant="t1"))
    assert len(tenant) == 2, "条目未标 tenant = 对 tenant 不设限"


def test_search_skips_corrupted_files(tmp_path):
    svc = _svc(tmp_path)
    _write_entries(svc, [_entry("healthy entry about deploy")])
    root = tmp_path / "memory"
    (root / "20261101-broken-deadbeef.md").write_text("---\ntags: [a]\n(永远缺结束围栏", encoding="utf-8")
    (root / "20261102-garbage-cafebabe.md").write_text("根本不是 frontmatter", encoding="utf-8")
    hits = _search(svc, "deploy")
    assert len(hits) == 1 and hits[0].content == "healthy entry about deploy", "损坏文件容错跳过"


# ---------------------------------------------------------------------------
# evict:删文件 + 审计日志
# ---------------------------------------------------------------------------


def test_evict_deletes_file_and_appends_audit_log(tmp_path):
    svc = _svc(tmp_path)

    async def main():
        ref = await svc.write(_entry("to be evicted"), Provenance(run_id="r1"))
        await svc.evict(ref, "用户撤回同意")
        return ref

    ref = asyncio.run(main())
    root = tmp_path / "memory"
    assert not (root / f"{ref.id}.md").exists(), "条目文件已删"
    lines = (root / ".evictions.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["id"] == ref.id and record["reason"] == "用户撤回同意"
    assert record["existed"] is True and record["ts"] > 0
    assert _search(svc, "evicted") == [], "驱逐后不可再检索到"


def test_evict_idempotent_and_traversal_safe(tmp_path):
    svc = _svc(tmp_path)

    async def main():
        await svc.evict(EntryRef(id="20990101-nope-00000000"), "重复驱逐")
        await svc.evict(EntryRef(id="../../etc/passwd"), "逃逸嫌疑")

    asyncio.run(main())  # 不存在的 id 幂等不报错;非法 id 按不存在处理
    lines = (tmp_path / "memory" / ".evictions.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["existed"] is False for line in lines), "未命中/非法 id 只记审计不删文件"

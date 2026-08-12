"""DocStore(docs/DOC-EDITOR.md §4;D1):Markdown 文档的存储层。

布局(与 DraftStore 同构哲学——working/版本不可变/上一版自动备份)::

    <docs_root>/<name>/
    ├── doc.md            # 当前全文(working)
    ├── meta.json         # {title, created_at, savedAt}
    ├── versions/vNNN/    # 快照(doc.md + meta.json{source, parent, at, ...extra})
    ├── bubbles/<hash>.json  # 每锚点一个消息流(D2 用;v2 起只读不删)
    ├── annotations/<hash>.json  # 单条批注记录(P1:status/generation;读取面含旧流压缩迁移)
    ├── review/<ts>.json     # 历次评审的批注集(D3 用)
    └── chat.json            # doc 作用域主对话消息流(D5 用)

红线:名字 = 点分校验(与 NAMING 同面,路径穿越防护);写之前上一版
自动 ``.bak``;版本不可变(rewind 恢复的是工作副本,历史不动)。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_os.skills.reanchor import quote_at

#: 文档名合法面(与草稿同构:≥2 段点分,段内小写 snake_case;同时是路径穿越防护)
_DOC_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$")

#: 批注状态机(设计 v2 §3.1;P1)
ANNOTATION_STATUSES = ("pending", "applied", "ignored", "outdated")


def _iso(ts: float | None = None) -> str:
    """ISO 时间戳(Annotation.createdAt;ts None = 现在)。"""
    return datetime.fromtimestamp(ts if ts else time.time(), tz=UTC).isoformat()


class DocStore:
    """Markdown 文档存取:CRUD / .bak / 快照 / rewind / bubbles / review。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 名字与目录(穿越防护是唯一入口纪律)
    # ------------------------------------------------------------------

    def check_name(self, name: str) -> None:
        if not _DOC_NAME_RE.match(name or ""):
            raise ValueError(f"文档名不合法: {name!r}(须 ≥2 段点分小写,如 design.new_ui)")

    def _dir(self, name: str) -> Path:
        self.check_name(name)
        return self._root / name

    # ------------------------------------------------------------------
    # CRUD(写之前上一版自动 .bak,与 DraftStore 同惯例)
    # ------------------------------------------------------------------

    def create(self, name: str, *, title: str = "", text: str = "") -> dict[str, Any]:
        """新建文档:目录 + doc.md + meta.json;已存在 → FileExistsError。"""
        d = self._dir(name)
        if d.exists():
            raise FileExistsError(f"文档已存在: {name}")
        d.mkdir(parents=True)
        (d / "doc.md").write_text(text, encoding="utf-8")
        self._write_meta(name, {"title": title or name, "created_at": time.time(), "savedAt": time.time()})
        return self.read(name)

    def read(self, name: str) -> dict[str, Any]:
        """读文档:{name, text, meta};不存在 → FileNotFoundError。"""
        d = self._dir(name)
        if not (d / "doc.md").is_file():
            raise FileNotFoundError(f"文档不存在: {name}")
        meta = {}
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}  # meta 坏不挡读(文档本体是事实源)
        return {"name": name, "text": (d / "doc.md").read_text(encoding="utf-8"), "meta": meta}

    def save(self, name: str, text: str, *, title: str | None = None) -> dict[str, Any]:
        """保存全文(整体替换):**写之前把上一版备份为 .bak**;meta.savedAt 刷新。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"文档不存在: {name}")
        doc = d / "doc.md"
        if doc.is_file():
            shutil.copy2(doc, d / "doc.md.bak")
        doc.write_text(text, encoding="utf-8")
        meta = self.read(name)["meta"]
        meta["savedAt"] = time.time()
        if title is not None:
            meta["title"] = title
        self._write_meta(name, meta)
        return self.read(name)

    def delete(self, name: str) -> None:
        """删除整目录(含版本;不存在 → FileNotFoundError)。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"文档不存在: {name}")
        shutil.rmtree(d)

    def list(self) -> list[dict[str, Any]]:
        """文档索引(Card Surface 数据源):标题/首行摘要/字数/最近编辑/有无气泡。"""
        out = []
        for d in sorted(p for p in self._root.iterdir() if p.is_dir()):
            if not (d / "doc.md").is_file():
                continue
            try:
                text = (d / "doc.md").read_text(encoding="utf-8")
            except OSError:
                continue
            meta: dict[str, Any] = {}
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            out.append(
                {
                    "name": d.name,
                    "title": meta.get("title") or d.name,
                    "first_line": first_line.lstrip("#").strip()[:60],
                    "chars": len(text),
                    "savedAt": meta.get("savedAt", 0),
                    "has_bubbles": (d / "bubbles").is_dir() and any((d / "bubbles").glob("*.json")),
                    "versions": len(self.list_versions(d.name)),
                }
            )
        out.sort(key=lambda r: -(r["savedAt"] or 0))
        return out

    # ------------------------------------------------------------------
    # 版本(快照不可变;rewind 恢复工作副本,历史不动——与 iterate 同语义)
    # ------------------------------------------------------------------

    def snapshot(
        self,
        name: str,
        *,
        source: str,
        parent: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """封存当前全文为 versions/vNNN(内容 = 保存时的全文)。返回版本号。
        ``extra``(P1):并进 meta.json 的附加字段(generate 的
        generationInput/annotationResults;版本不可变——只在封存时写一次)。"""
        d = self._dir(name)
        if not (d / "doc.md").is_file():
            raise FileNotFoundError(f"文档不存在: {name}")
        vdir = d / "versions"
        vdir.mkdir(exist_ok=True)
        existing = sorted(p.name for p in vdir.iterdir() if p.is_dir() and p.name.startswith("v"))
        vid = f"v{int(existing[-1][1:]) + 1:03d}" if existing else "v001"
        target = vdir / vid
        target.mkdir()
        shutil.copy2(d / "doc.md", target / "doc.md")
        meta: dict[str, Any] = {"version": vid, "source": source, "parent": parent, "at": time.time()}
        if extra:
            meta.update(extra)
        (target / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return vid

    def list_versions(self, name: str) -> list[dict[str, Any]]:
        """版本列表(新→旧;版本下拉数据源)。"""
        vdir = self._dir(name) / "versions"
        if not vdir.is_dir():
            return []
        out = []
        for p in sorted(vdir.iterdir(), reverse=True):
            if not p.is_dir() or not (p / "meta.json").is_file():
                continue
            try:
                out.append(json.loads((p / "meta.json").read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def restore(self, name: str, version: str) -> dict[str, Any]:
        """rewind:把快照覆盖回 working(**历史不动**——版本不可变,
        恢复的是工作副本);版本不存在 → FileNotFoundError。"""
        vdir = self._dir(name) / "versions" / version
        if not (vdir / "doc.md").is_file():
            raise FileNotFoundError(f"版本不存在: {name} {version}")
        shutil.copy2(vdir / "doc.md", self._dir(name) / "doc.md")
        return self.read(name)

    # ------------------------------------------------------------------
    # bubbles(D2)/ review(D3):锚点消息流与评审批注集的存取面
    # ------------------------------------------------------------------

    def save_bubble(self, name: str, anchor: str, message: dict[str, Any]) -> dict[str, Any]:
        """往锚点消息流追加一条(bubbles/<anchor-hash>.json;消息流 append)。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"文档不存在: {name}")
        bdir = d / "bubbles"
        bdir.mkdir(exist_ok=True)
        path = bdir / f"{hashlib.sha1(anchor.encode()).hexdigest()[:12]}.json"
        try:
            flow = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"anchor": anchor, "messages": []}
        except (OSError, json.JSONDecodeError):
            flow = {"anchor": anchor, "messages": []}
        flow["messages"].append({**message, "ts": message.get("ts", time.time())})
        path.write_text(json.dumps(flow, ensure_ascii=False, indent=2), encoding="utf-8")
        return flow

    def read_bubbles(self, name: str) -> list[dict[str, Any]]:
        """全文档气泡流列表(气泡栏数据源;坏文件隔离)。"""
        bdir = self._dir(name) / "bubbles"
        if not bdir.is_dir():
            return []
        out = []
        for f in sorted(bdir.glob("*.json")):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(doc, dict) and "anchor" in doc:
                    out.append(doc)
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def delete_bubble(self, name: str, anchor: str) -> None:
        """删除锚点批注(v3 气泡垃圾桶;P1 起两面都删:bubbles/ 旧消息流 +
        annotations/ 新记录)。两处都不存在 → FileNotFoundError。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"文档不存在: {name}")
        fname = f"{hashlib.sha1(anchor.encode()).hexdigest()[:12]}.json"
        hit = False
        for sub in ("bubbles", "annotations"):
            path = d / sub / fname
            if path.is_file():
                path.unlink()
                hit = True
        if not hit:
            raise FileNotFoundError(f"批注不存在: {name} {anchor}")

    # ------------------------------------------------------------------
    # annotations(P1,批注批处理工作流 v2,设计 §3):单条批注记录
    # (annotations/<anchor-hash>.json)与旧 bubbles/ 消息流并存。
    # 读取面 = 新记录 + 旧流压缩迁移(首条 user 消息 → content,其余进
    # history,status=pending);**只读旧写新,旧流文件不删**。
    # ------------------------------------------------------------------

    @staticmethod
    def _anchor_hash(anchor: str) -> str:
        return hashlib.sha1(anchor.encode()).hexdigest()[:12]

    def save_annotation(self, name: str, annotation: dict[str, Any]) -> dict[str, Any]:
        """写单条批注记录(annotations/<anchor-hash>.json;同锚点覆盖)。
        缺省字段补齐(createdAt/createdBy/status=pending/generation=null);
        status 白名单校验(ANNOTATION_STATUSES)。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"文档不存在: {name}")
        anchor = str(annotation.get("anchor") or "")
        status = str(annotation.get("status") or "pending")
        if status not in ANNOTATION_STATUSES:
            raise ValueError(f"批注状态非法: {status!r}(须 ∈ {ANNOTATION_STATUSES})")
        rec: dict[str, Any] = {
            "anchor": anchor,
            "quote": str(annotation.get("quote") or ""),
            "version": annotation.get("version"),
            "content": str(annotation.get("content") or ""),
            "createdAt": annotation.get("createdAt") or _iso(),
            "createdBy": annotation.get("createdBy") or "user",
            "status": status,
            "appliedInVersion": annotation.get("appliedInVersion"),
            "generation": annotation.get("generation"),
        }
        if "history" in annotation:  # 迁移面:旧流其余消息原样随记录保留
            rec["history"] = annotation["history"]
        if "migratedFrom" in annotation:  # 迁移面:旧流锚点(防 reanchor 改锚后旧流复活)
            rec["migratedFrom"] = annotation["migratedFrom"]
        adir = d / "annotations"
        adir.mkdir(exist_ok=True)
        (adir / f"{self._anchor_hash(anchor)}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return rec

    def delete_annotation(self, name: str, anchor: str) -> bool:
        """删 annotations/ 新记录(reanchor 后锚点变更的旧键清位)。返回是否删到。"""
        path = self._dir(name) / "annotations" / f"{self._anchor_hash(anchor)}.json"
        if path.is_file():
            path.unlink()
            return True
        return False

    def read_annotations(self, name: str) -> list[dict[str, Any]]:
        """批注读取面 = annotations/ 新记录 + bubbles/ 旧流压缩迁移(同锚点新优先,
        不重复计)。向后兼容:无 status 字段的旧记录读为 pending。坏文件隔离。"""
        d = self._dir(name)  # 名字校验(穿越防护同面)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        adir = d / "annotations"
        if adir.is_dir():
            for f in sorted(adir.glob("*.json")):
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(rec, dict) and "anchor" in rec:
                    rec.setdefault("status", "pending")
                    rec.setdefault("appliedInVersion", None)
                    rec.setdefault("generation", None)
                    out.append(rec)
                    seen.add(rec["anchor"])
                    if rec.get("migratedFrom"):  # 旧流锚点也计入(改锚后不复活)
                        seen.add(rec["migratedFrom"])
        for flow in self.read_bubbles(name):
            anchor = flow.get("anchor")
            if not anchor or anchor in seen:
                continue
            msgs = [m for m in (flow.get("messages") or []) if isinstance(m, dict)]
            first_user = next((m for m in msgs if m.get("role") == "user"), None)
            history = [m for m in msgs if m is not first_user]
            rec: dict[str, Any] = {
                "anchor": anchor,
                "quote": self._quote_for_anchor(d, anchor),
                "version": self._latest_version_no(d),
                "content": str(first_user.get("text") or "") if first_user else "",
                "createdAt": _iso(first_user.get("ts")) if first_user else _iso(),
                "createdBy": "user",
                "status": "pending",
                "appliedInVersion": None,
                "generation": None,
            }
            if history:
                rec["history"] = history
            out.append(rec)
        return out

    def set_annotation_status(
        self,
        name: str,
        anchor: str,
        *,
        status: str,
        applied_in_version: int | None = None,
        generation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """更新批注状态(applied_in_version/generation 给了才写)。记录不存在于
        annotations/ 时先从读取面(含旧流压缩)取——**set 即迁移写新**;
        完全没有 → FileNotFoundError。"""
        if status not in ANNOTATION_STATUSES:
            raise ValueError(f"批注状态非法: {status!r}(须 ∈ {ANNOTATION_STATUSES})")
        rec = next((a for a in self.read_annotations(name) if a.get("anchor") == anchor), None)
        if rec is None:
            raise FileNotFoundError(f"批注不存在: {name} {anchor}")
        if not (self._dir(name) / "annotations" / f"{self._anchor_hash(anchor)}.json").is_file():
            rec["migratedFrom"] = anchor  # 来自旧流压缩:set 即迁移写新,记下旧锚点
        rec["status"] = status
        if applied_in_version is not None:
            rec["appliedInVersion"] = applied_in_version
        if generation is not None:
            rec["generation"] = generation
        return self.save_annotation(name, rec)

    @staticmethod
    def _quote_for_anchor(d: Path, anchor: str) -> str:
        """迁移补 quote:按锚点从当前全文截取(截不到/文档不在 → \"\")。"""
        try:
            text = (d / "doc.md").read_text(encoding="utf-8")
        except OSError:
            return ""
        return quote_at(text, anchor) or ""

    @staticmethod
    def _latest_version_no(d: Path) -> int | None:
        """最新快照序号(迁移补 version;无版本 → None)。"""
        vdir = d / "versions"
        if not vdir.is_dir():
            return None
        vids = sorted(p.name for p in vdir.iterdir() if p.is_dir() and p.name.startswith("v"))
        return int(vids[-1][1:]) if vids else None

    # ------------------------------------------------------------------
    # chat(D5):doc 作用域主对话的消息流(chat.json;与 bubbles 区分——
    # chat 是主对话,bubbles 是段落批注,两个文件都留)
    # ------------------------------------------------------------------

    def save_chat(self, name: str, message: dict[str, Any]) -> dict[str, Any]:
        """往主对话追加一条(chat.json = {"messages": [...]},append)。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"文档不存在: {name}")
        path = d / "chat.json"
        try:
            flow = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"messages": []}
        except (OSError, json.JSONDecodeError):
            flow = {"messages": []}  # 坏文件不挡写(重开空流,旧内容不追回)
        flow["messages"].append({**message, "ts": message.get("ts", time.time())})
        path.write_text(json.dumps(flow, ensure_ascii=False, indent=2), encoding="utf-8")
        return flow

    def read_chat(self, name: str) -> list[dict[str, Any]]:
        """主对话消息列表(左栏种子;无文件/坏文件 → [])。"""
        path = self._dir(name) / "chat.json"
        if not path.is_file():
            return []
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            msgs = doc.get("messages") if isinstance(doc, dict) else None
            return [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def save_review(self, name: str, notes: list[dict[str, Any]]) -> str:
        """落一次评审批注集(review/<ts>.json;返回文件名)。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(f"文档不存在: {name}")
        rdir = d / "review"
        rdir.mkdir(exist_ok=True)
        fname = f"{int(time.time() * 1000)}.json"
        (rdir / fname).write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
        return fname

    def latest_review(self, name: str) -> list[dict[str, Any]]:
        """最近一次评审批注集(没有 → [])。"""
        rdir = self._dir(name) / "review"
        if not rdir.is_dir():
            return []
        files = sorted(rdir.glob("*.json"))
        if not files:
            return []
        try:
            doc = json.loads(files[-1].read_text(encoding="utf-8"))
            return doc if isinstance(doc, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    # ------------------------------------------------------------------

    def _write_meta(self, name: str, meta: dict[str, Any]) -> None:
        (self._dir(name) / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

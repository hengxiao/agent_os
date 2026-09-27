"""Doc Editor 数据源(docs/TUI-DOC.md §4/§7;T2:批注写面 + 字符精度锚点)。

三种来源同一形状:
- ``OnlineDocSource``:打既有 web_platform(统一信封解包,零新通道);
  **唯一可写源**(annotations upsert/delete,§1 五口之一);
- ``OfflineDocSource``:``--offline --docs-root`` 直读 DocStore 产物目录
  ("产物即真相",只读回放,不写);
- ``DemoDocSource``:``--demo`` 内置两篇示例文档(只读;demo 不做假写面)。

字符精度(用户裁决):锚点 = ``doc.md#L<行>:C<列>-L<行>:C<列>``(1-based、列闭
区间,语义见 skills/reanchor.py docstring);widget 侧用 0-based 半开区间,
转换收口在本文件的一对互逆 helper(``anchor_from_range``/``range_from_anchor``)。
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agent_os.host.tui.kernel.client import AgentOsClient
from agent_os.skills.doc_store import DocStore
from agent_os.skills.reanchor import make_anchor, parse_anchor, quote_at

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.pipeline import ActionPipeline

#: 批注内容上限(与 web_platform 端点同约束)
ANN_CONTENT_MAX = 500

# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------


@dataclass
class DocEntry:
    """信箱里的一封信(card surface 摘要;§4 映射表"信箱"行)。"""

    name: str
    title: str
    version: str = ""       # 最新版本号(vNNN);无版本 = 工作稿
    saved_at: float = 0.0
    annotations: int = 0    # 批注数([注×N];0 = 不显示)

    def date_label(self) -> str:
        return time.strftime("%m-%d", time.localtime(self.saved_at)) if self.saved_at else "--"

    def version_label(self) -> str:
        return self.version or "工作稿"


@dataclass
class Span:
    """批注在全文上的解析结果(0-based 半开;start/end None = outdated 不画,
    计数保留)。resolved ∈ exact / fuzzy / outdated(§7.1 同策略)。"""

    anchor: str
    quote: str
    content: str
    status: str
    start: int | None
    end: int | None
    resolved: str


@dataclass
class Letter:
    """一封打开的信:全文 text(偏移基准)+ 解析后的批注 span 列表 + 版本面。"""

    name: str
    title: str
    version: str
    text: str = ""
    spans: list[Span] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 锚点互逆(1-based 闭区间 L/C ↔ 0-based 半开区间偏移;round-trip 是本文件
# 的契约:quote_at(text, anchor_from_range(text, s, e)) == text[s:e])
# ---------------------------------------------------------------------------

def _offset_to_lc(text: str, offset: int) -> tuple[int, int]:
    """字符偏移(0-based,指向某字符)→ (行, 列)(1-based;列 = 该字符在本行序号)。"""
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset) + 1
    return line, offset - line_start + 1


def _line_starts(text: str) -> list[int]:
    """每行的起始偏移(0-based;len = 行数)。"""
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def anchor_from_range(text: str, start: int, end: int) -> str:
    """选区 [start, end)(0-based 半开)→ 字符精度锚点(与 ``quote_at`` 互逆)。

    边界决策(以 quote_at 的实现为权威面):
    - 跨行:起 = 起始行 1-based 列,末 = 末行 1-based 闭区间列;
    - **同行**:``quote_at`` 对单元素 seg 先切 [sc-1:] 再切 [:ec](实现即语义),
      故同行锚点 ec 编码**选区长度**(e - s)而非绝对末列——只有这样
      round-trip 才成立(既有代码面里 doc-editor.js 的绝对半开约定与
      quote_at 不一致,属已存在分歧,此处以 quote_at 为准并留痕);
    - 选区末字符是换行符:换行是所在行的终止符,为让它进 ``\\n``.join 的
      重建,把锚点末点推到下一行 C0(``quote_at`` 对该行截 [:0] = 空,
      净效果 = 包含该换行,不多拿一字)。
    """
    if not (0 <= start < end <= len(text)):
        raise ValueError(f"选区越界: [{start}, {end}) / 全文 {len(text)} 字")
    sl, sc = _offset_to_lc(text, start)
    last = end - 1
    if text[last] == "\n":
        el = text.count("\n", 0, last) + 2
        ec: int | None = 0
        return make_anchor(sl, sc, el, ec)
    el, ec_abs = _offset_to_lc(text, last)
    if el == sl:
        ec = end - start  # 同行:quote_at 语义下 ec = 选区长度
    else:
        ec = ec_abs
    return make_anchor(sl, sc, el, ec)


def range_from_anchor(text: str, anchor: str) -> tuple[int, int] | None:
    """锚点 → 0-based 半开区间 (start, end);非法/越界锚点 → None。

    ``quote_at`` 互逆约定:跨行末列 = 1-based 闭区间绝对列(半开 end =
    行首 + ec);**同行 ec = 长度**(end = start + ec)。行级锚点(列缺省)
    = 整行(不含换行符,与 ``quote_at`` 的 join 语义一致)。"""
    parsed = parse_anchor(anchor)
    if not parsed:
        return None
    sl, sc, el, ec = parsed
    lines = text.split("\n")
    if sl < 1 or el < sl or el > len(lines):
        return None
    starts = _line_starts(text)
    start = starts[sl - 1] + (sc - 1 if sc is not None else 0)
    if start > starts[sl - 1] + len(lines[sl - 1]):
        return None
    if ec is None:
        end = starts[el - 1] + len(lines[el - 1])
    elif el == sl:
        end = start + ec  # 同行:ec = 长度(quote_at 双重切片语义)
    else:
        end = starts[el - 1] + ec  # 跨行:ec = 1-based 闭区间末列
    if end > starts[el - 1] + len(lines[el - 1]):
        return None
    if end < start:
        return None
    return start, end


# ---------------------------------------------------------------------------
# span 解析(精确 → ±3 行模糊 → outdated;复用 reanchor.py 的策略面)
# ---------------------------------------------------------------------------

def resolve_spans(text: str, records: list[dict[str, Any]]) -> list[Span]:
    """批注记录 → 全文 span 列表。策略与 reanchor.py 同(§7.1,宁多勿错):

    1. 精确:``quote_at`` 原位截取 == quote → 用原锚点;
    2. 模糊:±3 行窗口内全文匹配 quote(可跨行)→ 命中位置直接出 span,
       锚点按 ``anchor_from_range``(quote_at 互逆约定)重写;
       ——不走 reanchor_one 的列重写,因为该行对同行锚点产"绝对闭区间"
       列,而 quote_at 读同行 ec 为长度(既有分歧,本侧以 quote_at 为准);
    3. 找不到/本就 outdated → start/end = None(不画 span,计数保留)。
    """
    out: list[Span] = []
    lines = text.split("\n")
    for rec in records:
        if not isinstance(rec, dict):
            continue
        anchor = str(rec.get("anchor") or "")
        quote = str(rec.get("quote") or "")
        status = str(rec.get("status") or "pending")
        content = str(rec.get("content") or "")
        if status == "outdated":
            out.append(Span(anchor, quote, content, status, None, None, "outdated"))
            continue
        rng = range_from_anchor(text, anchor)
        if rng is not None and quote_at(text, anchor) == quote:
            out.append(Span(anchor, quote, content, status, rng[0], rng[1], "exact"))
            continue
        # 模糊:±3 行(reanchor.py FUZZY_WINDOW 同值)
        hit = None
        parsed = parse_anchor(anchor)
        if quote and parsed:
            sl = parsed[0]
            lo = max(1, sl - 3)
            hi = min(len(lines), parsed[2] + 3)
            starts = _line_starts(text)
            block_start = starts[lo - 1]
            block = "\n".join(lines[lo - 1 : hi])
            idx = block.find(quote)
            if idx >= 0:
                hit = (block_start + idx, block_start + idx + len(quote))
        if hit is not None:
            out.append(Span(anchor_from_range(text, hit[0], hit[1]), quote, content,
                            status, hit[0], hit[1], "fuzzy"))
        else:
            out.append(Span(anchor, quote, content, "outdated", None, None, "outdated"))
    return out


# ---------------------------------------------------------------------------
# markdown 最小处理(§4:标题/列表/代码围栏;逐源行分类,偏移以全文为准)
# ---------------------------------------------------------------------------


@dataclass
class SrcLine:
    """一条源行:disp_off/text 是画出来的内容(剥修饰后,仍是源行子串,
    逐字偏移 = disp_off + i);end_off = 行尾换行位(光标"行尾"落点)。"""

    line_off: int   # 源行起始偏移
    disp_off: int   # 显示文本首字符偏移
    text: str       # 显示文本(heading 剥 #、list/text 剥行首空白)
    kind: str       # heading / list / code / text / blank
    end_off: int    # 行末换行符位(EOF 行 = len(text))


def classify_lines(text: str) -> list[SrcLine]:
    """全文 → SrcLine 列表。``` 围栏行本身不产出行(但占全文偏移)。"""
    out: list[SrcLine] = []
    in_code = False
    offset = 0
    for raw in text.split("\n"):
        end_off = offset + len(raw)
        stripped = raw.strip()
        lead = len(raw) - len(raw.lstrip())
        if stripped.startswith("```"):
            in_code = not in_code
        elif in_code:
            out.append(SrcLine(offset, offset, raw, "code", end_off))
        elif not stripped:
            out.append(SrcLine(offset, offset, "", "blank", end_off))
        elif stripped.startswith("#"):
            content = stripped.lstrip("#").strip()
            disp = offset + raw.index(content) if content else offset + lead
            out.append(SrcLine(offset, disp, content, "heading", end_off))
        elif stripped[:2] in ("- ", "* ", "+ ") or _is_num_list(stripped):
            out.append(SrcLine(offset, offset + lead, stripped, "list", end_off))
        else:
            out.append(SrcLine(offset, offset, raw, "text", end_off))
        offset = end_off + 1
    return out


def _is_num_list(stripped: str) -> bool:
    num, dot, _rest = stripped.partition(". ")
    return dot == ". " and num.isdigit()


def paragraph_blocks(text: str) -> list[tuple[int, int]]:
    """段块边界(空行分块;段首 = 首内容字符偏移,半开)。段移动的着陆面。"""
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for ln in classify_lines(text):
        if ln.kind == "blank":
            if start is not None:
                blocks.append((start, ln.line_off))
                start = None
        elif start is None:
            start = ln.disp_off
    if start is not None:
        blocks.append((start, len(text)))
    return blocks


# ---------------------------------------------------------------------------
# 数据源(protocol + 三实现;写面只有 Online)
# ---------------------------------------------------------------------------

class DocSource(Protocol):
    """信箱/信纸的数据面。``writable`` = 批注写面开关;``supports_versions`` =
    版本面开关(T3;file/demo 无版本面,versions intent 给状态行人话)。

    版本面纪律:读面全量(tree/版本全文/diff 摘要);写面(rewind)只有
    Online,且**走 action 管道**(spawn kind+ref 去重 → doc.rewind;§1 五口)。
    """

    writable: bool
    supports_versions: bool

    def list_docs(self) -> list[DocEntry]: ...
    def read_letter(self, name: str) -> Letter: ...
    def save_annotation(self, name: str, anchor: str, quote: str, content: str) -> None: ...
    def delete_annotation(self, name: str, anchor: str) -> None: ...
    def versions(self, name: str) -> dict[str, Any]:
        """{base, versions: [{version, parent, at, source}]}(新→旧)。"""
        ...
    def read_version(self, name: str, version: str) -> str:
        """版本快照全文(只读);不存在 → FileNotFoundError。"""
        ...
    def diff_summary(self, name: str, from_v: str, to_v: str) -> str | None:
        """LLM 人话摘要(服务端 diffsum 缓存);不可用/故障 → None(调用方
        回落客户端行差集,红绿行)。"""
        ...
    def rewind(self, name: str, version: str) -> None:
        """回溯工作稿到指定版本(历史不动)。只读源 → PermissionError。"""
        ...


def line_diff(old: str, new: str, max_lines: int = 6) -> list[tuple[str, str]]:
    """客户端行差集兜底(stdlib difflib;diff-summary 故障/离线面用)。
    返回 [("+", 新增行) / ("-", 删除行)](双编码:符号前缀 + 色归渲染层);
    空 = 一致。``max_lines`` 截断(版本条同行随行显示,只取头部)。"""
    out: list[tuple[str, str]] = []
    for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if ln.startswith(("+++", "---", "@@")):
            continue
        sign = ln[:1]
        # 空行增删不记入:版本条同行摘要里一个裸 +/- 符号是噪声(实锤)
        if sign in ("+", "-") and ln[1:]:
            out.append((sign, ln[1:]))
        if len(out) >= max_lines:
            break
    return out


def _latest_version(versions: list[str]) -> str:
    return max(versions) if versions else ""


class OnlineDocSource:
    """打既有服务(统一信封;读面失败给空,错误留状态行不归数据源)。
    rewind 走 action 管道(godot 先例:spawn kind+ref 去重 → doc.rewind)。"""

    writable = True
    supports_versions = True

    def __init__(self, client: AgentOsClient) -> None:
        self._client = client
        self._pipeline: ActionPipeline | None = None  # build_app 注入(cascade 要 tree)
        self._apps: dict[str, Any] = {}  # name → AppInstance(spawn 去重缓存)

    @property
    def client(self) -> AgentOsClient:
        return self._client

    def attach_pipeline(self, pipeline: ActionPipeline) -> None:
        self._pipeline = pipeline

    def list_docs(self) -> list[DocEntry]:
        rows = AgentOsClient.json_or(self._client.list_docs(), [])
        out: list[DocEntry] = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name") or "")
            ann = self._annotation_count(name) if r.get("has_bubbles") else 0
            out.append(DocEntry(
                name=name,
                title=str(r.get("title") or name),
                saved_at=float(r.get("savedAt") or 0),
                annotations=ann,
            ))
        return out

    def read_letter(self, name: str) -> Letter:
        doc = AgentOsClient.json_or(self._client.read_doc(name), {})
        if not isinstance(doc, dict) or "text" not in doc:
            raise FileNotFoundError(f"文档不存在或不可读: {name}")
        meta = doc.get("meta") or {}
        versions = doc.get("versions") if isinstance(doc.get("versions"), list) else []
        text = str(doc.get("text") or "")
        records = AgentOsClient.json_or(self._client.read_annotations(name), [])
        return Letter(
            name=name,
            title=str(meta.get("title") or name),
            version=str(meta.get("baseVersion") or _latest_version([str(v) for v in versions])),
            text=text,
            spans=resolve_spans(text, records if isinstance(records, list) else []),
        )

    def save_annotation(self, name: str, anchor: str, quote: str, content: str) -> None:
        """upsert(同锚点覆盖、状态回 pending;端点约束在服务端)。"""
        resp = self._client.save_annotation(name, anchor, quote, content)
        if not resp.get("ok", False):
            raise RuntimeError(str(resp.get("error") or "未知错误"))

    def delete_annotation(self, name: str, anchor: str) -> None:
        resp = self._client.delete_annotation(name, anchor)
        if not resp.get("ok", False):
            raise RuntimeError(str(resp.get("error") or "未知错误"))

    def _annotation_count(self, name: str) -> int:
        rows = AgentOsClient.json_or(self._client.read_annotations(name), [])
        return len(rows) if isinstance(rows, list) else 0

    # ------------------------------------------------------------------
    # 版本面(T3;读面直给,rewind 走管道)
    # ------------------------------------------------------------------

    def versions(self, name: str) -> dict[str, Any]:
        tree = AgentOsClient.json_or(self._client.read_version_tree(name), {})
        if not isinstance(tree, dict):
            return {"base": "", "versions": []}
        vs = tree.get("versions")
        return {"base": str(tree.get("base") or ""),
                "versions": [v for v in vs if isinstance(v, dict)] if isinstance(vs, list) else []}

    def read_version(self, name: str, version: str) -> str:
        doc = AgentOsClient.json_or(self._client.read_doc_version(name, version), {})
        if not isinstance(doc, dict) or "text" not in doc:
            raise FileNotFoundError(f"版本不存在或不可读: {name} {version}")
        return str(doc.get("text") or "")

    def diff_summary(self, name: str, from_v: str, to_v: str) -> str | None:
        resp = self._client.diff_summary(name, from_v, to_v)
        if not resp.get("ok", False):
            return None  # LLM 故障(503)等:调用方回落行差集
        j = resp.get("json")
        if isinstance(j, dict) and j.get("summary"):
            return str(j["summary"])
        return None

    def rewind(self, name: str, version: str) -> None:
        """doc.rewind 走管道(历史不动)。客户端侧 expected 越界校验:
        目标版本须在版本清单内;``rolled_back_from`` 带当前 base(C2 留痕先例)。
        """
        if self._pipeline is None:
            raise RuntimeError("管道未装配(attach_pipeline 缺)")
        known = [str(v.get("version") or "") for v in self.versions(name)["versions"]]
        if version not in known:
            raise ValueError(f"版本越界: {version}(已知: {', '.join(known) or '空'})")
        app = self._apps.get(name)
        if app is None:
            resp = self._client.spawn("doc", name, name, {"name": name}, created_by="tui")
            if not resp.get("ok", False) or resp.get("app") is None:
                raise RuntimeError(f"spawn 失败: {resp.get('error') or '未知错误'}")
            app = resp["app"]
            self._apps[name] = app
        resp = self._pipeline.invoke(app, "doc.rewind",
                                     {"version": version,
                                      "rolled_back_from": self.versions(name)["base"]},
                                     "tab")
        if not resp.get("ok", False):
            raise RuntimeError(str(resp.get("error") or "未知错误"))


class OfflineDocSource:
    """离线回放(§0):直读 <docs_root> 产物目录;只读,无任何写面。
    T3 版本面:版本列表/快照全文可读;diff 摘要 → None(调用方行差集兜底);
    rewind 显式拒绝(离线只读)。"""

    writable = False
    supports_versions = True

    def __init__(self, docs_root: str | Path) -> None:
        self._store = DocStore(docs_root)

    def list_docs(self) -> list[DocEntry]:
        out: list[DocEntry] = []
        for r in self._store.list():
            name = str(r.get("name") or "")
            try:
                # 计数 = annotations/ 新记录 + bubbles/ 旧流压缩(读取面同语义;
                # 不能只看 has_bubbles——P2 新记录不写 bubbles/)
                ann = len(self._store.read_annotations(name))
            except (OSError, ValueError):
                ann = 0
            versions = self._store.list_versions(name)
            out.append(DocEntry(
                name=name,
                title=str(r.get("title") or name),
                version=str(versions[0]["version"]) if versions else "",
                saved_at=float(r.get("savedAt") or 0),
                annotations=ann,
            ))
        return out

    def read_letter(self, name: str) -> Letter:
        doc = self._store.read(name)  # FileNotFoundError 直传(app 层归状态行)
        meta = doc.get("meta") or {}
        versions = [str(v.get("version") or "") for v in self._store.list_versions(name)]
        text = str(doc.get("text") or "")
        return Letter(
            name=name,
            title=str(meta.get("title") or name),
            version=str(meta.get("baseVersion") or _latest_version(versions)),
            text=text,
            spans=resolve_spans(text, self._store.read_annotations(name)),
        )

    def save_annotation(self, name: str, anchor: str, quote: str, content: str) -> None:
        raise PermissionError("离线面只读")

    def delete_annotation(self, name: str, anchor: str) -> None:
        raise PermissionError("离线面只读")

    def versions(self, name: str) -> dict[str, Any]:
        """DocStore.list_versions(新→旧;parent 字段缺失 = 线性排)。"""
        meta = self._store.read(name)["meta"]
        vs = self._store.list_versions(name)
        return {
            "base": str(meta.get("baseVersion") or (vs[0]["version"] if vs else "")),
            "versions": [
                {"version": str(v.get("version") or ""), "parent": v.get("parent"),
                 "at": v.get("at", 0), "source": str(v.get("source") or "")}
                for v in vs
            ],
        }

    def read_version(self, name: str, version: str) -> str:
        return str(self._store.read_version(name, version)["text"])

    def diff_summary(self, name: str, from_v: str, to_v: str) -> str | None:
        # 离线无 LLM 面;difsum/ 缓存若落盘过也可读(版本不可变,缓存天然安全)
        cached = self._store.read_diffsum(name, from_v, to_v)
        return str(cached["summary"]) if cached else None

    def rewind(self, name: str, version: str) -> None:
        raise PermissionError("离线面只读(rewind 拒绝)")


# ---------------------------------------------------------------------------
# demo(内置两篇示例;--demo 无需服务即可体验;批注锚点用 helper 现场算,
# 保证字符精度面在 demo 下也是真的)
# ---------------------------------------------------------------------------

_DEMO_DOCS: dict[str, tuple[str, str]] = {
    "demo.letter_model": (
        "信件模型(示例)",
        """# 信件模型速览

文档是一封信:信箱排队,信纸只读,批注写在页边。

# 三条铁律

- 信纸不可直改,正文是只读投影
- 文档只以版本演进,每次变更都是一次可审计的 action
- 可变更加口只有五个,全部出海、全部过服务端裁决

# 使用循环

读信,写批注,撕回执,生成下一版,盖章封存。必要时回溯。

```
读信 → 批注 → 生成 → 盖章 → 回溯
```

这封信是内置示例,--demo 模式的第二封同理,无需任何服务。""",
    ),
    "demo.tui_design": (
        "终端宿主(示例)",
        """# 终端宿主原则

键位是借的,原则是守的。Vim/Emacs 只贡献手怎么动,不贡献能干什么。

# 双键位包

vim 包裸字母给阅读期高频动作;emacs 包移动全靠 Ctrl/Meta。

- 裸键只给阅读期高频动作,其余进命令条
- hint 栏与帮助面板从活动 keymap 生成,永不硬编码

# 渲染

全量重渲 cell buffer,CJK 双宽收口在一个文件,组件只消费语义 token。""",
    ),
}

_DEMO_QUOTES: dict[str, list[tuple[str, str]]] = {
    "demo.letter_model": [
        ("信纸不可直改,正文是只读投影", "这条是红线,示例批注一。"),
        ("全部出海、全部过服务端裁决", "示例批注二:客户端零裁决。"),
    ],
    "demo.tui_design": [
        ("hint 栏与帮助面板从活动 keymap 生成", "示例批注:按 ? 可见。"),
    ],
}


def _demo_records(name: str, text: str) -> list[dict[str, Any]]:
    """示例批注:quote 现场找偏移 → 字符精度锚点(与在线面同构)。"""
    records: list[dict[str, Any]] = []
    for quote, content in _DEMO_QUOTES.get(name, []):
        idx = text.find(quote)
        if idx < 0:
            continue
        records.append({
            "anchor": anchor_from_range(text, idx, idx + len(quote)),
            "quote": quote,
            "content": content,
            "status": "pending",
        })
    return records


class DemoDocSource:
    """内置示例(--demo):两篇文档 + 合成批注,走与离线源同一形状。只读。"""

    writable = False
    supports_versions = False  # demo 无版本面(versions intent → 状态行人话)

    _SAVED_AT = 1_756_500_000.0  # 固定时间戳:demo 渲染可复现(快照断言友好)

    def list_docs(self) -> list[DocEntry]:
        return [
            DocEntry(
                name=name,
                title=title,
                version="v001",
                saved_at=self._SAVED_AT,
                annotations=len(_demo_records(name, text)),
            )
            for name, (title, text) in _DEMO_DOCS.items()
        ]

    def read_letter(self, name: str) -> Letter:
        if name not in _DEMO_DOCS:
            raise FileNotFoundError(f"demo 文档不存在: {name}")
        title, text = _DEMO_DOCS[name]
        return Letter(
            name=name,
            title=title,
            version="v001",
            text=text,
            spans=resolve_spans(text, _demo_records(name, text)),
        )

    def save_annotation(self, name: str, anchor: str, quote: str, content: str) -> None:
        raise PermissionError("演示面只读")

    def delete_annotation(self, name: str, anchor: str) -> None:
        raise PermissionError("演示面只读")

    def versions(self, name: str) -> dict[str, Any]:
        return {"base": "", "versions": []}

    def read_version(self, name: str, version: str) -> str:
        raise FileNotFoundError(f"demo 无版本: {name} {version}")

    def diff_summary(self, name: str, from_v: str, to_v: str) -> str | None:
        return None

    def rewind(self, name: str, version: str) -> None:
        raise PermissionError("演示面只读")


# ---------------------------------------------------------------------------
# 位置参数 FILE... 直查(只读;不经 DocStore——任意本地文件,无批注面)
# ---------------------------------------------------------------------------

class FileDocSource:
    """``agent-os-tui FILE...``(只读):本地文件直接进信箱,一封一文件。
    不校验点分文档名(不是 DocStore 产物);批注写面显式拒绝(诚实优先)。"""

    writable = False
    supports_versions = False  # 本地文件无版本面

    def __init__(self, files: list[str]) -> None:
        self._files: dict[str, Path] = {}
        for i, f in enumerate(files):
            p = Path(f).expanduser()
            if not p.is_file():
                raise SystemExit(f"文件不存在或不可读: {f}")  # 启动即拒(同 --offline 缺 --docs-root)
            self._files[f"file.{i:03d}"] = p

    def list_docs(self) -> list[DocEntry]:
        out: list[DocEntry] = []
        for name, path in self._files.items():
            try:
                saved_at = path.stat().st_mtime
            except OSError:
                saved_at = 0.0
            out.append(DocEntry(name=name, title=path.name, saved_at=saved_at))
        return out

    def read_letter(self, name: str) -> Letter:
        path = self._files.get(name)
        if path is None:
            raise FileNotFoundError(f"未知文件: {name}")
        text = path.read_text(encoding="utf-8")  # OSError 直传(app 归状态行)
        return Letter(name=name, title=path.name, version="", text=text, spans=[])

    def save_annotation(self, name: str, anchor: str, quote: str, content: str) -> None:
        raise PermissionError("文件面只读")

    def delete_annotation(self, name: str, anchor: str) -> None:
        raise PermissionError("文件面只读")

    def versions(self, name: str) -> dict[str, Any]:
        return {"base": "", "versions": []}

    def read_version(self, name: str, version: str) -> str:
        raise FileNotFoundError(f"文件面无版本: {name} {version}")

    def diff_summary(self, name: str, from_v: str, to_v: str) -> str | None:
        return None

    def rewind(self, name: str, version: str) -> None:
        raise PermissionError("文件面只读")

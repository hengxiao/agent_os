"""AgentOsClient(docs/TUI-DOC.md §3/§7;对译 godot/kernel/agent_os_client.gd)。

Agent OS Web Platform 的 REST 客户端(web_platform/app.py 的端点契约)。
只负责出海;动作语义(白名单/闸门/promote)全部在服务端,客户端零裁决。
缺省 base_url = 本地实例(run-web.sh 缺省 8391),**含平台挂载前缀 /platform**
(godot 侧是在各方法里写死 "/platform/api/…",此处收到 base 里,行为同源)。

所有方法返回统一信封:``{ok: bool, status: int, json: …}`` 或
``{ok: false, status: int, error: str}``(FastAPI 的 {detail} 已拆包)。
超时两档:DEFAULT_TIMEOUT 30s / LLM_TIMEOUT 180s(comment/chat/review 起
LLM run,给宽松上限)。实现用项目既有依赖 httpx(同步面)。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8391/platform"

DEFAULT_TIMEOUT = 30.0
LLM_TIMEOUT = 180.0  # comment/chat/review/generate 起 LLM run


class AgentOsClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # 统一信封
    # ------------------------------------------------------------------

    def _send(self, method: str, path: str, body: Any, timeout: float) -> dict[str, Any]:
        url = self.base_url + path
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(method, url, json=body) if body is not None else client.request(method, url)
        except httpx.HTTPError as e:
            return {"ok": False, "status": 0, "error": f"HTTP 发起失败: {e}"}
        code = resp.status_code
        text = resp.text
        if code >= 400:
            return {"ok": False, "status": code, "error": self._extract_detail(text, code)}
        parsed: Any = None
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        return {"ok": True, "status": code, "json": parsed}

    @staticmethod
    def _extract_detail(text: str, code: int) -> str:
        j: Any = None
        if text:
            try:
                j = json.loads(text)
            except json.JSONDecodeError:
                j = None
        if isinstance(j, dict) and "detail" in j:
            return f"{code}: {j['detail']}"
        return f"{code}: {text[:200]}"

    @staticmethod
    def json_or(resp: dict[str, Any], fallback: Any) -> Any:
        """便捷解包:ok 时返回 json,否则返回默认值(读面用;写面请检查 ok)。"""
        if resp.get("ok", False):
            j = resp.get("json")
            if j is not None:
                return j
        return fallback

    @staticmethod
    def _enc(name: str) -> str:
        return quote(name, safe="")

    # ------------------------------------------------------------------
    # docs 读面(web_platform/app.py;docs/TUI-DOC.md §7 零新通道)
    # ------------------------------------------------------------------

    def list_docs(self) -> dict[str, Any]:
        """GET /api/docs:文档索引(标题/首行/字数/最近编辑)"""
        return self._send("GET", "/api/docs", None, DEFAULT_TIMEOUT)

    def create_doc(self, doc_name: str, title: str = "", text: str = "") -> dict[str, Any]:
        """POST /api/docs {name,title?,text?}:新建(点分名校验在 store;重名 409)"""
        return self._send("POST", "/api/docs", {"name": doc_name, "title": title, "text": text},
                          DEFAULT_TIMEOUT)

    def read_doc(self, doc_name: str) -> dict[str, Any]:
        """GET /api/docs/{name}:全文 + meta + versions + chat 种子"""
        return self._send("GET", f"/api/docs/{self._enc(doc_name)}", None, DEFAULT_TIMEOUT)

    def read_bubbles(self, doc_name: str) -> dict[str, Any]:
        """GET …/bubbles:全文档气泡流(服务端事实源)"""
        return self._send("GET", f"/api/docs/{self._enc(doc_name)}/bubbles", None, DEFAULT_TIMEOUT)

    def send_comment(self, doc_name: str, anchor: str, text: str, cascade: list[dict[str, Any]]) -> dict[str, Any]:
        """POST …/comment {anchor,text,cascade} → {reply,edits}(D2 专属端点先例)"""
        return self._send("POST", f"/api/docs/{self._enc(doc_name)}/comment",
                          {"anchor": anchor, "text": text, "cascade": cascade}, LLM_TIMEOUT)

    def send_chat(self, doc_name: str, text: str) -> dict[str, Any]:
        """POST …/chat {text} → {reply,changed};changed=服务端全文对比(不信技能自报)"""
        return self._send("POST", f"/api/docs/{self._enc(doc_name)}/chat", {"text": text}, LLM_TIMEOUT)

    def read_doc_version(self, doc_name: str, version: str) -> dict[str, Any]:
        """GET …/versions/{version}:读版本快照内容(只读)"""
        return self._send("GET", f"/api/docs/{self._enc(doc_name)}/versions/{self._enc(version)}",
                          None, DEFAULT_TIMEOUT)

    def read_version_tree(self, doc_name: str) -> dict[str, Any]:
        """GET …/versions/tree:版本树(parent 链 + 工作稿祖版;v1.8 树状模型)"""
        return self._send("GET", f"/api/docs/{self._enc(doc_name)}/versions/tree", None, DEFAULT_TIMEOUT)

    def diff_summary(self, doc_name: str, from_v: str, to_v: str) -> dict[str, Any]:
        """GET …/diff-summary?from=vA&to=vB:版本差异的 LLM 人话摘要(difsum 缓存在服务端)"""
        return self._send("GET",
                          f"/api/docs/{self._enc(doc_name)}/diff-summary?from={self._enc(from_v)}&to={self._enc(to_v)}",
                          None, LLM_TIMEOUT)

    def generate_doc(self, doc_name: str, user_prompt: str = "") -> dict[str, Any]:
        """POST …/generate {baseVersion?,userPrompt?}:批注批处理生成下一版"""
        body: dict[str, Any] = {}
        if user_prompt:
            body["userPrompt"] = user_prompt
        return self._send("POST", f"/api/docs/{self._enc(doc_name)}/generate", body, LLM_TIMEOUT)

    def review_doc(self, doc_name: str) -> dict[str, Any]:
        """POST …/review:全文评审 → 锚点批注集自动挂段(带 severity)"""
        return self._send("POST", f"/api/docs/{self._enc(doc_name)}/review", {}, LLM_TIMEOUT)

    # ------------------------------------------------------------------
    # 批注(P2 annotations:纯用户批注,无即时 AI 回复,攒着批处理)
    # ------------------------------------------------------------------

    def read_annotations(self, doc_name: str) -> dict[str, Any]:
        """GET …/annotations:批注列表(新记录 + bubbles/ 旧流压缩迁移,同锚点新优先)"""
        return self._send("GET", f"/api/docs/{self._enc(doc_name)}/annotations", None, DEFAULT_TIMEOUT)

    def save_annotation(self, doc_name: str, anchor: str, quote_text: str, content: str) -> dict[str, Any]:
        """POST …/annotations {anchor,quote,content}:单条 upsert(创建=pending)"""
        return self._send("POST", f"/api/docs/{self._enc(doc_name)}/annotations",
                          {"anchor": anchor, "quote": quote_text, "content": content}, DEFAULT_TIMEOUT)

    def delete_annotation(self, doc_name: str, anchor: str) -> dict[str, Any]:
        """POST …/bubbles/delete {anchor}:删除(bubbles/ 旧流 + annotations/ 新记录两面都删,幂等)"""
        return self._send("POST", f"/api/docs/{self._enc(doc_name)}/bubbles/delete",
                          {"anchor": anchor}, DEFAULT_TIMEOUT)

    # ------------------------------------------------------------------
    # app 管道(APP-MODEL §4)
    # ------------------------------------------------------------------

    def spawn(self, kind: str, ref_name: str, title: str, state: dict[str, Any], created_by: str = "") -> dict[str, Any]:
        """POST /api/apps/spawn {kind,ref,title,state,created_by} → AppInstance(kind+ref 去重)"""
        from agent_os.host.tui.kernel.pipeline import AppInstance  # 延迟导入防环

        resp = self._send("POST", "/api/apps/spawn", {
            "kind": kind,
            "ref": ref_name,
            "title": title if title else ref_name,
            "state": state,
            "created_by": created_by,
        }, DEFAULT_TIMEOUT)
        if resp.get("ok", False) and isinstance(resp.get("json"), dict):
            inst = resp["json"].get("instance")
            if isinstance(inst, dict):
                resp["app"] = AppInstance.from_json(inst)
        return resp

    def invoke_action(self, instance_id: str, action_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/apps/{id}/actions/{action}(surface/args/session_id/cascade)"""
        return self._send("POST",
                          f"/api/apps/{self._enc(instance_id)}/actions/{self._enc(action_id)}",
                          body, DEFAULT_TIMEOUT)

    def ping(self) -> bool:
        """连通性探针(sessions 数组读面;兼作状态点数据源)"""
        resp = self._send("GET", "/api/sessions", None, 8.0)
        return bool(resp.get("ok", False))

    # ------------------------------------------------------------------
    # 调试器(docs/TUI-DEBUG.md §3;/api/debug/* 一族挂在 web 主 app,
    # **无 /platform 前缀**——调用方用 AgentOsClient("http://127.0.0.1:8391")
    # 裸 base 构造;信封纪律与上面完全一致)
    # ------------------------------------------------------------------

    def debug_open_session(self, skill: str | None, run_input: dict[str, Any] | None,
                           breakpoints: list[tuple[str, str]],
                           replay_run_id: str | None = None,
                           until_step: int | None = None) -> dict[str, Any]:
        """POST /api/debug/sessions:live ``{skill,input}`` 或 replay
        ``{replay_run_id,until_step?}``(互斥,校验在服务端)→ {session_id,run_id};
        run 校验失败归 200+{"status":"failed"}(§3.3 同归类)。"""
        body: dict[str, Any] = {
            "breakpoints": [{"kind": k, "match": m} for k, m in breakpoints],
        }
        if replay_run_id is not None:
            body["replay_run_id"] = replay_run_id
            if until_step is not None:
                body["until_step"] = until_step
        else:
            body["skill"] = skill
            body["input"] = run_input if run_input is not None else {}
        return self._send("POST", "/api/debug/sessions", body, DEFAULT_TIMEOUT)

    def debug_snapshot(self, sid: str) -> dict[str, Any]:
        """GET /api/debug/sessions/{sid}:state/pause_point/breakpoints/frame_stack"""
        return self._send("GET", f"/api/debug/sessions/{self._enc(sid)}", None,
                          DEFAULT_TIMEOUT)

    def debug_close(self, sid: str) -> dict[str, Any]:
        """DELETE /api/debug/sessions/{sid}:detach 放行(D4 面,端点已存在)"""
        return self._send("DELETE", f"/api/debug/sessions/{self._enc(sid)}", None,
                          DEFAULT_TIMEOUT)

    def debug_add_breakpoint(self, sid: str, kind: str, match: str = "*") -> dict[str, Any]:
        """POST …/breakpoints {kind,match}(REST 无 until 字段——until 步数断点
        仅 replay 的 until_step / 本地源可用)"""
        return self._send("POST", f"/api/debug/sessions/{self._enc(sid)}/breakpoints",
                          {"kind": kind, "match": match}, DEFAULT_TIMEOUT)

    def debug_remove_breakpoint(self, sid: str, bp_id: str) -> dict[str, Any]:
        """DELETE …/breakpoints/{bp_id}"""
        return self._send("DELETE",
                          f"/api/debug/sessions/{self._enc(sid)}/breakpoints/{self._enc(bp_id)}",
                          None, DEFAULT_TIMEOUT)

    def debug_command(self, sid: str, cmd: str) -> dict[str, Any]:
        """POST …/command {cmd}:continue/step_into/step_over/step_out/stop
        (仅 paused)/ pause(仅 running,GDB SIGINT 语义)"""
        return self._send("POST", f"/api/debug/sessions/{self._enc(sid)}/command",
                          {"cmd": cmd}, DEFAULT_TIMEOUT)

    def debug_frame(self, sid: str, fid: str) -> dict[str, Any]:
        """GET …/frames/{fid}:live 帧检视(messages/working/usage;404 = 帧不在)"""
        return self._send("GET",
                          f"/api/debug/sessions/{self._enc(sid)}/frames/{self._enc(fid)}",
                          None, DEFAULT_TIMEOUT)

    def debug_modify(self, sid: str, patch: dict[str, Any]) -> dict[str, Any]:
        """POST …/modify {patch}:改本次工具调用参数(仅停在 pre:tool.call;
        改完即放行;409 = 未暂停/停错点)"""
        return self._send("POST", f"/api/debug/sessions/{self._enc(sid)}/modify",
                          {"patch": patch}, DEFAULT_TIMEOUT)

    def debug_inject(self, sid: str, text: str,
                     frame_id: str | None = None) -> dict[str, Any]:
        """POST …/inject {text,frame_id?}:注入 user 消息(缺省暂停帧;注入即放行)
        → {ok, frame_id}"""
        body: dict[str, Any] = {"text": text}
        if frame_id is not None:
            body["frame_id"] = frame_id
        return self._send("POST", f"/api/debug/sessions/{self._enc(sid)}/inject",
                          body, DEFAULT_TIMEOUT)

    def debug_rerun(self, sid: str) -> dict[str, Any]:
        """POST …/rerun:以创建参数重开新会话 → {session_id, run_id};
        无 origin(replay/CLI 会话)→ 400"""
        return self._send("POST", f"/api/debug/sessions/{self._enc(sid)}/rerun",
                          None, DEFAULT_TIMEOUT)

    @staticmethod
    def debug_stream_path(sid: str) -> str:
        """调试会话 SSE 路径(SseClient start_stream 的 path 参数)"""
        return f"/api/debug/sessions/{quote(sid, safe='')}/stream"

    def run_signals(self, run_id: str) -> dict[str, Any]:
        """GET /api/runs/{rid}/signals:run 信号流(调试轨迹数据源;SSE 不覆盖它)"""
        return self._send("GET", f"/api/runs/{self._enc(run_id)}/signals", None,
                          DEFAULT_TIMEOUT)

    def run_detail(self, run_id: str) -> dict[str, Any]:
        """GET /api/runs/{rid}:终态 status 回填(SSE run_end 缺 status 的轮询路径)"""
        return self._send("GET", f"/api/runs/{self._enc(run_id)}", None, DEFAULT_TIMEOUT)

    def list_skills(self) -> dict[str, Any]:
        """GET /api/skills:技能清单(run 表单/连通性探针)"""
        return self._send("GET", "/api/skills", None, DEFAULT_TIMEOUT)

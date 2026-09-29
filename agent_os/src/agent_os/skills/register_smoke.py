"""register() 验证门默认实现:草稿重放 + expected/expect 双判定
(docs/DESIGN.md §6.2 验证门;docs/reports/ch08-self-evolution.md 验证门)。

``LocalFileSkillRegistry.register`` 步骤 4 的可选 smoke 钩子(``bind_register_smoke``)
此前只有扩展点、没有默认实现;本模块补上默认形态——**拿注册管线归一化出的生产条目
entry,在一次性内核里用草稿 ``drafts/<name>/tests/*.json`` 的重放用例跑真 run**,
全部用例通过才放行(对生产只读;拒绝即 register 中止、生产零变化)。

用例契约(docs/SKILL-DEV.md 用例形态 + 本里程碑新增的两个判定键)::

    {"input": {...},               # 必填(缺省 {}):技能输入
     "mock_script": [...],         # 可选:dict 形态 ChatResponse 列表(确定性重放;
                                   #   不给则用冒烟内核装配的 provider 真跑)
     "expected": <任意 JSON 值>,    # 可选:结构归一后深比较(确定性判定)
     "expect": "自然语言期望"}      # 可选:LLM 裁判(严格 JSON {"pass","reason"})

判定链(首个失败即定案):run 异常 → outputs schema 校验(内核输出闸的双保险)
→ expected 深比较 → expect LLM 裁判;既无 expected 也无 expect 的用例跑通即过
(纯冒烟)。

v1 边界(全部 fail-closed):

- **code 技能不支持**:handler 源码落盘发生在验证门之后(register 步骤 6),
  entry 内无可执行源码——需验证 code 技能请用 ``register_smoke="pkg.mod:func"``
  自定义 hook;
- **无草稿 / 无用例 = 拒**:默认验证门是写路径闸门,没有证据即不放行(不与
  Lab G4"无用例 warn"的编辑器哲学对齐);
- 冒烟内核由配置工厂(runtime/config.py ``_smoke_kernel_factory``)按例重建,
  剥离 watcher/MCP/sidecars 防按例泄漏与蒸馏副作用,宿主通道(user_channel /
  supervisor handler)不注入——候选技能引用 mcp.* 工具时用例 fail-closed;
- expect 判定要求注入裁判 provider(``[providers]`` + ``[skills]
  register_judge_model``),未配置时用例 fail-closed 并给配置指引。

依赖方向纪律:本模块在 skills/ 层,只依赖 skills.*/providers.*/api.*/kernel.* 与
jsonschema;overlay 换接、mock_script → ChatResponse 转换、MockProvider 替换三处与
host/(run_manager/app/replay)同逻辑,按"接受的 flagged 重复"内联(注释注明出处),
不反向 import host/。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import jsonschema

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)
from agent_os.kernel.errors import SkillLoadError
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider
from agent_os.skills.compound import CompoundSkillRegistry, StaticSkillRegistry
from agent_os.skills.loader import materialize
from agent_os.skills.manifest import parse_manifest

_log = logging.getLogger(__name__)

#: 单次验证门最多重放的用例数(注册是同步闸门,不能无界跑;超出截断,记 log + detail 注明)
_DEFAULT_MAX_CASES = 8

#: 无草稿 / 无用例时 fail-closed detail 共用的约定指引(告诉调用方怎么补证据)
_CASE_CONVENTION = (
    "默认验证门需要 drafts/<name>/tests/*.json 重放用例(至少一个),用例形如 "
    '{"input": {...}, "mock_script": [...], "expected"|"expect": ...}'
)

#: expect 自然语言期望的裁判系统提示。输出契约严格 JSON、消费侧 fail-closed,
#: 同 DistillSidecar verify 档(sidecars/builtins.py DISTILL_SYSTEM_REVIEW)。
_JUDGE_SYSTEM = """\
你是技能注册验证门的判定员。下面 USER 消息是一个重放用例的 JSON:
{"input": 技能输入, "expect": 对技能输出的自然语言期望, "result": 技能实际输出}。
请判定 result 是否满足 expect 描述的期望(只判 expect 写明的点,不额外苛求)。

输出契约:只输出严格 JSON,形如 {"pass": true} 或 {"pass": false, "reason": "一句话原因"};
不要输出任何其他文字,不要 markdown 围栏。"""

#: 聚合 detail 的长度上限(GateError 消息/provenance jsonl 都要装;逐用例 reason 已分别截断)
_DETAIL_MAX_CHARS = 400


class DefaultRegisterSmoke:
    """register() 验证门默认实现(docs/DESIGN.md §6.2;docs/reports/ch08-self-evolution.md 验证门)。

    ``production``:生产 skills registry(候选技能子技能引用的解析兜底);
    ``make_kernel``:零参工厂,每次调用产出一个全新装配的内核(按例重建,
    配置工厂已剥离 watcher/MCP/sidecars);``store``:DraftStore(用例来源,
    None = 一切注册 fail-closed);``judge_providers``/``judge_model``:expect
    判定的 LLM 裁判面(任意带 ``async chat(ChatRequest)`` 的对象;None =
    带 expect 的用例 fail-closed);``max_cases``:单次验证门用例数上限。
    """

    def __init__(
        self,
        *,
        production: Any,
        make_kernel: Any,
        store: Any = None,
        judge_providers: Any = None,
        judge_model: str = "",
        max_cases: int = _DEFAULT_MAX_CASES,
    ) -> None:
        self._production = production
        self._make_kernel = make_kernel
        self._store = store
        self._judge_providers = judge_providers
        self._judge_model = judge_model
        self._max_cases = max_cases

    async def __call__(self, name: str, entry: dict[str, Any]) -> dict[str, Any]:
        """``bind_register_smoke`` 契约:``{"ok": bool, "detail"?: str}``(附 ``cases`` 证据面)。"""
        # —— 1. code 技能 v1 fail-closed(handler 落盘在验证门之后,entry 内无源码可执行)——
        if entry.get("kind") == "code":
            return {
                "ok": False,
                "detail": "code 技能暂不支持默认重放验证门(handler 落盘在验证门之后,"
                "entry 内无源码可执行);如需验证 code 技能请用 "
                'register_smoke="pkg.mod:func" 自定义 hook',
            }

        # —— 2. 草稿重放用例(写路径闸门:没有证据即不放行,fail-closed)——
        if self._store is None:
            return {"ok": False, "detail": f"默认验证门未配置草稿存储(store);{_CASE_CONVENTION}"}
        try:
            draft = self._store.read(name)
        except (FileNotFoundError, SkillLoadError, ValueError) as e:
            return {"ok": False, "detail": f"默认验证门读草稿失败({e});{_CASE_CONVENTION}"}
        tests = draft.get("tests") or {}
        if not tests:
            return {"ok": False, "detail": f"草稿 {name} 没有任何重放用例;{_CASE_CONVENTION}"}

        # —— 3. 按文件名排序;超过 max_cases 截断(记 log + detail 注明,不静默少跑)——
        fnames = sorted(tests)
        note = ""
        if len(fnames) > self._max_cases:
            skipped = fnames[self._max_cases :]
            note = (
                f";注意:用例共 {len(fnames)} 个,超过 max_cases={self._max_cases},"
                f"只跑前 {self._max_cases} 个(跳过: {', '.join(skipped)})"
            )
            _log.warning(
                "register 验证门: 技能 %s 用例 %d 个超过上限 %d,截断(跳过: %s)",
                name,
                len(fnames),
                self._max_cases,
                skipped,
            )
            fnames = fnames[: self._max_cases]

        # —— 4. 从 entry 物化候选技能(验证门验的是"正在被注册的条目",不是草稿文件;
        #    parse → prompt 补丁 → materialize 三件套同 gate.py validate_draft 先例)——
        try:
            raw = {k: v for k, v in entry.items() if k != "prompt"}
            manifest = parse_manifest(raw)
            manifest.prompt = entry.get("prompt") or ""  # prompt.md 即指令体(§1.2)
            skill = materialize(manifest)
        except (SkillLoadError, ValueError) as e:
            return {
                "ok": False,
                "detail": f"候选技能物化失败(fail-closed;register 前置闸门应已拦截): {e}",
            }
        # 候选层优先、生产层兜底(子技能引用解析);writable 指生产层下标、
        # names 双层标注,同 OverlaySkillRegistry 的 super().__init__ 先例
        # (draft_store.py)——本组合只读,不触碰写入面
        overlay = CompoundSkillRegistry(
            [StaticSkillRegistry({name: skill}), self._production],
            writable=1,
            names=["candidate", "production"],
        )
        outputs = entry.get("outputs") or {}

        # —— 5/6. 逐用例重放 + 判定(首个失败即定案,逐例记录)——
        cases: list[dict[str, Any]] = []
        for fname in fnames:
            cases.append(await self._run_case(name, fname, tests[fname], overlay, outputs))

        # —— 7. 聚合:全过才放行;失败 detail 截断 ≤400 字符(GateError/jsonl 都要装)——
        failures = [c for c in cases if not c["ok"]]
        if not failures:
            return {
                "ok": True,
                "detail": f"默认重放验证门通过:{len(cases)} 个用例全部成功{note}",
                "cases": cases,
            }
        budget = _DETAIL_MAX_CHARS - len(note)
        detail = "; ".join(f"{c['case']}: {c['reason']}" for c in failures)
        if len(detail) > budget:
            detail = detail[: max(budget - 3, 0)] + "..."
        return {"ok": False, "detail": f"{detail}{note}", "cases": cases}

    async def _run_case(
        self,
        name: str,
        fname: str,
        raw_case: Any,
        overlay: Any,
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        """单用例:一次性内核 + overlay 换接 + (可选)MockProvider 确定性重放 → 判定链。"""
        try:
            case = _parse_case(raw_case)
        except (TypeError, ValueError) as e:
            return {"case": fname, "ok": False, "reason": str(e)}

        kernel = self._make_kernel()
        # overlay 换进内核全部 skills 引用点(host/web/run_manager.py swap_skills_overlay
        # 同款三处接线;接受的 flagged 重复——skills/ 层不反向依赖 host/)
        kernel.skills = overlay
        if getattr(kernel.context, "_skills", None) is not None:
            kernel.context._skills = overlay
        if getattr(kernel.tools, "_skills", None) is not None:
            kernel.tools._skills = overlay

        script = case.get("mock_script")
        if script:
            # dict 形态 mock_script → ChatResponse 列表(host/web/app.py
            # _lab_replace_providers 同款转换)+ provider 面整体替换
            # (host/shared/replay.py replace_providers 同款;均为接受的 flagged 重复)
            responses = [_chat_response_from_dict(item) for item in script]
            prefix = (kernel.config.model or "").partition("/")[0]
            kernel.providers = ProviderManager(
                [MockProvider(script=responses, name=prefix or None)]
            )

        try:
            result = await kernel.run(name, case.get("input") or {})
        except Exception as e:  # noqa: BLE001 — 重放任何失败都归"用例失败",不能炸穿钩子
            return {"case": fname, "ok": False, "reason": f"run 异常: {type(e).__name__}: {e}"}

        # —— 判定链(首个失败即返回;outputs 校验是内核输出闸的双保险,同 Lab /check)——
        if outputs:
            try:
                jsonschema.validate(result, outputs)
            except jsonschema.ValidationError as e:
                return {"case": fname, "ok": False, "reason": f"outputs 校验失败: {e.message}"}
        if "expected" in case and _normalize(result) != _normalize(case["expected"]):
            return {
                "case": fname,
                "ok": False,
                "reason": "expected 深比较不一致: "
                f"expected={json.dumps(_normalize(case['expected']), ensure_ascii=False, default=str)[:120]} "
                f"actual={json.dumps(_normalize(result), ensure_ascii=False, default=str)[:120]}",
            }
        if "expect" in case:
            ok, reason = await self._judge(case, result)
            if not ok:
                return {"case": fname, "ok": False, "reason": reason}
        # 既无 expected 也无 expect:跑通 + outputs 过即算过(纯冒烟)
        return {"case": fname, "ok": True, "reason": ""}

    async def _judge(self, case: dict[str, Any], result: Any) -> tuple[bool, str]:
        """expect 的 LLM 裁判:严格 JSON ``{"pass","reason"}`` 契约,fail-closed 语义同
        DistillSidecar verify 档(sidecars/builtins.py ``_review``)。返回 ``(通过, 原因)``。"""
        if self._judge_providers is None:
            return False, (
                "用例声明了 expect 自然语言期望,但默认验证门未配置裁判:请在 [providers] "
                "配置可用 provider 并用 [skills] register_judge_model 指定裁判模型"
                "(或改用 expected 确定性深比较)"
            )
        user = json.dumps(
            {"input": case.get("input"), "expect": case["expect"], "result": result},
            ensure_ascii=False,
            default=str,
        )
        try:
            resp = await self._judge_providers.chat(
                ChatRequest(
                    model=self._judge_model,
                    messages=[
                        Message(role=Role.SYSTEM, content=_JUDGE_SYSTEM),
                        Message(role=Role.USER, content=user),
                    ],
                    temperature=0.0,  # 判定要确定性(DistillSidecar verify 同款)
                )
            )
        except Exception as e:  # noqa: BLE001 — 裁判故障 fail-closed,不炸穿钩子
            return False, f"裁判 LLM 调用异常(fail-closed): {type(e).__name__}: {e}"
        raw = (resp.message.content or "").strip()
        if raw.startswith("```"):  # 容错:剥 ```json / ``` 围栏(_review 同款)
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
        try:
            verdict = json.loads(raw)
        except ValueError:
            return False, f"裁判输出非 JSON(fail-closed): {raw[:200]}"
        passed = verdict.get("pass") if isinstance(verdict, dict) else None
        if not isinstance(passed, bool):
            return False, f"裁判输出缺 pass 键或类型不对(fail-closed): {raw[:200]}"
        if not passed:
            return False, f"裁判不通过: {verdict.get('reason') or '(无理由)'}"
        return True, ""


def _parse_case(raw: Any) -> dict[str, Any]:
    """DraftStore.read 的 tests 值是文件原文 str(save 也接受 dict 注入,两种都容忍)。"""
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"用例 JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise TypeError(f"用例应为 JSON object,得到: {type(data).__name__}")
    return data


def _chat_response_from_dict(item: dict[str, Any]) -> ChatResponse:
    """dict 形态 mock_script 项 → ChatResponse(host/web/app.py ``_lab_replace_providers``
    同款转换,字段逐字对齐契约层;接受的 flagged 重复——skills/ 层不反向依赖 host/)。
    """
    msg = item.get("message") or {}
    usage = item.get("usage") or {}
    return ChatResponse(
        message=Message(
            role=Role(msg.get("role", "assistant")),
            content=msg.get("content", ""),
            tool_calls=[
                ToolCall(
                    id=str(tc.get("id", "")),
                    name=str(tc.get("name", "")),
                    args=dict(tc.get("args") or {}),
                )
                for tc in msg.get("tool_calls") or []
            ],
        ),
        finish_reason=item.get("finish_reason", "stop"),
        usage=ChatUsage(
            prompt=usage.get("prompt", 0),
            completion=usage.get("completion", 0),
            cost=usage.get("cost", 0.0),
        ),
    )


def _normalize(value: Any) -> Any:
    """expected 深比较的结构归一(std/learn_handlers.py ``verify_before_store`` 同款语义,
    std/memory.yaml common.memory.verify;std 处理器不在包 import 路径上,此处内联):
    dict 键排序(递归);tuple → list;int/float 统一为 float;bool 加类型标签
    (Python 里 ``True == 1.0``,不打标会被数字混同);非 JSON 类型按"类型名 + 字符串化"兜底。
    """
    if isinstance(value, dict):
        return {str(k): _normalize(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, bool):
        return ("__bool__", value)
    if isinstance(value, (int, float)):
        return float(value)
    if value is None or isinstance(value, str):
        return value
    return (type(value).__name__, str(value))

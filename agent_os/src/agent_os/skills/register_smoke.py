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

证据面(2026-09-30 扩展;两阶段都要过,引用证据**不免除**草稿用例):

- **(A) 录制 run 重放**:三参契约的 ``provenance`` 携带 ``detail["source_run_id"]``
  (非空 str)时,先用 telemetry/replay.py 重建的录制 mock 脚本确定性重放源技能
  (``<runs_root>/<source_run_id>/`` 的 meta/trace/checkpoint/result 四件套),
  判定任务真完成:重放成功 + 结果与录制 result 归一化一致 + 录制 status=="done"
  + (配置裁判时)LLM 裁判"任务真完成"判定;目录/产物缺失畸形、源技能已不在
  注册表、结果与录制不符、裁判否决一律 fail-closed;
- **(B) code 技能真冒烟**:register() 管线以 ``entry["_source"]`` 随调用携带
  handler 源码(local_file.py 步骤 4 拷贝注入,不落生产 yaml);验证门把源码
  登台为临时目录的 ``generated_handlers`` 正规包(置顶 sys.path——沙箱
  PYTHONPATH 在 exec 时取宿主 sys.path 构建,logic/python_sandbox.py:325),
  用例判定链与 prompt 技能同一套(code 技能不调 LLM,用例即 input+expected)。

v1 边界(全部 fail-closed):

- **code 技能无 ``_source`` = 拒**:handler 源码落盘发生在验证门之后(register
  步骤 6),绕过 register() 管线直接调用本钩子时 entry 内无可执行源码——
  需自定义验证请用 ``register_smoke="pkg.mod:func"`` 自定义 hook;
- **冒烟内核无 sandbox 后端 = code 用例拒**:code 技能强制沙箱执行(§6.2),
  ``[tools] python_exec = off`` 时没有沙箱可跑,fail-closed 并给配置指引;
- **无草稿 / 无用例 = 拒**:默认验证门是写路径闸门,没有证据即不放行(不与
  Lab G4"无用例 warn"的编辑器哲学对齐);
- 冒烟内核由配置工厂(runtime/config.py ``_smoke_kernel_factory``)按例重建,
  剥离 watcher/MCP/sidecars 防按例泄漏与蒸馏副作用,宿主通道(user_channel /
  supervisor handler)不注入——候选技能引用 mcp.* 工具时用例 fail-closed;
- expect 判定要求注入裁判 provider(``[providers]`` + ``[skills]
  register_judge_model``),未配置时用例 fail-closed 并给配置指引;
  录制重放阶段相反:裁判缺席降级为"仅确定性比对"(detail 注明),不拒。

依赖方向纪律:本模块在 skills/ 层,只依赖 skills.*/providers.*/api.*/kernel.*/
telemetry.* 与 jsonschema;overlay 换接、mock_script → ChatResponse 转换、
MockProvider 替换三处与 host/(run_manager/app/replay)同逻辑,按"接受的 flagged
重复"内联(注释注明出处),不反向 import host/(replay 脚本重建已下沉
telemetry/replay.py,正是为了让本模块合法消费)。
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import re
import sys
import tempfile
from pathlib import Path
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
from agent_os.telemetry.replay import build_mock_script, replace_providers

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

#: 录制重放阶段(A)的裁判系统提示:判"源 run 任务真完成"(契约与 expect 档相同)。
_REPLAY_JUDGE_SYSTEM = """\
你是技能注册验证门的判定员。下面 USER 消息是一条已录制动机 run 的证据 JSON:
{"input": 源 run 输入, "recorded_result": 源 run 录制结果,
 "candidate_skill": 正在注册的候选技能 {"name", "description"}}。
候选技能声称沉淀自该 run 解决的任务。请判定:源 run 的任务是否真正完成
(recorded_result 对 input 构成实质解答,而非占位/报错/空壳)。

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
    带 expect 的用例 fail-closed;录制重放阶段则降级为仅确定性比对);
    ``max_cases``:单次验证门用例数上限;``runs_root``:run 产物根目录
    (录制重放证据阶段的取数处,缺省 ``.agent-os/runs``,同 CLI --artifacts
    缺省)。
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
        runs_root: str = ".agent-os/runs",
    ) -> None:
        self._production = production
        self._make_kernel = make_kernel
        self._store = store
        self._judge_providers = judge_providers
        self._judge_model = judge_model
        self._max_cases = max_cases
        self._runs_root = runs_root

    async def __call__(
        self, name: str, entry: dict[str, Any], provenance: Any = None
    ) -> dict[str, Any]:
        """``bind_register_smoke`` 契约:``{"ok": bool, "detail"?: str}``(附 ``cases`` 证据面)。

        三参形态(2026-09-30 契约扩展):``provenance`` 携带
        ``detail["source_run_id"]`` 时先跑录制重放证据阶段(A);code 技能从
        ``entry["_source"]`` 取 handler 源码真冒烟(B)。两参调用行为与引入前一致。
        """
        source_run_id = _source_run_id_of(provenance)
        is_code = entry.get("kind") == "code"
        source = entry.get("_source") if is_code else ""
        # —— 1. code 技能无源码 fail-closed(handler 落盘在验证门之后,entry 内无源码
        #    可执行;register() 管线会以 _source 随调用携带,缺失 = 绕过管线的直接调用)——
        if is_code and not (isinstance(source, str) and source.strip()):
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
        #    parse → prompt 补丁 → materialize 三件套同 gate.py validate_draft 先例;
        #    _source 是随调用携带的冒烟源码,不属于 manifest,剔除)——
        try:
            raw = {k: v for k, v in entry.items() if k not in ("prompt", "_source")}
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

        # —— 5. (A) 录制 run 重放证据阶段(先于草稿用例;首个失败即定案)——
        cases: list[dict[str, Any]] = []
        replay_note = ""
        if source_run_id:
            verdict, replay_note = await self._replay_source_run(name, entry, source_run_id)
            cases.append(verdict)
            if not verdict["ok"]:
                detail = f"{verdict['case']}: {verdict['reason']}"
                if len(detail) > _DETAIL_MAX_CHARS:
                    detail = detail[: _DETAIL_MAX_CHARS - 3] + "..."
                return {"ok": False, "detail": detail, "cases": cases}

        # —— 6. 逐用例重放 + 判定(首个失败即定案,逐例记录;code 技能在临时 handler
        #    登台上下文里跑——沙箱 PYTHONPATH 取宿主 sys.path,见 _staged_code_handler)——
        if is_code:
            with _staged_code_handler(name, source):
                for fname in fnames:
                    cases.append(
                        await self._run_case(
                            name, fname, tests[fname], overlay, outputs, require_sandbox=True
                        )
                    )
        else:
            for fname in fnames:
                cases.append(await self._run_case(name, fname, tests[fname], overlay, outputs))

        # —— 7. 聚合:全过才放行;失败 detail 截断 ≤400 字符(GateError/jsonl 都要装)——
        failures = [c for c in cases if not c["ok"]]
        if not failures:
            return {
                "ok": True,
                "detail": f"默认重放验证门通过:{replay_note}{len(fnames)} 个用例全部成功{note}",
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
        *,
        require_sandbox: bool = False,
    ) -> dict[str, Any]:
        """单用例:一次性内核 + overlay 换接 + (可选)MockProvider 确定性重放 → 判定链。

        ``require_sandbox``(code 技能用):code 技能强制沙箱执行(§6.2),冒烟内核
        无 SANDBOX 后端时用例 fail-closed 并给 ``[tools] python_exec`` 配置指引。
        """
        try:
            case = _parse_case(raw_case)
        except (TypeError, ValueError) as e:
            return {"case": fname, "ok": False, "reason": str(e)}

        kernel = self._make_kernel()
        if require_sandbox:
            logic = getattr(kernel, "logic", None)
            route_sandbox = getattr(logic, "route_sandbox", None)
            if route_sandbox is None or route_sandbox() is None:
                return {
                    "case": fname,
                    "ok": False,
                    "reason": "code 技能冒烟需要 sandbox Logic Kernel,但冒烟内核未装配"
                    "([tools] python_exec = off 时没有沙箱,code 技能强制沙箱执行,§6.2);"
                    "请开启 [tools] python_exec(subprocess|docker),或用 "
                    'register_smoke="pkg.mod:func" 自定义 hook',
                }
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
        return await self._judge_chat(_JUDGE_SYSTEM, user)

    async def _judge_replay(
        self, meta: dict[str, Any], recorded: dict[str, Any], entry: dict[str, Any]
    ) -> tuple[bool, str]:
        """录制重放阶段的 LLM 裁判:"源 run 任务真完成?"——契约与 expect 档相同。

        输入 = 源 run 输入 + 录制结果 + 候选技能描述(调用方保证裁判已配置)。
        """
        user = json.dumps(
            {
                "input": meta.get("input"),
                "recorded_result": recorded.get("result"),
                "candidate_skill": {
                    "name": entry.get("name"),
                    "description": entry.get("description") or "",
                },
            },
            ensure_ascii=False,
            default=str,
        )
        ok, reason = await self._judge_chat(_REPLAY_JUDGE_SYSTEM, user)
        return (True, "") if ok else (False, f"录制重放判定:{reason}")

    async def _judge_chat(self, system: str, user: str) -> tuple[bool, str]:
        """裁判调用 + 严格 JSON 解析(expect 与录制重放两档共用;全部 fail-closed)。"""
        try:
            resp = await self._judge_providers.chat(
                ChatRequest(
                    model=self._judge_model,
                    messages=[
                        Message(role=Role.SYSTEM, content=system),
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

    async def _replay_source_run(
        self, name: str, entry: dict[str, Any], source_run_id: str
    ) -> tuple[dict[str, Any], str]:
        """(A) 录制 run 重放证据:确定性重放被引用的动机 run,判定任务真完成。

        返回 ``(用例形态 verdict, 通过时进 detail 的注记)``。全部失败分支
        fail-closed:引用的证据必须存在且可复现。verdict 的 ``case`` 标签取
        ``replay:<source_run_id>``,与草稿用例(文件名)区分。
        """
        del name  # 重放对象是源技能(meta.json 记录),不是候选技能
        label = f"replay:{source_run_id}"

        def _fail(reason: str) -> tuple[dict[str, Any], str]:
            return {"case": label, "ok": False, "reason": reason}, ""

        # 1. 引用证据必须存在且可复现(fail-closed;路径穿越形态直接拒)
        if "/" in source_run_id or "\\" in source_run_id or ".." in source_run_id:
            return _fail(f"source_run_id 含路径分隔符/父目录引用(fail-closed): {source_run_id!r}")
        run_dir = Path(self._runs_root) / source_run_id
        if not run_dir.is_dir():
            return _fail(f"引用的录制 run 目录不存在(fail-closed): {run_dir}")
        try:
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            recorded = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            script = build_mock_script(run_dir)  # trace/checkpoint 缺失/畸形同样归此
        except (OSError, ValueError) as e:  # OSError=文件缺失/不可读;ValueError=JSON/对齐畸形
            return _fail(f"录制 run 产物不可读或畸形(fail-closed): {e}")

        # 2. 一次性内核 + 录制脚本替换 provider 面 → 确定性重放源技能
        #    (内核重建失败——如冒烟工厂装配不过权限闸——同样归"证据不成立" fail-closed)
        try:
            kernel = self._make_kernel()
        except Exception as e:  # noqa: BLE001 — 重建失败归证据不成立,不炸穿钩子
            return _fail(f"冒烟内核重建失败(fail-closed): {type(e).__name__}: {e}")
        replace_providers(kernel, script)
        try:
            result = await kernel.run(meta.get("skill") or "", meta.get("input") or {})
        except SkillLoadError as e:
            return _fail(f"源技能不在冒烟内核注册表(已删除/未发布,fail-closed): {e}")
        except Exception as e:  # noqa: BLE001 — 重放任何失败都归"证据不成立",不炸穿钩子
            return _fail(f"录制 run 重放异常(fail-closed): {type(e).__name__}: {e}")

        # 3. 判定:结果须与录制一致(归一化深比较,同 expected 判定),录制须成功终态
        if _normalize(result) != _normalize(recorded.get("result")):
            return _fail(
                "重放结果与录制不符: "
                f"recorded={json.dumps(_normalize(recorded.get('result')), ensure_ascii=False, default=str)[:120]} "
                f"actual={json.dumps(_normalize(result), ensure_ascii=False, default=str)[:120]}"
            )
        status = recorded.get("status")
        if status != "done":
            return _fail(f"录制 run 状态为 {status!r}(须为 'done';非成功终态不能作证据)")

        # 4. LLM 裁判"任务真完成?"(仅配置裁判时;缺席降级为仅确定性比对,注记注明)
        if self._judge_providers is None:
            return {"case": label, "ok": True, "reason": ""}, (
                f"录制 run {source_run_id} 重放一致(未配置裁判,仅确定性比对);"
            )
        ok, reason = await self._judge_replay(meta, recorded, entry)
        if not ok:
            return _fail(reason)
        return {"case": label, "ok": True, "reason": ""}, (
            f"录制 run {source_run_id} 重放一致(裁判确认任务真完成);"
        )


def _source_run_id_of(provenance: Any) -> str:
    """从 provenance 取录制 run 引用(契约:``detail["source_run_id"]`` 非空 str 才生效,
    其余形态——无 provenance/无 detail/非 str/空白——一律视为未引用)。"""
    detail = getattr(provenance, "detail", None)
    candidate = detail.get("source_run_id") if isinstance(detail, dict) else None
    return candidate.strip() if isinstance(candidate, str) else ""


@contextlib.contextmanager
def _staged_code_handler(name: str, source: str) -> Any:
    """code 技能冒烟的临时 handler 登台:源码写进临时目录的 ``generated_handlers``
    正规包并置顶 sys.path,退出时清理,生产零残留。

    - ``__init__.py`` 必落:生产 skills 目录若在 sys.path 且已带同名**正规**包,
      namespace 包会被它压盖(正规包优先于 namespace 部分),临时源码将不可见;
    - 置顶 sys.path 的理由:沙箱 PYTHONPATH 在 exec 时取宿主 sys.path 构建
      (logic/python_sandbox.py:325)——子进程 ``import generated_handlers.<mod>``
      命中的就是这份临时源码;
    - 进出都弹 ``generated_handlers[.<mod>]`` 的 sys.modules 缓存 +
      ``importlib.invalidate_caches()``(纪律同 local_file.py
      ``_write_generated_handler``):生产旧版本模块若处缓存态被弹出,下次使用
      惰性重新解析(loader.py 惰性 import),临时模块文件随 tempdir 自动清理。
    """
    mod = name.replace(".", "_")
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "generated_handlers"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / f"{mod}.py").write_text(source, encoding="utf-8")
        sys.path.insert(0, tmp)
        sys.modules.pop(f"generated_handlers.{mod}", None)
        sys.modules.pop("generated_handlers", None)
        importlib.invalidate_caches()
        try:
            yield
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(tmp)
            sys.modules.pop(f"generated_handlers.{mod}", None)
            sys.modules.pop("generated_handlers", None)
            importlib.invalidate_caches()


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

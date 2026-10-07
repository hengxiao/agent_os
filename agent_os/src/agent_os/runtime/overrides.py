"""run 级覆盖选项(per-run override)治理注册表(docs/RUNNERS.md §2.5;K1)。

单一事实源(P2):CLI flag、Web ``POST /api/runs`` overrides 白名单、env 别名、
类型 coercion、校验与文档文本全部从 :data:`OVERRIDE_SPECS` 派生——新增可覆盖项 =
一行 spec + 测试,host 零改动。此前三处平行清单(config.py ``_RUN_FIELDS`` /
web ``OVERRIDE_FIELDS`` / CLI 手写 flag)已漂移,本模块收编为一份。

原则落点(§2.5):

- P3 fail-closed:未注册/未开放字段拒绝——:class:`OverrideError`(CLI 归退出码 2,
  Web 归 400);导入期断言每个 spec.field ∈ config.py ``_RUN_FIELDS``(权威字段集);
- P4 优先级链:flag > env(spec 声明了别名时)> toml > 默认;:func:`apply_overrides`
  只动 ``cfg["run"]`` 的副本(D3 锚点:配置文件永不被运行时改写);
  :func:`resolve_provenance` 给出每个最终生效字段的来源,写 meta.json;
- P6 安全分级:``web=False`` 的字段(workdir/read_paths 等影响爆炸半径的)
  只对 CLI 本机 principal 开放,不进 Web 多用户面(Bearer token)白名单。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_os.runtime.config import _RUN_FIELDS


class OverrideError(ValueError):
    """覆盖选项校验失败(未注册/未开放字段、类型 coercion 失败、validate 报错)。

    宿主归类(docs/RUNNERS.md §3.3):CLI 归退出码 2(与输入/技能校验错同族),
    Web 归 400(与"未知 skill set"同族)。
    """


def _no_check(value: Any) -> str | None:
    """无附加校验(类型 coercion 已够)。"""
    return None


def _choices(*options: str) -> Callable[[Any], str | None]:
    """枚举值校验;``check.options`` 供 argparse choices 复用(校验与 CLI 同源,P5)。"""
    def check(value: Any) -> str | None:
        if value not in options:
            return f"须为 {'|'.join(options)} 之一,得到: {value!r}"
        return None

    check.options = options
    return check


def _non_empty_str(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return f"须为非空字符串,得到: {value!r}"
    return None


def _non_negative_int(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return f"须为 >= 0 的整数,得到: {value!r}"
    return None


def _existing_dir(value: Any) -> str | None:
    """workdir 校验:路径必须存在且是目录(P6:写区边界,装配期 fail-closed)。"""
    if not isinstance(value, str) or not value:
        return f"须为非空路径字符串,得到: {value!r}"
    if not Path(value).is_dir():
        return f"路径不存在或不是目录: {value}"
    return None


def _existing_paths(value: Any) -> str | None:
    """read_paths 校验:每个路径必须存在(只读区边界,装配期 fail-closed)。"""
    if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
        return f"须为非空路径字符串列表,得到: {value!r}"
    missing = [p for p in value if not Path(p).exists()]
    if missing:
        return f"路径不存在: {missing}"
    return None


@dataclass(frozen=True)
class OverrideSpec:
    """一条 run 覆盖选项的声明(docs/RUNNERS.md §2.5 P2/P5)。

    ``field``:RunConfig 字段名 = toml ``[run]`` 键(导入期断言 ∈ ``_RUN_FIELDS``)。
    ``cli``:CLI flag(kebab-case,P5;``--max-steps`` ↔ ``max_steps``);None = 不对 CLI 开放。
    ``web``:是否进 ``POST /api/runs`` overrides 白名单(P6 安全分级)。
    ``env``:可选环境变量别名(优先级 flag > env > toml > 默认,P4)。
    ``type``:coercion 目标类型(str/int/float;列表型用 list,CLI 侧 action="append")。
    ``help``:CLI help 与文档同源。
    ``validate``:值校验,返回错误消息或 None。
    """

    field: str
    cli: str | None
    web: bool
    env: str | None
    type: type
    help: str
    validate: Callable[[Any], str | None]


#: run 覆盖选项注册表(§2.5 P2 单一事实源;声明序 = CLI help/文档序)。
#: workdir/read_paths 是 web=False(P6:写区/只读区边界影响爆炸半径,只对 CLI 本机开放)。
OVERRIDE_SPECS: tuple[OverrideSpec, ...] = (
    OverrideSpec(
        field="model",
        cli="--model",
        web=True,
        env="AGENT_OS_MODEL",
        type=str,
        help="覆盖本次 run 的 [run].model(§2.5;优先级 flag > env AGENT_OS_MODEL > toml);缺省用配置值",
        validate=_non_empty_str,
    ),
    OverrideSpec(
        field="max_cost",
        cli="--max-cost",
        web=True,
        env=None,
        type=float,
        help="覆盖本次 run 的 [run].max_cost(美元预算上限,§2.4);缺省用配置值",
        validate=_no_check,
    ),
    OverrideSpec(
        field="max_steps",
        cli="--max-steps",
        web=True,
        env=None,
        type=int,
        help="覆盖本次 run 的 [run].max_steps(全 run 总步数上限,§2.4);缺省用配置值",
        validate=_no_check,
    ),
    OverrideSpec(
        field="inline",
        cli="--inline",
        web=True,
        env=None,
        type=str,
        help="merge 消融开关(docs/SKILL-INLINING.md §9):off 时 inline 技能退化为压帧调用;缺省用配置值",
        validate=_choices("on", "off"),
    ),
    OverrideSpec(
        field="checkpoint_interval",
        cli="--checkpoint-interval",
        web=True,
        env=None,
        type=int,
        help="周期 checkpoint(Debugger P5):每 N 步覆盖写 checkpoint.json(最近现场);缺省用配置值,0=关",
        validate=_non_negative_int,
    ),
    OverrideSpec(
        field="workdir",
        cli="--workdir",
        web=False,
        env=None,
        type=str,
        help="run 工作目录(§W0-1;fs/shell 可写产出区,须为已存在目录);缺省每 run 临时目录",
        validate=_existing_dir,
    ),
    OverrideSpec(
        field="read_paths",
        cli="--read-paths",
        web=False,
        env=None,
        type=list,
        help="只读挂载路径(§W0-1;可重复 flag,每个路径须已存在);缺省无",
        validate=_existing_paths,
    ),
)

#: P3 fail-closed 自检:spec.field 必须 ∈ [run] 权威字段集(config.py);
#: 注册表与 _RUN_FIELDS 漂移在导入期即炸,不放行到运行时
_unknown = sorted({s.field for s in OVERRIDE_SPECS} - set(_RUN_FIELDS))
assert not _unknown, f"OVERRIDE_SPECS 含非 [run] 字段: {_unknown}(须 ∈ config._RUN_FIELDS)"

_SPECS_BY_FIELD: dict[str, OverrideSpec] = {s.field: s for s in OVERRIDE_SPECS}


def cli_flag(spec: OverrideSpec) -> str | None:
    """spec 的 CLI flag(``None`` = 不对 CLI 开放,P1/P6)。"""
    return spec.cli


def web_fields() -> tuple[str, ...]:
    """``POST /api/runs`` overrides 白名单(P6:仅 ``web=True`` 的注册字段)。"""
    return tuple(s.field for s in OVERRIDE_SPECS if s.web)


def collect_cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """从 argparse Namespace 收集用户**显式给出**的覆盖项(P4 的 flag 层)。

    argparse default 全为 None = 没给(与配置值区分);dest 约定 = toml 键
    (argparse 自动把 ``--checkpoint-interval`` 归到 ``checkpoint_interval``)。
    列表型 spec(``--read-paths``)经 action="append" 天然成 list。
    """
    out: dict[str, Any] = {}
    for spec in OVERRIDE_SPECS:
        if spec.cli is None:
            continue
        value = getattr(args, spec.field, None)
        if value is None:
            continue
        out[spec.field] = value
    return out


def _coerce(spec: OverrideSpec, raw: Any) -> Any:
    """按 spec.type coercion;失败抛 :class:`OverrideError`(消息带字段名与原值)。"""
    if spec.type is list:
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple)) and all(isinstance(p, str) for p in raw):
            return [str(p) for p in raw]
        raise OverrideError(f"覆盖字段 {spec.field} 须为字符串列表,得到: {raw!r}")
    if isinstance(raw, bool):  # bool 是 int 子类:数值字段先挡,防 true 静默当 1
        raise OverrideError(f"覆盖字段 {spec.field} 须为 {spec.type.__name__},得到: {raw!r}")
    if spec.type is int:
        if isinstance(raw, float) and not raw.is_integer():
            raise OverrideError(f"覆盖字段 {spec.field} 须为 int,得到: {raw!r}")
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise OverrideError(f"覆盖字段 {spec.field} 须为 int,得到: {raw!r}") from None
    if spec.type is float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise OverrideError(f"覆盖字段 {spec.field} 须为 float,得到: {raw!r}") from None
    if not isinstance(raw, str):
        raise OverrideError(f"覆盖字段 {spec.field} 须为 str,得到: {raw!r}")
    return raw


def validate_overrides(
    overrides: Mapping[str, Any], *, allowed: Iterable[str] | None = None
) -> dict[str, Any]:
    """白名单 + coercion + spec.validate(P3 fail-closed);返回规范化后的新 dict。

    ``allowed``:本面开放的字段集(缺省 = 全部注册字段;Web 传 :func:`web_fields`
    把 ``web=False`` 字段挡在门外,P6)。``None`` 值 = 未给,跳过(与 Web 请求体
    exclude_none 同款语义)。不改动入参。
    """
    scope = set(_SPECS_BY_FIELD) if allowed is None else set(allowed)
    unknown = sorted(set(overrides) - scope)
    if unknown:
        raise OverrideError(
            f"未注册/未开放的 run 覆盖字段: {unknown}(本面开放: {sorted(scope)})"
        )
    out: dict[str, Any] = {}
    for key, raw in overrides.items():
        if raw is None:
            continue
        spec = _SPECS_BY_FIELD[key]
        value = _coerce(spec, raw)
        error = spec.validate(value)
        if error is not None:
            raise OverrideError(f"覆盖字段 {key} 无效: {error}")
        out[key] = value
    return out


def apply_overrides(
    cfg: dict[str, Any], overrides: Mapping[str, Any], *, allowed: Iterable[str] | None = None
) -> dict[str, Any]:
    """把 overrides 合并进 ``cfg["run"]`` 的副本并返回新 dict(D3 锚点:不改原 dict)。

    空 overrides → 原样返回 cfg(零拷贝零改动);每个键值经
    :func:`validate_overrides`(未注册/未开放字段、非法值 → OverrideError)。
    只动 ``cfg["run"]`` 的副本,其余段共享引用(同宿主既有私有副本先例);
    配置文件永不被运行时改写(P4)。
    """
    if not overrides:
        return cfg
    clean = validate_overrides(overrides, allowed=allowed)
    if not clean:
        return cfg
    out = dict(cfg)
    run_section = dict(out.get("run") or {})
    run_section.update(clean)
    out["run"] = run_section
    return out


def resolve_effective(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """flag > env(P4):声明了 env 别名且 flag 未给的字段,env 值补进覆盖集。

    env 值走同一 coercion/validate 管线,失败抛 :class:`OverrideError`
    (消息带变量名,不回显值——泄露纪律同 config.py ``[credentials]`` 先例)。
    """
    out = dict(overrides)
    for spec in OVERRIDE_SPECS:
        if spec.field in out or spec.env is None:
            continue
        raw = os.environ.get(spec.env)
        if raw is None:
            continue
        try:
            value = _coerce(spec, raw)
        except OverrideError as e:
            raise OverrideError(f"环境变量 {spec.env} 无效: {e}") from None
        error = spec.validate(value)
        if error is not None:
            raise OverrideError(f"环境变量 {spec.env} 无效: {error}")
        out[spec.field] = value
    return out


def resolve_provenance(cfg_run: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, str]:
    """每个最终生效的注册覆盖字段的来源(P4 审计面,写 meta.json ``overrides`` 段)。

    flag 给了 → ``"flag"``;flag 未给但 env 别名已设置 → ``"env"``;前两者皆无
    但 toml ``[run]`` 含该键 → ``"toml"``;三者皆无(落 RunConfig 默认)→ 不出现。
    """
    prov: dict[str, str] = {}
    for spec in OVERRIDE_SPECS:
        if spec.field in overrides:
            prov[spec.field] = "flag"
        elif spec.env is not None and os.environ.get(spec.env) is not None:
            prov[spec.field] = "env"
        elif spec.field in cfg_run:
            prov[spec.field] = "toml"
    return prov

"""数据层 authN+Z 契约(docs/DATA-AUTHZ.md §2/§3;D1)。

三闸模型的"读"闸:**机密性由数据层 authN+Z 完成,升权系统只管副作用**
(docs/ESCALATION.md §1 划界)。Principal = "谁"(run 启动者身份,不可自升,§2.3);
DataDomain = 授权单位(不给 per-文件 ACL,域是最细粒度,§3.1);判定默认拒绝:
clearance 不够就是不够,没有兜底放行——唯一的例外是 principal 为 ``None``
(v1 单用户语义:宿主没注入身份 = 没启用数据层,拦截不发生)。

clearance/sensitivity 共用三级全序:``public < internal < confidential``,
分级语义与 TIER-STANDARDS 的副作用分档正交(那套管"改世界",这套管"看世界")。
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

__all__ = [
    "CONFIDENTIAL",
    "INTERNAL",
    "PUBLIC",
    "DataDomain",
    "DataPolicy",
    "Principal",
    "allow",
    "clearance_of",
    "cli_principal",
    "web_single_user_principal",
]

#: 三级敏感度/clearance(§3.2)
PUBLIC = "public"
INTERNAL = "internal"
CONFIDENTIAL = "confidential"

#: 三级全序:比较只凭本表,字符串本身无大小语义
_LEVEL_RANK = {PUBLIC: 0, INTERNAL: 1, CONFIDENTIAL: 2}


@dataclass(frozen=True)
class Principal:
    """调用方身份(§2.1)。``issuer`` = 谁认证的;``attrs`` 供 ABAC 判定(clearance 等)。"""

    subject: str = ""  # "user:hengxiao" | "service:ci-bot" | "agent:run-..."
    issuer: str = ""  # "cli" | "web-session" | "api-token" | "host-embedded"
    attrs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DataDomain:
    """数据域(§3.1):授权单位;边界(路径前缀/库表/URL 前缀)由宿主绑定,不在契约里。"""

    name: str = ""  # "fs.workdir" | "fs.shared" | "db.analytics" | "net.intranet" | ...
    sensitivity: str = PUBLIC


@dataclass(frozen=True)
class DataPolicy:
    """``[data]`` 配置段的解析产物(D2,§3.1/§3.2):域名表 + per-subject 域白名单。

    ``boundaries`` 是给宿主的注册输入(域名 → (``"fs"``|``"net"``, 前缀)),
    契约本身不持边界(同 :class:`DataDomain` 注);``whitelist_for`` 对未配置
    subject 返回空表——fail closed:忘了配 = 哪个域都不许,与"未配置域 =
    confidential"(§3.1)同旨。
    """

    domains: Mapping[str, DataDomain] = field(default_factory=dict)
    whitelists: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    boundaries: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    def whitelist_for(self, subject: str) -> tuple[str, ...]:
        """subject 的域白名单(glob 模式表);未配置 → 空表(fail closed)。"""
        return tuple(self.whitelists.get(subject, ()))


def clearance_of(principal: Principal) -> str:
    """principal 的 clearance;缺省按最低档(public)——fail closed,身份没说清不放大。"""
    return str(principal.attrs.get("clearance") or PUBLIC)


def allow(
    principal: Principal | None,
    domain: DataDomain,
    action: str = "read",
    *,
    whitelist: Iterable[str] | None = None,
) -> bool:
    """授权判定(§3.2,默认拒绝):``clearance(principal) >= sensitivity(domain)``。

    ``principal is None`` → True(v1 单用户语义:宿主未注入身份 = 数据层未启用,
    行为与引入本系统前完全一致);未知 clearance 按 public、未知 sensitivity 按
    confidential 计(两个方向都 fail closed)。``whitelist``(D2,§3.2 第二判据,
    glob 域名模式):非 None 时并入判定——clearance 够但域不匹配任一模式同样拒绝;
    None(缺省)时行为与 D1 逐字一致。``action`` 参数为协议面占位(读类语义),
    不参与判定。
    """
    if principal is None:
        return True
    have = _LEVEL_RANK.get(clearance_of(principal), 0)
    need = _LEVEL_RANK.get(domain.sensitivity, _LEVEL_RANK[CONFIDENTIAL])
    if have < need:
        return False
    if whitelist is not None and not any(
        fnmatch.fnmatchcase(domain.name, pattern) for pattern in whitelist
    ):
        return False
    return True


def _local_user() -> str:
    """本机登录名(Unix ``$USER``;Windows/缺环境变量时退化为 ``whoami`` 语义兜底)。"""
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def cli_principal() -> Principal:
    """CLI 来源(§2.2):本机用户即身份(信任本机账户)。

    单用户 = 机器的主人,clearance 给 confidential——已配置域对本机用户全通,
    与"CLI 不启用拦截"的单用户语义一致;拦截只对显式构造的低 clearance 身份生效。
    """
    return Principal(
        subject=f"user:{_local_user()}",
        issuer="cli",
        attrs={"clearance": CONFIDENTIAL},
    )


def web_single_user_principal(login: str | None = None) -> Principal:
    """Web 单用户模式来源(§2.2):部署者即身份;``login`` 取宿主配置,缺省本机用户。

    多用户会话映射(api-token / 逐会话身份)属 D3;D1 所有 Web run 共享部署者身份。
    """
    return Principal(
        subject=f"user:{login or _local_user()}",
        issuer="web-session",
        attrs={"clearance": CONFIDENTIAL},
    )

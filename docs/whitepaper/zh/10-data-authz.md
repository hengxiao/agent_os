# 数据层 authN+Z:Principal 与数据域

> 章次:10 · 状态:D1 已实现(Principal 模型、CLI/Web 单用户来源、fs 域、dispatch
> 强制点、身份不变量与 checkpoint 往返);D2 已实现(2026-08-31:`[data]` 配置段、
> per-subject 域白名单第二判据、net/db 域判定、`data.access.*` 审计信号、判据回写
> `credentials["_authz"]`);D3-lite 已落地(`[web.tokens]` 多用户映射);D3 派生链
> 最弱一环已实现(2026-09-28,via 链逐环判定);升权决策数据面经审计面板暴露;
> 确认卡片数据域展示已落地(2026-09-29,`domains`/`sensitive` 浅层并集);
> 跨 run 自动派生已落地(2026-10-01,宿主调度器 `[schedule]` 段 +
> `system.schedule.set`,via 链仍由宿主声明注入);
> 仍开口:完整多用户会话映射、卡片域的递归子技能并集与
> 审批选项按域动态化 ·
> 依据:`docs/DATA-AUTHZ.md`、`agent_os/src/agent_os/api/v1/principal.py`、
> `agent_os/src/agent_os/tools/local_registry.py`、
> `agent_os/tests/tools/test_data_authz.py`

## 1. 概述

数据层 authN+Z 是 Agent OS 三闸模型中的"读"闸:以 principal(谁启动的 run)
为判据,对数据域(授权单位)做读类授权判定,默认拒绝。它与档位升权系统正交——
升权闸管副作用("允不允许改世界"),数据闸管机密性("碰不碰得到这份数据"),
两套系统共享的只有 principal 字段(`docs/ESCALATION.md` §1 非目标明确把机密性
划给本系统)。D1 已落地:契约在 `api/v1/principal.py`,强制点在
`LocalPythonToolRegistry.dispatch` 的 `_check_data_access`。

## 2. 动机与背景(原因)

**为什么升权系统不管读。** 三档信任层级按副作用的客观语义分(none /
reversible / irreversible),判据里刻意不含机密性维度(`docs/ESCALATION.md`
§1 非目标)。原因是一个架构判断:"这份数据多敏感"与"这次调用改不改世界"
是两个独立变量——读机密文件是 L1(无副作用)却可能需要最高 clearance;
`system.shell.exec` 是 L3 却可能一个字节的有价值数据都不碰。把机密性折进
档位会让两套判据互相污染:要么读取类 skill 被无谓地抬进人审闸,要么高危
工具借"不碰机密"降档。划界的结果是三闸模型:读 = 数据层 authZ;写 = 档位
升权;泄露 = 写闸兜底(`docs/DATA-AUTHZ.md` 卷首)。

**为什么授权单位是"域"而不是 per-文件 ACL。** 给每个文件/表维护 ACL 的
配置成本随数据规模线性增长,而 agent 场景的保护对象天然是成片的目录、
库表、网段。域是最细粒度(`docs/DATA-AUTHZ.md` §3.1),宿主只为少量域绑
边界;粒度换可维护性是有意的取舍。

**为什么身份存放在帧上而不是 RunContext。** 设计稿 §2.3 原文写"存
RunContext",D1 实现改存 `SkillFrame.principal`(`docs/DATA-AUTHZ.md` §8
实现注 2):dispatch 只见帧,帧级存放让"子帧/升权帧原样继承"成为构造副产
品(`make_frame` 复制,`skills/local_file.py:146-148`),checkpoint 随帧
序列化也自动覆盖身份。这是"以代码为准"的一处明文修正。

**历史状态。** D1 之前,`ToolContext.principal` / `credentials` 是契约预留
字段,恒 None/空;`resolve_work_path` 路径沙箱是 fs 维度的 authZ 雏形——
它只管"出不出 workdir",不管"谁在读"。

## 3. 问题陈述(解决的问题)

没有数据层 authN+Z 时(`docs/DATA-AUTHZ.md` §1):

- **读权限全有或全无**:skill 白名单里只要有 `system.file.read`,它就能读
  沙箱内一切能拼出路径的数据。一行例子:客服 skill 读工单目录的权限,
  同时就是读同机财务目录的权限——白名单只按工具名授,不按数据授。
- **多租户无隔离判据**:Web 服务同时给多个人跑 run,所有 run 以同一进程
  身份读同一份文件系统,没有任何运行时判据能区分"这个 run 替谁读"。
- **审计无法归因**:principal 恒 None,信号流里没有身份,"谁读了什么"
  在 trace 里无从回答。
- **混淆代理人(已设计,D3)**:一个 run 委托另一个高权 run 代读,身份
  若不沿调用链降级,就等于"托高权的 agent 帮忙读"。

## 4. 设计与机制(解决的方法)

### 4.1 Principal:身份模型

```python
# agent_os/src/agent_os/api/v1/principal.py:40-46
@dataclass(frozen=True)
class Principal:
    subject: str   # "user:hengxiao" | "service:ci-bot" | "agent:run-..."
    issuer: str    # 谁认证的:"cli" | "web-session" | "api-token" | "host-embedded"
    attrs: Mapping[str, str]  # 属性(clearance 等),供 ABAC 判定
```

D1 实现两个来源(`principal.py:83-105`):CLI 取本机用户
(`user:$USER`,issuer=cli);Web 单用户取部署者登录名(宿主配置
`[web].user`,缺省本机用户;`host/web/run_manager.py:442-452`)。两者
clearance 都给 confidential——单用户 = 机器的主人,拦截只对显式构造的
低 clearance 身份(测试、嵌入宿主)生效(§8 实现注 3)。api-token 来源
已接线(D3-lite,2026-08-31:`[web.tokens]` Bearer 映射命中 →
`Principal(issuer="api-token", clearance=confidential)`,
`host/web/app.py`/`host/web/run_manager.py`);host-embedded 是协议面
已冻结、接线属 D3 的来源。

### 4.2 身份不变量:不可自升

- run 的 principal = 启动者的 principal,经 `Kernel.run(principal=)`
  注入根帧(`kernel/runner.py:206-228`),随 checkpoint 序列化
  (`kernel/checkpoint.py:156` 写入、`:226-234` 重建);
- 子帧、升权帧、code 技能沙箱帧**原样继承**父帧 principal
  (`skills/local_file.py:146-148`)。**升权改的是副作用许可,不是身份**:
  普通用户启动的 run 即便升权进入 L3 skill,能读的数据仍是这个用户能读的;
- agent 作为调用方时可降级为 `agent:<run_id>` 并带上游链
  (`attrs["via"]`),按链上最弱一环判定——防"托高权的 agent 帮忙读"
  (§2.3;**D3 已实现**,2026-09-28:`_check_data_access` 逐环过 `allow`、
  任一拒=拒、fail-closed(形状坏/深度 >8 拒),链由宿主声明注入)。

### 4.3 authZ:数据域与默认拒绝

授权单位是数据域(`principal.py:49-54`):`name` + `sensitivity`
(public < internal < confidential,三级全序见 `_LEVEL_RANK`,
`principal.py:32-37`)。工具在 `ToolSpec.data_domains` 声明自己会碰哪些
域(`api/v1/tools.py:119-121`);内置 fs 工具(read/write/edit/list/
search/stat/delete/mkdir)全部声明 `["fs.*"]`(`local_registry.py`
`with_builtins`)。宿主经 `register_fs_domain(domain, path_prefix)` 绑
边界,最长前缀优先,嵌套域按更具体者判(`local_registry.py:288-294`);
workdir 内路径回落到内置默认域 `fs.workdir = public`
(`local_registry.py:296-304`)。

判定函数(`principal.py:62-75`):

```
allow(principal, domain, action) ⟺ clearance_of(principal) ≥ domain.sensitivity
```

- 默认拒绝,clearance 不够没有兜底放行;唯一例外是 `principal is None`
  → 放行(v1 单用户语义:宿主没注入身份 = 没启用数据层,行为与引入本
  系统前完全一致);
- 两个方向都 fail closed:未知 clearance 按 public,未知 sensitivity 按
  confidential(`principal.py:73-74`);attrs 缺 clearance 同样按最低档
  (`principal.py:57-59`);
- §3.2 的第二判据(per-subject 域白名单)已生效(D2,2026-08-31:
  `[data.principals."<subject>"] domains = [...]` 配置段经
  `DataPolicy.whitelist_for` 传入 `allow()` 的 `whitelist=None` 关键字,
  None 与 D1 逐字一致);判据回写 `ctx.credentials["_authz"]`;
  `action` 参数是协议面占位,D1 不参与判定。

### 4.4 强制点:dispatch 双闸串联

```
ToolCall
  │
  ▼
schema 校验(jsonschema,fail fast,禁止"智能纠正")
  │
  ▼
数据层 authZ ── _check_data_access(local_registry.py:396-508)
  │  ① 工具未声明 data_domains → 跳过(不碰数据)
  │  ② principal is None       → 跳过(单用户语义)
  │  ③ net/db 声明且 policy 缺席 → 跳过(D1 语义逐字保留;policy 在场时
  │     net 按 args["url"] 前缀匹配、db 等其余族 glob 对 policy.domains 匹配,D2)
  │  ④ 无 path/url 参数可解析   → 跳过(无可判定目标)
  │  ⑤ resolve_work_path 越界   → 交还工具按沙箱语义报错,不重复判
  │  ⑥ 域解析为 None(未配置)  → policy 缺席:跳过(D1 兼容策略,见下);
  │     policy 在场:按 confidential(fs/net.unconfigured,D2 恢复原文语义)
  │  ⑦ allow()(clearance + 白名单第二判据)拒绝 → DATA_ACCESS_DENIED
  │     (带域名/敏感度/clearance,不回显路径、不含域内内容);
  │     拒绝/放行各发一条 data.access.* 审计信号,判据回写 credentials["_authz"]
  ▼
三层权限交集(帧白名单 ∩ RunConfig 上限;READ 档不占帧白名单)
  │
  ▼
wait_for 超时执行 → 结果归一化
```

顺序是有语义的:数据闸在权限闸**之前**(`local_registry.py:211-215`),
"碰不碰得到"先于"允不允许",各自独立失败——测试断言 WRITE 工具既不
在白名单又越数据域时报 `DATA_ACCESS_DENIED` 而非 `PERMISSION_DENIED`
(`test_data_authz.py:132-147`)。错误类别是契约层新增的
`ToolErrorKind.DATA_ACCESS_DENIED`(`api/v1/tools.py:57`)。同一份声明域解析
还服务确认卡片(2026-09-29):公开口 `LocalPythonToolRegistry.sensitive_domains(declared)`
与 `_check_data_access` 共用 `_resolve_declared_domains`(fnmatch 展开 +
整体落空合成 confidential 占位),升权/tool-confirm 两通道的确认 context
借此带上 `domains`/`sensitive` 数据域面(判定不另写第二份)。

**D1 兼容策略(设计/代码偏离,以代码为准)。** 设计稿 §3.3 原文要求
"域解析失败 → 按 confidential 处理";D1 实现刻意不生效(§8 实现注 1):
`[data]` 配置段属 D2,D1 若对未配置路径按 confidential 判,会把所有
既有单用户行为一刀切断。因此 D1 的语义是"**未配置 = 不启用数据层拦截**",
目标落不进任何已配置域时退化为 `resolve_work_path` 现状沙箱;默认拒绝
只作用于已配置域(内置 `fs.workdir`=public + `register_fs_domain`
程序化注册的域)。`resolve_work_path` 保留,与数据域正交:一个管
"能不能出 workdir",一个管"这个 principal 能不能读这片"。

### 4.5 出口:机密进 context 之后

v1 **不做 taint tracking**(标记机密内容并跟踪其在 context 内的流动)——
成本高且对 LLM 不可强制(§4)。防线放两端:入口 authZ 保证读到的都是
principal 有权读的;出口由写闸兜底——NET 发送类工具是 L2+ 推导档,跨层
调用被升权确认拦住。即"读得合法 ≠ 发得出去"。明示的残余风险:同
principal 的 run 内部不做数据隔离(低层 skill 读到机密写进输出,同 run
高层可见——那是同一个"人"的数据);多用户隔离由 run 边界承担。

## 5. 效果与验证(效果)

锚点测试 `agent_os/tests/tools/test_data_authz.py` 共 **23 例,本次运行
全绿**(D2 新增 10 例:policy 绑定后未配置域 confidential、白名单第二判据
glob、net 域 URL 前缀命中/未命中不泄漏、`data.access.*` 信号精确 payload、
判据回写 `credentials["_authz"]`;另 `tests/web/test_data_authz.py` 5 例
覆盖 D3-lite `[web.tokens]` 映射与未命中回落,合计 28 passed)。关键断言:

| 验证点 | 用例 | 断言要点 |
|---|---|---|
| 判定矩阵 | `test_allow_matrix_3x3`(:61) | 3 clearance × 3 sensitivity,仅 ≥ 放行 |
| fail closed | `test_allow_default_deny_and_fail_closed`(:71) | 未知 clearance→public、未知 sensitivity→confidential、attrs 缺省→public |
| 单用户语义 | `test_allow_none_principal_single_user`(:83)、`test_none_principal_not_intercepted`(:218) | principal None → 放行,已配置 confidential 域也不拦 |
| 来源构造 | `test_cli_principal_source`(:93)、`test_web_single_user_principal_source`(:102) | subject/issuer/clearance=confidential |
| 双闸顺序 | `test_dispatch_order_data_before_permission`(:132) | 数据拒绝先于权限拒绝 |
| 拒绝面不泄漏 | `test_configured_confidential_domain_denied_without_leak`(:150) | 错误面带域名与敏感度,**不含**目标路径与域内内容 |
| 兼容退化 | `test_unconfigured_path_degrades_to_sandbox`(:194) | 未配置路径维持沙箱语义 |
| 身份透传 | `test_tool_context_principal_filled`(:232) | `ToolContext.principal` 从预留变实填(`local_registry.py:255`) |
| 身份不变量 | `test_principal_identity_invariant_across_frames`(:366) | 根帧与 approve-once 放行的 L3 升权子帧 capture 到的 principal 是**同一对象** |
| checkpoint | `test_checkpoint_principal_round_trip`(:395) | 落盘帧全带 principal;resume 重建帧身份相等 |

宿主接线已生效:CLI 两个入口注入 `cli_principal()`
(`host/cli/main.py:163,292`);Web 单用户每个 run 注入
`web_single_user_principal`(`host/web/run_manager.py:565`)。

**可观察行为**:单用户部署(CLI / 未配多用户的 Web)行为与引入本系统前
零差异——principal 为 confidential 或 None,拦截不发生;嵌入宿主或测试
显式注入低 clearance principal 时,越域读立即得到结构化拒绝,模型可据
hint 恢复(换路径或请求更高 clearance 的身份)。

**涟漪效应**:principal 字段已成为其他子系统的判据锚点——Memory 检索层
权限过滤以 principal 为参数(`api/v1/memory.py:60`,预留);Skill Lab
promote 记录 `promoted_by`(`skills/gate.py:539`);升权确认卡片的数据面
展示(本调用将访问的域与敏感度)已落地(2026-09-29):`EscalationRequest`
additive `domains`/`sensitive`——白名单工具 `data_domains` 的浅层并集
(保序去重,不递归子技能)与其中 policy 判 confidential 的子集,判定复用
数据闸同一份解析;收件箱升权卡片与 tool-confirm 卡均以 chips 呈现(敏感域
--danger 双编码),仍开口的是递归子技能并集与审批选项按域动态化。

## 6. 局限性与边界(局限性)

1. **未配置 fail-open 残留(policy 缺席时)**。D2 已恢复"未配置域按
   confidential"原文语义,但仅在 `bind_data_policy` 注入 `[data]` 策略
   之后;未配置 `[data]` 段的部署仍保持 D1"未配置 = 不拦截"——忘了配段
   = 数据层整体不启用,静默不保护而非静默拒绝。
2. **出厂路径实际不拦截**。CLI 与 Web 单用户都给 confidential clearance,
   数据闸对自带宿主是 no-op;保护只对显式构造低 clearance 身份的嵌入方
   (或 `[web.tokens]` 命中的多用户映射)生效。换言之交付的是机制与不变量,
   不是开箱即用的多用户隔离。
3. **覆盖仍限于可解析形参**。`_check_data_access` 对 fs.* 解析
   `args["path"]`、对 net.* 解析 `args["url"]`(D2;db.* 等其余族按声明
   glob 对 `policy.domains` 匹配):`system.shell.exec` 不声明
   data_domains、命令串无法解析,白名单里有它的低 clearance principal
   可以用 `cat` 绕过数据闸(代价:shell 本身是 EXEC/L3,必过升权闸)。
4. **`action` 参数仍是协议面占位**。判定不区分 read/list/metadata
   (D1/D2 同);第二判据(per-subject 域白名单)与 `ToolContext.credentials`
   判据回写(`_authz`)已于 D2 生效。
5. **同 run 内无隔离**。低层 skill 读到的机密可经输出流向同 run 高层
   skill(§4 明示残余风险);v1 靠"同一 principal 即同一人"的假设接受
   这一点,防注入扩散依赖的是帧隔离与升权闸,不是数据闸。
6. **完整会话映射未实现;确认卡片数据面已落地、留两项增强开口**(D3 余项):
   派生链最弱一环已落地(2026-09-28——`attrs["via"]` 链每环过 `allow`、
   fail-closed、深度上限 8,链由宿主声明注入;跨 run 自动派生已落地
   (2026-10-01,宿主调度器 `[schedule]` 段 + `system.schedule.set`,
   链仍由宿主声明、引擎不伪造));
   确认卡片附"本调用将访问的域与敏感度"(§5.3)已落地(2026-09-29,additive
   `domains`/`sensitive` 浅层并集 + chips 渲染,见 §5 涟漪效应),仍开口的是
   递归子技能域并集与审批选项按域动态化;升权决策数据面另由审计面板暴露
   (`GET /api/runs/{run_id}/escalations`,2026-09-28)。多用户映射 D3-lite
   已落地(`[web.tokens]`,2026-08-31);未配置时所有 Web run 仍共享部署者身份。
7. **拒绝面泄漏域的存在性**(域名与敏感度进错误消息)。这是有意的可用性
   取舍(模型需要判据来恢复),但等于向低 clearance 调用方暴露了域的
   命名与分级。
8. **明确不做**(§9):taint tracking、per-文件/行级 ACL、SSO/OIDC、
   存储层加密;这些是范围外,不是遗漏。

## 7. 引用

- 设计文档:`docs/DATA-AUTHZ.md`(三闸划界、判定模型、D1 实现注、分期);
  `docs/ESCALATION.md` §1(范围边界:升权不管机密性)
- 契约:`agent_os/src/agent_os/api/v1/principal.py`(Principal/DataDomain/
  allow/clearance_of/cli_principal/web_single_user_principal);
  `agent_os/src/agent_os/api/v1/tools.py:57,119-121,145`
  (DATA_ACCESS_DENIED、ToolSpec.data_domains、ToolContext.principal);
  `agent_os/src/agent_os/api/v1/frames.py:102-105`(SkillFrame.principal)
- 强制点:`agent_os/src/agent_os/tools/local_registry.py:228-347`(dispatch
  流水线)、`:349-378`(register_fs_domain/register_net_domain 与
  _resolve_fs_domain/_resolve_net_domain)、`:396-508`(_check_data_access)、
  `:701-745`(resolve_work_path)
- 身份流:`agent_os/src/agent_os/kernel/runner.py:206-228`(run 注入点);
  `agent_os/src/agent_os/skills/local_file.py:146-148`(子帧继承);
  `agent_os/src/agent_os/kernel/checkpoint.py:156,226-234`(序列化往返);
  `agent_os/src/agent_os/host/cli/main.py:163,292`;
  `agent_os/src/agent_os/host/web/run_manager.py:442-452,565`
- 测试:`agent_os/tests/tools/test_data_authz.py`(23 例)、
  `agent_os/tests/web/test_data_authz.py`(5 例,D3-lite)

# 数据层 authN+Z:Principal 与数据域

> 章次:10 · 状态:D1 已实现(Principal 模型、CLI/Web 单用户来源、fs 域、dispatch
> 强制点、身份不变量与 checkpoint 往返);D2/D3 已设计未实现(配置段、per-subject
> 域白名单、db/net 域、审计信号、派生链、多用户会话映射) ·
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
`[web].user`,缺省本机用户;`host/web/run_manager.py:430-440`)。两者
clearance 都给 confidential——单用户 = 机器的主人,拦截只对显式构造的
低 clearance 身份(测试、嵌入宿主)生效(§8 实现注 3)。api-token 与
host-embedded 是协议面已冻结、接线属 D3 的来源。

### 4.2 身份不变量:不可自升

- run 的 principal = 启动者的 principal,经 `Kernel.run(principal=)`
  注入根帧(`kernel/runner.py:206-228`),随 checkpoint 序列化
  (`kernel/checkpoint.py:156` 写入、`:226-234` 重建);
- 子帧、升权帧、code 技能沙箱帧**原样继承**父帧 principal
  (`skills/local_file.py:146-148`)。**升权改的是副作用许可,不是身份**:
  普通用户启动的 run 即便升权进入 L3 skill,能读的数据仍是这个用户能读的;
- agent 作为调用方时可降级为 `agent:<run_id>` 并带上游链
  (`attrs["via"]`),按链上最弱一环判定——防"托高权的 agent 帮忙读"
  (§2.3,**D3 已设计未实现**)。

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
- §3.2 的第二判据(per-subject 域白名单)依赖宿主配置段,**D2 未实现**;
  `action` 参数是协议面占位,D1 不参与判定。

### 4.4 强制点:dispatch 双闸串联

```
ToolCall
  │
  ▼
schema 校验(jsonschema,fail fast,禁止"智能纠正")
  │
  ▼
数据层 authZ ── _check_data_access(local_registry.py:306-351)
  │  ① 工具未声明 data_domains → 跳过(不碰数据)
  │  ② principal is None       → 跳过(单用户语义)
  │  ③ 声明不含 "fs.*"         → 跳过(db/net 域判定属 D2)
  │  ④ 无 path 参数可解析       → 跳过(D1 只解析 path 形参)
  │  ⑤ resolve_work_path 越界   → 交还工具按沙箱语义报错,不重复判
  │  ⑥ 域解析为 None(未配置)  → 跳过(D1 兼容策略,见下)
  │  ⑦ allow() 拒绝 → DATA_ACCESS_DENIED(带域名/敏感度/clearance,
  │                     不回显路径、不含域内内容)
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
`ToolErrorKind.DATA_ACCESS_DENIED`(`api/v1/tools.py:57`)。

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

锚点测试 `agent_os/tests/tools/test_data_authz.py` 共 **13 例,本次运行
全绿**(`pytest tests/tools/test_data_authz.py -q` → 13 passed)。关键断言:

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
`web_single_user_principal`(`host/web/run_manager.py:553`)。

**可观察行为**:单用户部署(CLI / 未配多用户的 Web)行为与引入本系统前
零差异——principal 为 confidential 或 None,拦截不发生;嵌入宿主或测试
显式注入低 clearance principal 时,越域读立即得到结构化拒绝,模型可据
hint 恢复(换路径或请求更高 clearance 的身份)。

**涟漪效应**:principal 字段已成为其他子系统的判据锚点——Memory 检索层
权限过滤以 principal 为参数(`api/v1/memory.py:60`,预留);Skill Lab
promote 记录 `promoted_by`(`skills/gate.py:382`);升权确认卡片的数据面
展示(本调用将访问的域与敏感度)已列为 D3 的 EscalationRequest 扩展输入
(§5.3,已设计未实现)。

## 6. 局限性与边界(局限性)

1. **未配置域 fail-open(D1)**。设计语义"未配置域按 confidential"在 D1
   刻意不生效(§8 实现注 1):域注册写错前缀或漏注册,结果是静默不保护,
   而非静默拒绝。D2 配置段落地前,这是已知的最严口径缺口。
2. **出厂路径实际不拦截**。CLI 与 Web 单用户都给 confidential clearance,
   数据闸对自带宿主是 no-op;保护只对显式构造低 clearance 身份的嵌入方
   生效。换言之 D1 交付的是机制与不变量,不是开箱即用的多用户隔离。
3. **覆盖仅限 fs 域的 path 形参**。`_check_data_access` 只解析
   `args["path"]`(`local_registry.py:324-326`):`system.shell.exec` 不
   声明 data_domains、命令串无法解析,白名单里有它的低 clearance principal
   可以用 `cat` 绕过数据闸(代价:shell 本身是 EXEC/L3,必过升权闸);
   db/net 域的声明与判定整体属 D2。
4. **第二判据缺失**。per-subject 域白名单(§3.2)未实现,判定只剩
   clearance 一维;`ToolContext.credentials` 的判据回写同为 D2;`action`
   参数是占位,D1 不区分 read/list/metadata。
5. **同 run 内无隔离**。低层 skill 读到的机密可经输出流向同 run 高层
   skill(§4 明示残余风险);v1 靠"同一 principal 即同一人"的假设接受
   这一点,防注入扩散依赖的是帧隔离与升权闸,不是数据闸。
6. **审计信号未实现**。`data.access.denied` / `data.access.granted`
   (§6)属 D2;当前拒绝只以工具错误形式进 trace,无法按 principal 聚合
   "谁被拒了几次"。
7. **多用户与派生链未实现**(D3):所有 Web run 共享部署者身份;agent
   代调场景没有最弱一环降级,委托方身份原样传递。
8. **拒绝面泄漏域的存在性**(域名与敏感度进错误消息)。这是有意的可用性
   取舍(模型需要判据来恢复),但等于向低 clearance 调用方暴露了域的
   命名与分级。
9. **明确不做**(§9):taint tracking、per-文件/行级 ACL、SSO/OIDC、
   存储层加密;这些是范围外,不是遗漏。

## 7. 引用

- 设计文档:`docs/DATA-AUTHZ.md`(三闸划界、判定模型、D1 实现注、分期);
  `docs/ESCALATION.md` §1(范围边界:升权不管机密性)
- 契约:`agent_os/src/agent_os/api/v1/principal.py`(Principal/DataDomain/
  allow/clearance_of/cli_principal/web_single_user_principal);
  `agent_os/src/agent_os/api/v1/tools.py:57,119-121,145`
  (DATA_ACCESS_DENIED、ToolSpec.data_domains、ToolContext.principal);
  `agent_os/src/agent_os/api/v1/frames.py:102-105`(SkillFrame.principal)
- 强制点:`agent_os/src/agent_os/tools/local_registry.py:181-286`(dispatch
  流水线)、`:288-304`(register_fs_domain/_resolve_fs_domain)、
  `:306-351`(_check_data_access)、`:544-588`(resolve_work_path)
- 身份流:`agent_os/src/agent_os/kernel/runner.py:206-228`(run 注入点);
  `agent_os/src/agent_os/skills/local_file.py:146-148`(子帧继承);
  `agent_os/src/agent_os/kernel/checkpoint.py:156,226-234`(序列化往返);
  `agent_os/src/agent_os/host/cli/main.py:163,292`;
  `agent_os/src/agent_os/host/web/run_manager.py:430-440,553`
- 测试:`agent_os/tests/tools/test_data_authz.py`(13 例)

# 数据层 authN+Z · 设计稿

> 版本:v0.1(设计)
> 对象:Agent OS 内核(principal / dispatch / 数据访问工具)
> 关系:范围边界由 `ESCALATION.md` §1 划定——**机密性由数据层 authN+Z 完成,
>   升权系统只管副作用**。本文件是"读"这一闸的设计;三闸模型:
>   读 = 数据层 authZ(本文);写 = 档位升权(ESCALATION.md);泄露 = 写闸兜底。
> 现状:`ToolContext.principal` / `credentials` 是预留字段,恒 None/空
>   (api/v1/tools.py:121,126);路径沙箱 `resolve_work_path` 是 fs 维度的
>   authZ 雏形(tools/local_registry.py:451-495)。

---

## 1. 问题

升权系统回答"会不会改世界",不回答"能看到什么"。没有数据层 authN+Z 时:

- 任何 skill 只要白名单里有 `fs.read` / `db.query` / `http.fetch`,就能读到
  它能拼出路径/语句/URL 的**一切**数据——读权限是"全有或全无";
- 多租户/多用户场景(Web 服务同时给多个人跑 run)没有隔离判据:
  所有 run 以同一个进程身份读同一份文件系统;
- 审计无法回答"谁读了什么"——principal 恒 None,信号里没有身份。

目标:**以 principal(谁)对 resource(什么数据)的 action(读/列举/元数据)
做授权判定,默认拒绝**;写类 action 不归本文档(归档位升权),但数据层
判定的结果要成为升权确认的输入之一(见 §5.3)。

## 2. Principal:身份模型

### 2.1 定义

```python
# api/v1/principal.py(新)
@dataclass(frozen=True)
class Principal:
    subject: str                    # "user:hengxiao" | "service:ci-bot" | "agent:run-..."
    issuer: str                     # 谁认证的:"cli" | "web-session" | "api-token" | "host-embedded"
    attrs: Mapping[str, str]        # 属性:team/role/clearance 等,供 ABAC 判定
```

### 2.2 来源(authN)

| 入口 | 认证方式 | principal 构造 |
|---|---|---|
| CLI(`agent-os run`) | 本机用户即身份(无密码,信任本机账户) | `user:$USER`,issuer=cli |
| Web 服务 | 会话/token(宿主配置);未配置时单用户模式 = 部署者 | `user:<login>`,issuer=web-session |
| API token | `Authorization: Bearer`,token → subject 映射在宿主配置 | issuer=api-token |
| 嵌入宿主(KernelBuilder) | 宿主注入(已有 `supervisor(handler)` 同形扩展点) | issuer=host-embedded |

v1 不做 SSO/OIDC 集成;宿主配置里静态映射即可,协议面(principal 字段)
先稳定。

### 2.3 派生规则(不可自升)

- **run 的 principal = 启动者的 principal**,存 RunContext,随 checkpoint 序列化;
- **子帧原样继承父帧 principal**——skill 嵌套、升权帧、code 技能沙箱,
  全部不改变身份。**升权改的是副作用许可,不是身份**:一个普通用户启动的
  run,即便升权进入 L3 skill,数据层能读的仍是这个用户能读的;
- agent 作为调用方(另一个 run 触发本 run)时,principal 可降级为
  `agent:<run_id>` 并带上游 principal 链(attrs["via"]),授权判定按
  **链上最弱一环**——防"托高权的 agent 帮忙读"。

## 3. authZ:授权模型

### 3.1 资源模型:数据域(data domain)

不给每个文件/表做 ACL(太重)。数据按**域**归类,域是授权单位:

```python
@dataclass(frozen=True)
class DataDomain:
    name: str                       # "fs.workdir" | "fs.shared" | "db.analytics" | "net.intranet" | ...
    sensitivity: str                # "public" | "internal" | "confidential"
```

- 工具在 `ToolSpec` 新增 `data_domains: list[str]`——声明自己会碰哪些域
  (`fs.read` → ["fs.*"],`db.query` → ["db.*"],http 工具 → ["net.*"]);
- 宿主配置(agent-os.toml)给每个域绑具体边界(路径前缀/库表/URL 前缀)
  与敏感度;**未配置的域默认 confidential**——忘了配 = 最严,不是最松。

### 3.2 判定(ABAC,默认拒绝)

```
allow(principal, domain, action) ⟺
    clearance_of(principal) >= sensitivity_of(domain)
    and domain in principal 的域白名单(宿主配置 per subject/role)
```

- clearance 三级与 sensitivity 对应:public < internal < confidential;
- action 维度的读类:`read | list | metadata`;写类不在此判定(升权系统管),
  但**写类工具的 dispatch 同样要过域检查**——authZ 决定"碰不碰得到",
  升权决定"允不允许改",两个都过才放行;
- 判定结果 + 判据(命中哪条规则)进 `ToolContext.credentials`,工具可自省,
  审计信号可关联。

### 3.3 强制点(enforcement)

`LocalPythonToolRegistry.dispatch`(local_registry.py:174-272)在 schema 校验后、
三层权限交集前,插入数据层检查:

1. 工具声明的 `data_domains` ∩ 本次参数解析出的实际目标(路径/库表/URL
   前缀匹配宿主配置的域边界)→ 得到本次实际访问的域集合;
2. 逐域 `allow(principal, domain, action)`;任一拒绝 → DATA_ACCESS_DENIED
   结构化错误(带域名与所需 clearance,**不泄漏域内内容**);
3. 域解析失败(路径/URL 不属于任何已配置域)→ 按 confidential 处理。

`resolve_work_path`(fs 沙箱)保留,它管"能不能出 workdir",数据域管
"这个 principal 能不能读这片",两层正交。

## 4. 机密数据进入 context 之后

v1 **不做 taint tracking**(标记机密内容并跟踪其在 context 里的流动)——
成本高且对 LLM 不可强制。防线放在两端:

- **入口**:authZ(§3)保证读到的都是 principal 有权读的;
- **出口**:外泄必须过副作用闸——NET 发送类工具是 L2+ 推导档,
  跨层调用被升权确认拦住;同层直接调 NET 工具则由 manifest 白名单 +
  `ToolPolicy.max_permission` 约束(现状)。即"读得合法 ≠ 发得出去"。

已知残余风险(文档明示,不假装解决):同 run 内,低层 skill 读到机密后
写进自己的输出,高层 skill 在同 run 内可见——同 principal 的 run 内部
不做隔离(它是同一个"人"的数据)。多用户隔离由 run 边界承担。

## 5. 与升权系统的接口

1. **身份不变量**:升权帧的 `ToolContext.principal` 原样继承(ESCALATION.md
   §4 的 principal 填充加 `escalated/granted_by` 标记,subject 不变);
2. **双闸串联**:dispatch 顺序 = 数据层 authZ(碰不碰得到)→ 白名单/权限
   交集(允不允许)→ 执行。升权闸在 `_invoke_skill`(进不进得来),数据闸
   在 dispatch(碰不碰得到),各自独立失败;
3. **确认卡片的输入**:EscalationRequest 的展示面可附"本调用将访问的数据域
   与敏感度",人审时同时看到副作用面与数据面(E2 的 UI 工作消费这个字段)。

## 6. 信号与审计

| 信号 | 载荷 | 时机 |
|---|---|---|
| `data.access.denied` | {principal.subject, domain, sensitivity, tool} | 数据层拒绝 |
| `data.access.granted` | {principal.subject, domains, tool} | 数据访问放行(debug 级,可关) |

run 详情页可按 principal 过滤:谁、读了哪些域、被拒几次。

## 7. 对现有代码的改动面

| 模块 | 改动 |
|---|---|
| `api/v1/principal.py`(新) | Principal/DataDomain 数据类、clearance 比较、allow() 判定 |
| `api/v1/tools.py` | `ToolSpec.data_domains`;`ToolContext.principal/credentials` 从预留变实填 |
| `kernel/runner.py` | run 启动时构造/继承 principal,注入 ToolDispatchContext |
| `tools/local_registry.py` | dispatch 插入数据层检查;内置 fs/db/http 工具声明 data_domains |
| `host/web/app.py` | 会话 → principal 映射;单用户模式缺省 |
| 配置(agent-os.toml) | `[data]` 段:域边界、敏感度、per-subject 域白名单 |
| 测试 | 判定矩阵(3 级 clearance × 3 级 sensitivity)/ 默认拒绝 / 未配置域=confidential / 子帧身份不变 / 降代链最弱一环 / dispatch 串联顺序 |

## 8. 分期

| 期 | 内容 |
|---|---|
| D1 ✅ | Principal 模型 + CLI/单用户 Web 来源 + fs 域(复用 resolve_work_path 边界)+ dispatch 强制点 + 默认拒绝。已实现:`api/v1/principal.py`、`SkillFrame.principal` 随帧继承与 checkpoint 往返、dispatch 数据闸(schema 后/权限前)、`ToolSpec.data_domains` + 内置 fs 工具声明 `["fs.*"]`、`register_fs_domain` 宿主 API;759 测试全绿 |

> 实现注(D1):
> 1. **"未配置域按 confidential"在 D1 不生效**——配置段(`[data]`、per-subject
>    域白名单)属 D2,D1 的兼容策略是"**未配置 = 不启用数据层拦截**":目标路径
>    落不进任何已配置域时退化为现状 `resolve_work_path` 沙箱语义,保证既有行为
>    零破坏。**默认拒绝只作用于已配置域**(内置默认域 `fs.workdir`=public +
>    `register_fs_domain` 程序化注册的域);D2 配置段落地后恢复"未配置域按
>    confidential"的原文语义。
> 2. 存放点在**帧**(`SkillFrame.principal`)而非 RunContext:dispatch 只见帧,
>    帧级存放天然给出"子帧/升权帧原样继承"的身份不变量(make_frame 复制),
>    checkpoint 随帧序列化;`Kernel.run(principal=)`/`execute_run(principal=)`
>    是宿主的注入点。
> 3. 来源 clearance:CLI 与 Web 单用户都给 confidential——单用户 = 机器的主人,
>    与"D1 默认不拦截"的单用户语义一致;拦截只对显式构造的低 clearance 身份
>    (测试/嵌入宿主)生效。`[web].user` 提供部署者登录名,缺省 `user:$USER`。
> 4. `allow()` 的第二判据(per-subject 域白名单)与 `ToolContext.credentials`
>    判据回写依赖配置段,属 D2;`action` 参数为协议面占位,D1 不参与判定。
| D2 | 域配置段 + db/net 工具声明 + 审计信号 + 拒绝面不泄内容检查 |
| D3 | 派生链最弱一环 + EscalationRequest 数据面展示 + 多用户 Web 会话映射 |

## 9. 不做

- 不做 taint tracking / 机密内容在 context 内的流动标记(v1 靠入口 authZ +
  出口写闸,§4 明示残余风险);
- 不做 per-文件/per-行级 ACL(域是最细粒度);
- 不做 SSO/OIDC/企业目录集成(协议面先稳定,宿主静态映射);
- 不做加密/密钥管理(数据在存储层的保护是部署问题,不是 agent 运行时问题);
- 不改变升权系统的任何语义——两套系统正交,共享的只有 principal 字段。
